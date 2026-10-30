# -*- coding: utf8 -*-
"""
    The Meta credentials of a chatbot. A client loads and rotates them here and never gets them back: the answer
    only says which ones are loaded. The only one they are handed to is the response engine, which sends the
    answers with them, and each time it leaves a line in the log.
"""
import logging

from fastapi import status
from fastapi.responses import JSONResponse

from classes import BotCredentialsRequest, Settings
from crypto.key_service import KeyServiceUnavailable
from auth.principal import Principal
from repository import NotFound, TenantRepository

from .answers import answer
from .bot_state import bot_accepted

__all__ = ["ctr_set_bot_credentials", "ctr_get_bot_tokens"]

settings = Settings()
log = logging.getLogger(settings.environment)


def ctr_set_bot_credentials(repo: TenantRepository, bot_id: str, request: BotCredentialsRequest) -> JSONResponse:
    try:
        bot = repo.set_bot_credentials(bot_id, facebookToken=request.facebookToken,
                                       whatsappToken=request.whatsappToken, appSecret=request.appSecret,
                                       wabaId=request.wabaId)
    except NotFound:
        return answer(status.HTTP_404_NOT_FOUND, "This chatbot was not found")
    except KeyServiceUnavailable as e:
        log.error(f"Key service unavailable: {e}")
        return answer(status.HTTP_503_SERVICE_UNAVAILABLE, "SERVICE UNAVAILABLE")
    log.info(f"Credentials loaded for chatbot {bot.uuid}")
    if request.whatsappToken or request.facebookToken:
        # A new token: a chatbot in ERR is given its chance again (HIG-74). If Meta turns it away too, it goes back.
        bot_accepted(repo, bot.uuid, "the client loaded a new token", bot.status)
    return answer(status.HTTP_200_OK, data={"uuid": str(bot.uuid), "credentials": bot.chatbot_info()["credentials"]})


def ctr_get_bot_tokens(repo: TenantRepository, bot_id: str, engine: Principal) -> JSONResponse:
    """The tokens the response engine sends with. Not the App Secret: that one only checks what Meta sends."""
    try:
        bot = repo.get_bot(bot_id)
        secrets = repo.bot_secrets(bot)
    except NotFound:
        return answer(status.HTTP_404_NOT_FOUND, "This chatbot was not found")
    except KeyServiceUnavailable as e:
        log.error(f"Key service unavailable: {e}")
        return answer(status.HTTP_503_SERVICE_UNAVAILABLE, "SERVICE UNAVAILABLE")
    # A warning, so that it is kept at the level production logs at: who took the tokens, of which chatbot.
    log.warning(f"Meta tokens of chatbot {bot.uuid} handed to the response engine: client {engine.client_id}, "
                f"key {engine.kid}, worker {engine.worker_name}")
    response = answer(status.HTTP_200_OK, data={"uuid": str(bot.uuid), "facebookToken": secrets.facebook_token,
                                                "whatsappToken": secrets.whatsapp_token})
    response.headers["Cache-Control"] = "no-store"
    return response
