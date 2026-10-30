# -*- coding: utf-8 -*-
"""
    The service Meta calls: the verification of each chatbot's webhook and its notifications. It runs apart from
    the management API (app.py), though both are published by the tunnel (ADR-0024): it holds neither the gateway's
    keys nor the register of erasures, and it serves no route but these.

        uvicorn webhook:app
"""
from inspect import currentframe

from fastapi import Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse

from shared import bus
from auth import request_limits
from classes import ChatBot, FacebookRequest
from constants import HUB_CHALLENGE, HUB_MODE, HUB_SIGNATURE, HUB_VERIFY_TOKEN, SUBSCRIBE, WEBHOOK_DESCRIPTION
from controller import RETRY_LATER, ctr_process_messages
from controller.bot_state import bot_verified
from controller.incoming import NotUnderstood, read_notification
from crypto.fields import NotEncrypted
from crypto.key_service import KeyServiceUnavailable
from repository import BotSecrets, NotFound, TenantRepository, database_failed, find_webhook_bot
from service import build, log, settings
from utils import describe, is_valid_signature, looks_like_signature, secrets_match

# The webhook only publishes (ADR-0021): without the bus, nothing would identify, store or queue what Meta sends.
# Only development ingests in the webhook itself.
if not bus.configured() and settings.environment != "development":
    raise ValueError("The webhook needs NATS_URL")


def database_unavailable(error: Exception) -> HTTPException:
    """What Meta is answered when the database does not do its part: a 503, so that it sends it again."""
    log.error(f"The database is unavailable: {describe(error)}")
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="HTTP_503_SERVICE_UNAVAILABLE")


def get_webhook_bot(bot_id: str) -> ChatBot:
    # A function and not a coroutine: FastAPI runs it in its pool of threads, and a slow read of the chatbot holds
    # only its own notification, not the process (H14).
    try:
        # A malformed id is turned away before it costs a database round trip.
        return find_webhook_bot(bot_id)
    except NotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="HTTP_404_NOT_FOUND")
    except Exception as e:
        if not database_failed(e):
            raise
        raise database_unavailable(e) from None


def webhook_secrets(bot: ChatBot) -> BotSecrets:
    """
        What Meta's verification and signature are checked against, opened with the key of the client that
        owns the chatbot. Without the key there is nothing to check against, and the answer is never to let
        the request through.
    """
    try:
        return TenantRepository(bot.clientId).bot_secrets(bot)
    except KeyServiceUnavailable as e:
        log.error(f"Key service unavailable: {e}")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="HTTP_503_SERVICE_UNAVAILABLE") from None
    except NotEncrypted:
        # Secrets written by hand, or from before they were encrypted and the migration has not reached them.
        log.error(f"Chatbot {bot.uuid} holds secrets that are not encrypted: run the encryption migration")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="HTTP_404_NOT_FOUND") from None
    except NotFound:
        # The client that owned it is gone, and its key with it: there is nobody to receive this for.
        log.error(f"Chatbot {bot.uuid} belongs to a client that does not exist")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="HTTP_404_NOT_FOUND") from None
    except Exception as e:
        if not database_failed(e):
            raise
        raise database_unavailable(e) from None


async def get_signed_webhook_bot(request: Request, _bot: ChatBot = Depends(get_webhook_bot)) -> ChatBot:
    """
        Meta signs every notification with the App Secret, over the body exactly as it was sent. A request without
        anything that could be a signature is turned away before the secrets of the chatbot are opened: otherwise
        anybody who knows its address makes the gateway ask the key service for its client's key.
    """
    header = request.headers.get(HUB_SIGNATURE)
    if not looks_like_signature(header):
        log.warning(f"Rejected webhook for chatbot {_bot.uuid}: missing or invalid {HUB_SIGNATURE}")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="HTTP_403_FORBIDDEN")
    # Opening the secrets can wait on MongoDB and the key service: in a thread, not in the event loop (H14).
    secret = (await run_in_threadpool(webhook_secrets, _bot)).app_secret or settings.facebook_app_secret
    if not is_valid_signature(secret, await request.body(), header):
        log.warning(f"Rejected webhook for chatbot {_bot.uuid}: missing or invalid {HUB_SIGNATURE}")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="HTTP_403_FORBIDDEN")
    return _bot


# An origin that keeps sending what Meta did not sign waits, before its body is read (HIG-139).
app = build(limit=request_limits.WEBHOOK, title="Meta webhook", summary="What Meta calls for each chatbot",
            description=WEBHOOK_DESCRIPTION,
            openapi_tags=[{"name": "Chat", "description": "The verification and the notifications of a chatbot."}])


@app.get(path="/chat/{bot_id}", tags=["Chat"])
def verify(request: Request, bot_id, _bot: ChatBot = Depends(get_webhook_bot)):
    """The challenge Meta sends when the webhook is subscribed, answered only with the verification token."""
    log.info(f"Starting: {currentframe().f_code.co_name}")
    challenge = request.query_params.get(HUB_CHALLENGE)
    if (request.query_params.get(HUB_MODE) != SUBSCRIBE or not challenge
            or not secrets_match(webhook_secrets(_bot).verify_token, request.query_params.get(HUB_VERIFY_TOKEN))):
        log.warning(f"Rejected webhook verification for chatbot {_bot.uuid}")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="HTTP_403_FORBIDDEN")

    # Meta reached the webhook: a REG chatbot is live (HIG-75). The one write of the webhook (ADR-0021, as amended),
    # out of the way of the messages. If it fails, Meta still gets its challenge: the ingestion activates the chatbot
    # with its first signed message.
    try:
        bot_verified(TenantRepository(_bot.clientId), _bot, "Meta verified its webhook")
    except Exception as e:
        if not database_failed(e):
            raise
        log.warning(f"Chatbot {_bot.uuid} was verified, but its state could not be written: {describe(e)}")

    log.info(f"Ending: {currentframe().f_code.co_name}")
    return PlainTextResponse(challenge)


# The body is not declared as a parameter: it would be parsed before the signature is checked. It is read by
# hand once the signature is good, and this is only what the contract says of it.
NOTIFICATION = {"requestBody": {"required": True, "content": {"application/json": {
    "schema": FacebookRequest.model_json_schema()}}}}


@app.post(path="/chat/{bot_id}", tags=["Chat"], openapi_extra=NOTIFICATION)
async def message_processor(request: Request, bot_id, _bot: ChatBot = Depends(get_signed_webhook_bot)):
    """
        What Meta is answered. 403 without a good signature, before anything of the body is read. 200 for
        everything it signed, also for what the service does not understand or cannot process: asking for it
        again would not change it, and Meta stops delivering to a webhook that keeps failing. 503 only when
        something the service needs is not there: then Meta sends it again later.
    """
    log.info(f"Starting: {currentframe().f_code.co_name}")
    try:
        notification = read_notification(await request.body())
    except NotUnderstood as e:
        log.warning(f"A signed notification for chatbot {_bot.uuid} was not understood: {e}")
        return "HTTP_200_OK", status.HTTP_200_OK
    try:
        await ctr_process_messages(req=notification, _bot=_bot)
    except RETRY_LATER:
        # Anything but a 200 makes Meta deliver the notification again later.
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="HTTP_503_SERVICE_UNAVAILABLE")

    log.info(f"Ending: {currentframe().f_code.co_name}")
    return "HTTP_200_OK", status.HTTP_200_OK
