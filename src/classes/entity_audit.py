# -*- coding: utf-8 -*-
import logging

from mongoengine import DateTimeField, DictField, Document, IntField, ListField, StringField, UUIDField

from .tool_clock import utc_now
from .tool_database import connect_database
from .tool_settings import Settings

settings = Settings()
log = logging.getLogger(settings.environment)

connect_database(settings)

# What an entry is about, as the screen of the audit groups it (RF18).
CATEGORIES = ("config", "people", "keys", "sends", "data", "engine")
RESULTS = ("ok", "denied", "failed")


class AuditEntry(Document):
    """
        One request of a client's credential or person to the management API that the client's audit keeps (RF18,
        HIG-95; decisions of Jeremi of 2026-11-01): who, what, on which resources, when, in UTC, and what came of it.
        Only identifiers: never a body, a text, a number or a name (RS07).

        It expires after 12 months, and goes with the client. Nobody changes or deletes one by hand.
    """
    id = StringField(primary_key=True)
    clientId = UUIDField(required=True)
    at = DateTimeField(required=True, default=utc_now)
    # "person <uuid>" or "key <kid>", as the log writes it; and which kind of caller it was.
    actor = StringField(required=True, max_length=200)
    actorKind = StringField(required=True, choices=("person", "machine", "engine", "operator"))
    # For the response engine, which of its workers.
    worker = StringField(max_length=40)
    # The route, by its name and its template: never the address as it came.
    action = StringField(required=True, max_length=100)
    method = StringField(required=True, max_length=10)
    route = StringField(required=True, max_length=200)
    category = StringField(required=True, choices=CATEGORIES)
    # The identifiers in the address, by the name of each: one that is not an identifier is not kept.
    resource = DictField()
    # The same identifiers, to find the entries about one.
    ids = ListField(StringField(max_length=64))
    result = StringField(required=True, choices=RESULTS)
    status = IntField(required=True)
    expiresAt = DateTimeField(required=True)

    meta = {"collection": "audit", "indexes": [{"fields": ["expiresAt"], "expireAfterSeconds": 0},
                                               {"fields": ["clientId", "-at"]}, {"fields": ["clientId", "ids"]}]}

    def info(self) -> dict:
        return {"at": self.at, "actor": self.actor, "actorKind": self.actorKind, "worker": self.worker,
                "action": self.action, "method": self.method, "route": self.route, "category": self.category,
                "resource": dict(self.resource or {}), "result": self.result, "status": self.status}
