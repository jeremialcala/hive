# -*- coding: utf-8 -*-
"""
    The limits of POST /oauth/token (ADR-0028): an origin that asks too often waits, an origin whose assertions keep
    failing is turned away, and a client's key gets a bounded number of tokens an hour. Failures are counted by origin,
    so bad assertions in a client's name never lock that client out. Every refusal is a 429 with Retry-After, given
    before the assertion is decrypted.
"""
import pytest
from fastapi.testclient import TestClient

import app as app_module
from auth import token_limits
from auth.token_limits import TokenLimits, origin_of
from conftest import signed_assertion
from shared.auth.client import new_signing_key, token_request

ATTACKER, CLIENT_SITE = "203.0.113.7", "198.51.100.20"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch):
    """The limits of the service, on a clock the test moves."""
    clock = Clock()
    monkeypatch.setattr(token_limits, "LIMITS", TokenLimits(clock))
    return clock


@pytest.fixture
def http():
    return TestClient(app_module.app)


@pytest.fixture
def caller(register):
    return register("acme")


def asking(http, assertion: str, origin: str = CLIENT_SITE):
    return http.post("/oauth/token", data=token_request(assertion), headers={"CF-Connecting-IP": origin})


def assert_turned_away(response):
    assert response.status_code == 429
    assert response.json() == {"error": "temporarily_unavailable"}
    assert int(response.headers["retry-after"]) >= 1
    assert (response.headers["cache-control"], response.headers["pragma"]) == ("no-store", "no-cache")


# --- the windows ------------------------------------------------------------------------------------------------------

def test_an_origin_asks_its_share_a_minute_and_then_waits():
    clock = Clock()
    limits = TokenLimits(clock)

    assert all(limits.admit("a") is None for _ in range(token_limits.ORIGIN_REQUESTS))
    refusal = limits.admit("a")

    assert refusal.reason == "requests" and refusal.retry_after == 60
    clock.now += 30
    assert limits.admit("a").retry_after == 30
    assert limits.admit("b") is None
    clock.now += 30
    assert limits.admit("a") is None


def test_an_origin_that_keeps_failing_is_turned_away_for_ten_minutes():
    clock = Clock()
    limits = TokenLimits(clock)
    for _ in range(token_limits.FAILURES):
        limits.failed("a")

    refusal = limits.admit("a")

    assert refusal.reason == "failures" and refusal.retry_after == 600
    clock.now += 599
    assert limits.admit("a").reason == "failures"
    clock.now += 1
    assert limits.admit("a") is None


def test_a_key_gets_its_share_of_tokens_an_hour():
    clock = Clock()
    limits = TokenLimits(clock)

    assert all(limits.issue("client", "k1") is None for _ in range(token_limits.KEY_TOKENS))

    assert limits.issue("client", "k1").reason == "tokens"
    assert limits.issue("client", "k2") is None
    assert limits.issue("other", "k1") is None
    clock.now += 3600
    assert limits.issue("client", "k1") is None


def test_many_origins_cost_memory_up_to_a_bound(monkeypatch):
    monkeypatch.setattr(token_limits, "MAX_KEYS", 50)
    limits = TokenLimits(Clock())

    for i in range(500):
        limits.admit(f"origin-{i}")

    assert len(limits.requests) <= 50
    assert limits.admit("origin-499") is None


def test_making_room_drops_what_expired_before_an_origin_that_is_still_failing(monkeypatch):
    """
        A burst from many origins must not make the limits forget an origin that is turned away: the room is made by
        dropping the windows that are over first (HIG-137, a surviving mutant).
    """
    monkeypatch.setattr(token_limits, "MAX_KEYS", 50)
    clock = Clock()
    limits = TokenLimits(clock)
    # The attacker is the oldest of the 50 origins the limits can hold.
    limits.failed(ATTACKER)
    for i in range(49):
        limits.failed(f"old-{i}")
    clock.now += token_limits.FAILURES_WINDOW / 2
    for _ in range(token_limits.FAILURES):
        limits.failed(ATTACKER)
    # Now the windows of the others are over; the attacker's last failures are not.
    clock.now += token_limits.FAILURES_WINDOW / 2 + 1

    limits.failed("a-new-one")

    assert len(limits.failures) == 2
    assert limits.admit(ATTACKER).reason == "failures"


@pytest.mark.parametrize("header,peer,origin", [
    ("203.0.113.7", "172.18.0.5", "203.0.113.7"),
    (None, "172.18.0.5", "172.18.0.5"),
    ("not an address", "172.18.0.5", "172.18.0.5"),
    ("2001:db8:1:2:3:4:5:6", None, "2001:db8:1:2::/64"),
    ("2001:db8:1:2:ffff::1", None, "2001:db8:1:2::/64"),
    ("::ffff:203.0.113.7", None, "203.0.113.7"),
    (None, "testclient", "unknown"),
], ids=["what cloudflare saw", "the peer", "a bad header", "ipv6", "ipv6 with zeros", "ipv4 mapped", "no address"])
def test_the_origin_is_the_address_cloudflare_saw_and_an_ipv6_counts_by_its_64(header, peer, origin):
    headers = {} if header is None else {"cf-connecting-ip": header}

    assert origin_of(headers, peer) == origin


# --- through the service ----------------------------------------------------------------------------------------------

def test_an_origin_that_asks_too_often_gets_a_429_and_another_origin_does_not(http, caller, clock):
    # Good assertions, so that it is the number of requests that counts and not the failures.
    for _ in range(token_limits.ORIGIN_REQUESTS):
        assert asking(http, caller.assertion(), ATTACKER).status_code == 200

    assert_turned_away(asking(http, caller.assertion(), ATTACKER))
    assert asking(http, caller.assertion(), CLIENT_SITE).status_code == 200


def test_bad_assertions_in_a_clients_name_do_not_lock_that_client_out(http, caller, clock):
    stolen_name = new_signing_key()  # the attacker knows the client's identifier, not its key
    for _ in range(token_limits.FAILURES):
        clock.now += 7  # under the limit of requests: only the failures count here
        assert asking(http, signed_assertion(stolen_name, caller.client_id), ATTACKER).status_code == 401

    assert_turned_away(asking(http, caller.assertion(), ATTACKER))
    assert asking(http, caller.assertion(), CLIENT_SITE).status_code == 200


def test_a_malformed_request_counts_as_a_failure(http, clock):
    for _ in range(token_limits.FAILURES):
        clock.now += 7
        assert http.post("/oauth/token", content=b"{}", headers={"CF-Connecting-IP": ATTACKER,
                                                                  "Content-Type": "application/json"}).status_code == 400

    assert asking(http, "rubbish", ATTACKER).status_code == 429


def test_a_key_that_got_its_share_this_hour_gets_no_more_tokens_from_anywhere(http, caller, clock):
    for i in range(token_limits.KEY_TOKENS):
        assert asking(http, caller.assertion(), f"198.51.100.{i}").status_code == 200

    assert_turned_away(asking(http, caller.assertion(), "192.0.2.1"))
    clock.now += 3600
    assert asking(http, caller.assertion(), "192.0.2.1").status_code == 200


def test_a_refused_request_is_not_decrypted(http, caller, clock, monkeypatch):
    for _ in range(token_limits.ORIGIN_REQUESTS):
        asking(http, "rubbish", ATTACKER)
    monkeypatch.setattr(app_module, "exchange", lambda *args: pytest.fail("the assertion was decrypted"))

    assert asking(http, caller.assertion(), ATTACKER).status_code == 429


def test_the_engine_on_the_private_network_is_counted_by_its_own_address():
    limits = TokenLimits(Clock())
    worker, other_worker = origin_of({}, "172.18.0.5"), origin_of({}, "172.18.0.6")

    for _ in range(token_limits.ORIGIN_REQUESTS):
        limits.admit(worker)

    assert limits.admit(worker).reason == "requests"
    assert limits.admit(other_worker) is None


def test_the_contract_says_what_a_429_means():
    responses = app_module.app.openapi()["paths"]["/oauth/token"]["post"]["responses"]

    assert "Retry-After" in responses["429"]["headers"]
