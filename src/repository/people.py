# -*- coding: utf-8 -*-
"""
    Who a person of the IdP is in Hive (ADR-0029): the only read of people that is not bound to one client, because the
    exchange of a token is where the client is decided. It gives back only memberships that may get a token: the person
    invited or active, and their client active. A person blocked or removed, or of a client that is gone, gets nothing.
"""
from datetime import datetime, timezone
from uuid import UUID

from classes import Client, ClientUser
from enums import PersonStatus, Role, Status

__all__ = ["Membership", "memberships_of", "came_in"]

# Who may still get a token: an invited person proves who they are with their first exchange.
MAY_ENTER = (PersonStatus.PENDING.value, PersonStatus.ACTIVE.value)


class Membership:
    """A person in one client, and what that client is in the registry."""

    def __init__(self, person: ClientUser, client_role: Role):
        self.person = person
        self.client_role = client_role

    @property
    def client_id(self) -> UUID:
        return self.person.clientId


def memberships_of(issuer: str, subject: str) -> list[Membership]:
    people = list(ClientUser.objects(issuer=issuer, subject=subject, status__in=MAY_ENTER))
    if not people:
        return []
    clients = {client.uuid: client for client in Client.objects(uuid__in=[person.clientId for person in people],
                                                                 status=Status.ACT.value)}
    return [Membership(person, Role(clients[person.clientId].role)) for person in people if person.clientId in clients]


def came_in(person: ClientUser) -> None:
    """An invited person got their first token: they are active from now on. Only if they still were invited."""
    ClientUser.objects(key=person.key, status=PersonStatus.PENDING.value).update_one(
        set__status=PersonStatus.ACTIVE.value, set__statusDate=datetime.now(timezone.utc))
