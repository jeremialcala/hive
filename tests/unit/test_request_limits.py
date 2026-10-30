# -*- coding: utf-8 -*-
"""
    The limits of the rest of the gateway (HIG-139, ADR-0028 as amended): the windows of a credential and of the
    failures of an origin, what each service counts as a failure, how much memory a flood costs, and that an origin
    turned away is answered before its request reaches the application. What the two services answer is in
    tests/security/test_route_limits.py.
"""
import asyncio
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from auth import request_limits, token_limits
from auth.principal import Principal
from auth.request_limits import API, WEBHOOK, OriginFailures, RequestLimits
from enums import PersonRole, Role

SIGNATURE = "sha256=" + "0" * 64


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def scope(method: str = "GET", path: str = "/bots", headers: dict | None = None, peer: str = "172.18.0.9") -> dict:
    return {"type": "http", "method": method, "path": path, "client": (peer, 40000),
            "headers": [(name.lower().encode(), value.encode()) for name, value in (headers or {}).items()]}


def headers_of(request: dict):
    from starlette.datastructures import Headers

    return Headers(scope=request)


# --- the windows ------------------------------------------------------------------------------------------------------

def test_a_credential_makes_its_requests_a_minute_and_then_waits():
    clock = Clock()
    limits = RequestLimits(clock)
    client = uuid4()

    assert all(limits.admit(client, "k1") is None for _ in range(request_limits.CREDENTIAL_REQUESTS))
    refusal = limits.admit(client, "k1")

    assert refusal.reason == "requests" and refusal.retry_after == 60
    clock.now += 45
    assert limits.admit(client, "k1").retry_after == 15
    clock.now += 15
    assert limits.admit(client, "k1") is None


def test_one_credential_over_its_limit_does_not_hold_any_other_up():
    limits = RequestLimits(Clock())
    client, other = uuid4(), uuid4()
    person = Principal(client_id=client, role=Role.CLIENT, kid=None, jti="", user_id=uuid4(),
                       user_role=PersonRole.ADMIN)
    for _ in range(request_limits.CREDENTIAL_REQUESTS):
        limits.admit(client, "k1")

    assert limits.admit(client, "k1") is not None
    assert limits.admit(client, "k2") is None              # another key of the same client
    assert limits.admit(other, "k1") is None               # the same kid, of another client
    assert limits.admit(client, person.limited_as) is None  # a person of the same client


def test_the_numbers_are_the_ones_decided():
    """Jeremi, 2026-10-30: about 300 a minute per credential. The rest is argued in request_limits.py."""
    assert (request_limits.CREDENTIAL_REQUESTS, request_limits.CREDENTIAL_WINDOW) == (300, 60.0)
    assert (request_limits.API_FAILURES, request_limits.API_FAILURES_WINDOW) == (30, 600.0)
    assert (request_limits.WEBHOOK_FAILURES, request_limits.WEBHOOK_FAILURES_WINDOW) == (20, 60.0)


def test_many_credentials_and_many_origins_cost_memory_up_to_a_bound(monkeypatch):
    monkeypatch.setattr(token_limits, "MAX_KEYS", 50)
    limits = RequestLimits(Clock())

    for i in range(500):
        limits.admit(uuid4(), f"k{i}")
        limits.api_failures.count(f"198.51.100.{i}")
        limits.webhook_failures.count(f"203.0.113.{i}")

    assert len(limits.credentials) <= 50
    assert len(limits.api_failures) <= 50
    assert len(limits.webhook_failures) <= 50


# --- what each service counts -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("path,headers,origin", [
    ("/bots", {"CF-Connecting-IP": "203.0.113.7"}, "203.0.113.7"),
    ("/bots", {"CF-Connecting-IP": "2001:db8:1:2:3:4:5:6"}, "2001:db8:1:2::/64"),
    ("/bots", {}, None),                                     # the portal or the engine, inside midgard
    ("/health", {"CF-Connecting-IP": "203.0.113.7"}, None),
    ("/oauth/token", {"CF-Connecting-IP": "203.0.113.7"}, None),
])
def test_the_api_counts_by_the_address_cloudflare_saw_and_not_its_own_callers(path, headers, origin):
    request = scope(path=path, headers=headers)

    assert API.origin(request, headers_of(request)) == origin


@pytest.mark.parametrize("code,failed", [(200, False), (401, True), (403, True), (404, False), (422, False),
                                         (429, False), (500, False)])
def test_the_api_counts_401_and_403(code, failed):
    request = scope(headers={"CF-Connecting-IP": "203.0.113.7"})

    assert API.failed(request, headers_of(request), code) is failed


@pytest.mark.parametrize("method,signature,code,failed", [
    ("POST", SIGNATURE, 200, False),     # what Meta signed
    ("POST", SIGNATURE, 404, False),     # Meta, for a chatbot deleted while still subscribed
    ("POST", SIGNATURE, 403, True),      # a signature of the right shape that is not good
    ("POST", None, 403, True),
    ("POST", None, 404, True),           # not signed, for a chatbot that is not there
    ("POST", "sha256=nothex", 403, True),
    ("POST", None, 413, True),
    ("GET", None, 200, False),           # the verification, with the right token
    ("GET", None, 403, True),            # with the wrong one
    ("GET", None, 404, True),
    ("GET", None, 503, False),
])
def test_the_webhook_counts_what_meta_did_not_sign(method, signature, code, failed):
    request = scope(method, "/chat/x", {} if signature is None else {"X-Hub-Signature-256": signature})

    assert WEBHOOK.failed(request, headers_of(request), code) is failed


def test_the_webhook_lets_in_only_a_notification_with_a_signature_of_metas_shape():
    signed, unsigned = scope("POST", "/chat/x", {"X-Hub-Signature-256": SIGNATURE}), scope("POST", "/chat/x")
    verification = scope("GET", "/chat/x", {"X-Hub-Signature-256": SIGNATURE})

    assert WEBHOOK.let_in(signed, headers_of(signed))
    assert not WEBHOOK.let_in(unsigned, headers_of(unsigned))
    assert not WEBHOOK.let_in(verification, headers_of(verification))


def test_the_webhook_counts_every_origin_but_not_its_health_check():
    behind_cloudflare = scope(path="/chat/x", headers={"CF-Connecting-IP": "203.0.113.7"})
    from_the_peer, health = scope(path="/chat/x", peer="172.18.0.9"), scope(path="/health")

    assert WEBHOOK.origin(behind_cloudflare, headers_of(behind_cloudflare)) == "203.0.113.7"
    assert WEBHOOK.origin(from_the_peer, headers_of(from_the_peer)) == "172.18.0.9"
    assert WEBHOOK.origin(health, headers_of(health)) is None


# --- the middleware ---------------------------------------------------------------------------------------------------

class Application:
    """What is behind the middleware: it answers `code`, and says whether it was called."""

    def __init__(self, code: int):
        self.code, self.called = code, 0

    async def __call__(self, _scope, _receive, send):
        self.called += 1
        await send({"type": "http.response.start", "status": self.code, "headers": []})
        await send({"type": "http.response.body", "body": b""})


def call(middleware, request: dict) -> int:
    sent = []

    async def receive():
        pytest.fail("the body was read")

    async def send(message):
        sent.append(message)

    asyncio.run(middleware(request, receive, send))
    return sent[0]["status"]


def test_an_origin_over_the_cap_is_answered_before_the_application_or_its_body(monkeypatch):
    monkeypatch.setattr(request_limits, "LIMITS", RequestLimits(Clock()))
    application = Application(403)
    middleware = OriginFailures(application, rule=WEBHOOK, log=Mock())
    unsigned = scope("POST", "/chat/x", {"CF-Connecting-IP": "203.0.113.7"})

    codes = [call(middleware, unsigned) for _ in range(request_limits.WEBHOOK_FAILURES + 2)]

    assert codes == [403] * request_limits.WEBHOOK_FAILURES + [429, 429]
    assert application.called == request_limits.WEBHOOK_FAILURES


def test_a_request_that_is_not_http_goes_through():
    application = Application(200)

    asyncio.run(OriginFailures(application, rule=API, log=Mock())({"type": "lifespan"}, None, AsyncMock()))

    assert application.called == 1


def test_the_429_is_the_one_of_oauth_token():
    answer = request_limits.too_many(42)

    assert answer.status_code == 429
    assert answer.body == b'{"error":"temporarily_unavailable"}'
    assert (answer.headers["retry-after"], answer.headers["cache-control"], answer.headers["pragma"]) == \
        ("42", "no-store", "no-cache")
