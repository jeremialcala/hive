# -*- coding: utf-8 -*-
import logging
from datetime import datetime, timezone
from uuid import uuid4
from mongoengine import *
from jwcrypto import jwe
from inspect import currentframe

from enums import Status

from .tool_settings import Settings
from .tool_database import connect_database
from .entity_jwk import EventJwk
from .entity_jwk import Jwk
from .dto_protected_header import ProtectedHeader
from .dto_message_body import MessageBody


settings = Settings()
log = logging.getLogger(settings.environment)
connect_database(settings)


def _now():
    # With its time zone: the instant in the envelope must not depend on the clock setting of the machine.
    return datetime.now(timezone.utc)


class QueueMessage(Document):
    # Version of the envelope, as described in docs/02-design/contracts/asyncapi.yaml.
    schemaVersion = IntField(required=True, default=1)
    _uuid = UUIDField(required=True, unique=True, default=uuid4)
    # The identifier Meta gave the message: the same one when Meta delivers it again.
    messageId = StringField(required=True)
    eventId = UUIDField(required=True)
    endpoint = StringField()
    operation = StringField()
    jwe_body = StringField()
    createdAt = DateTimeField(required=True, default=_now)
    status = IntField(required=True, default=Status.REG.value)
    statusDate = DateTimeField(required=True, default=_now)

    def set_jwe_body(self, body: MessageBody):
        log.info(f"Starting: {currentframe().f_code.co_name}")
        try:
            ev_jk = [ev_jk for ev_jk in EventJwk.objects(eventId=self.eventId)][-1]
            log.info(ev_jk)

            _jwk = Jwk.get_jwk_from_uuid(ev_jk.jwkId)
            log.info(_jwk)

            _protected_header = ProtectedHeader(kid=_jwk.x5t)
            log.info(_protected_header.json())

            _jwe_tk = jwe.JWE(
                body.json().encode("utf-8"),
                recipient=_jwk.get_jwk(),
                protected=_protected_header.__dict__
            )

            self.jwe_body = _jwe_tk.serialize(compact=True)
            log.info(f"Ending: {currentframe().f_code.co_name}")
        except Exception as e:
            log.error(type(e).__name__)

