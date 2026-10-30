# -*- coding: utf-8 -*-
"""
    A06 of the OWASP matrix (.ai-dlc/owasp-mapping.md; RS10, HIG-139): the routes of hive-api other than /oauth/token,
    and the webhook, have their limits. A credential that asks too often waits, and so does an origin that keeps
    getting 401 or 403 from hive-api, or keeps sending the webhook what Meta did not sign: before its body is read.
    What Meta signed always goes in, and the health checks are never limited. Every refusal is the 429 of
    /oauth/token, with Retry-After.

    The limits are made small here, so that a test does not need hundreds of requests to reach them; the numbers of
    the service are checked in tests/unit/test_request_limits.py.
"""
import hashlib
import hmac
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app as app_module
import webhook as webhook_module
from auth import request_limits
from auth.request_limits import RequestLimits
from enums import Status
from shared.auth.client import token_request
from utils import BodySizeLimit

ATTACKER, CLIENT_SITE = "203.0.113.7", "198.51.100.20"
PER_CREDENTIAL, FAILURES = 5, 3
APP_SECRET = os.environ["FACEBOOK_APP_SECRET"]
BOT_ID = str(uuid4())
WEBHOOK = f"/chat/{BOT_ID}"
VERIFY_TOKEN = "the-verify-token-of-this-bot"
BODY = json.dumps({"object": "whatsapp_business_account", "entry": [{"id": "101831186329557", "changes": [{
    "field": "messages", "value": {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "15550001111", "phone_number_id": "100000000000001"},
        "contacts": [{"profile": {"name": "Ana"}, "wa_id": "584120000000"}],
        "messages": [{"from": "584120000000", "id": "wamid.TEST", "type": "text", "text": {"body": "hola"}}],
    }}]}]}).encode()


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch):
    """The limits of both services, small, on a clock the test moves."""
    clock = Clock()
    monkeypatch.setattr(request_limits, "CREDENTIAL_REQUESTS", PER_CREDENTIAL)
    monkeypatch.setattr(request_limits, "API_FAILURES", FAILURES)
    monkeypatch.setattr(request_limits, "WEBHOOK_FAILURES", FAILURES)
    monkeypatch.setattr(request_limits, "LIMITS", RequestLimits(clock))
    return clock


@pytest.fixture
def api():
    return TestClient(app_module.app)


@pytest.fixture
def hook():
    return TestClient(webhook_module.app)


def from_(origin: str, headers: dict | None = None) -> dict:
    return {"CF-Connecting-IP": origin, **(headers or {})}


def assert_turned_away(response, retry_after: int | None = None):
    assert response.status_code == 429
    assert response.json() == {"error": "temporarily_unavailable"}
    assert int(response.headers["retry-after"]) >= 1
    if retry_after is not None:
        assert int(response.headers["retry-after"]) == retry_after
    assert (response.headers["cache-control"], response.headers["pragma"]) == ("no-store", "no-cache")


def nothing_is_read(monkeypatch):
    """From here on, reading the body of a request fails the test."""
    async def reads(*_args):
        pytest.fail("the body was read")
    monkeypatch.setattr(BodySizeLimit, "__call__", reads)


# --- hive-api: per credential -----------------------------------------------------------------------------------------

def test_a_credential_gets_its_requests_a_minute_and_then_a_429_with_retry_after(api, register, clock):
    acme = register("acme").auth
    for _ in range(PER_CREDENTIAL):
        assert api.get("/bots", headers=from_(CLIENT_SITE, acme)).status_code == 200

    assert_turned_away(api.get("/bots", headers=from_(CLIENT_SITE, acme)), retry_after=60)
    clock.now += 60
    assert api.get("/bots", headers=from_(CLIENT_SITE, acme)).status_code == 200


def test_a_credential_is_counted_wherever_it_asks_from(api, register, clock):
    """The engine and the portal ask from inside midgard, without the header of Cloudflare: they are counted too."""
    acme = register("acme").auth
    for i in range(PER_CREDENTIAL):
        assert api.get("/bots", headers=from_(f"198.51.100.{i}", acme)).status_code == 200

    assert_turned_away(api.get("/bots", headers=acme))


def test_a_credential_over_its_limit_does_not_hold_another_up(api, register, clock):
    acme, other = register("acme").auth, register("other").auth
    for _ in range(PER_CREDENTIAL):
        api.get("/bots", headers=from_(CLIENT_SITE, acme))
    assert_turned_away(api.get("/bots", headers=from_(CLIENT_SITE, acme)))

    assert api.get("/bots", headers=from_(CLIENT_SITE, other)).status_code == 200


def test_a_credential_turned_away_reaches_nothing(api, register, clock, monkeypatch):
    acme = register("acme").auth
    for _ in range(PER_CREDENTIAL):
        api.get("/bots", headers=acme)
    monkeypatch.setattr(app_module, "ctr_list_bots", lambda *_args: pytest.fail("the route was reached"))

    assert_turned_away(api.get("/bots", headers=acme))


# --- hive-api: the failures of an origin ------------------------------------------------------------------------------

def test_an_origin_that_keeps_getting_401_or_403_is_turned_away_before_its_body_is_read(api, register, clock,
                                                                                       monkeypatch):
    acme = register("acme").auth
    assert api.get("/bots", headers=from_(ATTACKER)).status_code == 401
    assert api.get("/bots", headers=from_(ATTACKER, {"Authorization": "DPoP not.a.token"})).status_code == 401
    assert api.get("/clients", headers=from_(ATTACKER, acme)).status_code == 403  # the operator's

    nothing_is_read(monkeypatch)
    response = api.post("/bot", json={"name": "x"}, headers=from_(ATTACKER, acme))

    assert_turned_away(response, retry_after=600)


def test_an_origin_turned_away_does_not_hold_up_another(api, register, clock):
    acme = register("acme").auth
    for _ in range(FAILURES):
        api.get("/bots", headers=from_(ATTACKER))
    assert_turned_away(api.get("/bots", headers=from_(ATTACKER, acme)))

    assert api.get("/bots", headers=from_(CLIENT_SITE, acme)).status_code == 200
    clock.now += 600
    assert api.get("/bots", headers=from_(ATTACKER, acme)).status_code == 200


def test_the_callers_inside_midgard_are_not_counted_by_origin(api, clock):
    """The portal carries the requests of every person: one who keeps failing would lock all the others out."""
    for _ in range(FAILURES * 3):
        assert api.get("/bots").status_code == 401


def test_oauth_token_keeps_its_own_limits_and_is_not_counted_again(api, register, clock):
    caller = register("acme")
    for _ in range(FAILURES):
        refused = api.post("/oauth/token", data=token_request("rubbish"), headers=from_(ATTACKER))
        assert refused.status_code in (400, 401)
    assert api.get("/bots", headers=from_(ATTACKER, caller.auth)).status_code == 200

    for _ in range(FAILURES):
        api.get("/bots", headers=from_(ATTACKER))
    assert api.post("/oauth/token", data=token_request(caller.assertion()),
                    headers=from_(ATTACKER)).status_code == 200


def test_the_health_check_of_the_api_is_never_limited(api, register, clock):
    acme = register("acme").auth
    for _ in range(FAILURES):
        api.get("/bots", headers=from_(ATTACKER))
    for _ in range(PER_CREDENTIAL + 1):
        api.get("/bots", headers=acme)

    assert all(api.get("/health", headers=from_(ATTACKER, acme)).status_code == 200 for _ in range(PER_CREDENTIAL * 3))


# --- the webhook ------------------------------------------------------------------------------------------------------

def sign(body: bytes, secret: str = APP_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def bot(monkeypatch):
    _bot = SimpleNamespace(uuid=BOT_ID, clientId=uuid4(), verifyToken=VERIFY_TOKEN, appSecret=None,
                           status=Status.ACT.value)
    monkeypatch.setattr(webhook_module, "find_webhook_bot", lambda bot_id: _bot)
    monkeypatch.setattr(webhook_module, "webhook_secrets",
                        lambda bot: SimpleNamespace(verify_token=bot.verifyToken, app_secret=bot.appSecret))
    monkeypatch.setattr(webhook_module, "bot_verified", lambda *_args: None)
    return _bot


@pytest.fixture
def process(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(webhook_module, "ctr_process_messages", mock)
    return mock


def notify(hook, origin: str, signature: str | None, body: bytes = BODY):
    headers = from_(origin, {"Content-Type": "application/json"})
    if signature is not None:
        headers["X-Hub-Signature-256"] = signature
    return hook.post(WEBHOOK, content=body, headers=headers)


def verifying(hook, origin: str, token: str):
    return hook.get(WEBHOOK, params={"hub.mode": "subscribe", "hub.challenge": "1", "hub.verify_token": token},
                    headers=from_(origin))


def test_what_is_not_signed_past_the_cap_gets_a_429_without_its_body_read(hook, bot, process, clock, monkeypatch):
    for _ in range(FAILURES):
        assert notify(hook, ATTACKER, None).status_code == 403
    assert notify(hook, CLIENT_SITE, None).status_code == 403  # another origin is answered as always

    nothing_is_read(monkeypatch)
    monkeypatch.setattr(webhook_module, "find_webhook_bot", lambda bot_id: pytest.fail("the chatbot was looked for"))

    assert_turned_away(notify(hook, ATTACKER, None), retry_after=60)
    assert_turned_away(notify(hook, ATTACKER, "sha256=not-a-signature"))
    process.assert_not_awaited()


def test_a_body_too_big_from_an_origin_turned_away_is_not_even_measured(hook, bot, clock):
    for _ in range(FAILURES):
        notify(hook, ATTACKER, None)

    assert_turned_away(notify(hook, ATTACKER, None, body=b"x" * (3 * 1024 * 1024 + 1)))


def test_what_meta_signed_always_goes_in_even_past_the_cap(hook, bot, process, clock):
    for _ in range(FAILURES):
        notify(hook, ATTACKER, None)
    assert_turned_away(notify(hook, ATTACKER, None))

    answers = [notify(hook, ATTACKER, sign(BODY)).status_code for _ in range(FAILURES * 3)]

    assert answers == [200] * (FAILURES * 3)
    assert process.await_count == FAILURES * 3


def test_a_signature_that_is_not_good_counts_as_a_failure(hook, bot, process, clock):
    for _ in range(FAILURES):
        assert notify(hook, ATTACKER, sign(BODY, "not-the-app-secret")).status_code == 403

    assert_turned_away(notify(hook, ATTACKER, None))
    # A signature of Meta's shape is still checked: only reading the body says whether it is Meta's.
    assert notify(hook, ATTACKER, sign(BODY, "not-the-app-secret")).status_code == 403
    assert notify(hook, ATTACKER, sign(BODY)).status_code == 200


def test_a_verification_with_the_wrong_token_counts_as_a_failure(hook, bot, clock):
    for _ in range(FAILURES):
        assert verifying(hook, ATTACKER, "not-the-verify-token").status_code == 403

    assert_turned_away(verifying(hook, ATTACKER, VERIFY_TOKEN), retry_after=60)
    assert verifying(hook, CLIENT_SITE, VERIFY_TOKEN).text == "1"
    clock.now += 60
    assert verifying(hook, ATTACKER, VERIFY_TOKEN).text == "1"


def test_the_health_check_of_the_webhook_is_never_limited(hook, bot, clock):
    for _ in range(FAILURES):
        notify(hook, ATTACKER, None)

    assert all(hook.get("/health", headers=from_(ATTACKER)).status_code == 200 for _ in range(FAILURES * 3))
