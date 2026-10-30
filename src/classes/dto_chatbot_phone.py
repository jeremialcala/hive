# -*- coding: utf-8 -*-
import logging

from faker import Faker
from pydantic import BaseModel, Field

from .tool_settings import Settings

fk = Faker()
settings = Settings()
log = logging.getLogger(settings.environment)


class ChatbotPhoneRequest(BaseModel):
    environment: str | None = Field(default="development")
    phoneNumber: str = Field(default="15550097517")
    phoneId: str = Field(default="111000594951366")
