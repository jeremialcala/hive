# -*- coding: utf-8 -*-
"""
    The versions of a chatbot's configuration (RF26, ADR-0035): its persona, rules and intents. Every change writes one,
    with who made it and when, and they are kept for as long as the chatbot is. The cliente-admin goes back to one,
    which writes a new version: nothing is rewritten. The cliente-analista sees them and what changed, and does not go
    back (ANALYST_ROUTES). The Meta credentials and the verifyToken are not in them.

    Going back checks the intents of the version again with the rules they are saved with today (RS16, AB29): a
    destination that was allowed then and is not now is not brought back.

    After each change the management API says so on Bifröst, `gateway.bot.configured`, with the number of the version:
    the engine's coordinator drops what it remembers of the chatbot instead of waiting out its 5 minutes. A notice that
    does not go out undoes nothing: the engine reads the chatbot again within those 5 minutes.
"""
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from classes import BotVersion, IntentRequest, Settings
from classes.tool_clock import as_utc
from repository import NotFound, TenantRepository, database_failed
from shared import bus
from shared.contract import CONFIGURED_SUBJECT, BotConfigured

from .answers import answer
from .webhook_event import SCHEMA_VERSION, traceparent

__all__ = ["CREATED", "RULES_ADDED", "INTENT_CREATED", "INTENT_CHANGED", "INTENT_DELETED", "RESTORED", "versioned",
           "record", "announce", "ctr_list_versions", "ctr_get_version", "ctr_restore_version"]

settings = Settings()
log = logging.getLogger(settings.environment)

# What each version says it was.
CREATED, RULES_ADDED, RESTORED = "chatbot created", "rules added", "restored"
INTENT_CREATED, INTENT_CHANGED, INTENT_DELETED = "intent created", "intent changed", "intent deleted"
# The first version of a chatbot from before versions: what it had when it was first changed.
BEFORE_VERSIONS, HIVE = "before versions", "hive"

CHATBOT_NOT_FOUND = "This chatbot was not found"
VERSION_NOT_FOUND = "This version was not found"
ALREADY_CURRENT = "ALREADY_CURRENT: the chatbot already has the configuration of this version"
NOT_ALLOWED = "NOT_ALLOWED: an intent of this version is not allowed any more"


@dataclass
class Change:
    """What `versioned` hands to the change it wraps: the version it wrote, once it did."""
    version: BotVersion | None = None


def record(repo: TenantRepository, bot_id, author: str, change: str,
           restored_from: int | None = None) -> BotVersion | None:
    """
        Writes the version a change left. A version that cannot be written does not undo the change, already made: the
        answer is what the change was, and the error stays in the log.
    """
    try:
        return repo.add_version(bot_id, author, change, restored_from)
    except NotFound:
        # The chatbot was deleted in the meantime, and its versions with it.
        return None
    except Exception as e:
        if not database_failed(e):
            raise
        log.error(f"Chatbot {bot_id} was changed ({change}), but its version could not be written: "
                  f"{type(e).__name__}")
        return None


@contextmanager
def versioned(repo: TenantRepository, bot_id, author: str, change: str, restored_from: int | None = None):
    """
        Wraps a change of the configuration: once it is made, its version is written. If the change raises, nothing is.
        A chatbot from before versions first gets one with what it had, so that the change can be undone.
    """
    bot = repo.get_bot(bot_id)
    if not bot.configVersion:
        record(repo, bot.uuid, HIVE, BEFORE_VERSIONS)
    made = Change()
    yield made
    made.version = record(repo, bot.uuid, author, change, restored_from)


async def announce(repo: TenantRepository, bot_id) -> None:
    """Tells the engine that the chatbot's configuration changed, and which version it is at now."""
    publisher = bus.publisher() if bus.configured() else None
    if getattr(publisher, "announce", None) is None:
        return
    try:
        bot = await run_in_threadpool(repo.get_bot, bot_id)
    except NotFound:
        return
    except Exception as e:
        if not database_failed(e):
            raise
        # Not the id: it is what the address carried.
        log.warning(f"The engine was not told of a change of a chatbot of client {repo.client_id}: "
                    f"{type(e).__name__}")
        return
    if not bot.configVersion:
        # Nothing was versioned: there is no version to tell.
        return
    notice = BotConfigured(schemaVersion=SCHEMA_VERSION, uuid=uuid4(), clientId=repo.client_id, botId=bot.uuid,
                           version=bot.configVersion, configuredAt=datetime.now(timezone.utc))
    try:
        await publisher.announce(CONFIGURED_SUBJECT, notice.model_dump_json().encode("utf-8"),
                                 headers={"traceparent": traceparent()})
    except bus.BusUnavailable as e:
        log.warning(f"The engine was not told of a change of chatbot {bot.uuid} ({type(e).__name__}): it reads it "
                    f"again within 5 minutes")


def _number(value) -> int:
    """The number of a version in an address. One that cannot exist is answered like one that does not."""
    text = str(value)
    if not text.isascii() or not text.isdigit() or len(text) > 9 or int(text) < 1:
        raise NotFound(value)
    return int(text)


def version_info(version: BotVersion) -> dict:
    return {"version": version.version, "author": version.author, "change": version.change,
            "restoredFrom": version.restoredFrom, "createdAt": as_utc(version.createdAt)}


def _by_uuid(items: list) -> dict:
    return {item["uuid"]: item for item in items}


def differences(before: dict | None, after: dict) -> dict:
    """What changed from the configuration `before` to `after`: from nothing, for the first version."""
    before = before or {"persona": {}, "rules": [], "intents": []}
    persona = {field: {"from": before["persona"].get(field), "to": value}
               for field, value in after["persona"].items() if before["persona"].get(field) != value}
    old_rules, new_rules = _by_uuid(before["rules"]), _by_uuid(after["rules"])
    rules = {"added": [rule for key, rule in new_rules.items() if key not in old_rules],
             "removed": [rule for key, rule in old_rules.items() if key not in new_rules],
             "changed": [{"uuid": key, "from": old_rules[key]["rule"], "to": rule["rule"]}
                         for key, rule in new_rules.items() if key in old_rules and old_rules[key] != rule]}
    old_intents, new_intents = _by_uuid(before["intents"]), _by_uuid(after["intents"])
    intents = {"added": [{"uuid": key, "name": intent["name"]} for key, intent in new_intents.items()
                         if key not in old_intents],
               "removed": [{"uuid": key, "name": intent["name"]} for key, intent in old_intents.items()
                           if key not in new_intents],
               "changed": [{"uuid": key, "name": intent["name"],
                            "fields": sorted(field for field in intent if intent[field] != old_intents[key].get(field))}
                           for key, intent in new_intents.items()
                           if key in old_intents and old_intents[key] != intent]}
    return {"persona": persona, "rules": rules, "intents": intents}


def ctr_list_versions(repo: TenantRepository, bot_id: str, offset: int, limit: int) -> JSONResponse:
    try:
        total, versions = repo.list_versions(bot_id, offset, limit)
    except NotFound:
        return answer(status.HTTP_404_NOT_FOUND, CHATBOT_NOT_FOUND)
    return answer(status.HTTP_200_OK, data={"total": total, "offset": offset, "limit": limit,
                                            "versions": [version_info(version) for version in versions]})


def ctr_get_version(repo: TenantRepository, bot_id: str, number: str, against: int | None) -> JSONResponse:
    """A version with all it holds, and what changed from `against`: by default, the version before it."""
    try:
        repo.get_bot(bot_id)
    except NotFound:
        return answer(status.HTTP_404_NOT_FOUND, CHATBOT_NOT_FOUND)
    try:
        version = repo.get_version(bot_id, _number(number))
        against = version.version - 1 if against is None else against
        before = repo.get_version(bot_id, against).config if against >= 1 else None
    except NotFound:
        return answer(status.HTTP_404_NOT_FOUND, VERSION_NOT_FOUND)
    return answer(status.HTTP_200_OK, data={**version_info(version), "config": version.config,
                                            "against": against if before is not None else None,
                                            "changes": differences(before, version.config)})


def _without_empty(value):
    """A stored value without what it left unset: an intent saved before a field had a default comes without it."""
    if isinstance(value, dict):
        return {key: _without_empty(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_without_empty(item) for item in value]
    return value


def _not_allowed(config: dict) -> list[str]:
    """The intents of a configuration that would not be saved today: the rules of an intent are checked again."""
    refused = []
    for intent in config["intents"]:
        try:
            IntentRequest.model_validate(_without_empty(intent))
        except ValidationError:
            refused.append(intent["uuid"])
    return refused


def ctr_restore_version(repo: TenantRepository, bot_id: str, number: str, author: str) -> JSONResponse:
    """
        Gives the chatbot the configuration of a version, and writes it as a new one. 409 if it already has it; 422 if
        an intent of it is not allowed any more (RS16), and then nothing changes.
    """
    try:
        repo.get_bot(bot_id)
    except NotFound:
        return answer(status.HTTP_404_NOT_FOUND, CHATBOT_NOT_FOUND)
    try:
        version = repo.get_version(bot_id, _number(number))
    except NotFound:
        return answer(status.HTTP_404_NOT_FOUND, VERSION_NOT_FOUND)
    if repo.bot_config(bot_id) == version.config:
        return answer(status.HTTP_409_CONFLICT, ALREADY_CURRENT)
    refused = _not_allowed(version.config)
    if refused:
        # The ids only: their names and destinations are what the client wrote.
        log.warning(f"{author} could not restore version {version.version} of chatbot {version.botId}: intents "
                    f"{', '.join(refused)} are not allowed any more")
        names = [intent["name"] for intent in version.config["intents"] if intent["uuid"] in refused]
        return answer(status.HTTP_422_UNPROCESSABLE_ENTITY, NOT_ALLOWED, data={"intents": names})
    try:
        with versioned(repo, bot_id, author, RESTORED, restored_from=version.version) as made:
            repo.apply_config(bot_id, version.config)
    except NotFound:
        return answer(status.HTTP_404_NOT_FOUND, CHATBOT_NOT_FOUND)
    log.warning(f"Audit: {author} restored version {version.version} of chatbot {version.botId}")
    return answer(status.HTTP_200_OK, data=version_info(made.version) if made.version else None)
