# -*- coding: utf-8 -*-
"""
    The event the webhook publishes on the bus: one per notification that brings any message from a person, with all
    of them, not yet identified (ADR-0021, EventoWebhook in asyncapi-v2.yaml). One event and not one per message:
    each write costs an fsync on Bifröst's disk, and a notification of ten messages would not fit in time.

    And one per notification that brings notices of the templates' states, on `webhook.meta.templates`
    (CambioDePlantilla, HIG-86): the same stream, and the same consumer of the ingestion.
"""
import hashlib
import json
import logging
import secrets
from datetime import datetime, timezone

from classes import ChatBot, Settings
from shared import bus, tracing
# The version of the events, written once for the gateway and the engine.
from shared.contract import SCHEMA_VERSION

from .incoming import MESSENGER, WHATSAPP, IncomingMessage, TemplateChange

__all__ = ["CHANNELS", "TEMPLATES", "SCHEMA_VERSION", "event_message_id", "traceparent", "webhook_event",
           "publish_notification", "template_event", "template_message_id", "publish_template_changes"]

log = logging.getLogger(Settings().environment)

# The kind of object Meta notifies about, and the channel it is on the bus: the last token of the subject.
CHANNELS = {WHATSAPP: "whatsapp", MESSENGER: "messenger"}
# The last token of the subject of the notices of templates, and the `channel` their event says.
TEMPLATES = "templates"


def event_message_id(bot: ChatBot, messages: list[IncomingMessage]) -> str:
    """
        What the bus drops a second copy of the event by: the chatbot and a digest of the identifiers Meta gave its
        messages, in any order. Meta sends the same notification again with the same messages.
    """
    digest = hashlib.sha256(",".join(sorted(message.message_id for message in messages)).encode()).hexdigest()
    return f"{bot.uuid}:{digest}"


def traceparent() -> str:
    """
        The W3C context the event goes with: the one of the current span (I5), so that whoever reads the event goes
        on with the same trace. Outside a span, one of its own.
    """
    return tracing.traceparent() or f"00-{secrets.token_hex(16)}-{secrets.token_hex(8)}-01"


def webhook_event(channel: str, bot: ChatBot, messages: list[IncomingMessage], received_at: datetime) -> dict:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "botId": str(bot.uuid),
        "clientId": str(bot.clientId),
        "channel": channel,
        "receivedAt": received_at.isoformat(),
        "messages": [{"messageId": message.message_id, "message": message.content} for message in messages],
    }


async def publish_notification(kind: str, bot: ChatBot, messages: list[IncomingMessage]) -> bus.Published:
    """
        Publishes the notification as one event and returns once the bus has kept it. Raises BusUnavailable: the
        webhook answers 503 and Meta sends the notification again.
    """
    channel = CHANNELS[kind]
    event = webhook_event(channel, bot, messages, datetime.now(timezone.utc))
    published = await bus.publisher().publish(
        f"webhook.meta.{channel}", json.dumps(event, separators=(",", ":")).encode("utf-8"),
        event_message_id(bot, messages), headers={"traceparent": traceparent()})
    if published.duplicate:
        log.info(f"A notification for chatbot {bot.uuid} was already on the bus")
    return published


def template_event(bot: ChatBot, changes: list[TemplateChange], received_at: datetime) -> dict:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "botId": str(bot.uuid),
        "clientId": str(bot.clientId),
        "channel": TEMPLATES,
        "receivedAt": received_at.isoformat(),
        "changes": [change.as_event() for change in changes],
    }


def template_message_id(bot: ChatBot, changes: list[TemplateChange]) -> str:
    """
        What the bus drops a second copy by: the chatbot and a digest of the notices, when included. A template
        approved, paused and approved again gives three events: their times are not the same.
    """
    digest = hashlib.sha256(json.dumps([change.as_event() for change in changes], sort_keys=True).encode()).hexdigest()
    return f"{bot.uuid}:{TEMPLATES}:{digest}"


async def publish_template_changes(bot: ChatBot, changes: list[TemplateChange]) -> bus.Published:
    """Like publish_notification: returns once the bus kept the event, and raises BusUnavailable if it did not."""
    event = template_event(bot, changes, datetime.now(timezone.utc))
    published = await bus.publisher().publish(
        f"webhook.meta.{TEMPLATES}", json.dumps(event, separators=(",", ":")).encode("utf-8"),
        template_message_id(bot, changes), headers={"traceparent": traceparent()})
    if published.duplicate:
        log.info(f"The notices of templates for chatbot {bot.uuid} were already on the bus")
    return published
