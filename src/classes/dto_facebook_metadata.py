# -*- coding: utf-8 -*-
import logging

from faker import Faker
from pydantic import BaseModel, Field

from .tool_settings import Settings

fk = Faker()
settings = Settings()
log = logging.getLogger(settings.environment)


class Metadata(BaseModel):
    display_phone_number: str = Field(default=["12025550101"])
    phone_number_id: str = Field(default=["269126859616372"])
