# -*- coding: utf-8 -*-
import logging

from faker import Faker
from pydantic import BaseModel

from .tool_settings import Settings

fk = Faker()
settings = Settings()
log = logging.getLogger(settings.environment)


class MessageBody(BaseModel):
    botId: str
    # Whose chatbot it is: the response engine picks the credential it holds for that client.
    clientId: str
    type: str
    contact: dict
    message: dict
