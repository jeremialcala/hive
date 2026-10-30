# -*- coding: utf-8 -*-
import logging

from faker import Faker
from pydantic import BaseModel, Field

from .tool_settings import Settings

fk = Faker()
settings = Settings()
log = logging.getLogger(settings.environment)


class RulesRequest(BaseModel):
    rules: list = Field(default=["If a question does not make any sense,or is not factually coherent",
                                 "If you don't know the answer to a question, please don't share false information."])

