# -*- coding: utf-8 -*-
import logging
from uuid import uuid4
from mongoengine import *
from inspect import currentframe

from enums import Status
from .tool_settings import Settings
from .tool_database import connect_database
from .entity_credentials import Credentials
from .tool_clock import as_utc, utc_now


settings = Settings()
log = logging.getLogger(settings.environment)
connect_database(settings)


class ChatBot(Document):
    uuid = UUIDField(required=True, unique=True, default=uuid4)
    clientId = UUIDField(required=True)
    name = StringField(required=True)
    role = StringField(required=True)
    description = StringField(required=True)
    tone = StringField(required=True)
    verifyToken = StringField()
    facebookToken = StringField()
    whatsappToken = StringField()
    appSecret = StringField()
    # The WhatsApp Business Account whose templates the chatbot sends (HIG-85). An id of Meta, not a secret.
    wabaId = StringField(regex=r"^[0-9]{5,25}$")
    createdAt = DateTimeField(required=True, default=utc_now)
    status = IntField(required=True, default=Status.REG.value)
    statusDate = DateTimeField(required=True, default=utc_now)
    # The number of the last version of its configuration (RF26, ADR-0035): 0 until the first one is written.
    configVersion = IntField(default=0)

    def chatbot_info(self) -> dict:
        log.info(f"Executing: {currentframe().f_code.co_name}")
        return {
            "uuid": self.uuid,
            "name": self.name,
            "rol": self.role,
            "tone": self.tone,
            # Its state by name, as the surfaces show it (HIG-74): REG till Meta reaches it, ACT, ERR... (bot_state).
            "status": Status(self.status).name,
            "statusDate": as_utc(self.statusDate),
            # Which Meta credentials are loaded, never their values: those go in and do not come out.
            "credentials": {
                "facebookToken": bool(self.facebookToken),
                "whatsappToken": bool(self.whatsappToken),
                "appSecret": bool(self.appSecret),
                "wabaId": bool(self.wabaId)
            }
        }

    def generate_verification_token(self):
        log.info(f"Executing: {currentframe().f_code.co_name}")
        _cred = Credentials()
        _cred.set_claims(claim=self.name)
        _cred.set_jwt()
        _cred.jwt.make_signed_token(_cred.key)
        # log.info(_cred.to_json())
        # _cred.save()
        self.verifyToken = _cred.jwt.serialize()
