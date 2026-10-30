# -*- coding: utf-8 -*-
import logging
from jwcrypto import jwk, jwt
from uuid import uuid4
from mongoengine import *
from inspect import currentframe
from .tool_settings import Settings
from .tool_database import connect_database


settings = Settings()
log = logging.getLogger(settings.environment)
connect_database(settings)

"""
    This Object generates credentials for token generation
    
"""


class Credentials(Document):
    uuid = UUIDField(required=True, unique=True, default=uuid4)
    _header = DictField(required=True, default=lambda: {"alg": "HS256"})
    _claims = DictField()
    # A callable, so every credential gets its own key instead of the one generated when the module was imported.
    _key = StringField(default=lambda: jwk.JWK(generate='oct', size=256).export_symmetric())
    jwt = StringField()

    @property
    def key(self) -> jwk.JWK:
        return jwk.JWK().from_json(self._key)

    def set_header(self, alg: str = "HS256"):
        self._header = {"alg": alg}

    def set_claims(self, claim):
        log.info(f"executing: {currentframe().f_code.co_name}")
        self._claims = {"info": claim}

    def set_jwt(self):
        self.jwt = jwt.JWT(header=self._header, claims=self._claims)
