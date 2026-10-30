# -*- coding: utf-8 -*-
import logging
from random import choice
from faker import Faker
from pydantic import BaseModel, Field
from .tool_settings import Settings

fk = Faker()
settings = Settings()
log = logging.getLogger(settings.environment)


class ChatbotRequest(BaseModel):
    name: str = Field(default=fk.name(), examples=[fk.name() for _ in range(5)])
    role: str = Field(default=choice(["Sales Assistant", "Office Assistant", "Tech Support"]), examples=["Sales Assistant", "Office Assistant", "Tech Support"])
    description: str = Field(default=["Please ensure that your responses are socially unbiased and positive in nature."])
    tone: str = Field(default=choice(["helpful", "respectful", "honest"]))
