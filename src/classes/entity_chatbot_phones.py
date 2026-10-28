# -*- coding: utf-8 -*-
import logging
from uuid import uuid4
from mongoengine import *
from enums import Status, Environment
from .tool_settings import Settings
from .tool_database import connect_database
from .dto_chatbot_phone import ChatbotPhoneRequest
from .tool_clock import as_utc, utc_now

settings = Settings()
log = logging.getLogger(settings.environment)
connect_database(settings)


class ChatbotPhone(Document):
    uuid = UUIDField(required=True, unique=True, default=uuid4)
    clientId = UUIDField(required=True)
    bot_id = UUIDField(required=True)
    environment = StringField(required=True, default="development")
    phoneNumber = StringField()
    phoneId = StringField()
    createdAt = DateTimeField(required=True, default=utc_now)
    status = IntField(required=True, default=Status.REG.value)
    statusDate = DateTimeField(required=True, default=utc_now)

    def get_phones_info(self):
        return {
            "uuid": str(self.uuid),
            "environment": self.environment,
            "status": Status(self.status).name,
            "createdAt": as_utc(self.createdAt)
        }

    @staticmethod
    def generate_chatbot_phone_from_request(req: ChatbotPhoneRequest):
        return ChatbotPhone(
            environment=req.environment if req.environment is not None else Environment.development.name,
            phoneNumber=req.phoneNumber,
            phoneId=req.phoneId
        )
