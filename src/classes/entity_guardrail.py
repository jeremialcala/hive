# -*- coding: utf-8 -*-
import logging
from uuid import uuid4

from mongoengine import DateTimeField, Document, StringField, UUIDField

from .tool_clock import utc_now
from .tool_database import connect_database
from .tool_settings import Settings

settings = Settings()
log = logging.getLogger(settings.environment)

connect_database(settings)

BLOCK, LOG = "block", "log"


class Guardrail(Document):
    """
        A topic of its own that a client keeps a chatbot away from (RF27, HIG-103), above the base of the platform,
        which nobody turns off. The engine's classifier is told of each with the chatbot's configuration: a message on
        it is either answered with `reply` and goes no further (`block`), or goes on and is only recorded (`log`).

        It is the client's configuration, not what its contacts said: it is versioned with the persona and the rules
        (RF26) and goes with the chatbot and the client.
    """
    uuid = UUIDField(required=True, unique=True, default=uuid4)
    clientId = UUIDField(required=True)
    botId = UUIDField(required=True)
    topic = StringField(required=True)
    description = StringField(required=True)
    action = StringField(required=True, choices=(BLOCK, LOG))
    # What the contact gets instead of an answer, when the topic blocks.
    reply = StringField()
    createdAt = DateTimeField(required=True, default=utc_now)

    meta = {"collection": "guardrails"}

    def info(self) -> dict:
        return {"uuid": str(self.uuid), "topic": self.topic, "description": self.description, "action": self.action,
                "reply": self.reply}
