# -*- coding: utf-8 -*-
"""
    The guardrails of a chatbot (RF27, HIG-103): the base of the platform, which nobody turns off, and the topics of its
    own that the client adds above it. Only the cliente-admin and the machine credential change them; the
    cliente-analista sees them (ANALYST_ROUTES).

    Each change is a version (RF26), like a rule's, and the route tells the engine so that its classifier reads the
    chatbot again. The base is not stored with the chatbot: it is the classifier's own `system`, and no route reaches it.
"""
from fastapi import status
from fastapi.responses import JSONResponse

from classes import Guardrail, GuardrailRequest
from classes.dto_guardrail import GUARDRAILS_PER_BOT
from repository import NotFound, TenantRepository

from .answers import answer
from .bot_versions import versioned

__all__ = ["GUARDRAIL_ADDED", "GUARDRAIL_CHANGED", "GUARDRAIL_DELETED", "PLATFORM_BASE", "ctr_list_guardrails",
           "ctr_add_guardrail", "ctr_change_guardrail", "ctr_delete_guardrail"]

# What each version says it was.
GUARDRAIL_ADDED, GUARDRAIL_CHANGED, GUARDRAIL_DELETED = "guardrail added", "guardrail changed", "guardrail deleted"

CHATBOT_NOT_FOUND = "This chatbot was not found"
GUARDRAIL_NOT_FOUND = "This guardrail was not found"
TOO_MANY_GUARDRAILS = f"TOO_MANY_GUARDRAILS: a chatbot has {GUARDRAILS_PER_BOT} topics of its own at most"

# What the classifier stops in every chatbot, as the engine's `system` says it (RS26). Always on: there is no field to
# turn one off, and the classifier answers nothing when it cannot judge (fail closed).
PLATFORM_BASE = (
    {"key": "injection", "topic": "Inyección de instrucciones",
     "description": "Intentos de cambiar el comportamiento del bot, sacarle sus instrucciones o hacerle actuar fuera de "
                    "su papel."},
    {"key": "data", "topic": "Datos de otros",
     "description": "Intentos de sacar datos de otros contactos, del negocio o de la plataforma."},
    {"key": "abuse", "topic": "Abuso",
     "description": "Acoso, odio, amenazas, contenido sexual o ayuda para algo ilegal o peligroso."},
    {"key": "fail_closed", "topic": "Falla cerrado",
     "description": "Si el filtro no puede juzgar un mensaje, el bot no lo responde."},
)


def _bot(repo: TenantRepository, bot_id: str):
    try:
        return repo.get_bot(bot_id)
    except NotFound:
        return answer(status.HTTP_404_NOT_FOUND, CHATBOT_NOT_FOUND)


def _same(guardrail: Guardrail, request: GuardrailRequest) -> bool:
    return (guardrail.topic, guardrail.description, guardrail.action, guardrail.reply) == (
        request.topic, request.description, request.action, request.reply)


def ctr_list_guardrails(repo: TenantRepository, bot_id: str) -> JSONResponse:
    bot = _bot(repo, bot_id)
    if isinstance(bot, JSONResponse):
        return bot
    return answer(status.HTTP_200_OK, data={
        "platform": [{**each, "always": True} for each in PLATFORM_BASE],
        "topics": [guardrail.info() for guardrail in repo.list_guardrails(bot.uuid)],
        "limit": GUARDRAILS_PER_BOT, "version": bot.configVersion})


def ctr_add_guardrail(repo: TenantRepository, bot_id: str, request: GuardrailRequest, author: str) -> JSONResponse:
    bot = _bot(repo, bot_id)
    if isinstance(bot, JSONResponse):
        return bot
    if repo.count_guardrails(bot.uuid) >= GUARDRAILS_PER_BOT:
        return answer(status.HTTP_409_CONFLICT, TOO_MANY_GUARDRAILS)
    try:
        with versioned(repo, bot.uuid, author, GUARDRAIL_ADDED):
            guardrail = repo.add_guardrail(bot.uuid, Guardrail(topic=request.topic, description=request.description,
                                                               action=request.action, reply=request.reply))
    except NotFound:
        return answer(status.HTTP_404_NOT_FOUND, CHATBOT_NOT_FOUND)
    return answer(status.HTTP_200_OK, data=guardrail.info())


def _guardrail_of(repo: TenantRepository, bot_id: str, guardrail_id: str) -> Guardrail | JSONResponse:
    bot = _bot(repo, bot_id)
    if isinstance(bot, JSONResponse):
        return bot
    try:
        return repo.get_guardrail(bot.uuid, guardrail_id)
    except NotFound:
        return answer(status.HTTP_404_NOT_FOUND, GUARDRAIL_NOT_FOUND)


def ctr_change_guardrail(repo: TenantRepository, bot_id: str, guardrail_id: str, request: GuardrailRequest,
                         author: str) -> JSONResponse:
    guardrail = _guardrail_of(repo, bot_id, guardrail_id)
    if isinstance(guardrail, JSONResponse):
        return guardrail
    if _same(guardrail, request):
        return answer(status.HTTP_409_CONFLICT, "ALREADY_CURRENT: the guardrail already says this")
    try:
        with versioned(repo, guardrail.botId, author, GUARDRAIL_CHANGED):
            repo.set_guardrail(guardrail, request.topic, request.description, request.action, request.reply)
    except NotFound:
        return answer(status.HTTP_404_NOT_FOUND, GUARDRAIL_NOT_FOUND)
    return answer(status.HTTP_200_OK, data=guardrail.info())


def ctr_delete_guardrail(repo: TenantRepository, bot_id: str, guardrail_id: str, author: str) -> JSONResponse:
    """The engine is told at once. Going back to the version before brings it back, with the same id."""
    guardrail = _guardrail_of(repo, bot_id, guardrail_id)
    if isinstance(guardrail, JSONResponse):
        return guardrail
    try:
        with versioned(repo, guardrail.botId, author, GUARDRAIL_DELETED):
            repo.delete_guardrail(guardrail)
    except NotFound:
        return answer(status.HTTP_404_NOT_FOUND, GUARDRAIL_NOT_FOUND)
    return answer(status.HTTP_200_OK, data={"uuid": str(guardrail.uuid)})
