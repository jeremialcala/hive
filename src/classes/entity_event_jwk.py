# -*- coding: utf-8 -*-
import logging
from mongoengine import *
from .tool_settings import Settings
from .tool_database import connect_database


settings = Settings()
log = logging.getLogger(settings.environment)
connect_database(settings)


class EventJwk(Document):
    eventId = UUIDField(required=True)
    jwkId = UUIDField(required=True)
