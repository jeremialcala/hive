# -*- coding: utf-8 -*-
"""
    What a client sends to its contacts (ADR-0031, RF29, RF31): the API checks and publishes, and only the engine's
    notifier sends. A request is accepted with a 202 once the bus holds it; what Meta did with it comes back to the
    history, like every answer of the bot.

    Before publishing, in this order: the contact and the chatbot are the client's (404 otherwise, like what does
    not exist); a text needs the window of 24 hours open, from the contact's last message; a template has to be
    approved in the copy of Hive, with as many values as it takes; outside the window it also needs the contact's
    consent for its category; and the client has to be within its limits. An idempotency key seen in the last 24
    hours gives back the same request, without publishing another.

    The log says who sent, to which chatbot and contact, and which template: never the text, the values or the
    number (RS07). It is the audit of each send until there is one per client (RF18).
"""
import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from shared import bus, tracing
from shared.contract import outbound_subject
from classes import ConsentRequest, OutboundMessageRequest, OutboundRequest, Settings
from classes.entity_outbound import APPROVED, CATEGORIES, IDEMPOTENCY_WINDOW
from classes.tool_clock import as_utc
from crypto.key_service import KeyServiceUnavailable
from repository import NotFound, TenantRepository

from .answers import answer
from .ingestion import partition
from .send_limits import LIMITS
from .webhook_event import SCHEMA_VERSION, traceparent

__all__ = ["WINDOW", "ctr_send_message", "ctr_set_consent", "ctr_withdraw_consent", "consent_info",
           "IDEMPOTENCY_KEY"]

settings = Settings()
log = logging.getLogger(settings.environment)

# Meta's customer service window: a contact's message opens it for 24 hours, and each new one starts it again.
WINDOW = timedelta(hours=24)
# What an idempotency key may be: what a client generates, a uuid or similar, never anything it wrote about a person.
IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")

CONTACT_NOT_FOUND = "This contact was not found"
UNAVAILABLE = "SERVICE UNAVAILABLE"


def _refused(code: str, why: str) -> JSONResponse:
    return answer(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{code}: {why}")


def consent_info(contact) -> dict | None:
    consent = contact.consent
    if consent is None:
        return None
    return {"categories": list(consent.categories), "source": consent.source, "grantedAt": as_utc(consent.grantedAt),
            "withdrawnAt": as_utc(consent.withdrawnAt) if consent.withdrawnAt else None}


def _consents(contact, category: str) -> bool:
    consent = contact.consent
    return consent is not None and consent.withdrawnAt is None and category in consent.categories


def _fingerprint(request: OutboundMessageRequest) -> str:
    return hashlib.sha256(json.dumps(request.model_dump(), sort_keys=True).encode()).hexdigest()


def _accepted(record: OutboundRequest) -> JSONResponse:
    return answer(status.HTTP_202_ACCEPTED, data={"uuid": str(record.uuid), "botId": str(record.botId),
                                                  "contactId": str(record.contactId), "kind": record.kind})


def _check(repo: TenantRepository, bot_id: str, contact_id: str, request: OutboundMessageRequest,
           now: datetime):
    """The contact, and the template if there is one; or the answer that turns the request away."""
    contact = repo.get_contact(bot_id, contact_id)
    last = repo.last_incoming_at(contact)
    inside = last is not None and now - as_utc(last) < WINDOW
    if request.type == "text":
        if not inside:
            return None, None, _refused("OUTSIDE_WINDOW", "outside the 24 hours since the contact last wrote, "
                                                          "only an approved template can be sent")
        return contact, None, None
    wanted = request.template
    template = repo.find_template(contact.botId, wanted.name, wanted.language)
    if template is None or template.status != APPROVED or template.category not in CATEGORIES:
        return None, None, _refused("TEMPLATE_NOT_APPROVED", "this chatbot has no approved template with that name "
                                                             "and language")
    if len(wanted.parameters) != template.parameters:
        return None, None, _refused("TEMPLATE_PARAMETERS", f"the template takes {template.parameters} values")
    if not inside and not _consents(contact, template.category):
        return None, None, _refused("NO_CONSENT", f"outside the window, the contact has to have agreed to "
                                                  f"{template.category} messages")
    return contact, template, None


def _envelope(record: OutboundRequest, request: OutboundMessageRequest, template, now: datetime) -> dict:
    """The PedidoDeEnvio of asyncapi-v2.yaml: identifiers, and what to send. No number: the notifier looks it up."""
    sent = {"schemaVersion": SCHEMA_VERSION, "uuid": str(record.uuid), "clientId": str(record.clientId),
            "botId": str(record.botId), "contactId": str(record.contactId), "channel": "whatsapp",
            "kind": record.kind, "sentBy": record.sentBy, "requestedAt": now.isoformat()}
    if request.type == "text":
        sent["text"] = request.text
    else:
        sent["template"] = {"name": template.name, "language": template.language, "category": template.category,
                            "parameters": list(request.template.parameters)}
    return sent


async def ctr_send_message(repo: TenantRepository, bot_id: str, contact_id: str, request: OutboundMessageRequest,
                           idempotency_key: str | None, sent_by: str) -> JSONResponse:
    now = datetime.now(timezone.utc)
    fingerprint = _fingerprint(request)
    try:
        # Whose the contact is, first: for anybody else it does not exist, whatever else the request says.
        await run_in_threadpool(repo.get_contact, bot_id, contact_id)
        if not idempotency_key or not IDEMPOTENCY_KEY.match(idempotency_key):
            return _refused("IDEMPOTENCY_KEY", "an Idempotency-Key header of 8 to 128 letters, digits or ._:- is "
                                               "required")
        seen = await run_in_threadpool(repo.outbound_request, idempotency_key)
        if seen is not None:
            if seen.fingerprint != fingerprint:
                return _refused("IDEMPOTENCY_KEY_REUSED", "that key went with another request")
            return _accepted(seen)
        contact, template, refusal = await run_in_threadpool(_check, repo, bot_id, contact_id, request, now)
    except NotFound:
        return answer(status.HTTP_404_NOT_FOUND, CONTACT_NOT_FOUND)
    except KeyServiceUnavailable as e:
        log.error(f"Key service unavailable: {e}")
        return answer(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE)
    if refusal is not None:
        log.warning(f"Client {repo.client_id} was not let send to contact {UUID(contact_id)} of chatbot "
                    f"{UUID(bot_id)}: {refusal.body.decode()[:80]}")
        return refusal
    if not bus.configured():
        log.error("A send to a contact was asked for with no bus to publish it on")
        return answer(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE)
    try:
        refused = await LIMITS.take(repo.client_id, contact.botId, now)
    except bus.BusUnavailable as e:
        log.error(f"The counters of the limits of sending did not answer ({e}): the send is turned away")
        return answer(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE)
    if refused is not None:
        log.warning(f"Client {repo.client_id} was turned away a send to its contacts: too many {refused.reason}")
        return JSONResponse(status_code=status.HTTP_429_TOO_MANY_REQUESTS, headers={"Retry-After": str(refused.retry_after)},
                            content={"code": 429, "message": f"TOO_MANY_SENDS: too many {refused.reason}"})
    record = OutboundRequest(key=OutboundRequest.key_for(repo.client_id, idempotency_key), clientId=repo.client_id,
                             uuid=uuid4(), botId=contact.botId, contactId=contact._uuid, fingerprint=fingerprint,
                             kind=request.type, template=template.name if template else None, sentBy=sent_by,
                             createdAt=now, expiresAt=now + IDEMPOTENCY_WINDOW)
    if not await run_in_threadpool(repo.remember_outbound, record):
        # Another request with the same key got there first: it is that one.
        seen = await run_in_threadpool(repo.outbound_request, idempotency_key)
        if seen is None or seen.fingerprint != fingerprint:
            return _refused("IDEMPOTENCY_KEY_REUSED", "that key went with another request")
        return _accepted(seen)
    subject = outbound_subject(partition(contact.botId, contact._uuid), contact.botId, contact._uuid)
    with tracing.span("outbound.publish", **{"gateway.client_id": str(repo.client_id),
                                             "gateway.bot_id": str(contact.botId)}):
        try:
            await bus.publisher().publish(subject, json.dumps(_envelope(record, request, template, now)).encode(),
                                          str(record.uuid), headers={"traceparent": traceparent()})
        except bus.BusUnavailable as e:
            await run_in_threadpool(repo.forget_outbound, record)
            log.error(f"The bus did not take a send of client {repo.client_id} ({type(e).__name__}): it was not "
                      f"accepted, and the same key can be sent again")
            return answer(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE)
    # A warning, so that it is kept at the level production logs at: it is the record of who sent what to whom.
    log.warning(f"Send {record.uuid} accepted: client {repo.client_id}, by {sent_by}, chatbot {contact.botId}, "
                f"contact {contact._uuid}, {request.type}" + (f" {template.name} ({template.language})"
                                                               if template else ""))
    return _accepted(record)


def ctr_set_consent(repo: TenantRepository, bot_id: str, contact_id: str, request: ConsentRequest) -> JSONResponse:
    try:
        contact = repo.get_contact(bot_id, contact_id)
        granted = request.grantedAt or datetime.now(timezone.utc)
        contact = repo.set_consent(contact, request.categories, request.source, granted)
    except NotFound:
        return answer(status.HTTP_404_NOT_FOUND, CONTACT_NOT_FOUND)
    log.warning(f"Client {repo.client_id} recorded the consent of contact {contact._uuid} of chatbot {contact.botId}: "
                f"{', '.join(request.categories)}")
    return answer(status.HTTP_200_OK, data={"uuid": str(contact._uuid), "consent": consent_info(contact)})


def ctr_withdraw_consent(repo: TenantRepository, bot_id: str, contact_id: str) -> JSONResponse:
    try:
        contact = repo.withdraw_consent(repo.get_contact(bot_id, contact_id), datetime.now(timezone.utc))
    except NotFound:
        return answer(status.HTTP_404_NOT_FOUND, CONTACT_NOT_FOUND)
    log.warning(f"Client {repo.client_id} withdrew the consent of contact {contact._uuid} of chatbot {contact.botId}")
    return answer(status.HTTP_200_OK, data={"uuid": str(contact._uuid), "consent": consent_info(contact)})
