# -*- coding: utf-8 -*-
import logging
from uuid import UUID

from mongoengine import *

from .tool_settings import Settings
from .tool_database import connect_database

settings = Settings()
log = logging.getLogger(settings.environment)

connect_database(settings)


class SeenToken(Document):
    """
        The jti of every authentication token already presented, so none is accepted twice.

        The client and the jti together are the primary key, not a separate unique index: MongoDB always enforces
        the primary key, while an index disappears with the collection and is only created again when the
        service restarts. The other index only cleans up: MongoDB deletes each entry by itself once the token
        could no longer be presented.
    """
    key = StringField(primary_key=True)
    clientId = UUIDField(required=True)
    expiresAt = DateTimeField(required=True)

    meta = {"indexes": [{"fields": ["expiresAt"], "expireAfterSeconds": 0}]}

    @staticmethod
    def key_for(client_id: UUID, jti: str) -> str:
        # A jti is unique among those of its client. The uuid has a fixed length, so the two cannot be confused.
        return f"{client_id}:{jti}"
