# -*- coding: utf-8 -*-
"""
    What a client's messages to its contacts need stored (ADR-0031): the copy of the WhatsApp templates of each
    chatbot, with their state, and each request to send, so that a repeated one is not sent twice.

    Neither holds a number nor the text of a message: the text travels in the request on the bus and stays, encrypted,
    in the history.
"""
from datetime import timedelta

from mongoengine import DateTimeField, Document, IntField, StringField, UUIDField

from .tool_clock import utc_now

# The two categories of template a client may send in the v1 (ADR-0031): Hive has no use for authentication codes.
MARKETING, UTILITY = "MARKETING", "UTILITY"
CATEGORIES = (MARKETING, UTILITY)
APPROVED = "APPROVED"
# How long a request is remembered by its idempotency key (ADR-0031): the same key within this time is the same send.
IDEMPOTENCY_WINDOW = timedelta(hours=24)


class MessageTemplate(Document):
    """
        A WhatsApp template of a chatbot, as Hive last saw it in Meta (HIG-85, HIG-86). Only one that says APPROVED
        is sent. The chatbot, the name and the language are its key: Meta names a template by the two.
    """
    key = StringField(primary_key=True)
    clientId = UUIDField(required=True)
    botId = UUIDField(required=True)
    name = StringField(required=True, max_length=512)
    language = StringField(required=True, max_length=15)
    category = StringField(required=True)
    status = StringField(required=True)
    # How many {{n}} the body takes: a send has to bring exactly that many values.
    parameters = IntField(required=True, min_value=0, default=0)
    updatedAt = DateTimeField(required=True, default=utc_now)

    meta = {"collection": "templates", "indexes": [("clientId", "botId")]}

    @staticmethod
    def key_for(bot_id, name: str, language: str) -> str:
        return f"{bot_id}:{language}:{name}"


class OutboundRequest(Document):
    """
        One request of a client to send a message to a contact, under the idempotency key it came with. The client and
        the key are the primary key, so two requests with the same key cannot both be stored, and MongoDB forgets
        it 24 hours later by itself.
    """
    key = StringField(primary_key=True)
    clientId = UUIDField(required=True)
    uuid = UUIDField(required=True)
    botId = UUIDField(required=True)
    contactId = UUIDField(required=True)
    # A hash of what was asked for: the same key with something else is not the same request.
    fingerprint = StringField(required=True)
    kind = StringField(required=True, choices=("text", "template"))
    template = StringField()
    # Who asked: the key of the machine credential, or the person, when there are persons (ADR-0029).
    sentBy = StringField(required=True)
    createdAt = DateTimeField(required=True, default=utc_now)
    expiresAt = DateTimeField(required=True)

    meta = {"collection": "outbound", "indexes": [{"fields": ["expiresAt"], "expireAfterSeconds": 0},
                                                  ("clientId", "botId", "contactId")]}

    @staticmethod
    def key_for(client_id, idempotency_key: str) -> str:
        return f"{client_id}:{idempotency_key}"
