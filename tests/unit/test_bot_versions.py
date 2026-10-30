# -*- coding: utf-8 -*-
"""
    The versions of a chatbot's configuration (RF26, HIG-101, ADR-0035): every change of its persona, rules or intents
    writes exactly one, with who made it; going back to one writes a new one and checks its intents again (RS16, AB29);
    the cliente-analista sees them and does not go back; they go with the chatbot (RF10). After each change the engine
    is told on Bifröst, with the number of the version.
"""
import asyncio
import json
import logging
import pathlib
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker

import app as app_module
from auth.idp import IdentityProvider
from classes import BotVersion, ChatBot, Intent, Rule
from conftest import KEY_OF
from controller import bot_versions
from enums import PersonRole, PersonStatus
from fake_idp import AUDIENCE, BFF, ISSUER, Zitadel, auth, exchange
from repository import TenantRepository
from shared import bus
from shared.contract import BotConfigured

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTRACT = yaml.safe_load((ROOT / "docs" / "02-design" / "contracts" / "asyncapi-v2.yaml").read_text(encoding="utf-8"))
NOTICE = Draft202012Validator({"$ref": "#/components/schemas/BotConfigurado", "components": CONTRACT["components"]},
                              format_checker=FormatChecker())
HEADERS = Draft202012Validator({"$ref": "#/components/schemas/CabecerasDeAviso", "components": CONTRACT["components"]})
BOT = {"name": "Mine", "role": "Sales Assistant", "description": "A test chatbot", "tone": "helpful"}
INTENT = {"name": "check_order", "description": "Order status",
          "actions": [{"name": "get_order", "description": "Fetch an order",
                       "apiCall": {"method": "GET", "url": "https://api.example.com/orders/{order_id}"}}]}
# A destination the rule refuses today: as if it was saved before the rule had grown to cover it.
NOT_ALLOWED_ANY_MORE = "https://10.0.0.5/orders"


class Bifrost:
    """Keeps the notices the management API sends, as the engine would hear them."""

    def __init__(self, down=False):
        self.heard, self.down = [], down

    async def publish(self, subject, payload, message_id, headers=None):
        return bus.Published(stream="none", sequence=0, duplicate=False)

    async def announce(self, subject, payload, headers=None):
        if self.down:
            raise bus.BusUnavailable("TimeoutError")
        self.heard.append(SimpleNamespace(subject=subject, notice=json.loads(payload), headers=headers))

    async def close(self):
        pass


@pytest.fixture
def bifrost():
    heard = Bifrost()
    bus.configure(heard)
    yield heard
    bus.configure(None)


@pytest.fixture
def http():
    return TestClient(app_module.app)


@pytest.fixture
def caller(register):
    return register("acme")


@pytest.fixture
def repo(caller):
    return TenantRepository(caller.client_id)


@pytest.fixture
def bot(http, caller):
    return http.post("/bot", headers=caller.auth, json=BOT).json()["data"]["uuid"]


@pytest.fixture
def logged(caplog):
    caplog.set_level(logging.WARNING)
    return lambda: "\n".join(record.getMessage() for record in caplog.records)


def versions(http, caller, bot) -> list:
    response = http.get(f"/chat/{bot}/versions", headers=caller.auth)
    assert response.status_code == 200, response.text
    return response.json()["data"]["versions"]


def version(http, caller, bot, number, **params):
    return http.get(f"/chat/{bot}/versions/{number}", headers=caller.auth, params=params)


def restore(http, caller, bot, number):
    return http.post(f"/chat/{bot}/versions/{number}/restore", headers=caller.auth)


def add_intent(http, caller, bot, body=None) -> str:
    response = http.post(f"/chat/{bot}/intents", headers=caller.auth, json=body or INTENT)
    assert response.status_code == 200, response.text
    return response.json()["data"]["uuid"]


# --- One version per change -----------------------------------------------------------------------------------------

def test_each_change_writes_exactly_one_version_with_who_made_it(http, caller, bot):
    http.post(f"/chat/{bot}/rules", headers=caller.auth, json={"rules": ["Be kind"]})
    intent = add_intent(http, caller, bot)
    http.put(f"/chat/{bot}/intents/{intent}", headers=caller.auth, json={**INTENT, "description": "Where it is"})
    http.delete(f"/chat/{bot}/intents/{intent}", headers=caller.auth)

    listed = versions(http, caller, bot)

    assert [(each["version"], each["change"]) for each in listed] == [
        (5, "intent deleted"), (4, "intent changed"), (3, "intent created"), (2, "rules added"),
        (1, "chatbot created")]
    assert {each["author"] for each in listed} == {f"key {caller.kid}"}
    assert all(datetime.fromisoformat(each["createdAt"]).utcoffset() == timedelta(0) for each in listed)
    assert ChatBot.objects(uuid=bot).first().configVersion == 5


def test_a_change_that_does_not_go_through_writes_no_version(http, caller, bot):
    missing = "6f1c7e2a-0d2b-4c1e-9a77-2f3b6a1d9e10"

    assert http.put(f"/chat/{bot}/intents/{missing}", headers=caller.auth, json=INTENT).status_code == 404
    assert http.delete(f"/chat/{bot}/intents/{missing}", headers=caller.auth).status_code == 404
    assert len(versions(http, caller, bot)) == 1


def test_what_a_version_holds_is_the_configuration_right_after_its_change(http, caller, bot):
    http.post(f"/chat/{bot}/rules", headers=caller.auth, json={"rules": ["Be kind"]})
    intent = add_intent(http, caller, bot)

    config = version(http, caller, bot, 3).json()["data"]["config"]

    assert config["persona"] == {"name": "Mine", "role": "Sales Assistant", "tone": "helpful",
                                 "description": "A test chatbot"}
    assert [rule["rule"] for rule in config["rules"]] == ["Be kind"]
    assert [each["uuid"] for each in config["intents"]] == [intent]
    assert config["intents"][0]["actions"][0]["apiCall"]["url"] == INTENT["actions"][0]["apiCall"]["url"]


def test_neither_the_meta_credentials_nor_the_verify_token_are_versioned(http, caller, bot):
    verify_token = http.get(f"/chat/{bot}/info", headers=caller.auth).json()["data"]["verifyToken"]
    http.put(f"/chat/{bot}/credentials", headers=caller.auth,
             json={"facebookToken": "EAAG-versioned-token", "appSecret": "an-app-secret"})

    assert len(versions(http, caller, bot)) == 1
    stored = json.dumps([each.config for each in BotVersion.objects(botId=bot)])
    for secret in ("EAAG-versioned-token", "an-app-secret", verify_token, "verifyToken", "facebookToken"):
        assert secret not in stored


def test_a_chatbot_from_before_versions_keeps_what_it_had_as_its_first(http, caller, repo):
    old = repo.add_bot(ChatBot(name="Old", role="r", description="d", tone="t"))
    repo.add_rules(old.uuid, ["From before"])

    add_intent(http, caller, old.uuid)

    listed = versions(http, caller, old.uuid)
    assert [(each["version"], each["author"], each["change"]) for each in listed] == [
        (2, f"key {caller.kid}", "intent created"), (1, "hive", "before versions")]
    first = version(http, caller, old.uuid, 1).json()["data"]["config"]
    assert [rule["rule"] for rule in first["rules"]] == ["From before"] and first["intents"] == []


# --- Seeing what changed --------------------------------------------------------------------------------------------

def test_a_version_says_what_changed_from_the_one_before(http, caller, bot):
    http.post(f"/chat/{bot}/rules", headers=caller.auth, json={"rules": ["Be kind"]})
    intent = add_intent(http, caller, bot)
    http.put(f"/chat/{bot}/intents/{intent}", headers=caller.auth, json={**INTENT, "description": "Where it is"})

    changes = version(http, caller, bot, 4).json()["data"]["changes"]

    assert changes["intents"] == {"added": [], "removed": [],
                                  "changed": [{"uuid": intent, "name": "check_order", "fields": ["description"]}]}
    assert changes["persona"] == {} and changes["rules"] == {"added": [], "removed": [], "changed": []}


def test_a_version_is_compared_with_any_other(http, caller, bot):
    http.post(f"/chat/{bot}/rules", headers=caller.auth, json={"rules": ["Be kind"]})
    intent = add_intent(http, caller, bot)

    data = version(http, caller, bot, 3, against=1).json()["data"]

    assert data["against"] == 1
    assert [rule["rule"] for rule in data["changes"]["rules"]["added"]] == ["Be kind"]
    assert data["changes"]["intents"]["added"] == [{"uuid": intent, "name": "check_order"}]


def test_the_first_version_is_compared_with_nothing(http, caller, bot):
    data = version(http, caller, bot, 1).json()["data"]

    assert data["against"] is None
    assert data["changes"]["persona"]["name"] == {"from": None, "to": "Mine"}


@pytest.mark.parametrize("number", ["0", "9", "abc", "1.0", "-1", "١"])
def test_a_version_that_does_not_exist_is_not_found(http, caller, bot, number):
    assert version(http, caller, bot, number).status_code == 404
    assert restore(http, caller, bot, number).status_code == 404


def test_a_version_compared_with_one_that_does_not_exist_is_not_found(http, caller, bot):
    assert version(http, caller, bot, 1, against=7).status_code == 404


def test_the_versions_come_a_page_at_a_time(http, caller, bot):
    for rule in ("one", "two", "three"):
        http.post(f"/chat/{bot}/rules", headers=caller.auth, json={"rules": [rule]})

    page = http.get(f"/chat/{bot}/versions", headers=caller.auth, params={"offset": 1, "limit": 2}).json()["data"]

    assert page["total"] == 4 and [each["version"] for each in page["versions"]] == [3, 2]


# --- Going back -----------------------------------------------------------------------------------------------------

def test_going_back_brings_the_configuration_of_the_version_and_writes_a_new_one(http, caller, bot, repo):
    http.post(f"/chat/{bot}/rules", headers=caller.auth, json={"rules": ["Be kind"]})
    intent = add_intent(http, caller, bot)
    wanted = repo.bot_config(bot)
    http.delete(f"/chat/{bot}/intents/{intent}", headers=caller.auth)
    http.post(f"/chat/{bot}/rules", headers=caller.auth, json={"rules": ["Be brief"]})
    ChatBot.objects(uuid=bot).update(set__tone="blunt")

    response = restore(http, caller, bot, 3)

    assert response.status_code == 200, response.text
    assert response.json()["data"]["version"] == 6 and response.json()["data"]["restoredFrom"] == 3
    assert repo.bot_config(bot) == wanted
    assert [tool["name"] for tool in http.get(f"/chat/{bot}/tools", headers=caller.auth).json()["data"]] == [
        "get_order"]
    assert [(each["version"], each["change"]) for each in versions(http, caller, bot)][:2] == [
        (6, "restored"), (5, "rules added")]
    # Nothing was rewritten: the versions in between are still there as they were.
    assert len(version(http, caller, bot, 4).json()["data"]["config"]["intents"]) == 0


def test_going_back_leaves_out_an_intent_made_after_the_version(http, caller, bot, repo):
    later = add_intent(http, caller, bot)

    assert restore(http, caller, bot, 1).status_code == 200

    assert repo.bot_config(bot)["intents"] == []
    assert Intent.objects(uuid=later).first().status != 1
    assert http.get(f"/chat/{bot}/intents/{later}", headers=caller.auth).status_code == 404


def test_going_back_to_the_configuration_the_chatbot_has_is_a_conflict(http, caller, bot):
    response = restore(http, caller, bot, 1)

    assert response.status_code == 409 and response.json()["message"].startswith("ALREADY_CURRENT")
    assert len(versions(http, caller, bot)) == 1


def test_going_back_to_an_intent_whose_destination_is_not_allowed_any_more_changes_nothing(http, caller, bot, repo,
                                                                                         logged):
    intent = add_intent(http, caller, bot)
    stored = BotVersion.objects(botId=bot, version=2).first()
    stored.config["intents"][0]["actions"][0]["apiCall"]["url"] = NOT_ALLOWED_ANY_MORE
    stored.save()
    http.delete(f"/chat/{bot}/intents/{intent}", headers=caller.auth)
    before = repo.bot_config(bot)

    response = restore(http, caller, bot, 2)

    assert response.status_code == 422
    assert response.json()["message"].startswith("NOT_ALLOWED") and response.json()["data"] == {
        "intents": ["check_order"]}
    assert repo.bot_config(bot) == before and len(versions(http, caller, bot)) == 3
    assert NOT_ALLOWED_ANY_MORE not in logged() and "check_order" not in logged()
    assert intent in logged()


def test_going_back_brings_the_rules_with_the_same_ids(http, caller, bot):
    http.post(f"/chat/{bot}/rules", headers=caller.auth, json={"rules": ["Be kind", "Be brief"]})
    ids = sorted(str(rule.uuid) for rule in Rule.objects(botId=bot))
    Rule.objects(botId=bot).delete()
    http.post(f"/chat/{bot}/rules", headers=caller.auth, json={"rules": ["Other"]})

    assert restore(http, caller, bot, 2).status_code == 200

    assert sorted(str(rule.uuid) for rule in Rule.objects(botId=bot)) == ids


def test_who_went_back_is_in_the_audit(http, caller, bot, logged):
    add_intent(http, caller, bot)

    restore(http, caller, bot, 1)

    assert f"Audit: key {caller.kid} restored version 1 of chatbot {bot}" in logged()


# --- Who may go back ------------------------------------------------------------------------------------------------

@pytest.fixture
def analyst(http, repo, monkeypatch):
    idp = Zitadel()
    monkeypatch.setattr(app_module, "IDP", IdentityProvider(ISSUER, AUDIENCE, read_keys=idp.jwks))
    repo.add_person("an@acme.test", ISSUER, "analyst-subject", PersonRole.ANALYST, invited_by="test",
                    status=PersonStatus.ACTIVE)
    token = exchange(http, idp.id_token(sub="analyst-subject")).json()["access_token"]
    KEY_OF[token] = BFF
    return SimpleNamespace(auth=auth(token))


def test_the_analyst_sees_the_versions_and_what_changed_but_does_not_go_back(http, caller, bot, analyst):
    add_intent(http, caller, bot)

    assert http.get(f"/chat/{bot}/versions", headers=analyst.auth).status_code == 200
    assert http.get(f"/chat/{bot}/versions/2", headers=analyst.auth).json()["data"]["changes"]["intents"]["added"]
    assert http.post(f"/chat/{bot}/versions/1/restore", headers=analyst.auth).status_code == 403
    assert len(versions(http, caller, bot)) == 2


# --- Deleting -------------------------------------------------------------------------------------------------------

def test_deleting_the_chatbot_deletes_its_versions(http, caller, bot):
    add_intent(http, caller, bot)
    assert BotVersion.objects(botId=bot).count() == 2

    deleted = http.delete(f"/bot/{bot}", headers=caller.auth)

    assert deleted.status_code == 200 and deleted.json()["data"]["deleted"]["versions"] == 2
    assert BotVersion.objects(botId=bot).count() == 0


def test_a_version_written_while_its_chatbot_is_deleted_is_not_left_behind(repo, monkeypatch):
    made = repo.add_bot(ChatBot(name="Bot", role="r", description="d", tone="t"))
    save = BotVersion.save

    def deleted_meanwhile(self, *args, **kwargs):
        result = save(self, *args, **kwargs)
        ChatBot.objects(uuid=made.uuid).delete()
        return result

    monkeypatch.setattr(BotVersion, "save", deleted_meanwhile)

    assert bot_versions.record(repo, made.uuid, "test", "a change") is None
    assert BotVersion.objects(botId=made.uuid).count() == 0


# --- Telling the engine ---------------------------------------------------------------------------------------------

def test_each_change_is_told_to_the_engine_with_its_version(http, caller, bot, bifrost):
    intent = add_intent(http, caller, bot)
    http.delete(f"/chat/{bot}/intents/{intent}", headers=caller.auth)
    restore(http, caller, bot, 2)

    assert [heard.subject for heard in bifrost.heard] == ["gateway.bot.configured"] * 3
    assert [heard.notice["version"] for heard in bifrost.heard] == [2, 3, 4]
    notice = bifrost.heard[0].notice
    assert (notice["clientId"], notice["botId"], notice["schemaVersion"]) == (str(caller.client_id), bot, 2)
    assert not list(NOTICE.iter_errors(notice)) and not list(HEADERS.iter_errors(bifrost.heard[0].headers))
    assert BotConfigured.model_validate(notice).version == 2


def test_a_change_that_does_not_go_through_is_not_told(http, caller, bot, bifrost):
    http.delete(f"/chat/{bot}/intents/6f1c7e2a-0d2b-4c1e-9a77-2f3b6a1d9e10", headers=caller.auth)
    restore(http, caller, bot, 1)

    assert bifrost.heard == []


def test_a_notice_the_bus_does_not_take_undoes_nothing(http, caller, bot, logged):
    bus.configure(Bifrost(down=True))
    try:
        response = http.post(f"/chat/{bot}/intents", headers=caller.auth, json=INTENT)
    finally:
        bus.configure(None)

    assert response.status_code == 200 and len(versions(http, caller, bot)) == 2
    assert f"The engine was not told of a change of chatbot {bot} (BusUnavailable)" in logged()


def test_the_rules_say_which_version_they_are(http, caller, bot):
    http.post(f"/chat/{bot}/rules", headers=caller.auth, json={"rules": ["Be kind"]})

    assert http.get(f"/chat/{bot}/rules", headers=caller.auth).json()["data"]["version"] == 2


def test_without_a_bus_nothing_is_told(caller, repo):
    """In development there is no bus, and nobody to tell."""
    made = repo.add_bot(ChatBot(name="Bot", role="r", description="d", tone="t"))

    asyncio.run(bot_versions.announce(repo, made.uuid))
