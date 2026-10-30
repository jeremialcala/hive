# -*- coding: utf-8 -*-
"""
    Changing what a chatbot is after it was created (RF23, RF24): its persona, and one of its rules, changed or deleted.
    Only the cliente-admin and the machine credential: the cliente-analista sees them and gets 403 (ANALYST_ROUTES).

    The text has the same limits as when it is created (RS11, classes.bot_text), checked by the request models. Each
    change is a version (RF26, ADR-0035), so a rule deleted by mistake comes back by going back to the version before,
    and the route tells the engine so that it reads the chatbot again.
"""
import logging

from fastapi import status
from fastapi.responses import JSONResponse

from classes import PersonaRequest, Rule, RuleRequest, Settings
from repository import NotFound, TenantRepository

from .answers import answer
from .bot_versions import versioned

__all__ = ["PERSONA_CHANGED", "RULE_CHANGED", "RULE_DELETED", "TOO_MANY_RULES", "TooManyRules", "rule_info",
           "persona_info", "ctr_set_persona", "ctr_change_rule", "ctr_delete_rule"]

settings = Settings()
log = logging.getLogger(settings.environment)

# What each version says it was.
PERSONA_CHANGED, RULE_CHANGED, RULE_DELETED = "persona changed", "rule changed", "rule deleted"

CHATBOT_NOT_FOUND = "This chatbot was not found"
RULE_NOT_FOUND = "This rule was not found"
TOO_MANY_RULES = "TOO_MANY_RULES: a chatbot has 50 rules at most"


class TooManyRules(Exception):
    """Adding these rules would leave the chatbot with more than it may have."""


def rule_info(rule: Rule) -> dict:
    return {"uuid": str(rule.uuid), "rule": rule.rule}


def persona_info(bot) -> dict:
    return {"name": bot.name, "role": bot.role, "tone": bot.tone, "description": bot.description,
            "version": bot.configVersion}


def ctr_set_persona(repo: TenantRepository, bot_id: str, persona: PersonaRequest, author: str) -> JSONResponse:
    """The whole persona: name, role, tone and description. 409 if it is the one the chatbot already has."""
    try:
        bot = repo.get_bot(bot_id)
        if (bot.name, bot.role, bot.tone, bot.description) == (persona.name, persona.role, persona.tone,
                                                                persona.description):
            return answer(status.HTTP_409_CONFLICT, "ALREADY_CURRENT: the chatbot already has this persona")
        with versioned(repo, bot.uuid, author, PERSONA_CHANGED):
            repo.set_persona(bot.uuid, persona.name, persona.role, persona.tone, persona.description)
        return answer(status.HTTP_200_OK, data=persona_info(repo.get_bot(bot.uuid)))
    except NotFound:
        return answer(status.HTTP_404_NOT_FOUND, CHATBOT_NOT_FOUND)


def _rule_of(repo: TenantRepository, bot_id: str, rule_id: str) -> Rule | JSONResponse:
    try:
        repo.get_bot(bot_id)
    except NotFound:
        return answer(status.HTTP_404_NOT_FOUND, CHATBOT_NOT_FOUND)
    try:
        return repo.get_rule(bot_id, rule_id)
    except NotFound:
        return answer(status.HTTP_404_NOT_FOUND, RULE_NOT_FOUND)


def ctr_change_rule(repo: TenantRepository, bot_id: str, rule_id: str, request: RuleRequest,
                    author: str) -> JSONResponse:
    rule = _rule_of(repo, bot_id, rule_id)
    if isinstance(rule, JSONResponse):
        return rule
    if rule.rule == request.rule:
        return answer(status.HTTP_409_CONFLICT, "ALREADY_CURRENT: the rule already says this")
    try:
        with versioned(repo, rule.botId, author, RULE_CHANGED):
            repo.set_rule(rule, request.rule)
    except NotFound:
        return answer(status.HTTP_404_NOT_FOUND, RULE_NOT_FOUND)
    return answer(status.HTTP_200_OK, data=rule_info(rule))


def ctr_delete_rule(repo: TenantRepository, bot_id: str, rule_id: str, author: str) -> JSONResponse:
    """The engine is told at once. Going back to the version before brings it back, with the same id."""
    rule = _rule_of(repo, bot_id, rule_id)
    if isinstance(rule, JSONResponse):
        return rule
    try:
        with versioned(repo, rule.botId, author, RULE_DELETED):
            repo.delete_rule(rule)
    except NotFound:
        return answer(status.HTTP_404_NOT_FOUND, RULE_NOT_FOUND)
    return answer(status.HTTP_200_OK, data={"uuid": str(rule.uuid)})
