# -*- coding: utf-8 -*-
"""
    What the PWA reads of a client (HIG-87): its chatbots, the conversation of a contact with the window of 24 hours,
    and what came of each request to send.

    The conversation goes without the contact's number: the cliente-analista reads it too (RF28, the profile of the
    role × action matrix), and each time a person reads one, the log says who and whose, the audit until RF18. Nor
    with Meta's ids, which in WhatsApp carry the number: each message has an opaque `ref` instead (HIG-149).

    What came of a send: accepted, once the bus held it; sent, once Meta took it and the history has it; or failed,
    with Meta's code and a reason the PWA explains (developers.facebook.com, «Error codes», read on 2026-10-29).
"""
import logging
from datetime import datetime, timezone

from fastapi import status
from fastapi.responses import JSONResponse

from classes import Settings
from classes.tool_clock import as_utc
from crypto.key_service import KeyServiceUnavailable
from repository import NotFound, TenantRepository

from .answers import answer
from .outbound import WINDOW, consent_info

__all__ = ["ctr_list_bots", "ctr_conversation", "ctr_send_status", "REASONS"]

settings = Settings()
log = logging.getLogger(settings.environment)

# What each code of Meta means for a send of the client, in a word the PWA explains.
REASONS = {131047: "window_closed", 132015: "template_paused", 132016: "template_disabled",
           132000: "template_parameters", 132001: "template_missing", 131049: "marketing_limit",
           131026: "not_deliverable", 131051: "unsupported",
           # Not in Meta's codes; what it said on 2026-10-30: «Hello World templates can only be sent from the Public
           # Test Numbers».
           131058: "sample_template",
           # The chatbot's token, or its permissions: these put it in ERR too (bot_state, HIG-74).
           0: "token_rejected", 190: "token_rejected", 10: "permission", 131005: "permission"}
MESSAGES = 50

UNAVAILABLE = "SERVICE UNAVAILABLE"


def _unavailable(e: Exception) -> JSONResponse:
    log.error(f"Key service unavailable: {e}")
    return answer(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE)


def ctr_list_bots(repo: TenantRepository) -> JSONResponse:
    """The chatbots of the client: what each is and which credentials it has loaded, never their values."""
    bots = [{**bot.chatbot_info(), "uuid": str(bot.uuid), "createdAt": as_utc(bot.createdAt)}
            for bot in repo.list_bots()]
    return answer(status.HTTP_200_OK, data={"bots": bots})


def window_of(last_incoming: datetime | None, now: datetime | None = None) -> dict:
    """Meta's window of 24 hours: whether it is open, and until when."""
    now = now or datetime.now(timezone.utc)
    if last_incoming is None:
        return {"open": False, "lastIncomingAt": None, "closesAt": None}
    last_incoming = as_utc(last_incoming)
    closes = last_incoming + WINDOW
    return {"open": now < closes, "lastIncomingAt": last_incoming, "closesAt": closes}


def ctr_conversation(repo: TenantRepository, bot_id: str, contact_id: str, actor: str,
                     person: bool) -> JSONResponse:
    """A contact's last messages and its window, without its number."""
    try:
        contact = repo.get_contact(bot_id, contact_id)
        messages = repo.recent_history(contact, MESSAGES)
        ratings = repo.ratings_of(contact)
        window = window_of(repo.last_incoming_at(contact))
    except NotFound:
        return answer(status.HTTP_404_NOT_FOUND, "This contact was not found")
    except KeyServiceUnavailable as e:
        return _unavailable(e)
    if person:
        log.warning(f"Audit: {actor} read the conversation of contact {contact._uuid} of chatbot {contact.botId}")
    channel = "whatsapp" if contact.waIdIndex or contact.waId else "messenger" if contact.fbId else None
    for message in messages:
        # What the people of the client thought of each answer of the bot (RF28, HIG-111).
        message["ratings"] = ratings.get(message["ref"], []) if message["direction"] == "out" else []
    return answer(status.HTTP_200_OK, data={
        "contact": {"uuid": str(contact._uuid), "botId": str(contact.botId), "firstName": contact.firstName,
                    "lastName": contact.lastName, "channel": channel, "consent": consent_info(contact)},
        "window": window, "messages": messages})


def ctr_send_status(repo: TenantRepository, bot_id: str, contact_id: str, request_id: str) -> JSONResponse:
    """What came of a request to send to that contact, while it is remembered: 24 hours."""
    try:
        contact = repo.get_contact(bot_id, contact_id)
        request = repo.outbound_of(contact, request_id)
    except NotFound:
        request = None
    if request is None:
        return answer(status.HTTP_404_NOT_FOUND, "This request to send was not found")
    reason = request.failureReason
    if reason in ("rejected", "gave_up"):
        # Given up with Meta's last code: a token or a permission says more than «tried several times» (HIG-74).
        reason = REASONS.get(request.failureCode, reason)
    return answer(status.HTTP_200_OK, data={
        "uuid": str(request.uuid), "status": request.status, "kind": request.kind, "template": request.template,
        "createdAt": as_utc(request.createdAt), "sentAt": as_utc(request.sentAt) if request.sentAt else None,
        "messageId": request.messageId,
        "failure": {"code": request.failureCode, "reason": reason,
                    "at": as_utc(request.failedAt) if request.failedAt else None} if request.status == "failed"
        else None})
