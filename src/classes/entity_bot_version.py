# -*- coding: utf-8 -*-
"""
    One version of the configuration of a chatbot (RF26, ADR-0035): its persona, rules and intents as they were right
    after a change, who made it and when. A version is written once and never changed; going back to one writes a new
    one. Neither the Meta credentials nor the verifyToken are in it.
"""
import logging

from mongoengine import DateTimeField, DictField, Document, IntField, StringField, UUIDField

from .tool_clock import utc_now
from .tool_database import connect_database
from .tool_settings import Settings

settings = Settings()
log = logging.getLogger(settings.environment)
connect_database(settings)


class BotVersion(Document):
    # "<botId>:<version>": two changes at once cannot write the same number, whatever indexes the database holds.
    id = StringField(primary_key=True)
    clientId = UUIDField(required=True)
    botId = UUIDField(required=True)
    version = IntField(required=True, min_value=1)
    # Who made the change, as the log writes it: "person <uuid>", "key <kid>", or "hive" for what was there before.
    author = StringField(required=True)
    change = StringField(required=True)
    # The version this one went back to, when it is a restore.
    restoredFrom = IntField()
    config = DictField(required=True)
    createdAt = DateTimeField(required=True, default=utc_now)

    meta = {"collection": "bot_versions"}
