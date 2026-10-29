# -*- coding: utf-8 -*-
"""
    The isolation between clients, seen from outside. Two clients are registered and each calls the service with
    its own token: every route of a chatbot of client A, called by client B, must answer 404, exactly as for a
    chatbot that does not exist.

    The routes are taken from the application, so a new one that is not covered here fails the test.
"""
from uuid import uuid4

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import app as app_module
from classes import ChatBot, ChatbotPhone, Contact, Intent, Rule
from repository import TenantRepository

INTENT = {"name": "check_order", "description": "Order status"}
TEMPLATE = {"name": "order_update", "language": "es", "category": "UTILITY",
            "components": [{"type": "BODY", "text": "Your order {{1}} is on its way",
                            "example": {"body_text": [["A-1"]]}}]}
BODIES = {
    ("POST", "/chat/{bot_id}/phones"): {"phoneNumber": "12025550101", "phoneId": "100000000000001"},
    ("POST", "/chat/{bot_id}/rules"): {"rules": ["Be kind"]},
    ("POST", "/chat/{bot_id}/intents"): INTENT,
    ("PUT", "/chat/{bot_id}/intents/{intent_id}"): INTENT,
    ("PUT", "/chat/{bot_id}/credentials"): {"appSecret": "an-app-secret"},
    ("PUT", "/chat/{bot_id}/contacts/{contact_id}/consent"): {"categories": ["UTILITY"], "source": "web form"},
    ("POST", "/chat/{bot_id}/contacts/{contact_id}/messages"): {"type": "text", "text": "Hello"},
    ("POST", "/chat/{bot_id}/templates"): TEMPLATE,
    ("PUT", "/chat/{bot_id}/templates/{template_id}"): {"components": TEMPLATE["components"]},
}
# Sending is accepted with a 202 once the bus holds it, and only inside the window: test_outbound covers the owner.
# Whoever is not the owner gets the same 404 here as anywhere else.
SENDING = ("POST", "/chat/{bot_id}/contacts/{contact_id}/messages")
# Making, editing and deleting a template goes to Meta: test_templates covers the owner, against a Meta of its own.
IN_META = {("POST", "/chat/{bot_id}/templates"), ("PUT", "/chat/{bot_id}/templates/{template_id}"),
           ("DELETE", "/chat/{bot_id}/templates/{template_id}")}
TEMPLATE_ID = "1234567890"
BOT = {"name": "Mine", "role": "Sales Assistant", "description": "A test chatbot", "tone": "helpful"}
PUBLIC = {("GET", "/chat/{bot_id}"), ("POST", "/chat/{bot_id}"), ("GET", "/health")}
ROUTES = sorted((method, route.path) for route in app_module.app.routes if isinstance(route, APIRoute)
                for method in route.methods)
# What only the response engine gets, with the key it holds for a client: test_service_credential covers it.
ENGINE_ONLY = {("GET", "/chat/{bot_id}/tokens"), ("GET", "/chat/{bot_id}/contacts/{contact_id}/turns")}
BOT_ROUTES = [route for route in ROUTES if route not in PUBLIC | ENGINE_ONLY and "{bot_id}" in route[1]]
# What is not about one chatbot: creating one, and the registry of clients and keys.
OTHER_ROUTES = [route for route in ROUTES if route not in PUBLIC | ENGINE_ONLY and route not in BOT_ROUTES]


@pytest.fixture
def http():
    return TestClient(app_module.app)


@pytest.fixture
def owner(register):
    return register("acme")


@pytest.fixture
def stranger(register):
    return register("globex")


@pytest.fixture
def stored(owner):
    """A chatbot of the owner with one of everything."""
    repo = TenantRepository(owner.client_id)
    bot = repo.add_bot(ChatBot(name="Bot", role="Sales Assistant", description="A test chatbot", tone="helpful"))
    repo.add_phone(bot.uuid, ChatbotPhone(phoneNumber="12025550101", phoneId="100000000000001"))
    repo.add_rules(bot.uuid, ["Be kind"])
    intent = repo.add_intent(bot.uuid, Intent(name="check_order", description="Order status"))
    contact = repo.add_contact(bot.uuid, Contact(firstName="Ana", waId="12025550143"))
    return bot, intent, contact


def call(http: TestClient, caller, method: str, path: str, bot_id, intent_id, contact_id):
    return http.request(method, path.format(bot_id=bot_id, intent_id=intent_id, contact_id=contact_id,
                                            template_id=TEMPLATE_ID),
                        headers=caller.auth, json=BODIES.get((method, path)))


def test_every_route_of_a_chatbot_is_covered():
    assert len(BOT_ROUTES) == 23 and ENGINE_ONLY <= set(ROUTES) and IN_META <= set(BOT_ROUTES)
    # The search of contacts and the deletion of a client have their own tests in test_export_and_delete, and
    # the keys of the response engine in test_service_credential.
    assert OTHER_ROUTES == [
        ("DELETE", "/clients/{client_id}"), ("DELETE", "/clients/{client_id}/keys/{kid}"),
        ("DELETE", "/clients/{client_id}/service-keys/{kid}"), ("DELETE", "/keys/{kid}"),
        ("GET", "/clients"), ("GET", "/gateway/keys"), ("GET", "/keys"), ("POST", "/bot"), ("POST", "/clients"),
        ("POST", "/clients/{client_id}/keys"), ("POST", "/clients/{client_id}/service-keys"),
        ("POST", "/contacts/search"), ("POST", "/keys"), ("POST", "/oauth/token"),
    ]


@pytest.mark.parametrize("method,path", [route for route in BOT_ROUTES if route != SENDING and route not in IN_META])
def test_the_owner_reaches_its_chatbot(http, owner, stored, method, path):
    bot, intent, contact = stored

    assert call(http, owner, method, path, bot.uuid, intent.uuid, contact._uuid).status_code == 200


@pytest.mark.parametrize("method,path", BOT_ROUTES)
def test_another_client_gets_not_found(http, stranger, stored, method, path):
    bot, intent, contact = stored
    before = {document: document.objects.count() for document in (ChatBot, ChatbotPhone, Rule, Intent, Contact)}
    secrets = ChatBot.objects(uuid=bot.uuid).first().appSecret

    response = call(http, stranger, method, path, bot.uuid, intent.uuid, contact._uuid)

    assert response.status_code == 404
    assert ChatBot.objects(uuid=bot.uuid).first().appSecret == secrets
    assert {document: document.objects.count() for document in before} == before
    assert Intent.objects(uuid=intent.uuid).first().status == intent.status


@pytest.mark.parametrize("method,path", BOT_ROUTES)
def test_a_chatbot_of_another_client_answers_like_one_that_does_not_exist(http, stranger, stored, method, path):
    bot, intent, contact = stored

    foreign = call(http, stranger, method, path, bot.uuid, intent.uuid, contact._uuid)
    missing = call(http, stranger, method, path, uuid4(), intent.uuid, contact._uuid)

    assert (foreign.status_code, foreign.json()["code"]) == (missing.status_code, missing.json()["code"]) == (404, 404)


@pytest.mark.parametrize("method,path", BOT_ROUTES)
def test_the_operator_does_not_reach_the_chatbot_of_a_client(http, operator, stored, method, path):
    """The operator registers clients. It is not a way into their data: on these routes it is one more client."""
    bot, intent, contact = stored

    assert call(http, operator, method, path, bot.uuid, intent.uuid, contact._uuid).status_code == 404


@pytest.mark.parametrize("method,path", [route for route in BOT_ROUTES if "{intent_id}" in route[1]])
def test_an_intent_is_not_reachable_through_another_chatbot(http, owner, stored, method, path):
    _, intent, contact = stored
    other = TenantRepository(owner.client_id).add_bot(ChatBot(name="Other", role="r", description="d", tone="t"))

    assert call(http, owner, method, path, other.uuid, intent.uuid, contact._uuid).status_code == 404


@pytest.mark.parametrize("method,path", [route for route in BOT_ROUTES if "{contact_id}" in route[1]])
def test_a_contact_is_not_reachable_through_another_chatbot(http, owner, stored, method, path):
    _, intent, contact = stored
    other = TenantRepository(owner.client_id).add_bot(ChatBot(name="Other", role="r", description="d", tone="t"))

    assert call(http, owner, method, path, other.uuid, intent.uuid, contact._uuid).status_code == 404
    assert Contact.objects.count() == 1


def test_a_chatbot_belongs_to_whoever_creates_it(http, owner, stranger):
    created = http.post("/bot", headers=owner.auth, json=BOT)
    bot_id = created.json()["data"]["uuid"]

    assert created.status_code == 200
    assert ChatBot.objects(uuid=bot_id).first().clientId == owner.client_id
    assert http.get(f"/chat/{bot_id}/info", headers=owner.auth).status_code == 200
    assert http.get(f"/chat/{bot_id}/info", headers=stranger.auth).status_code == 404


def test_the_operator_has_its_own_chatbots(http, operator, owner):
    created = http.post("/bot", headers=operator.auth, json=BOT)
    bot_id = created.json()["data"]["uuid"]

    assert ChatBot.objects(uuid=bot_id).first().clientId == operator.client_id
    assert http.get(f"/chat/{bot_id}/info", headers=operator.auth).status_code == 200
    assert http.get(f"/chat/{bot_id}/info", headers=owner.auth).status_code == 404
