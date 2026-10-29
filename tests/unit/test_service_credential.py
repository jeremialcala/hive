# -*- coding: utf-8 -*-
"""
    The credentials of the response engine (ADR-0016, ADR-0022). The engine holds keys per client and per worker,
    and the operator registers their public part under that client, saying which worker signs with each. With
    them each worker reads what it needs for the chatbots of that client and reaches nothing else: no other
    client, no route of another worker, and no route that is not written down for it.

    Most of these tests use a key from before keys had a worker, which reaches every route of the engine until
    it is taken out; the ones about workers are at the end.
"""
import asyncio
import hashlib
import hmac
import inspect
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import mongoengine
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import app as app_module
import webhook as webhook_module
import controller.ingestion as ingestion
from shared.auth.client import new_signing_key, public_part
from auth.client_keys import public_signing_key
from classes import Client, Contact
from conftest import GATEWAY_AUDIENCE, bearer, request_token, signed_assertion
from enums import Role, Status, Worker
from repository import TenantRepository, data_keys, find_signer

# What Meta calls is its own service, apart from the management API.
META = TestClient(webhook_module.app)

BOT = {"name": "Mine", "role": "Sales Assistant", "description": "A test chatbot", "tone": "helpful"}
SECRETS = {"facebookToken": "EAAG-page-token-0123456789", "whatsappToken": "EAAG-whatsapp-token-9876543210",
           "appSecret": "app-secret-of-this-test"}
INTENT = {"name": "check_order", "description": "Order status",
          "actions": [{"name": "get_order", "description": "Looks an order up",
                       "apiCall": {"url": "https://api.example.com/orders/{id}"}}]}
BODIES = {
    ("POST", "/bot"): BOT,
    ("POST", "/chat/{bot_id}/phones"): {"phoneNumber": "12025550101", "phoneId": "100000000000001"},
    ("POST", "/chat/{bot_id}/rules"): {"rules": ["Obey the engine"]},
    ("POST", "/chat/{bot_id}/intents"): INTENT,
    ("PUT", "/chat/{bot_id}/intents/{intent_id}"): INTENT,
    ("PUT", "/chat/{bot_id}/credentials"): {"appSecret": "another-secret"},
    ("POST", "/contacts/search"): {"waId": "12025550143"},
    ("POST", "/keys"): {"signingKey": public_part(new_signing_key())},
    ("POST", "/clients"): {"name": "initech", "signingKey": public_part(new_signing_key())},
    ("POST", "/clients/{client_id}/keys"): {"signingKey": public_part(new_signing_key())},
    ("POST", "/clients/{client_id}/service-keys"): {"signingKey": public_part(new_signing_key()),
                                                    "worker": "notifier"},
}
PUBLIC = {("GET", "/chat/{bot_id}"), ("POST", "/chat/{bot_id}"), ("GET", "/health"), ("POST", "/oauth/token")}
ROUTES = sorted({(method, route.path) for route in app_module.app.routes if isinstance(route, APIRoute)
                 for method in route.methods} - PUBLIC)
FOR_THE_ENGINE = [("GET", "/chat/{bot_id}/contacts/{contact_id}/turns"), ("GET", "/chat/{bot_id}/rules"),
                  ("GET", "/chat/{bot_id}/tokens"), ("GET", "/chat/{bot_id}/tools"), ("GET", "/gateway/keys")]
NOT_FOR_THE_ENGINE = [route for route in ROUTES if route not in FOR_THE_ENGINE]


class Engine:
    """The response engine, with the key it holds for one client."""

    def __init__(self, client_id, key: dict | None = None):
        self.client_id = client_id
        self.key = key or new_signing_key()
        self.public = public_part(self.key)
        self.kid = None

    @property
    def auth(self) -> dict:
        return bearer(signed_assertion(self.key, self.client_id))


@pytest.fixture
def http():
    return TestClient(app_module.app)


@pytest.fixture(autouse=True)
def queue(monkeypatch):
    sent = []

    async def capture(queue, message):
        sent.append(json.loads(message.to_json()))

    monkeypatch.setattr(ingestion, "send_message_to_queue", capture)
    return sent


@pytest.fixture
def acme(register):
    return register("acme")


@pytest.fixture
def globex(register):
    return register("globex")


def new_bot(http, caller, secrets=SECRETS) -> dict:
    created = http.post("/bot", headers=caller.auth, json=BOT).json()["data"]
    calls = [("POST", "rules", {"rules": ["Be kind"]}), ("POST", "intents", INTENT)]
    if secrets:
        calls.append(("PUT", "credentials", secrets))
    for method, path, body in calls:
        assert http.request(method, f"/chat/{created['uuid']}/{path}", headers=caller.auth,
                            json=body).status_code == 200
    return created


@pytest.fixture
def bot(http, acme):
    return new_bot(http, acme)["uuid"]


def let_in(http, operator, client_id, engine: Engine, worker: str = "notifier"):
    """The operator registers the key a worker of the engine holds for a client."""
    response = http.post(f"/clients/{client_id}/service-keys", headers=operator.auth,
                         json={"signingKey": engine.public, "worker": worker})
    if response.status_code == 200:
        engine.kid = response.json()["data"]["serviceKeys"][-1]["kid"]
    return response


def let_in_without_a_worker(client_id, engine: Engine) -> Engine:
    """A key of the engine as it was registered before keys had a worker (ADR-0016): the API no longer takes one."""
    key = public_signing_key(engine.public)
    Client.objects(uuid=client_id).update_one(push__serviceKeys=key)
    engine.kid = key["kid"]
    return engine


@pytest.fixture
def engine(acme):
    """The response engine, let in for acme with a key from before keys had a worker."""
    return let_in_without_a_worker(acme.client_id, Engine(acme.client_id))


def dump() -> str:
    """Everything in the database but the tokens already used, as text."""
    database = mongoengine.get_db()
    return json.dumps({name: list(database[name].find({})) for name in sorted(database.list_collection_names())
                       if name != "seen_token"}, default=str, sort_keys=True)


# --- What the engine gets -----------------------------------------------------------------------------------------

def test_the_engine_gets_the_tokens_of_a_chatbot_of_its_client(http, engine, bot):
    response = http.get(f"/chat/{bot}/tokens", headers=engine.auth)

    assert response.status_code == 200
    assert response.json()["data"] == {"uuid": bot, "facebookToken": SECRETS["facebookToken"],
                                       "whatsappToken": SECRETS["whatsappToken"]}


def test_the_engine_gets_the_rest_of_what_it_answers_with(http, engine, bot):
    rules = http.get(f"/chat/{bot}/rules", headers=engine.auth)
    tools = http.get(f"/chat/{bot}/tools", headers=engine.auth)
    keys = http.get("/gateway/keys", headers=engine.auth)

    assert (rules.status_code, tools.status_code, keys.status_code) == (200, 200, 200)
    # The persona and the rules as data, for the engine to write Claude's system with: no prompt of Llama 2.
    assert rules.json()["data"] == {"name": "Mine", "role": "Sales Assistant", "tone": "helpful",
                                    "description": "A test chatbot", "rules": ["Be kind"]}
    assert "<<SYS>>" not in rules.text and "<s>" not in rules.text
    assert [tool["name"] for tool in tools.json()["data"]] == ["get_order"]
    assert keys.json()["data"]["audience"] == GATEWAY_AUDIENCE


def test_what_the_engine_may_call_is_written_down_and_nothing_else_is():
    assert sorted(app_module.SERVICE_ROUTES) == FOR_THE_ENGINE
    assert set(FOR_THE_ENGINE) <= set(ROUTES)


def test_the_tokens_come_without_the_secrets_the_engine_has_no_use_for(http, acme, engine):
    """The App Secret only checks what Meta sends, and the verification token is the client's to paste in Meta."""
    created = new_bot(http, acme)

    response = http.get(f"/chat/{created['uuid']}/tokens", headers=engine.auth)

    assert set(response.json()["data"]) == {"uuid", "facebookToken", "whatsappToken"}
    assert SECRETS["appSecret"] not in response.text and created["verifyToken"] not in response.text


def test_a_chatbot_without_tokens_has_none_to_give(http, acme, engine):
    created = new_bot(http, acme, secrets=None)

    response = http.get(f"/chat/{created['uuid']}/tokens", headers=engine.auth)

    assert response.status_code == 200
    assert response.json()["data"] == {"uuid": created["uuid"], "facebookToken": None, "whatsappToken": None}


def test_the_answer_with_the_tokens_is_not_to_be_kept(http, engine, bot):
    assert http.get(f"/chat/{bot}/tokens", headers=engine.auth).headers["cache-control"] == "no-store"


@pytest.mark.parametrize("bot_id", [str(uuid4()), "not-a-uuid"])
def test_there_are_no_tokens_of_a_chatbot_that_does_not_exist(http, engine, bot_id):
    response = http.get(f"/chat/{bot_id}/tokens", headers=engine.auth)

    assert (response.status_code, response.json()["code"]) == (404, 404)


def test_without_the_key_service_no_token_is_handed_out(http, engine, bot, key_service):
    key_service.down = True
    data_keys.forget()

    response = http.get(f"/chat/{bot}/tokens", headers=engine.auth)

    assert (response.status_code, response.json()["code"]) == (503, 503)
    assert "data" not in response.json()


def test_a_failure_handing_out_the_tokens_says_nothing_of_itself(http, engine, bot, monkeypatch, caplog):
    # Everything the service writes, down to its debug lines: development and staging keep those.
    caplog.set_level(logging.DEBUG, logger=app_module.settings.environment)

    def fails(*args, **kwargs):
        raise RuntimeError(f"failure-text-marker {SECRETS['facebookToken']}")

    monkeypatch.setattr(TenantRepository, "bot_secrets", fails)

    response = http.get(f"/chat/{bot}/tokens", headers=engine.auth)

    assert response.status_code == 500
    for part in ("failure-text-marker", SECRETS["facebookToken"]):
        assert part not in response.text and part not in caplog.text


# --- One key per client ---------------------------------------------------------------------------------------------

def test_the_key_for_one_client_does_not_reach_the_chatbots_of_another(http, operator, globex, engine, bot):
    theirs = new_bot(http, globex)["uuid"]

    for path in ("tokens", "rules", "tools"):
        foreign = http.get(f"/chat/{theirs}/{path}", headers=engine.auth)
        missing = http.get(f"/chat/{uuid4()}/{path}", headers=engine.auth)

        assert (foreign.status_code, foreign.json()["code"]) == (missing.status_code, missing.json()["code"]) == (
            404, 404)
        assert SECRETS["facebookToken"] not in foreign.text


def test_the_engine_needs_another_key_for_another_client(http, operator, globex, engine, bot):
    theirs = new_bot(http, globex)["uuid"]
    for_globex = Engine(globex.client_id)
    assert let_in(http, operator, globex.client_id, for_globex).status_code == 200

    assert http.get(f"/chat/{theirs}/tokens", headers=for_globex.auth).status_code == 200
    assert http.get(f"/chat/{bot}/tokens", headers=for_globex.auth).status_code == 404
    assert http.get(f"/chat/{bot}/tokens", headers=engine.auth).status_code == 200


def test_the_key_registered_for_one_client_does_not_sign_for_another(http, globex, engine):
    """The same key, saying it is for globex: it is not registered there."""
    as_globex = Engine(globex.client_id, engine.key)
    theirs = new_bot(http, globex)["uuid"]

    assert http.get(f"/chat/{theirs}/tokens", headers=as_globex.auth).status_code == 401


def test_the_envelope_says_whose_chatbot_it_is_and_that_is_all_the_engine_needs(http, operator, acme, bot, queue):
    """From the message in the queue to the tokens: the chatbot and its client come in the envelope."""
    engine_key = new_signing_key()
    payload = json.dumps({"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{
        "field": "messages", "value": {
            "messaging_product": "whatsapp", "metadata": {"phone_number_id": "100000000000001"},
            "contacts": [{"profile": {"name": "Ana"}, "wa_id": "12025550143"}],
            "messages": [{"from": "12025550143", "id": "wamid.ONE", "type": "text", "text": {"body": "hola"}}]}}]}]})
    signature = "sha256=" + hmac.new(SECRETS["appSecret"].encode(), payload.encode(), hashlib.sha256).hexdigest()
    assert META.post(f"/chat/{bot}", content=payload, headers={"content-type": "application/json",
                                                               "X-Hub-Signature-256": signature}).status_code == 200
    (envelope,) = queue
    body = envelope["jwe_body"]
    assert (body["botId"], body["clientId"]) == (bot, str(acme.client_id))

    engine = Engine(body["clientId"], engine_key)
    assert let_in(http, operator, body["clientId"], engine).status_code == 200

    assert http.get(f"/chat/{body['botId']}/tokens", headers=engine.auth).json()["data"]["whatsappToken"] == SECRETS[
        "whatsappToken"]


# --- What the engine does not get -----------------------------------------------------------------------------------

@pytest.mark.parametrize("method,path", NOT_FOR_THE_ENGINE)
def test_the_engine_reaches_no_other_route(http, acme, operator, engine, bot, method, path):
    intent = http.get(f"/chat/{bot}/intents", headers=acme.auth).json()["data"][0]["uuid"]
    address = path.format(bot_id=bot, intent_id=intent, contact_id=uuid4(), client_id=acme.client_id,
                          kid=acme.kid)
    before = dump()

    response = http.request(method, address, headers=engine.auth, json=BODIES.get((method, path)))

    assert response.status_code == 403
    assert dump() == before


def test_every_route_is_either_for_the_engine_or_closed_to_it():
    assert len(NOT_FOR_THE_ENGINE) == 29
    assert sorted(FOR_THE_ENGINE + NOT_FOR_THE_ENGINE) == ROUTES


def test_a_client_does_not_get_its_own_tokens_back(http, acme, operator, bot, caplog):
    """They go in through the API and do not come out to whoever loaded them."""
    # Everything the service writes, down to its debug lines: development and staging keep those.
    caplog.set_level(logging.DEBUG, logger=app_module.settings.environment)
    for caller in (acme, operator):
        response = http.get(f"/chat/{bot}/tokens", headers=caller.auth)

        assert response.status_code == 403
        assert SECRETS["facebookToken"] not in response.text
    assert "handed to the response engine" not in caplog.text


def test_an_assertion_of_the_engine_is_good_once_and_its_access_token_for_many_requests(http, engine, bot):
    assertion = signed_assertion(engine.key, engine.client_id)
    headers = bearer(assertion)

    assert request_token(assertion).status_code == 401
    assert [http.get(f"/chat/{bot}/tokens", headers=headers).status_code for _ in range(2)] == [200, 200]


# --- A trace of every time ------------------------------------------------------------------------------------------

def test_every_time_the_tokens_are_handed_out_leaves_a_trace_that_production_keeps(http, acme, engine, bot, caplog):
    # Everything the service writes, down to its debug lines: development and staging keep those.
    caplog.set_level(logging.DEBUG, logger=app_module.settings.environment)
    http.get(f"/chat/{bot}/tokens", headers=engine.auth)
    http.get(f"/chat/{bot}/tokens", headers=engine.auth)

    kept = [record.getMessage() for record in caplog.records
            if record.levelno >= logging.WARNING and "handed to the response engine" in record.getMessage()]
    assert kept == [f"Meta tokens of chatbot {bot} handed to the response engine: client {acme.client_id}, "
                    f"key {engine.kid}, worker none"] * 2
    for secret in SECRETS.values():
        assert secret not in caplog.text


def test_tokens_that_were_not_handed_out_leave_no_such_trace(http, globex, engine, caplog):
    # Everything the service writes, down to its debug lines: development and staging keep those.
    caplog.set_level(logging.DEBUG, logger=app_module.settings.environment)
    theirs = new_bot(http, globex)["uuid"]

    http.get(f"/chat/{theirs}/tokens", headers=engine.auth)

    assert "handed to the response engine" not in caplog.text


# --- Letting the engine in, and out -----------------------------------------------------------------------------------

def test_only_the_operator_lets_the_engine_in(http, acme, globex, engine):
    another = Engine(acme.client_id)

    for caller in (acme, globex, engine):
        added = http.post(f"/clients/{acme.client_id}/service-keys", headers=caller.auth,
                          json={"signingKey": another.public, "worker": "notifier"})
        removed = http.delete(f"/clients/{acme.client_id}/service-keys/{engine.kid}", headers=caller.auth)

        assert (added.status_code, removed.status_code) == (403, 403)
    assert [key["kid"] for key in Client.objects(uuid=acme.client_id).first().serviceKeys] == [engine.kid]


def test_the_client_sees_which_keys_of_the_engine_reach_its_chatbots(http, acme, operator, engine):
    mine = http.get("/keys", headers=acme.auth).json()["data"]
    listed = {client["name"]: client for client in http.get("/clients", headers=operator.auth).json()["data"]}

    assert (mine["keys"], mine["serviceKeys"]) == ([acme.kid], [{"kid": engine.kid, "worker": None}])
    assert listed["acme"]["serviceKeys"] == mine["serviceKeys"] and listed["operator"]["serviceKeys"] == []


def test_only_the_public_part_of_the_key_of_the_engine_is_stored(http, operator, acme, engine):
    stored = Client.objects(uuid=acme.client_id).first().serviceKeys

    assert [set(key) for key in stored] == [{"kty", "crv", "x", "y", "kid", "use", "alg"}]
    assert engine.key["d"] not in dump()


@pytest.mark.parametrize("key", [new_signing_key(), {"kty": "RSA", "n": "AQAB", "e": "AQAB"}, {"kty": "EC"}, "a-key"],
                         ids=["a private key", "not an elliptic curve key", "without coordinates", "not a key"])
def test_only_a_public_p256_key_is_accepted_for_the_engine(http, operator, acme, key):
    response = http.post(f"/clients/{acme.client_id}/service-keys", headers=operator.auth,
                         json={"signingKey": key, "worker": "notifier"})

    assert response.status_code == 422
    assert Client.objects(uuid=acme.client_id).first().serviceKeys == []


@pytest.mark.parametrize("client_id", [str(uuid4()), "not-a-uuid"])
def test_the_engine_is_not_let_in_for_a_client_that_does_not_exist(http, operator, client_id):
    added = http.post(f"/clients/{client_id}/service-keys", headers=operator.auth,
                      json={"signingKey": Engine(client_id).public, "worker": "notifier"})
    removed = http.delete(f"/clients/{client_id}/service-keys/a-kid", headers=operator.auth)

    assert (added.status_code, removed.status_code) == (404, 404)


def test_removing_a_key_that_is_not_there_is_not_found(http, operator, acme, engine):
    response = http.delete(f"/clients/{acme.client_id}/service-keys/{acme.kid}", headers=operator.auth)

    assert response.status_code == 404
    assert len(Client.objects(uuid=acme.client_id).first().signingKeys) == 1


def test_the_engine_rotates_its_key_for_a_client_without_a_gap(http, operator, acme, engine, bot):
    new = Engine(acme.client_id)
    assert let_in(http, operator, acme.client_id, new).status_code == 200

    # Both work while the engine moves to the new one.
    assert [http.get(f"/chat/{bot}/tokens", headers=key.auth).status_code for key in (engine, new)] == [200, 200]

    taken_out = http.delete(f"/clients/{acme.client_id}/service-keys/{engine.kid}", headers=operator.auth)

    assert taken_out.status_code == 200
    assert [http.get(f"/chat/{bot}/tokens", headers=key.auth).status_code for key in (engine, new)] == [401, 200]


def test_a_worker_has_two_keys_for_a_client_at_most(http, operator, acme, engine):
    """The key from before keys had a worker does not count: the limit is per worker."""
    for _ in range(2):
        assert let_in(http, operator, acme.client_id, Engine(acme.client_id)).status_code == 200

    third = let_in(http, operator, acme.client_id, Engine(acme.client_id))
    for_another_worker = let_in(http, operator, acme.client_id, Engine(acme.client_id), worker="coordinator")

    assert (third.status_code, third.json()["detail"]) == (409, "TOO_MANY_KEYS")
    assert for_another_worker.status_code == 200
    stored = Client.objects(uuid=acme.client_id).first().serviceKeys
    assert [key.get("worker") for key in stored] == [None, "notifier", "notifier", "coordinator"]


def test_a_key_is_the_client_s_or_the_engine_s_never_both(http, operator, acme, engine):
    """Which list a key is in says who signs with it. In both, it would say two things."""
    as_engine = http.post(f"/clients/{acme.client_id}/service-keys", headers=operator.auth,
                          json={"signingKey": public_part(acme.key), "worker": "notifier"})
    # Even holding its private part, which a client never has: without it, the proof alone turns it away.
    as_client = http.post("/keys", **acme.adding(engine.key))
    again = let_in(http, operator, acme.client_id, engine)

    for response in (as_engine, as_client, again):
        assert (response.status_code, response.json()["detail"]) == (409, "KEY_ALREADY_REGISTERED")
    client = Client.objects(uuid=acme.client_id).first()
    assert ([key["kid"] for key in client.signingKeys], [key["kid"] for key in client.serviceKeys]) == (
        [acme.kid], [engine.kid])


def test_who_signs_is_told_by_the_list_its_key_is_in(operator, acme, engine):
    assert find_signer(acme.client_id, acme.kid)[2] is Role.CLIENT
    assert find_signer(acme.client_id, engine.kid)[2] is Role.SERVICE
    assert find_signer(operator.client_id, operator.kid)[2] is Role.OPERATOR
    assert find_signer(acme.client_id, "another-kid") is None


def test_the_engine_does_not_get_in_for_a_client_that_was_shut_out(http, engine, bot, acme):
    Client.objects(uuid=acme.client_id).update_one(set__status=Status.DIS.value)

    assert http.get(f"/chat/{bot}/tokens", headers=engine.auth).status_code == 401


def test_deleting_a_client_takes_the_key_of_the_engine_with_it(http, operator, acme, engine, bot):
    deleted = http.delete(f"/clients/{acme.client_id}", headers=operator.auth)

    assert deleted.status_code == 200
    assert http.get(f"/chat/{bot}/tokens", headers=engine.auth).status_code == 401
    assert engine.kid not in dump()


def test_letting_the_engine_in_and_out_leaves_a_trace_that_production_keeps(http, operator, acme, caplog):
    engine = Engine(acme.client_id)

    let_in(http, operator, acme.client_id, engine)
    http.delete(f"/clients/{acme.client_id}/service-keys/{engine.kid}", headers=operator.auth)

    kept = [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING]
    assert kept == [f"Service key {engine.kid} of worker notifier added to client {acme.client_id}",
                    f"Service key removed from client {acme.client_id}"]


# --- The last turns of a conversation, for the coordinator (RF15) ------------------------------------------------

def conversation(acme, bot, turns: int) -> Contact:
    """A contact of the chatbot who wrote `turns` times, and got two answers each time, a minute apart."""
    repo = TenantRepository(acme.client_id)
    contact = repo.add_contact(bot, Contact(firstName="Ana", waId="12025550143"))
    start = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
    for turn in range(turns):
        at = start + timedelta(minutes=turn)
        repo.record_message(contact, "in", f"wamid.IN{turn:02d}", "whatsapp", at, text=f"pregunta {turn}")
        for answer in range(2):
            repo.record_message(contact, "out", f"wamid.OUT{turn:02d}{answer}", "whatsapp",
                                at + timedelta(seconds=answer + 1), text=f"respuesta {turn}.{answer}",
                                in_reply_to=f"wamid.IN{turn:02d}")
    return contact


def turns_of(http, engine, bot, contact):
    return http.get(f"/chat/{bot}/contacts/{contact._uuid}/turns", headers=engine.auth)


def test_the_engine_gets_the_last_ten_turns_both_ways_and_oldest_first(http, acme, engine, bot):
    contact = conversation(acme, bot, turns=12)

    response = turns_of(http, engine, bot, contact)

    assert response.status_code == 200
    messages = response.json()["data"]["messages"]
    assert [m["messageId"] for m in messages if m["direction"] == "in"] == [f"wamid.IN{t:02d}" for t in range(2, 12)]
    assert len(messages) == 30
    assert (messages[0]["text"], messages[1]["inReplyTo"]) == ("pregunta 2", "wamid.IN02")
    assert response.headers["Cache-Control"] == "no-store"


def test_a_shorter_conversation_comes_whole(http, acme, engine, bot):
    contact = conversation(acme, bot, turns=3)

    assert len(turns_of(http, engine, bot, contact).json()["data"]["messages"]) == 9


def test_a_contact_who_never_wrote_has_no_turns(http, acme, engine, bot):
    contact = conversation(acme, bot, turns=0)

    assert turns_of(http, engine, bot, contact).json()["data"]["messages"] == []


def test_the_turns_of_a_contact_of_another_client_are_not_found(http, operator, acme, globex, engine, bot):
    theirs = new_bot(http, globex)["uuid"]
    contact = conversation(globex, theirs, turns=2)

    assert turns_of(http, engine, theirs, contact).status_code == 404
    assert turns_of(http, engine, bot, contact).status_code == 404


def test_a_client_does_not_get_the_turns_with_its_own_key(http, acme, bot):
    """It has the export of the contact for that: the turns are the engine's."""
    contact = conversation(acme, bot, turns=1)

    assert http.get(f"/chat/{bot}/contacts/{contact._uuid}/turns", headers=acme.auth).status_code == 403


def test_without_the_key_service_no_turn_is_handed_out(http, acme, engine, bot, key_service):
    contact = conversation(acme, bot, turns=1)
    key_service.down = True
    data_keys.forget()

    response = turns_of(http, engine, bot, contact)

    assert response.status_code == 503 and "pregunta" not in response.text


def test_a_slow_read_for_the_engine_does_not_hold_the_other_requests(http, acme, engine, bot, monkeypatch):
    """
        On midgard one query to Atlas that never came back left the whole API without an answer, its health check
        included: the request that waited held the event loop. A read for the engine now waits in a thread.
    """
    contact = conversation(acme, bot, turns=1)
    monkeypatch.setattr(TenantRepository, "last_turns", lambda self, contact, turns=10: time.sleep(1.5) or [])

    async def turns_and_health():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_module.app),
                                     base_url="http://testserver") as client:
            # Timed from before the slow request: a held event loop would hold the clock too.
            started = time.monotonic()
            turns = asyncio.create_task(client.get(f"/chat/{bot}/contacts/{contact._uuid}/turns",
                                                   headers=engine.auth))
            await asyncio.sleep(0.3)
            health = await client.get("/health")
            return health.status_code, time.monotonic() - started, (await turns).status_code

    health, answered_after, turns = asyncio.run(turns_and_health())

    assert (health, turns) == (200, 200)
    assert answered_after < 1.0


@pytest.mark.parametrize("route", [("GET", "/chat/{bot_id}/contacts/{contact_id}/turns"),
                                   ("GET", "/chat/{bot_id}/rules"), ("GET", "/chat/{bot_id}/tokens"),
                                   ("GET", "/chat/{bot_id}/tools")])
def test_the_routes_of_the_engine_and_the_authentication_run_in_a_thread(route):
    endpoint = next(r.endpoint for r in app_module.app.routes
                    if isinstance(r, APIRoute) and (route[1], route[0]) in {(r.path, m) for m in r.methods})

    assert not inspect.iscoroutinefunction(endpoint)
    assert not inspect.iscoroutinefunction(app_module.get_principal)
    assert not inspect.iscoroutinefunction(app_module.get_repository)


def test_every_time_the_turns_are_handed_out_leaves_a_trace_that_production_keeps(http, acme, engine, bot, caplog):
    contact = conversation(acme, bot, turns=2)
    caplog.set_level(logging.WARNING)

    turns_of(http, engine, bot, contact)

    assert f"Last turns of contact {contact._uuid} of chatbot {bot} handed to the response engine" in caplog.text
    assert "pregunta" not in caplog.text and "12025550143" not in caplog.text


# --- One key per worker (ADR-0022) ---------------------------------------------------------------------------------

WORKERS = [worker.value for worker in Worker]
ENGINE_ROUTES = {"gateway": ("GET", "/gateway/keys"), "rules": ("GET", "/chat/{bot_id}/rules"),
                 "tools": ("GET", "/chat/{bot_id}/tools"), "tokens": ("GET", "/chat/{bot_id}/tokens"),
                 "turns": ("GET", "/chat/{bot_id}/contacts/{contact_id}/turns")}
# The table of ADR-0022, written out again: changing what a worker reaches has to change it here too. All of them
# also reach the gateway's public keys.
ADR_0022 = {"classifier": {"rules"}, "coordinator": {"rules", "tools", "turns"}, "link": {"tools"},
            "responder": {"rules"}, "evaluator": {"rules"}, "notifier": {"tokens"}}


def worker_engine(http, operator, client, worker: str) -> Engine:
    """A worker of the engine, let in for a client with a key of its own."""
    engine = Engine(client.client_id)
    assert let_in(http, operator, client.client_id, engine, worker=worker).status_code == 200
    return engine


def test_each_worker_reaches_what_adr_0022_says_and_the_gateway_keys():
    assert {worker.value: routes for worker, routes in app_module.WORKER_ROUTES.items()} == {
        worker: {ENGINE_ROUTES[name] for name in names | {"gateway"}} for worker, names in ADR_0022.items()}


def test_no_worker_reaches_a_route_that_is_not_the_engine_s():
    assert set().union(*app_module.WORKER_ROUTES.values()) == set(FOR_THE_ENGINE)
    assert sorted(ADR_0022) == sorted(WORKERS)


@pytest.mark.parametrize("name", sorted(ENGINE_ROUTES))
@pytest.mark.parametrize("worker", WORKERS)
def test_a_worker_gets_its_routes_and_none_of_the_others(http, operator, acme, bot, worker, name):
    contact = conversation(acme, bot, turns=1)
    engine = worker_engine(http, operator, acme, worker)
    method, path = ENGINE_ROUTES[name]

    response = http.request(method, path.format(bot_id=bot, contact_id=contact._uuid), headers=engine.auth)

    if name == "gateway" or name in ADR_0022[worker]:
        assert response.status_code == 200
    else:
        assert response.status_code == 403
        assert SECRETS["facebookToken"] not in response.text and "pregunta" not in response.text


@pytest.mark.parametrize("body", [{}, {"worker": None}, {"worker": "scheduler"}, {"worker": "NOTIFIER"}],
                         ids=["without a worker", "a null worker", "a worker that does not exist", "in capitals"])
def test_a_key_of_the_engine_is_only_let_in_for_a_worker_it_names(http, operator, acme, body):
    response = http.post(f"/clients/{acme.client_id}/service-keys", headers=operator.auth,
                         json={"signingKey": Engine(acme.client_id).public, **body})

    assert response.status_code == 422
    assert Client.objects(uuid=acme.client_id).first().serviceKeys == []


def test_the_key_of_a_worker_is_stored_with_its_worker_and_its_public_part_only(http, operator, acme):
    engine = worker_engine(http, operator, acme, "coordinator")

    (stored,) = Client.objects(uuid=acme.client_id).first().serviceKeys

    assert set(stored) == {"kty", "crv", "x", "y", "kid", "use", "alg", "worker"}
    assert (stored["kid"], stored["worker"]) == (engine.kid, "coordinator")
    assert engine.key["d"] not in dump()


def test_who_signs_comes_with_its_worker(http, operator, acme, engine):
    coordinator = worker_engine(http, operator, acme, "coordinator")

    assert find_signer(acme.client_id, coordinator.kid)[2:] == (Role.SERVICE, Worker.COORDINATOR)
    assert find_signer(acme.client_id, engine.kid)[2:] == (Role.SERVICE, None)
    assert find_signer(acme.client_id, acme.kid)[2:] == (Role.CLIENT, None)
    # What verifies the token is the key alone.
    assert "worker" not in find_signer(acme.client_id, coordinator.kid)[1]


def test_the_client_sees_which_worker_signs_with_each_key_of_the_engine(http, operator, acme, engine):
    coordinator = worker_engine(http, operator, acme, "coordinator")

    mine = http.get("/keys", headers=acme.auth).json()["data"]["serviceKeys"]

    assert mine == [{"kid": engine.kid, "worker": None}, {"kid": coordinator.kid, "worker": "coordinator"}]


def test_a_worker_rotates_its_key_without_a_gap(http, operator, acme, bot):
    old, new = [worker_engine(http, operator, acme, "notifier") for _ in range(2)]

    assert [http.get(f"/chat/{bot}/tokens", headers=key.auth).status_code for key in (old, new)] == [200, 200]

    taken_out = http.delete(f"/clients/{acme.client_id}/service-keys/{old.kid}", headers=operator.auth)

    assert taken_out.status_code == 200
    assert [http.get(f"/chat/{bot}/tokens", headers=key.auth).status_code for key in (old, new)] == [401, 200]


def test_the_same_key_is_not_let_in_for_two_workers(http, operator, acme):
    engine = worker_engine(http, operator, acme, "classifier")

    again = let_in(http, operator, acme.client_id, engine, worker="notifier")

    assert (again.status_code, again.json()["detail"]) == (409, "KEY_ALREADY_REGISTERED")
    assert [key["worker"] for key in Client.objects(uuid=acme.client_id).first().serviceKeys] == ["classifier"]


def test_the_log_says_which_worker_took_the_tokens(http, operator, acme, bot, caplog):
    caplog.set_level(logging.WARNING)
    notifier = worker_engine(http, operator, acme, "notifier")

    http.get(f"/chat/{bot}/tokens", headers=notifier.auth)

    assert (f"Meta tokens of chatbot {bot} handed to the response engine: client {acme.client_id}, "
            f"key {notifier.kid}, worker notifier") in caplog.text


def test_the_log_says_which_worker_took_the_turns(http, operator, acme, bot, caplog):
    contact = conversation(acme, bot, turns=1)
    caplog.set_level(logging.WARNING)
    coordinator = worker_engine(http, operator, acme, "coordinator")

    turns_of(http, coordinator, bot, contact)

    assert f"key {coordinator.kid}, worker coordinator, 3 messages" in caplog.text


def test_a_key_without_a_worker_leaves_a_warning_every_time_it_is_used(http, acme, engine, bot, caplog):
    caplog.set_level(logging.WARNING)

    http.get(f"/chat/{bot}/rules", headers=engine.auth)
    http.get(f"/chat/{bot}/tools", headers=engine.auth)

    warned = [record.getMessage() for record in caplog.records
              if record.levelno >= logging.WARNING and "which has no worker" in record.getMessage()]
    assert warned == [f"The response engine used key {engine.kid} of client {acme.client_id}, which has no "
                      f"worker: register one per worker (ADR-0022)"] * 2


def test_a_key_with_a_worker_leaves_no_such_warning(http, operator, acme, bot, caplog):
    caplog.set_level(logging.WARNING)
    coordinator = worker_engine(http, operator, acme, "coordinator")

    assert http.get(f"/chat/{bot}/rules", headers=coordinator.auth).status_code == 200
    assert "which has no worker" not in caplog.text


def test_a_route_denied_to_a_worker_leaves_a_trace_with_the_worker(http, operator, acme, bot, caplog):
    caplog.set_level(logging.WARNING)
    classifier = worker_engine(http, operator, acme, "classifier")

    assert http.get(f"/chat/{bot}/tokens", headers=classifier.auth).status_code == 403
    assert (f"The response engine was denied a route with its key for client {acme.client_id}, "
            f"worker classifier") in caplog.text
    assert "handed to the response engine" not in caplog.text
