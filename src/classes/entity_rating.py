# -*- coding: utf-8 -*-
import logging

from mongoengine import *

from .tool_settings import Settings
from .tool_database import connect_database
from .tool_clock import utc_now

settings = Settings()
log = logging.getLogger(settings.environment)

connect_database(settings)

UP, DOWN = "up", "down"


class Rating(Document):
    """
        What a person of the client thought of an answer of the bot (RF28, HIG-111): good or bad, and why if they
        said. Decided on 2026-10-31: a collection of its own (D9), one per person and answer, which they can change.

        It goes with the contact, the chatbot and the client, and it expires when the answer it rates does. The
        comment can repeat what the conversation said: it is stored encrypted with the client's data key.
    """
    # The chatbot, the answer (Meta's id of what was sent) and who rated it: rating again changes it.
    key = StringField(primary_key=True)
    clientId = UUIDField(required=True)
    botId = UUIDField(required=True)
    contactId = UUIDField(required=True)
    messageId = StringField(required=True)
    score = StringField(required=True, choices=(UP, DOWN))
    comment = StringField()
    # A person, as the log writes it: "person <uuid>". Never the email.
    ratedBy = StringField(required=True, max_length=200)
    ratedAt = DateTimeField(required=True, default=utc_now)
    # When the answer it rates expires: then this goes too.
    expiresAt = DateTimeField(required=True)

    meta = {
        "collection": "ratings",
        "indexes": [
            {"fields": ["expiresAt"], "expireAfterSeconds": 0},
            # The ratings of a conversation, and those of a chatbot for its metrics.
            ("clientId", "botId", "contactId"),
        ],
    }

    @staticmethod
    def key_for(bot_id, message_id: str, rated_by: str) -> str:
        return f"{bot_id}:{message_id}:{rated_by}"
