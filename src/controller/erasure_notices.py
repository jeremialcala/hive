# -*- coding: utf-8 -*-
"""
    The notices of erasure (ADR-0017, RF15): once the management API deletes a contact, a chatbot or a client, it says
    so on SUPRESIONES, so that the engine's coordinator deletes the context it keeps of them in Bifröst. One Supresion
    of asyncapi-v2.yaml per thing deleted, with identifiers only.

    A notice the bus does not take does not undo the deletion nor make it fail: the context it was about expires by
    itself 24 hours after its last message. It is left in the log.
"""
import json
import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

from shared import bus
from shared import tracing
from classes import Settings

from .webhook_event import SCHEMA_VERSION, traceparent

__all__ = ["CONTACT", "CHATBOT", "CLIENT", "notice", "announce"]

log = logging.getLogger(Settings().environment)

# What was deleted, as the subject and the notice name it.
CONTACT, CHATBOT, CLIENT = "contact", "bot", "client"


def notice(entity: str, client_id: UUID, bot_id: UUID | None = None, contact_id: UUID | None = None) -> dict:
    erased = {"schemaVersion": SCHEMA_VERSION, "uuid": str(uuid4()), "entity": entity, "clientId": str(client_id),
              "erasedAt": datetime.now(timezone.utc).isoformat()}
    if bot_id is not None:
        erased["botId"] = str(bot_id)
    if contact_id is not None:
        erased["contactId"] = str(contact_id)
    return erased


async def announce(entity: str, client_id: UUID, bot_id: UUID | None = None, contacts: list | None = None) -> int:
    """
        Publishes the notices of one deletion: one per contact for contacts, one for a chatbot or a client. Says how
        many the bus took. Without a bus, in development, there is nobody to tell.
    """
    if not bus.configured():
        return 0
    notices = ([notice(entity, client_id, bot_id, contact) for contact in contacts] if entity == CONTACT
               else [notice(entity, client_id, bot_id)])
    with tracing.span("erasure.notices", **{"gateway.entity": entity, "gateway.client_id": str(client_id),
                                            "gateway.messages": len(notices)}):
        return await _publish(entity, client_id, notices)


async def _publish(entity: str, client_id: UUID, notices: list[dict]) -> int:
    taken = 0
    for erased in notices:
        try:
            await bus.publisher().publish(f"gateway.{entity}.erased", json.dumps(erased).encode("utf-8"),
                                          erased["uuid"], headers={"traceparent": traceparent()})
            taken += 1
        except bus.BusUnavailable as e:
            log.error(f"The bus did not take the notice of erasure of a {entity} of client {client_id} "
                      f"({type(e).__name__}): its context expires by itself within 24 hours")
    return taken
