# -*- coding: utf-8 -*-
import logging

from faker import Faker
from pydantic import BaseModel

from .tool_settings import Settings
from constants import JWE, JWE_ENC, JWE_ALGO

fk = Faker()
settings = Settings()
log = logging.getLogger(settings.environment)


class ProtectedHeader(BaseModel):
    alg: str = JWE_ALGO
    enc: str = JWE_ENC
    typ: str = JWE
    kid: str
