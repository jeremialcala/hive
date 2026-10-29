# -*- coding: utf-8 -*-
"""
    The events of version 2 that cross between the gateway and the response engine, as models
    (docs/02-design/contracts/asyncapi-v2.yaml): what the ingestion publishes for the engine, and what the engine's
    notifier publishes back for the history. Both sides validate against these, so a change of the contract is a
    change here, tested on both sides at once (ADR-0023).

    They read what comes in strictly: a field missing or of another type is a broken event, not a default.
"""
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 2

Channel = Literal["whatsapp", "messenger", "instagram"]


def identified_subject(partition: int, bot_id, contact_id) -> str:
    """Where the ingestion publishes a message identified, in the stream MENSAJES."""
    return f"gateway.message.{partition}.{bot_id}.{contact_id}"


def outbound_subject(partition: int, bot_id, contact_id) -> str:
    """Where the management API publishes a client's request to send to a contact, in the stream SALIENTES."""
    return f"gateway.outbound.{partition}.{bot_id}.{contact_id}"


def reply_subject(bot_id) -> str:
    """Where the notifier publishes what it sent, in the stream RESPUESTAS."""
    return f"engine.reply.sent.{bot_id}"


class _Event(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    schemaVersion: Literal[2]
    botId: UUID
    # The client that owns the chatbot: which key the worker signs with (ADR-0022).
    clientId: UUID


class Contact(BaseModel):
    """Who writes. Personal data: the engine needs the id of the channel to answer, and passes none of it to the LLM."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    uuid: UUID
    name: str | None = None
    waId: str | None = None
    fbId: str | None = None
    igsid: str | None = None


class IdentifiedMessage(_Event):
    """MensajeIdentificado: one per message, from the ingestion to the engine's classifier."""
    messageId: str = Field(min_length=1)
    # The value of the WhatsApp change reduced to this message, or one entry[].messaging[] item of Messenger or of
    # Instagram. Personal data.
    message: dict
    channel: Channel
    receivedAt: datetime
    orderKey: str = Field(min_length=1)
    contact: Contact


class SentAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: str


class SentReply(_Event):
    """RespuestaEnviada: what the notifier sent and Meta accepted, for the history of the conversation."""
    contactId: UUID
    channel: Channel
    # The Meta id of the message it answers.
    inReplyTo: str = Field(min_length=1)
    # The id Meta gave to what was sent.
    messageId: str = Field(min_length=1)
    sentAt: datetime
    text: str = Field(max_length=4096)
    attachments: list[SentAttachment] = []


# --- What a message says ----------------------------------------------------------------------------------------------

def _first(items) -> dict:
    return items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else {}


def _text(value) -> str | None:
    return value if isinstance(value, str) and value else None


def what_was_said(channel: str, content: dict) -> tuple[str | None, tuple, datetime | None]:
    """
        What a message says, from the `message` of a MensajeIdentificado: its text, the type of each attachment and
        when Meta says it was sent. Of an attachment, never its address (ADR-0017). The history keeps this, and the
        engine's classifier reads the same, so both agree on what the person said.
    """
    if channel == "whatsapp":
        message = _first(content.get("messages"))
        kind = message.get("type")
        body = message.get(kind) if isinstance(message.get(kind), dict) else {}
        reply = body.get("button_reply") or body.get("list_reply") or {}
        text = (_text(body.get("body")) if kind == "text" else
                _text(reply.get("title")) if kind == "interactive" else
                _text(body.get("text")) if kind == "button" else
                _text(body.get("caption")))
        attachments = () if kind in ("text", "interactive", "button") or not _text(kind) else (kind,)
        sent = message.get("timestamp")
        sent_at = datetime.fromtimestamp(int(sent), timezone.utc) if str(sent).isdigit() else None
    else:
        message = content.get("message") if isinstance(content.get("message"), dict) else {}
        text = _text(message.get("text"))
        attachments = tuple(_text(each.get("type")) or "unknown" for each in message.get("attachments") or []
                            if isinstance(each, dict))
        sent = content.get("timestamp")
        sent_at = datetime.fromtimestamp(sent / 1000, timezone.utc) if isinstance(sent, (int, float)) else None
    return text, attachments, sent_at
