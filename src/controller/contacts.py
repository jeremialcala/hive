# -*- coding: utf8 -*-
"""
    The people who wrote to a client's chatbots, for the client to answer them when they ask for their data:
    listing them, finding one by number, handing over everything stored about one, and deleting it. Deleting a
    chatbot is here too: it takes its contacts with it.

    Nothing here writes a name, a number or a Messenger id to the log: only the ids the service made up.
"""
import logging
from uuid import UUID

from fastapi import status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from classes import Contact, ContactSearchRequest, Settings
from classes.tool_clock import as_utc
from crypto.fields import NotEncrypted
from crypto.key_service import KeyServiceUnavailable
from enums import Status
from repository import NotFound, RegisterUnavailable, TenantRepository

from .answers import answer
from .erasure_notices import CHATBOT, CONTACT, announce

__all__ = ["ctr_list_contacts", "ctr_search_contacts", "ctr_export_contact", "ctr_last_turns", "ctr_delete_contact",
           "ctr_delete_chatbot"]

settings = Settings()
log = logging.getLogger(settings.environment)

CONTACT_NOT_FOUND = "This contact was not found"
UNAVAILABLE = "SERVICE UNAVAILABLE"
CHATBOT_NOT_FOUND = "This chatbot was not found"


def contact_info(repo: TenantRepository, contact: Contact) -> dict:
    """Everything the service stores about a contact, with its number opened."""
    return {
        "uuid": str(contact._uuid),
        "botId": str(contact.botId),
        "firstName": contact.firstName,
        "lastName": contact.lastName,
        "profilePic": contact.profilePic,
        "waId": repo.contact_number(contact),
        "fbId": contact.fbId,
        "isAdmin": contact.isAdmin,
        "status": Status(contact.status).name,
        # Read back without a time zone, as pymongo hands dates over: they are UTC, and the answer says so.
        "createdAt": as_utc(contact.createdAt),
        "statusDate": as_utc(contact.statusDate),
    }


def contact_export(repo: TenantRepository, contact: Contact) -> dict:
    """Everything stored about a contact, its conversation included: it is theirs too (RF14)."""
    return {**contact_info(repo, contact), "messages": repo.history(contact)}


def _read(found_nothing: str, read) -> JSONResponse:
    """Answers with what `read` returns, or with what went wrong reading it."""
    try:
        return answer(status.HTTP_200_OK, data=read())
    except NotFound:
        return answer(status.HTTP_404_NOT_FOUND, found_nothing)
    except KeyServiceUnavailable as e:
        log.error(f"Key service unavailable: {e}")
        return answer(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE)
    except NotEncrypted:
        # A number from before numbers were encrypted. It is not handed over as it is stored, and an answer
        # without it would pass for everything there is.
        log.error("A contact holds a number that is not encrypted: run the encryption migration")
        return answer(status.HTTP_500_INTERNAL_SERVER_ERROR, "INTERNAL SERVER ERROR")


def _not_written_down(error: RegisterUnavailable) -> JSONResponse:
    """A deletion that could not be written down in the register of erasures is not made."""
    log.error(f"The register of erasures is unavailable, nothing was deleted: {error}")
    return answer(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE)


def ctr_list_contacts(repo: TenantRepository, bot_id: str, offset: int, limit: int) -> JSONResponse:
    def read():
        total, contacts = repo.list_contacts(bot_id, offset, limit)
        return {"total": total, "offset": offset, "limit": limit,
                "contacts": [contact_info(repo, contact) for contact in contacts]}

    return _read(CHATBOT_NOT_FOUND, read)


def ctr_search_contacts(repo: TenantRepository, request: ContactSearchRequest) -> JSONResponse:
    return _read(CONTACT_NOT_FOUND, lambda: [contact_info(repo, contact) for contact in
                                             repo.find_contacts(wa_id=request.waId, fb_id=request.fbId)])


def ctr_export_contact(repo: TenantRepository, bot_id: str, contact_id: str) -> JSONResponse:
    return _read(CONTACT_NOT_FOUND, lambda: contact_export(repo, repo.get_contact(bot_id, contact_id)))


def ctr_last_turns(repo: TenantRepository, bot_id: str, contact_id: str, engine) -> JSONResponse:
    """
        The last 10 turns of a conversation, for the engine's coordinator to rebuild its context with (RF15). Who
        took them is logged, like who takes the tokens: they carry what the person wrote.
    """
    def read():
        contact = repo.get_contact(bot_id, contact_id)
        turns = repo.last_turns(contact)
        log.warning(f"Last turns of contact {contact._uuid} of chatbot {contact.botId} handed to the response engine: "
                    f"client {engine.client_id}, key {engine.kid}, worker {engine.worker_name}, "
                    f"{len(turns)} messages")
        return {"uuid": str(contact._uuid), "botId": str(contact.botId), "messages": turns}

    response = _read(CONTACT_NOT_FOUND, read)
    response.headers["Cache-Control"] = "no-store"
    return response


async def ctr_delete_contact(repo: TenantRepository, bot_id: str, contact_id: str) -> JSONResponse:
    try:
        # The database and the register of erasures, in a thread (H14): only the notice to the engine is awaited.
        bot_uuid, copies = await run_in_threadpool(repo.contact_copies, bot_id, contact_id)
        deleted = await run_in_threadpool(repo.delete_contact, bot_id, contact_id)
    except NotFound:
        return answer(status.HTTP_404_NOT_FOUND, CONTACT_NOT_FOUND)
    except RegisterUnavailable as e:
        return _not_written_down(e)
    # A warning, so that it is kept at the level production logs at: a deletion cannot be undone.
    log.warning(f"Client {repo.client_id} deleted contact {UUID(contact_id)} of chatbot {UUID(bot_id)}: "
                f"{deleted} stored")
    # Each copy of that person has a context of its own in the engine.
    await announce(CONTACT, repo.client_id, bot_uuid, copies)
    return answer(status.HTTP_200_OK, data={"uuid": str(UUID(contact_id)), "deleted": deleted})


async def ctr_delete_chatbot(repo: TenantRepository, bot_id: str) -> JSONResponse:
    try:
        deleted = await run_in_threadpool(repo.delete_bot, bot_id)
    except NotFound:
        return answer(status.HTTP_404_NOT_FOUND, CHATBOT_NOT_FOUND)
    except RegisterUnavailable as e:
        return _not_written_down(e)
    log.warning(f"Client {repo.client_id} deleted chatbot {UUID(bot_id)}: {deleted}")
    await announce(CHATBOT, repo.client_id, UUID(bot_id))
    return answer(status.HTTP_200_OK, data={"uuid": str(UUID(bot_id)), "deleted": deleted})
