# -*- coding: utf-8 -*-
import logging

from mongoengine import *

from .tool_settings import Settings
from .tool_database import connect_database
from .tool_clock import utc_now

settings = Settings()
log = logging.getLogger(settings.environment)

connect_database(settings)

INCOMING, OUTGOING = "in", "out"



class ConversationMessage(Document):
    """
        One message of a conversation, in either direction: the history of ADR-0017. It is what a contact's export
        carries, what goes with the contact when it is deleted, and what tells a message Meta sent again from a new
        one.

        The chatbot, the direction and Meta's identifier together are the primary key, not a separate unique index:
        MongoDB always enforces the primary key, while an index disappears with the collection and is only created
        again when the service restarts. Meta sends a WhatsApp message again for up to 7 days.

        Who wrote is the contact's uuid, never the number nor the Messenger id. The text is stored encrypted with the
        client's data key; of an attachment, only its type: Meta's address expires and can carry the contact's data.
    """
    key = StringField(primary_key=True)
    clientId = UUIDField(required=True)
    botId = UUIDField(required=True)
    contactId = UUIDField(required=True)
    direction = StringField(required=True, choices=(INCOMING, OUTGOING))
    messageId = StringField(required=True)
    channel = StringField(required=True)
    # When Meta says it was sent, in UTC. MongoDB keeps no time zone: it is read back as UTC.
    sentAt = DateTimeField(required=True)
    text = StringField()
    attachments = ListField(StringField())
    intent = StringField()
    # Of an answer, the identifier of Meta of the message it answers.
    inReplyTo = StringField()
    createdAt = DateTimeField(required=True, default=utc_now)
    # MongoDB deletes the message by itself at that time: 12 months at most (RNF05).
    expiresAt = DateTimeField(required=True)

    meta = {
        "collection": "messages",
        "indexes": [
            {"fields": ["expiresAt"], "expireAfterSeconds": 0},
            # A conversation read in order: the export of a contact, and the last turns for the coordinator.
            ("clientId", "botId", "contactId", "sentAt"),
        ],
    }

    @staticmethod
    def key_for(bot_id, direction: str, message_id: str) -> str:
        # The uuid and the direction have a fixed form, so neither can be confused with the identifier of Meta.
        return f"{bot_id}:{direction}:{message_id}"
