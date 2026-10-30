# -*- coding: utf-8 -*-
"""
    A person of a client (ADR-0029): who they are in Zitadel, by the issuer and the subject of their ID token, and what
    they may do in Hive. The role and the client are Hive's, never the IdP's: Zitadel only says who is there.

    The email is a personal datum: it is stored encrypted with the client's data key, and searched by a blind index,
    like a contact's number. A person may be of more than one client: one document per client.
"""
from uuid import uuid4

from mongoengine import DateTimeField, Document, StringField, UUIDField

from enums import PersonRole, PersonStatus
from .tool_clock import utc_now


class ClientUser(Document):
    # The client, the issuer and the subject: the same person cannot be twice in a client, whatever the indexes.
    key = StringField(primary_key=True)
    uuid = UUIDField(required=True, unique=True, default=uuid4)
    clientId = UUIDField(required=True)
    # Who the person is in the IdP: the `iss` and the `sub` of the ID token.
    issuer = StringField(required=True, max_length=255)
    subject = StringField(required=True, max_length=255)
    email = StringField(required=True)
    emailIndex = StringField(required=True)
    role = StringField(required=True, choices=[role.value for role in PersonRole])
    status = StringField(required=True, choices=[status.value for status in PersonStatus],
                         default=PersonStatus.PENDING.value)
    # Who invited them: the person, or the key of the machine credential, as the log writes it.
    invitedBy = StringField(required=True, max_length=80)
    invitedAt = DateTimeField(required=True, default=utc_now)
    statusDate = DateTimeField(required=True, default=utc_now)

    meta = {"collection": "client_users",
            "indexes": [("issuer", "subject"), ("clientId", "emailIndex"), ("clientId", "uuid")]}

    @staticmethod
    def key_for(client_id, issuer: str, subject: str) -> str:
        return f"{client_id}:{issuer}:{subject}"
