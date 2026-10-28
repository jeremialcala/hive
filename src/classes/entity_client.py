# -*- coding: utf-8 -*-
import logging
from uuid import uuid4

from mongoengine import *

from enums import Status, Role
from .tool_settings import Settings
from .tool_database import connect_database
from .tool_clock import utc_now

settings = Settings()
log = logging.getLogger(settings.environment)

connect_database(settings)


class Client(Document):
    """
        A customer of the service: the owner of chatbots and of everything stored for them. Every document that
        holds a client's data carries this uuid as its clientId.
    """
    uuid = UUIDField(required=True, unique=True, default=uuid4)
    name = StringField(required=True, unique=True)
    role = StringField(required=True, default=Role.CLIENT.value, choices=[role.value for role in Role])
    # The public keys the client signs its tokens with: one normally, two while it rotates. Never a private one.
    signingKeys = ListField(DictField())
    # The public keys the response engine signs with when it acts for this client. Whoever signs with one of
    # these is not the client: it reads what it needs to answer for the client's chatbots, and nothing else.
    serviceKeys = ListField(DictField())
    # Not used yet: the client's data key, wrapped by the master key.
    wrappedDataKey = StringField()
    createdAt = DateTimeField(required=True, default=utc_now)
    status = IntField(required=True, default=Status.REG.value)
    statusDate = DateTimeField(required=True, default=utc_now)
