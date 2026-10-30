# -*- coding: utf-8 -*-
import logging
from asyncio import ensure_future
from uuid import uuid4
from mongoengine import *
from enums import Status
from .tool_settings import Settings
from .tool_database import connect_database
from .entity_jwk import Jwk
from .tool_clock import utc_now

settings = Settings()
log = logging.getLogger(settings.environment)
connect_database(settings)


class Event(Document):
    uuid = UUIDField(required=True, unique=True, default=uuid4)
    resource = StringField()
    operation = StringField()
    origen = StringField()
    result = StringField()
    createdAt = DateTimeField(required=True, default=utc_now)
    status = IntField(required=True, default=Status.REG.value)
    statusDate = DateTimeField(required=True, default=utc_now)

    async def prepare_event(self):
        ensure_future(Jwk.create_key_for_event(self.uuid))
        self.save()

