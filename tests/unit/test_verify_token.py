# -*- coding: utf-8 -*-
"""
    Rotating the verifyToken of a chatbot (RF25, HIG-104): a new random value, and the one before stops working at
    once, in the webhook too, whatever it kept in memory. What Meta already subscribed keeps coming in. Only the
    cliente-admin and the machine credential rotate it and see it; the rotation is in the audit, never the value.
"""
import json
import logging
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app as app_module
import webhook as webhook_module
from auth.idp import IdentityProvider
from classes import AuditEntry, ChatBot
from conftest import KEY_OF
from enums import PersonRole, PersonStatus
from fake_idp import AUDIENCE, BFF, ISSUER, Zitadel, auth, exchange
from repository import TenantRepository, kept_bots

META = TestClient(webhook_module.app)
BOT = {"name": "Mine", "role": "Sales Assistant", "description": "A test chatbot", "tone": "helpful"}


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
    made = http.post("/bot", headers=caller.auth, json=BOT).json()["data"]
    return SimpleNamespace(uuid=made["uuid"], verify_token=made["verifyToken"])


@pytest.fixture
def logged(caplog):
    caplog.set_level(logging.INFO)
    return lambda: "\n".join(record.getMessage() for record in caplog.records)


def verify(bot_id, token):
    return META.get(f"/chat/{bot_id}", params={"hub.mode": "subscribe", "hub.challenge": "42",
                                               "hub.verify_token": token})


def rotate(http, who, bot_id):
    return http.post(f"/chat/{bot_id}/verify-token", headers=who.auth)


def test_a_rotation_gives_a_new_random_token_and_the_old_one_stops_working(http, caller, bot):
    before = verify(bot.uuid, bot.verify_token)
    assert (before.status_code, before.text) == (200, "42")

    response = rotate(http, caller, bot.uuid)

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    token = response.json()["data"]["verifyToken"]
    assert token != bot.verify_token and len(token) >= 40
    old, new = verify(bot.uuid, bot.verify_token), verify(bot.uuid, token)
    assert old.status_code == 403
    assert (new.status_code, new.text) == (200, "42")
    card = http.get(f"/chat/{bot.uuid}/info", headers=caller.auth).json()["data"]
    assert card["verifyToken"] == token


def test_two_rotations_never_give_the_same_token(http, caller, bot):
    first = rotate(http, caller, bot.uuid).json()["data"]["verifyToken"]
    second = rotate(http, caller, bot.uuid).json()["data"]["verifyToken"]

    assert first != second


def test_the_token_is_stored_encrypted(http, caller, bot):
    token = rotate(http, caller, bot.uuid).json()["data"]["verifyToken"]

    stored = ChatBot.objects(uuid=bot.uuid).first()

    assert token not in json.dumps(stored.to_mongo().to_dict(), default=str)


def test_the_webhook_does_not_wait_out_what_it_kept_in_memory(http, caller, bot):
    # The webhook keeps the chatbot it found for 30 s, in its own process: there the rotation cannot make it forget.
    stale = ChatBot.objects(uuid=bot.uuid).first()
    token = rotate(http, caller, bot.uuid).json()["data"]["verifyToken"]
    kept_bots.keep(stale)

    old, new = verify(bot.uuid, bot.verify_token), verify(bot.uuid, token)

    assert old.status_code == 403 and new.status_code == 200


def test_another_client_cannot_rotate_it(http, bot, register):
    globex = register("globex")

    refused = rotate(http, globex, bot.uuid)

    assert refused.status_code == 404
    assert verify(bot.uuid, bot.verify_token).status_code == 200


@pytest.fixture
def analyst(http, repo, monkeypatch):
    idp = Zitadel()
    monkeypatch.setattr(app_module, "IDP", IdentityProvider(ISSUER, AUDIENCE, read_keys=idp.jwks))
    repo.add_person("an@acme.test", ISSUER, "analyst-subject", PersonRole.ANALYST, invited_by="test",
                    status=PersonStatus.ACTIVE)
    token = exchange(http, idp.id_token(sub="analyst-subject")).json()["access_token"]
    KEY_OF[token] = BFF
    return SimpleNamespace(auth=auth(token))


def test_the_analyst_gets_403_and_never_sees_the_token(http, bot, analyst):
    refused = rotate(http, analyst, bot.uuid)
    card = http.get(f"/chat/{bot.uuid}/info", headers=analyst.auth)

    assert refused.status_code == 403 and bot.verify_token not in refused.text
    assert card.status_code == 200 and "verifyToken" not in card.json()["data"]
    assert verify(bot.uuid, bot.verify_token).status_code == 200


def test_the_rotation_is_in_the_audit_and_the_log_but_not_the_value(http, caller, bot, logged):
    token = rotate(http, caller, bot.uuid).json()["data"]["verifyToken"]

    entries = http.get("/audit", headers=caller.auth, params={"action": "rotate_verify_token"}).json()["data"]
    entry = entries["entries"][0]

    assert (entry["category"], entry["result"], entry["resource"]) == ("keys", "ok", {"bot_id": bot.uuid})
    assert f"rotated the verifyToken of chatbot {bot.uuid}" in logged()
    stored = json.dumps([each.to_mongo().to_dict() for each in AuditEntry.objects], default=str)
    for secret in (token, bot.verify_token):
        assert secret not in stored and secret not in logged()
