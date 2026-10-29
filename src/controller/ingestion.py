# -*- coding: utf-8 -*-
"""
    The ingestion of a notification (ADR-0021): what the webhook used to do before answering Meta, now done after,
    by the ingestion worker (ingest.py), from the event the webhook published on WEBHOOK. For each message of the
    notification, in order:

        1. it is skipped if it is already in the history: Meta sent it again;
        2. the contact is found, or stored, as before, asking the Graph API in Messenger;
        3. the message identified is published on its conversation's partition of MENSAJES, and the envelope of
           version 1 goes to the legacy queue `llm` of Hermod, until Hermod is retired;
        4. the message is stored in the history.

    Publishing goes before storing: if the worker stops in between, the event is delivered again and the message
    published again, and the bus drops the second copy by Nats-Msg-Id. The other way round, a message stored but
    never published would be skipped on the next delivery and lost. The engine has to bear a duplicate anyway.

    Without a bus (development and the tests) the webhook runs this itself, on the event it would have published.

    The same consumer reads the notices of the templates' states (CambioDePlantilla, HIG-86): each one is applied to
    the copy of its template, if it is of the chatbot's WhatsApp Business Account and not older than what the copy
    knows. The send route only sends a copy that says APPROVED.

    What waits on MongoDB, the key service or the Graph API runs in a thread (H14): the worker reads two streams in
    the same event loop, and the client of the bus answers its heartbeats from it.
"""
import asyncio
import hashlib
import json
import logging
from collections import Counter
from datetime import datetime, timezone
from uuid import UUID

from classes import ChatBot, Contact, MessageBody, QueueMessage, Settings
from classes.entity_outbound import APPROVED
from classes.entity_conversation_message import INCOMING, OUTGOING
from crypto.key_service import KeyServiceUnavailable
from repository import NotFound, TenantRepository, database_failed
from shared import bus, tracing
from shared.contract import identified_subject, what_was_said
from utils import describe

from .amqp_broker import BrokerUnavailable, send_message_to_queue
from .facebook import ctr_identify_contact_by_wa_id, ctr_identify_fb_contact
from .incoming import META_ID, WORD, IncomingMessage
from .webhook_event import SCHEMA_VERSION, TEMPLATES, traceparent

__all__ = ["DatabaseUnavailable", "Unprocessable", "RETRY_LATER", "PARTITIONS", "partition", "ingest_notification",
           "ingest_reply"]

settings = Settings()
log = logging.getLogger(settings.environment)

# The queue the response engine reads from, until it reads the bus.
QUEUE = "llm"
# Fixed: changing it means emptying MENSAJES first (ADR-0019).
PARTITIONS = 16


class DatabaseUnavailable(Exception):
    """The database did not do its part while a notification was being processed."""


class Unprocessable(Exception):
    """
        An event that would fail the same way however many times it is delivered: of a version this service does
        not know, or for a chatbot that no longer exists. It is dropped, and the log says why.
    """


# What makes the whole notification wait and be tried again: something the service needs is not there, and it will be.
RETRY_LATER = (BrokerUnavailable, bus.BusUnavailable, KeyServiceUnavailable, DatabaseUnavailable)


def partition(bot_id, contact_id) -> int:
    """
        The partition of a conversation: the first four bytes of the SHA-256 of `<botId>.<contactId>`, as an
        unsigned big-endian integer, modulo 16 (asyncapi-v2.yaml). Each one is read by a single worker, in order.
    """
    digest = hashlib.sha256(f"{bot_id}.{contact_id}".encode()).digest()
    return int.from_bytes(digest[:4], "big") % PARTITIONS


# --- What a message of the event says ---------------------------------------------------------------------------------

def _first(items) -> dict:
    return items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else {}


def _text(value) -> str | None:
    return value if isinstance(value, str) and value else None


def incoming(channel: str, item: dict) -> IncomingMessage:
    """
        A message of the event as the webhook read it: who sent it and what the engine receives. WhatsApp sends
        the value of the change reduced to that message; Messenger, the element of `messaging`.
    """
    content = item["message"]
    if channel == "whatsapp":
        contact, message = _first(content.get("contacts")), _first(content.get("messages"))
        profile = contact.get("profile") if isinstance(contact.get("profile"), dict) else {}
        sender = _text(contact.get("wa_id")) or _text(message.get("from"))
        name = _text(profile.get("name"))
    else:
        sender, name = _text((content.get("sender") or {}).get("id")), None
    if not sender:
        raise ValueError("a message without sender")
    return IncomingMessage(message_id=item["messageId"], sender=sender, name=name, content=content)


# --- Each step --------------------------------------------------------------------------------------------------------

def _identify(channel: str, message: IncomingMessage, bot: ChatBot) -> Contact:
    if channel == "whatsapp":
        contact = ctr_identify_contact_by_wa_id(wa_id=message.sender, name=message.name, _bot=bot)
        # Which of the bot's numbers it wrote to: a message of the client goes from there (ADR-0031).
        metadata = message.content.get("metadata")
        phone_id = _text(metadata.get("phone_number_id")) if isinstance(metadata, dict) else None
        if phone_id and phone_id.isdigit() and isinstance(getattr(contact, "clientId", None), UUID):
            TenantRepository(contact.clientId).remember_phone(contact, phone_id)
        return contact
    return ctr_identify_fb_contact(fbId=message.sender, _bot=bot)


def identified_event(event: dict, item: dict, contact: Contact, message: IncomingMessage) -> dict:
    """MensajeIdentificado (asyncapi-v2.yaml): what the engine's workers read, one per message."""
    who = {"uuid": str(contact._uuid), "name": contact.firstName}
    who["waId" if event["channel"] == "whatsapp" else "fbId"] = message.sender
    return {
        "schemaVersion": SCHEMA_VERSION,
        "botId": event["botId"],
        "clientId": event["clientId"],
        "messageId": item["messageId"],
        "message": item["message"],
        "channel": event["channel"],
        "receivedAt": event["receivedAt"],
        "orderKey": f"{event['botId']}.{contact._uuid}",
        "contact": who,
    }


async def _publish(event: dict, item: dict, contact: Contact, message: IncomingMessage) -> None:
    if bus.configured():
        await bus.publisher().publish(
            identified_subject(partition(event["botId"], contact._uuid), event["botId"], contact._uuid),
            json.dumps(identified_event(event, item, contact, message), separators=(",", ":")).encode("utf-8"),
            f"{event['botId']}:{item['messageId']}", headers={"traceparent": traceparent()})
    body = MessageBody(botId=event["botId"], clientId=event["clientId"], type=_KINDS[event["channel"]],
                       contact={"name": contact.firstName, ("waId" if event["channel"] == "whatsapp" else "fbId"):
                                message.sender},
                       message=message.content)
    await send_message_to_queue(queue=QUEUE, message=QueueMessage(messageId=item["messageId"], jwe_body=body.__dict__))


# What a notice of Meta leaves the copy of a template as, when it is not the notice's own word. REINSTATED is a
# template that was paused and is sent again. FLAGGED says its quality fell: Meta still sends it, until it pauses or
# disables it, each with a notice of its own; the state stays as it was.
STATUS_OF = {"REINSTATED": APPROVED, "FLAGGED": None}


def _apply_template_changes(event: dict, bot: ChatBot) -> dict:
    repo = TenantRepository(bot.clientId)
    received_at = datetime.fromisoformat(event["receivedAt"])
    outcome = Counter()
    for change in event.get("changes") or []:
        if not (isinstance(change, dict) and isinstance(change.get("templateId"), str)
                and META_ID.fullmatch(change["templateId"]) and isinstance(change.get("event"), str)
                and WORD.fullmatch(change["event"])):
            outcome["malformed"] += 1
            continue
        if not bot.wabaId or change.get("account") != bot.wabaId:
            # Of a WhatsApp Business Account that is not the chatbot's: its templates are not this chatbot's.
            outcome["of another account"] += 1
            continue
        at = change.get("at")
        when = (datetime.fromtimestamp(at, timezone.utc) if isinstance(at, int) and not isinstance(at, bool) and at > 0
                else received_at)
        reason = change.get("reason")
        reason = reason if isinstance(reason, str) and WORD.fullmatch(reason) else None
        status = STATUS_OF.get(change["event"], change["event"])
        try:
            applied = repo.apply_template_notice(bot.uuid, change["templateId"], status, reason, when)
        except Exception as e:
            if database_failed(e):
                raise DatabaseUnavailable(type(e).__name__) from None
            raise
        if applied == "applied":
            log.info(f"Template {change['templateId']} of chatbot {bot.uuid} is {change['event']} by Meta's notice")
        outcome[applied] += 1
    if outcome.keys() - {"applied"}:
        log.warning(f"Notices of templates for chatbot {bot.uuid} not applied: {dict(outcome)}")
    return dict(outcome)


# The kind of object of version 1, which the envelope still carries as `type`.
_KINDS = {"whatsapp": "whatsapp_business_account", "messenger": "page"}


async def ingest_notification(event: dict, trace: str | None = None, bot: ChatBot | None = None) -> dict:
    """
        Ingests one EventoWebhook. Raises Unprocessable for an event that cannot ever be ingested, and one of
        RETRY_LATER when it has to wait. A message that fails by itself is left in the log and the rest go on.
        Says how many messages were ingested, skipped as already seen, and failed. The webhook, which has just
        found the chatbot, passes it along so that it is not looked for again.

        `trace` is the traceparent the event came with: the ingestion goes on with that trace, and each message
        identified leaves with it. Without it, the trace is the current one.
    """
    with tracing.span("ingest.notification", parent=trace, kind=tracing.SpanKind.CONSUMER,
                      **{"messaging.system": "nats", "messaging.destination.name": "WEBHOOK"}) as current:
        if not isinstance(event, dict) or event.get("schemaVersion") != SCHEMA_VERSION:
            raise Unprocessable("not an event of version 2")
        try:
            repo = TenantRepository(UUID(event["clientId"]))
            bot = bot or await asyncio.to_thread(repo.get_bot, event["botId"])
        except (NotFound, KeyError, ValueError, TypeError):
            raise Unprocessable("for a chatbot that does not exist") from None
        except Exception as e:
            if database_failed(e):
                raise DatabaseUnavailable(type(e).__name__) from None
            raise
        if event.get("channel") == TEMPLATES:
            current.set(**{"gateway.bot_id": str(bot.uuid), "gateway.client_id": str(bot.clientId),
                           "gateway.channel": TEMPLATES})
            try:
                return await asyncio.to_thread(_apply_template_changes, event, bot)
            except (KeyError, ValueError, TypeError):
                raise Unprocessable("notices of templates without their shape") from None
        current.set(**{"gateway.bot_id": str(bot.uuid), "gateway.client_id": str(bot.clientId),
                       "gateway.channel": event.get("channel"), "gateway.messages": len(event["messages"])})
        received_at = datetime.fromisoformat(event["receivedAt"])
        outcome = {"ingested": 0, "seen": 0, "failed": 0}
        for item in event["messages"]:
            outcome[await _ingest_message(repo, bot, event, item, received_at)] += 1
        return outcome


async def _ingest_message(repo: TenantRepository, bot: ChatBot, event: dict, item: dict, received_at) -> str:
    """One message of the notification, in a span of its own. Says whether it was ingested, seen or failed."""
    with tracing.span("ingest.message", **{"gateway.bot_id": str(bot.uuid)}) as current:
        try:
            if await asyncio.to_thread(repo.has_message, bot.uuid, INCOMING, item["messageId"]):
                current.set(**{"gateway.outcome": "seen"})
                return "seen"
            message = incoming(event["channel"], item)
            contact = await asyncio.to_thread(_identify, event["channel"], message, bot)
            await _publish(event, item, contact, message)
            current.set(**{"gateway.contact_id": str(contact._uuid)})
            text, attachments, sent_at = what_was_said(event["channel"], message.content)
            await asyncio.to_thread(repo.record_message, contact, INCOMING, item["messageId"], event["channel"],
                                    sent_at or received_at, text=text, attachments=attachments)
            current.set(**{"gateway.outcome": "ingested"})
            return "ingested"
        except RETRY_LATER:
            raise
        except Exception as e:
            if database_failed(e):
                log.error(f"The database is unavailable: {describe(e)}")
                raise DatabaseUnavailable(type(e).__name__) from None
            # A message that fails by itself would fail again, and must not take the rest of the notification with it.
            log.error(f"A message for chatbot {bot.uuid} could not be ingested: {describe(e)}")
            current.failed(type(e).__name__)
            current.set(**{"gateway.outcome": "failed"})
            return "failed"


async def ingest_reply(event: dict, trace: str | None = None) -> dict:
    """
        Stores in the history what the engine's notifier sent and Meta accepted: one RespuestaEnviada of RESPUESTAS
        (asyncapi-v2.yaml), as an answer of the contact it went to. Raises Unprocessable for one that can never be
        stored, a contact deleted in the meantime included: its conversation went with it. Says whether it was new.
        It goes on with the trace of the answer, `trace`, which the engine started with the message it answers.
    """
    with tracing.span("ingest.answer", parent=trace, kind=tracing.SpanKind.CONSUMER,
                      **{"messaging.system": "nats", "messaging.destination.name": "RESPUESTAS"}) as current:
        stored = await asyncio.to_thread(_store_reply, event)
        current.set(**{"gateway.bot_id": event.get("botId"), "gateway.client_id": event.get("clientId"),
                       "gateway.contact_id": event.get("contactId"),
                       "gateway.outcome": "stored" if stored["stored"] else "seen"})
        return stored


def _store_reply(event: dict) -> dict:
    if not isinstance(event, dict) or event.get("schemaVersion") != SCHEMA_VERSION:
        raise Unprocessable("not an answer of version 2")
    try:
        repo = TenantRepository(UUID(event["clientId"]))
        contact = repo.get_contact(event["botId"], event["contactId"])
        sent_at = datetime.fromisoformat(event["sentAt"])
        message_id, channel = event["messageId"], event["channel"]
    except (NotFound, KeyError, ValueError, TypeError):
        raise Unprocessable("for a contact that does not exist") from None
    except Exception as e:
        if database_failed(e):
            raise DatabaseUnavailable(type(e).__name__) from None
        raise
    attachments = tuple(each["type"] for each in event.get("attachments") or []
                        if isinstance(each, dict) and _text(each.get("type")))
    try:
        origin = "client" if event.get("origin") == "client" else "engine"
        sent_by = _text(event.get("sentBy")) if origin == "client" else None
        stored = repo.record_message(contact, OUTGOING, message_id, channel, sent_at, text=event.get("text"),
                                     attachments=attachments, in_reply_to=event.get("inReplyTo"), origin=origin,
                                     sent_by=sent_by)
    except NotFound:
        raise Unprocessable("for a contact deleted in the meantime") from None
    except Exception as e:
        if database_failed(e):
            raise DatabaseUnavailable(type(e).__name__) from None
        raise
    return {"stored": stored}
