# -*- coding: utf-8 -*-
"""
    The compliance of a chatbot (RF28, HIG-111): what its people think of its answers, and what the engine's evaluator
    decided of them (HIG-147). The cliente-admin and the cliente-analista do both.

    - **A rating** is good (`up`) or bad (`down`) with a comment if wanted, one per person and answer, which they can
      change. Only a person rates: it is a human judgement, and a machine credential has nothing to say about it.
      Each rating leaves its line in the log, the audit until RF18 (HIG-95), with who and whose, never what.
    - **The metrics** count the verdicts of the evaluator, the criteria and rules that failed, the fixed texts (which
      include what the classifier blocked, until the guardrails of HIG-103 count their own) and the ratings.
"""
import logging
from datetime import datetime, timezone

from fastapi import status
from fastapi.responses import JSONResponse

from classes import RatingRequest, Settings
from classes.tool_clock import as_utc
from crypto.key_service import KeyServiceUnavailable
from repository import NotFound, TenantRepository

from .answers import answer

__all__ = ["ctr_rate", "ctr_compliance", "ctr_answers"]

ANSWERS = ("unrated", "flagged", "all")

settings = Settings()
log = logging.getLogger(settings.environment)

UNAVAILABLE = "SERVICE UNAVAILABLE"


def ctr_rate(repo: TenantRepository, bot_id: str, contact_id: str, request: RatingRequest, actor: str,
             person: bool) -> JSONResponse:
    """A person rates an answer of the bot to that contact; rating it again changes theirs."""
    try:
        # First whose it is: to anyone but the client, the chatbot and the contact do not exist.
        contact = repo.get_contact(bot_id, contact_id)
        if not person:
            return answer(status.HTTP_403_FORBIDDEN, "Only a person rates an answer")
        rating = repo.rate(contact, request.ref, actor, request.score, request.comment)
    except NotFound:
        return answer(status.HTTP_404_NOT_FOUND, "This answer of the chatbot was not found")
    except KeyServiceUnavailable as e:
        log.error(f"Key service unavailable: {e}")
        return answer(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE)
    # Who and whose, never the answer.
    log.warning(f"Audit: {actor} rated an answer to contact {contact._uuid} of chatbot {contact.botId}")
    return answer(status.HTTP_200_OK, data={"rating": {
        "ref": request.ref, "score": rating.score, "comment": request.comment,
        "ratedBy": rating.ratedBy, "ratedAt": as_utc(rating.ratedAt)}})


def ctr_answers(repo: TenantRepository, bot_id: str, which: str, limit: int) -> JSONResponse:
    """The last answers of a chatbot that the evaluator judged, to rate them: with no text, number or Meta id."""
    if which not in ANSWERS:
        return answer(status.HTTP_422_UNPROCESSABLE_ENTITY, f"filter is one of {', '.join(ANSWERS)}")
    try:
        bot = repo.get_bot(bot_id)
    except NotFound:
        return answer(status.HTTP_404_NOT_FOUND, "This chatbot was not found")
    return answer(status.HTTP_200_OK, data={"answers": repo.recent_answers(bot.uuid, which, max(1, min(limit, 100)))})


def ctr_compliance(repo: TenantRepository, bot_id: str, since: datetime | None) -> JSONResponse:
    """The metrics of compliance of a chatbot of the client, over its history, from `since` if given."""
    try:
        bot = repo.get_bot(bot_id)
    except NotFound:
        return answer(status.HTTP_404_NOT_FOUND, "This chatbot was not found")
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    metrics = repo.compliance(bot.uuid, since)
    return answer(status.HTTP_200_OK, data={
        **metrics,
        # The rules by their number, as JSON has its keys.
        "rules": {str(rule): count for rule, count in metrics["rules"].items()},
        # What the card of the guardrails shows until HIG-103: the fixed texts, the blocked messages among them.
        "fixedTexts": metrics["verdicts"]["fixed"],
        "since": as_utc(since) if since else None})
