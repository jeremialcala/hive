# -*- coding: utf-8 -*-
"""
    The events of version 2 that cross between the gateway and the response engine, as models
    (docs/02-design/contracts/asyncapi-v2.yaml): what the ingestion publishes for the engine, what the management API
    asks the notifier to send for a client (ADR-0031), and what the engine's notifier publishes back for the history. Both sides validate against these, so a change of the contract is a
    change here, tested on both sides at once (ADR-0023).

    They read what comes in strictly: a field missing or of another type is a broken event, not a default.
"""
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 2

Channel = Literal["whatsapp", "messenger", "instagram"]

# Where the management API says that a chatbot's configuration changed (ADR-0035): plain NATS, not a stream.
CONFIGURED_SUBJECT = "gateway.bot.configured"


def identified_subject(partition: int, bot_id, contact_id) -> str:
    """Where the ingestion publishes a message identified, in the stream MENSAJES."""
    return f"gateway.message.{partition}.{bot_id}.{contact_id}"


def outbound_subject(partition: int, bot_id, contact_id) -> str:
    """Where the management API publishes a client's request to send to a contact, in the stream SALIENTES."""
    return f"gateway.outbound.{partition}.{bot_id}.{contact_id}"


def reply_subject(bot_id) -> str:
    """Where the notifier publishes what it sent, in the stream RESPUESTAS."""
    return f"engine.reply.sent.{bot_id}"


def failure_subject(bot_id) -> str:
    """Where the notifier publishes a request of a client it could not send, in the stream RESPUESTAS (HIG-87)."""
    return f"engine.reply.failed.{bot_id}"


class _Event(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    schemaVersion: Literal[2]
    botId: UUID
    # The client that owns the chatbot: which key the worker signs with (ADR-0022).
    clientId: UUID


class BotConfigured(_Event):
    """
        BotConfigurado: the management API changed the configuration of a chatbot, and says which version it is at now
        (RF26, ADR-0035). A notice on CONFIGURED_SUBJECT that nobody keeps: whoever remembers the chatbot forgets what
        it had from a version before this one. Only identifiers and the number.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)

    uuid: UUID
    version: int = Field(ge=1, strict=True)
    configuredAt: datetime


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


# Who a message sent to a contact comes from: the bot answering, or the client writing on its own (ADR-0031).
Origin = Literal["engine", "client"]


class TemplateToSend(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=512)
    language: str = Field(min_length=2, max_length=15)
    category: Literal["MARKETING", "UTILITY"]
    # In order, as the template's {{1}}, {{2}}... Personal data, like a text.
    parameters: list[str] = Field(default_factory=list, max_length=20)


class OutboundRequest(_Event):
    """
        PedidoDeEnvio: what a client asks the notifier to send to a contact (ADR-0031), on SALIENTES. It carries the
        number and the account of the bot it goes from, as a message of MOTOR does: personal data, on a bus that is
        encrypted at rest and reached by TLS.
    """
    uuid: UUID
    contactId: UUID
    channel: Literal["whatsapp"]
    kind: Literal["text", "template"]
    sentBy: str = Field(min_length=1, max_length=200)
    requestedAt: datetime
    # The contact's WhatsApp number, and the phone_number_id of the bot's number it last wrote to.
    to: str = Field(pattern=r"^[0-9]{6,20}$")
    phoneId: str = Field(pattern=r"^[0-9]{1,32}$")
    text: str | None = Field(default=None, min_length=1, max_length=4096)
    template: TemplateToSend | None = None


class Evaluation(BaseModel):
    """
        What the engine's evaluator decided of an answer (ADR-0012 of the engine, HIG-147), without its text (RS07):
            approved     it complied the first time
            rewritten    it did not, and the rewritten one does
            replaced     neither did, and the fixed text of "I cannot" went instead
            fixed        a fixed text, which is not judged
            unevaluated  it could not be judged, and the fixed text of "not available" went instead
        `criterion` and `rule` are those of the last judgement that failed, if any; `rule` counts from 1, or is 0.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)

    verdict: Literal["approved", "rewritten", "replaced", "fixed", "unevaluated"]
    criterion: Literal["none", "rule", "invented", "contact", "language", "tone", "format", "instructions",
                       "role"] = "none"
    rule: int = Field(default=0, ge=0, le=50)
    model: str | None = Field(default=None, max_length=100)
    # How long the evaluator took to judge it, the last time it did.
    seconds: float | None = Field(default=None, ge=0, le=600)


class SentReply(_Event):
    """RespuestaEnviada: what the notifier sent and Meta accepted, for the history of the conversation."""
    contactId: UUID
    channel: Channel
    # The Meta id of the message it answers. A message the client sent on its own answers nothing.
    inReplyTo: str | None = Field(default=None, min_length=1)
    # The id Meta gave to what was sent.
    messageId: str = Field(min_length=1)
    sentAt: datetime
    text: str = Field(max_length=4096)
    attachments: list[SentAttachment] = []
    origin: Origin = "engine"
    # Of a client's message: who asked for it, and the request it came from (ADR-0031).
    sentBy: str | None = Field(default=None, min_length=1, max_length=200)
    requestId: UUID | None = None
    # Of an answer of the bot, what the evaluator decided (HIG-147). A message of the client is not judged.
    evaluation: Evaluation | None = None


class FailedSend(_Event):
    """
        EnvioFallido (HIG-87): a request of the client that the notifier did not send, and will not. Meta's code, when
        it was Meta that said no, or the last one it gave before the notifier gave up; and the reason, a word of the
        notifier: rejected, no_token, gave_up. A code of the token or its permissions puts the chatbot in ERR (HIG-74).
    """
    contactId: UUID
    channel: Channel
    requestId: UUID
    code: int | None = None
    reason: Literal["rejected", "no_token", "gave_up"]
    failedAt: datetime


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
