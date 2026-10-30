# -*- coding: utf-8 -*-
"""
    A05 of the OWASP matrix (.ai-dlc/owasp-mapping.md; HIG-134): what a hostile caller puts where Hive expects an id,
    a string or a number. Whatever it is, the answer is a 4xx: never a 5xx, never something that matched, and never
    the payload repeated back.

    - every id in the address of every route the client reaches, one at a time;
    - MongoDB operators where a body expects text;
    - an Idempotency-Key far too long;
    - a signed notification of Meta nested deeper than any parser should follow.
"""
import hashlib
import hmac
import json
from urllib.parse import quote

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import app as app_module
import webhook as webhook_module
from classes import ChatBot, ChatbotPhone, Contact, Intent
from repository import TenantRepository

HOSTILE = [
    "' OR '1'='1",
    '{"$gt": ""}',
    "$where",
    "../../../../etc/passwd",
    "..%2F..%2Fetc%2Fpasswd",
    "<script>alert(1)</script>",
    "\x00",
    "a" * 5000,
    "00000000-0000-0000-0000-000000000000",
    "1; DROP TABLE contacts",
    "*",
    "%",
]
# A stored id of each kind: only the hostile one changes in each call.
TEMPLATE_ID, REQUEST_ID = "1234567890", "6f1c7e2a-0d2b-4c1e-9a77-2f3b6a1d9e10"
TEMPLATE = {"name": "order_update", "language": "es", "category": "UTILITY",
            "components": [{"type": "BODY", "text": "Your order {{1}} is on its way",
                            "example": {"body_text": [["A-1"]]}}]}
BODIES = {
    ("POST", "/chat/{bot_id}/phones"): {"phoneNumber": "12025550101", "phoneId": "100000000000001"},
    ("POST", "/chat/{bot_id}/rules"): {"rules": ["Be kind"]},
    ("POST", "/chat/{bot_id}/intents"): {"name": "check_order", "description": "Order status"},
    ("PUT", "/chat/{bot_id}/intents/{intent_id}"): {"name": "check_order", "description": "Order status"},
    ("PUT", "/chat/{bot_id}/credentials"): {"appSecret": "an-app-secret"},
    ("PUT", "/chat/{bot_id}/contacts/{contact_id}/consent"): {"categories": ["UTILITY"], "source": "web form"},
    ("POST", "/chat/{bot_id}/contacts/{contact_id}/messages"): {"type": "text", "text": "Hello"},
    ("POST", "/chat/{bot_id}/templates"): TEMPLATE,
    ("PUT", "/chat/{bot_id}/templates/{template_id}"): {"components": TEMPLATE["components"]},
    ("PUT", "/people/{person_id}"): {"role": "cliente-analista"},
    ("POST", "/clients/{client_id}/keys"): {},
    ("POST", "/clients/{client_id}/service-keys"): {},
}
# Meta's own routes are signed instead (test_webhook_security), and these are not of a client.
PUBLIC = {("GET", "/chat/{bot_id}"), ("POST", "/chat/{bot_id}"), ("GET", "/health"), ("POST", "/oauth/token")}
WITH_IDS = sorted((method, route.path) for route in app_module.app.routes if isinstance(route, APIRoute)
                  for method in route.methods if "{" in route.path and (method, route.path) not in PUBLIC)
CASES = [(method, path, name) for method, path in WITH_IDS for name in __import__("re").findall(r"{(\w+)}", path)]
APP_SECRET = "an-app-secret-of-the-hostile-tests"


@pytest.fixture
def http():
    return TestClient(app_module.app)


@pytest.fixture
def caller(register):
    return register("acme")


@pytest.fixture
def stored(caller):
    """One of everything, so that only the hostile id of each call can be what is not found."""
    repo = TenantRepository(caller.client_id)
    bot = repo.add_bot(ChatBot(name="Bot", role="Sales Assistant", description="A test chatbot", tone="helpful"))
    repo.add_phone(bot.uuid, ChatbotPhone(phoneNumber="12025550101", phoneId="100000000000001"))
    intent = repo.add_intent(bot.uuid, Intent(name="check_order", description="Order status"))
    contact = repo.add_contact(bot.uuid, Contact(firstName="Ana", waId="12025550143"))
    return {"bot_id": str(bot.uuid), "intent_id": str(intent.uuid), "contact_id": str(contact._uuid),
            "template_id": TEMPLATE_ID, "request_id": REQUEST_ID, "client_id": str(caller.client_id),
            "person_id": REQUEST_ID, "kid": caller.kid}


def address(path: str, ids: dict, hostile_name: str, hostile: str) -> str:
    for name, value in ids.items():
        path = path.replace("{" + name + "}", quote(hostile, safe="") if name == hostile_name else value)
    return path


@pytest.mark.parametrize("hostile", HOSTILE, ids=lambda value: repr(value[:24]))
@pytest.mark.parametrize("method,path,name", CASES, ids=lambda part: str(part))
def test_a_hostile_id_in_the_address_is_a_4xx_that_matches_nothing(http, caller, stored, method, path, name,
                                                                    hostile):
    url = address(path, stored, name, hostile)
    answer = http.request(method, url, headers={**caller.auth, "Idempotency-Key": "hostile-test-key"},
                          json=BODIES.get((method, path)))

    assert 400 <= answer.status_code < 500, (method, url, answer.status_code)
    if len(hostile) > 3:
        assert hostile not in answer.text and quote(hostile, safe="") not in answer.text


OPERATORS = [{"$ne": None}, {"$gt": ""}, {"$regex": ".*"}, {"$where": "sleep(1000)"}, ["$ne", ""]]
INTO_TEXT = [
    ("POST", "/bot", lambda op: {"name": op, "role": "r", "description": "d", "tone": "t"}),
    ("PUT", "/chat/{bot_id}/credentials", lambda op: {"whatsappToken": op}),
    ("PUT", "/chat/{bot_id}/contacts/{contact_id}/consent", lambda op: {"categories": [op], "source": "web"}),
    ("POST", "/chat/{bot_id}/contacts/{contact_id}/messages", lambda op: {"type": "text", "text": op}),
    ("POST", "/chat/{bot_id}/templates", lambda op: {**TEMPLATE, "name": op}),
    ("POST", "/chat/{bot_id}/intents", lambda op: {"name": op, "description": "d"}),
    ("POST", "/contacts/search", lambda op: {"waId": op}),
    ("POST", "/people", lambda op: {"email": op, "role": "cliente-analista"}),
]


@pytest.mark.parametrize("operator", OPERATORS, ids=lambda op: json.dumps(op))
@pytest.mark.parametrize("method,path,body", INTO_TEXT, ids=lambda part: part if isinstance(part, str) else "")
def test_a_mongodb_operator_where_text_is_expected_is_refused(http, caller, stored, method, path, body, operator):
    answer = http.request(method, address(path, stored, "", ""), json=body(operator),
                          headers={**caller.auth, "Idempotency-Key": "hostile-test-key"})

    assert answer.status_code == 422, (method, path, answer.status_code)
    assert "sleep(1000)" not in answer.text and ".*" not in answer.text


def test_an_idempotency_key_far_too_long_is_refused_before_anything(http, caller, stored):
    path = address("/chat/{bot_id}/contacts/{contact_id}/messages", stored, "", "")
    answer = http.post(path, json={"type": "text", "text": "Hello"},
                       headers={**caller.auth, "Idempotency-Key": "k" * 10_000})

    assert 400 <= answer.status_code < 500 and "k" * 100 not in answer.text


def test_a_signed_notification_nested_too_deep_is_not_a_failure_of_the_webhook(http, caller, stored):
    """Only Meta can sign it, but a parser that falls over on it would make Meta send it again and again."""
    assert http.put(f"/chat/{stored['bot_id']}/credentials", headers=caller.auth,
                    json={"appSecret": APP_SECRET}).status_code == 200
    depth = 200_000
    raw = ('{"object": "whatsapp_business_account", "entry": ' + "[" * depth + "]" * depth + "}").encode()
    signature = "sha256=" + hmac.new(APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()

    answer = TestClient(webhook_module.app).post(f"/chat/{stored['bot_id']}", content=raw, headers={
        "content-type": "application/json", "X-Hub-Signature-256": signature})

    # Signed but not understood: 200, so that Meta does not keep delivering it (ADR-0015).
    assert answer.status_code == 200
