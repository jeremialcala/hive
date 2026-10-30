# -*- coding: utf-8 -*-
import logging

from uuid import UUID
from faker import Faker
from pydantic import BaseModel, Field

from .tool_settings import Settings

fk = Faker()
settings = Settings()
log = logging.getLogger(settings.environment)


class EventTransport(BaseModel):
    uuid: UUID
    resource: str
    operation: str
    origen: str
    result: str | None = Field(default=None)
