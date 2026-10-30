# -*- coding: utf-8 -*-
"""
    What the service writes to its log and puts in its error answers. The rule the tests hold it to: nothing that a
    caller sends, and nothing that Meta sends about a person, is written to the log, at any level; and the text of
    a failure goes neither to the log nor to the answer.

    Every value sent here is a marker that would be recognised if it showed up.
"""
import hashlib
import hmac
import json
import logging
from types import SimpleNamespace
from uuid import uuid4

import pytest
import requests
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import app as app_module
import service
import webhook as webhook_module
import controller.clients as clients_controller
import controller.facebook as facebook
import controller.ingestion as ingestion
from shared.auth.client import new_signing_key, public_part
from classes import ChatBot, Contact
from controller import BrokerUnavailable
from repository import TenantRepository
from utils import configure_logging, describe

# What Meta calls is its own service, apart from the management API.
META = TestClient(webhook_module.app)

BOT = {"name": "bot-name-marker", "role": "bot-role-marker", "description": "bot-description-marker",
       "tone": "bot-tone-marker"}
SECRETS = {"facebookToken": "EAAG-page-token-marker", "whatsappToken": "EAAG-whatsapp-token-marker",
           "appSecret": "app-secret-marker"}
PHONE = {"phoneNumber": "12025550101", "phoneId": "100000000000001", "environment": "environment-marker"}
RULES = {"rules": ["rule-text-marker"]}
INTENT = {"name": "intent-name-marker", "description": "intent-description-marker",
          "samplePhrases": ["sample-phrase-marker"],
          "actions": [{"name": "action-name-marker", "description": "action-description-marker",
                       "apiCall": {"url": "https://api.example.com/api-url-marker",
                                   "headers": {"X-Marker": "api-header-marker"}}}]}
WA_ID, PSID = "12025550143", "123456789"
PROFILE = {"id": PSID, "first_name": "first-name-marker", "last_name": "last-name-marker",
           "profile_pic": "https://example.com/profile-pic-marker.jpg"}
WHATSAPP = {"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{"field": "messages", "value": {
    "messaging_product": "whatsapp",
    "metadata": {"display_phone_number": "12025550101", "phone_number_id": "100000000000001"},
    "contacts": [{"profile": {"name": "whatsapp-name-marker"}, "wa_id": WA_ID}],
    "messages": [{"from": WA_ID, "id": "wamid.message-id-marker", "timestamp": "1760000000", "type": "text",
                  "text": {"body": "message-text-marker"}}]}}]}]}
MESSENGER = {"object": "page", "entry": [{"id": "1", "time": 1760000000000, "messaging": [{
    "sender": {"id": PSID}, "recipient": {"id": "987654321"}, "timestamp": 1760000000000,
    "message": {"mid": "m_message-id-marker", "text": "messenger-text-marker"}}]}]}
# Not a marker by its name: what the service makes up, or the kind of thing it is, may be logged.
SENT = [*BOT.values(), *SECRETS.values(), PHONE["phoneNumber"], PHONE["phoneId"], PHONE["environment"],
        "rule-text-marker", "intent-name-marker", "intent-description-marker", "sample-phrase-marker",
        "action-name-marker", "action-description-marker", "api-url-marker", "api-header-marker", WA_ID, PSID,
        "first-name-marker", "last-name-marker", "profile-pic-marker", "whatsapp-name-marker", "message-id-marker",
        "message-text-marker", "messenger-text-marker", "987654321"]


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


@pytest.fixture(autouse=True)
def graph_api(monkeypatch):
    answer = SimpleNamespace(status_code=200, text=json.dumps(PROFILE), json=lambda: PROFILE)
    monkeypatch.setattr(facebook.requests, "get", lambda url, timeout: answer)
    return answer


@pytest.fixture
def caller(register):
    return register("acme")


# What the logging of the service keeps from any output below a warning: the last tests of this file check it.
# With a real database behind the tests, the driver writes every query at the debug level.
SILENCED = ("uvicorn.access", "pymongo", "urllib3")


@pytest.fixture
def logged(caplog):
    """Returns a function that gives back everything logged so far, at any level, by anything but the test client."""
    caplog.set_level(logging.DEBUG)

    def text() -> str:
        return "\n".join(
            record.getMessage() for record in caplog.records
            if not record.name.startswith(("httpx", "httpcore"))
            and not (record.name.startswith(SILENCED) and record.levelno < logging.WARNING))

    return text


def notify(http, bot_id, payload, secret=SECRETS["appSecret"]):
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return META.post(f"/chat/{bot_id}", content=body,
                     headers={"content-type": "application/json", "X-Hub-Signature-256": signature})


def headers_of(caller) -> dict:
    """
        One access token per caller, as a client keeps it for its ten minutes (ADR-0025): the lines these tests count
        are the calls', not the requests for a token.
    """
    if "headers" not in caller.__dict__:
        caller.headers = caller.auth
    return caller.headers


def call(http, caller, method, path, body=None, **params):
    return http.request(method, path, headers=headers_of(caller), json=body, params=params or None)


def set_up(http, caller) -> dict:
    """A chatbot with one of everything, and two people who wrote to it. Every call is expected to work."""
    created = call(http, caller, "POST", "/bot", BOT).json()["data"]
    bot = created["uuid"]
    done = [call(http, caller, "PUT", f"/chat/{bot}/credentials", SECRETS),
            call(http, caller, "POST", f"/chat/{bot}/phones", PHONE),
            call(http, caller, "POST", f"/chat/{bot}/rules", RULES),
            call(http, caller, "POST", f"/chat/{bot}/intents", INTENT),
            notify(http, bot, WHATSAPP), notify(http, bot, MESSENGER)]
    assert [response.status_code for response in done] == [200] * 6
    intent = call(http, caller, "GET", f"/chat/{bot}/intents").json()["data"][0]["uuid"]
    contacts = call(http, caller, "GET", f"/chat/{bot}/contacts").json()["data"]["contacts"]
    return {"bot": bot, "intent": intent, "contact": contacts[0]["uuid"], "verify": created["verifyToken"]}


# --- Nothing that is sent reaches the log --------------------------------------------------------------------------

def test_nothing_a_caller_or_meta_sends_reaches_the_log(http, caller, operator, logged, queue):
    made = set_up(http, caller)
    bot, intent, contact = made["bot"], made["intent"], made["contact"]
    new_key = new_signing_key()
    key = public_part(new_key)
    adding = caller.adding(new_key)
    token = caller.access_token()

    answers = [
        call(http, caller, "GET", f"/chat/{bot}/info"),
        call(http, caller, "GET", f"/chat/{bot}/phones"),
        call(http, caller, "GET", f"/chat/{bot}/phones", environment=PHONE["environment"]),
        call(http, caller, "GET", f"/chat/{bot}/rules"),
        call(http, caller, "GET", f"/chat/{bot}/intents/{intent}"),
        call(http, caller, "PUT", f"/chat/{bot}/intents/{intent}", INTENT),
        call(http, caller, "GET", f"/chat/{bot}/tools"),
        call(http, caller, "GET", f"/chat/{bot}/contacts", offset=0, limit=10),
        call(http, caller, "POST", "/contacts/search", {"waId": WA_ID}),
        call(http, caller, "POST", "/contacts/search", {"fbId": PSID}),
        call(http, caller, "GET", f"/chat/{bot}/contacts/{contact}"),
        META.get(f"/chat/{bot}", params={"hub.mode": "subscribe", "hub.challenge": "challenge-marker",
                                         "hub.verify_token": made["verify"]}),
        http.get("/keys", headers={"Authorization": f"DPoP {token}"}),
        http.post("/keys", **adding),
        call(http, operator, "POST", "/clients", {"name": "client-name-marker", "signingKey": key}),
        call(http, caller, "DELETE", f"/chat/{bot}/intents/{intent}"),
        call(http, caller, "DELETE", f"/chat/{bot}/contacts/{contact}"),
        call(http, caller, "DELETE", f"/bot/{bot}"),
    ]

    assert [response.status_code for response in answers] == [200] * len(answers)
    assert len(queue) == 2
    text = logged()
    for sent in (*SENT, "challenge-marker", made["verify"], token, "client-name-marker", key["x"], key["y"],
                 adding["json"]["proof"]):
        assert sent not in text


def test_what_is_turned_away_does_not_reach_the_log_either(http, caller, logged):
    made = set_up(http, caller)
    bot = made["bot"]

    answers = [
        META.get(f"/chat/{bot}", params={"hub.mode": "subscribe", "hub.challenge": "challenge-marker",
                                         "hub.verify_token": "wrong-verify-token-marker"}),
        notify(http, bot, WHATSAPP, secret="wrong-secret-marker"),
        META.post(f"/chat/{bot}", content=b'{"object": "body-marker"', headers={"content-type": "application/json"}),
        http.get("/keys", headers={"Authorization": "Bearer bearer-marker"}),
        http.get("/keys", headers={"Authorization": "authorization-marker"}),
        call(http, caller, "PUT", f"/chat/{bot}/credentials", {"appSecret": ["rejected-secret-marker"]}),
        call(http, caller, "POST", "/contacts/search", {"waId": "rejected number marker"}),
        call(http, caller, "POST", "/bot", {"name": ["rejected-name-marker"]}),
        call(http, caller, "POST", f"/chat/{bot}/phones", {"phoneNumber": {"rejected": "rejected-phone-marker"}}),
        call(http, caller, "GET", "/chat/path-marker/info"),
        call(http, caller, "GET", f"/chat/{bot}/intents/intent-path-marker"),
        call(http, caller, "GET", f"/chat/{bot}/contacts", limit="limit-marker"),
        call(http, caller, "GET", "/nowhere/nowhere-marker", query="query-marker"),
        call(http, caller, "DELETE", f"/clients/{uuid4()}"),
    ]

    assert [response.status_code for response in answers] == [403, 403, 403, 401, 401, 422, 422, 422, 422, 404, 404,
                                                              422, 404, 403]
    text = logged()
    for sent in (*SENT, "challenge-marker", "wrong-verify-token-marker", "wrong-secret-marker", "body-marker",
                 "bearer-marker", "authorization-marker", "rejected-secret-marker", "rejected number marker",
                 "rejected-name-marker", "rejected-phone-marker", "path-marker", "limit-marker", "nowhere-marker",
                 "query-marker"):
        assert sent not in text


@pytest.mark.parametrize("failure", ["refused", "not a profile", "unreachable"])
def test_what_the_graph_api_answers_does_not_reach_the_log(http, caller, logged, graph_api, monkeypatch, failure):
    """Its answer is about a person, and its address carries the token of the page."""
    bot = call(http, caller, "POST", "/bot", BOT).json()["data"]["uuid"]
    call(http, caller, "PUT", f"/chat/{bot}/credentials", SECRETS)
    if failure == "refused":
        graph_api.status_code, graph_api.text = 400, '{"error": "graph-error-marker", "id": "' + PSID + '"}'
    elif failure == "not a profile":
        graph_api.json = lambda: {"first_name": ["graph-shape-marker"], "id": {"nested": PSID}}
    else:
        def unreachable(url, timeout):
            raise requests.ConnectionError(f"Max retries exceeded with url: {url}")

        monkeypatch.setattr(facebook.requests, "get", unreachable)

    notify(http, bot, MESSENGER)

    text = logged()
    assert "Error" in text or "Exception" in text or "answered 400" in text
    for sent in (*SENT, "graph-error-marker", "graph-shape-marker", "access_token"):
        assert sent not in text
    # The contact is stored without the profile: the message does not wait for it.
    assert [contact.firstName for contact in Contact.objects] == [None]


def test_a_failure_of_the_broker_is_logged_by_its_kind(http, caller, logged, monkeypatch, another):
    made = set_up(http, caller)

    async def down(queue, message):
        raise BrokerUnavailable("amqp://user:broker-password-marker@broker-host-marker")

    monkeypatch.setattr(ingestion, "send_message_to_queue", down)

    assert notify(http, made["bot"], another(WHATSAPP)).status_code == 503
    for sent in (*SENT, "broker-password-marker", "broker-host-marker"):
        assert sent not in logged()


# --- The line a request leaves ---------------------------------------------------------------------------------------

def request_lines(text: str) -> list:
    """The lines the service writes of each request: they start with the method."""
    return [line for line in text.splitlines() if line.split(" ")[0] in (*service.METHODS, "OTHER")]


def test_a_request_leaves_one_line_with_its_route_and_its_identifiers(http, caller, logged):
    made = set_up(http, caller)
    before = len(request_lines(logged()))

    call(http, caller, "GET", f"/chat/{made['bot']}/contacts/{made['contact']}")
    call(http, caller, "GET", f"/chat/{made['bot']}/contacts", offset=0, limit=7)
    http.get("/health")

    lines = request_lines(logged())[before:]
    assert [line.split(" time:")[0] for line in lines] == [
        f"GET /chat/{{bot_id}}/contacts/{{contact_id}} {made['bot']} {made['contact']} code:200",
        f"GET /chat/{{bot_id}}/contacts {made['bot']} code:200",
        "GET /health code:200",
    ]


def test_an_identifier_is_written_the_one_way_whatever_way_it_was_sent(http, caller, logged):
    made = set_up(http, caller)
    shouted = made["bot"].upper()

    assert call(http, caller, "GET", f"/chat/{shouted}/info").status_code == 200
    assert shouted not in logged()
    assert request_lines(logged())[-1].startswith(f"GET /chat/{{bot_id}}/info {made['bot']} code:200")


@pytest.mark.parametrize("planted", ["%0AFORGED LINE code:200", "%0D%0AFORGED", "%1B[31mFORGED", "FORGED%09TAB",
                                     "not-a-uuid-FORGED", "..%2F..%2FFORGED"])
def test_an_address_cannot_plant_anything_in_the_log(http, caller, logged, planted):
    made = set_up(http, caller)
    before = len(request_lines(logged()))

    call(http, caller, "GET", f"/chat/{made['bot']}{planted}/info")
    call(http, caller, "GET", f"/chat/{made['bot']}/intents/{planted}")
    call(http, caller, "GET", f"/chat/{planted}")

    text = logged()
    assert "FORGED" not in text
    assert len(request_lines(text)) == before + 3
    for character in ("\r", "\x1b", "\t"):
        assert character not in text


def test_a_route_that_does_not_exist_is_logged_without_its_address(http, logged):
    http.get("/nowhere/nowhere-marker")

    assert request_lines(logged())[-1].startswith("GET no route code:404")


def test_a_method_that_is_not_one_of_the_usual_is_not_written_as_sent(http, logged):
    http.request("METHOD-MARKER", "/health")

    assert "METHOD-MARKER" not in logged()
    assert request_lines(logged())[-1].startswith("OTHER ")


# --- The text of a failure -------------------------------------------------------------------------------------------

LEAK = "failure-text-marker 12025550143 EAAG-token-in-the-failure"
TEMPLATE = {"name": "order_update", "language": "es", "category": "UTILITY",
            "components": [{"type": "BODY", "text": f"{LEAK} {{{{1}}}}", "example": {"body_text": [["A-1"]]}}]}
TEMPLATE_ID = "1234567890"
PUBLIC = {("GET", "/chat/{bot_id}"), ("POST", "/chat/{bot_id}"), ("GET", "/health"), ("POST", "/oauth/token")}
# It answers from what the service holds in memory: there is nothing in it to fail. And the tokens and the last
# turns are only for the response engine: the operator, who makes these calls, is turned away before anything can
# fail.
NOTHING_TO_FAIL = {("GET", "/gateway/keys"), ("GET", "/chat/{bot_id}/tokens"),
                   ("GET", "/chat/{bot_id}/contacts/{contact_id}/turns")}
ROUTES = sorted({(method, route.path) for route in app_module.app.routes if isinstance(route, APIRoute)
                 for method in route.methods} - PUBLIC - NOTHING_TO_FAIL)
BODIES = {
    ("POST", "/bot"): BOT,
    ("POST", "/chat/{bot_id}/phones"): PHONE,
    ("POST", "/chat/{bot_id}/rules"): RULES,
    ("POST", "/chat/{bot_id}/intents"): INTENT,
    ("PUT", "/chat/{bot_id}/intents/{intent_id}"): INTENT,
    ("PUT", "/chat/{bot_id}/credentials"): SECRETS,
    ("POST", "/contacts/search"): {"waId": WA_ID},
    ("POST", "/clients"): {"name": "globex", "signingKey": public_part(new_signing_key())},
    ("POST", "/clients/{client_id}/keys"): {"signingKey": public_part(new_signing_key())},
    ("POST", "/clients/{client_id}/service-keys"): {"signingKey": public_part(new_signing_key()), "worker": "notifier"},
    ("PUT", "/chat/{bot_id}/contacts/{contact_id}/consent"): {"categories": ["UTILITY"], "source": LEAK},
    ("POST", "/chat/{bot_id}/contacts/{contact_id}/messages"): {"type": "text", "text": LEAK},
    ("POST", "/chat/{bot_id}/templates"): TEMPLATE,
    ("PUT", "/chat/{bot_id}/templates/{template_id}"): {"components": TEMPLATE["components"]},
    ("POST", "/people"): {"email": "failure-text-marker@acme.test", "role": "cliente-analista"},
    ("PUT", "/people/{person_id}"): {"status": "blocked"},
}


ADDRESSED = sorted((method, route.path, name) for route in app_module.app.routes if isinstance(route, APIRoute)
                   for method in route.methods for name in route.param_convertors)


@pytest.mark.parametrize("method,path,planted", ADDRESSED)
def test_no_route_writes_what_its_address_carries(http, operator, logged, method, path, planted):
    """One part of the address at a time is not an identifier, in every route that has one."""
    made = set_up(http, operator)
    parts = {"bot_id": made["bot"], "intent_id": made["intent"], "contact_id": made["contact"],
             "client_id": str(operator.client_id), "kid": operator.kid, "template_id": TEMPLATE_ID,
             "person_id": str(uuid4()), "request_id": str(uuid4())}
    parts[planted] = "address-marker%0AFORGED"

    response = call(http, operator, method, path.format(**parts), BODIES.get((method, path)))

    assert response.status_code in (403, 404)
    for sent in ("address-marker", "FORGED", *SENT):
        assert sent not in logged()


@pytest.fixture
def everything_fails(monkeypatch):
    """Returns a function after which whatever reaches the data of a client, or the registry, fails."""
    def fails(*args, **kwargs):
        raise RuntimeError(LEAK)

    def now():
        for name in ("_own", "add_bot", "delete_bot", "delete_contact", "find_contacts"):
            monkeypatch.setattr(TenantRepository, name, fails)
        for name in ("register_client", "list_clients", "get_client", "add_signing_key", "remove_signing_key",
                     "delete_client", "add_service_key", "remove_service_key"):
            monkeypatch.setattr(clients_controller, name, fails)

    return now


@pytest.mark.parametrize("method,path", ROUTES)
def test_the_text_of_a_failure_is_neither_answered_nor_logged(http, operator, logged, everything_fails, method, path):
    made = set_up(http, operator)
    address = path.format(bot_id=made["bot"], intent_id=made["intent"], contact_id=made["contact"],
                          client_id=operator.client_id, kid=operator.kid, template_id=TEMPLATE_ID,
                          person_id=uuid4(), request_id=uuid4())
    # Adding a key of its own needs a proof for the token of that same request: the body cannot be made ahead.
    request = (operator.adding(new_signing_key()) if (method, path) == ("POST", "/keys") else
               {"headers": operator.auth, "json": BODIES.get((method, path))})
    everything_fails()

    response = http.request(method, address, **request)

    assert response.status_code == 500
    assert response.json()["code"] == 500
    for part in (LEAK, "failure-text-marker", "EAAG-token-in-the-failure", "RuntimeError", "Traceback", ".py"):
        assert part not in response.text
    assert "RuntimeError at " in logged()
    for part in (LEAK, "failure-text-marker", "EAAG-token-in-the-failure", *SENT):
        assert part not in logged()


def test_a_failure_while_a_message_is_processed_is_logged_by_its_kind(http, caller, logged, monkeypatch, another):
    made = set_up(http, caller)

    def fails(*args, **kwargs):
        raise RuntimeError(LEAK)

    monkeypatch.setattr(TenantRepository, "find_contact_by_wa_id", fails)

    notify(http, made["bot"], another(WHATSAPP))

    assert "RuntimeError at " in logged()
    for part in (LEAK, "failure-text-marker", *SENT):
        assert part not in logged()


def test_a_failure_is_described_by_its_kind_and_where_it_came_from():
    def fails():
        raise ValueError("12025550143 is not a valid number")

    with pytest.raises(ValueError) as failure:
        fails()

    described = describe(failure.value)
    assert described.startswith("ValueError at test_logs_and_errors.py:")
    assert "12025550143" not in described and "valid number" not in described


def test_a_failure_inside_a_library_says_which_line_of_the_service_was_running(caller):
    repo = TenantRepository(caller.client_id)
    bot = repo.add_bot(ChatBot(name="x", role="x", description="x", tone="x"))

    with pytest.raises(Exception) as failure:
        # The database library refuses it, and says so repeating the value.
        repo.add_contact(bot.uuid, Contact(firstName="Ana", profilePic="12025550143 is not an address"))

    described = describe(failure.value)
    assert "12025550143" in str(failure.value)
    assert described.startswith("ValidationError at tenant.py:") and described.endswith(")")
    assert "(document.py:" in described and "12025550143" not in described


def test_a_failure_outside_the_service_says_the_line_that_raised_it():
    with pytest.raises(ValueError) as failure:
        json.loads("{12025550143")

    described = describe(failure.value)
    assert described.startswith("JSONDecodeError at decoder.py:") and "12025550143" not in described


def test_a_failure_that_was_never_raised_is_described_by_its_kind():
    assert describe(KeyError("12025550143")) == "KeyError"


# --- What other libraries would write --------------------------------------------------------------------------------

@pytest.fixture
def production_logging(capsys):
    """Applies the logging the service starts with, and puts back what there was when the test ends."""
    names = ("development", "staging", "production", *SILENCED)
    loggers = [logging.getLogger()] + [logging.getLogger(name) for name in names]
    before = [(logger, logger.level, list(logger.handlers), logger.propagate, logger.disabled) for logger in loggers]
    configure_logging()
    yield
    for logger, level, handlers, propagate, disabled in before:
        logger.setLevel(level)
        logger.handlers[:] = handlers
        logger.propagate, logger.disabled = propagate, disabled


@pytest.mark.parametrize("name,line", [
    ("uvicorn.access", '127.0.0.1:5000 - "GET /chat/x?hub.verify_token=verify-token-marker HTTP/1.1" 200'),
    ("pymongo.command", '{"command": {"find": "contact", "filter": {"fbId": "123456789"}}}'),
    ("urllib3.connectionpool", 'https://graph.facebook.com:443 "GET /v19.0/123456789?access_token=EAAG-marker"'),
])
def test_what_other_libraries_write_of_a_request_reaches_no_output(capsys, production_logging, name, line):
    for level in (logging.DEBUG, logging.INFO):
        logging.getLogger(name).log(level, line)
    logging.getLogger(name).warning("a-warning-still-comes-out")

    output = capsys.readouterr()
    assert "a-warning-still-comes-out" in output.out + output.err
    for sent in ("verify-token-marker", "123456789", "EAAG-marker"):
        assert sent not in output.out + output.err


# --- What depends on the environment ---------------------------------------------------------------------------------

@pytest.mark.parametrize("environment", ["staging", "production"])
def test_the_documentation_is_only_served_in_development(environment):
    """RD-16 of the dual review of Gate 2: it showed every route and model in every environment."""
    from fastapi import FastAPI

    elsewhere = TestClient(FastAPI(**service.documentation(environment)))
    here = TestClient(app_module.app)

    for path in ("/docs", "/redoc", "/openapi.json"):
        assert elsewhere.get(path).status_code == 404
        assert here.get(path).status_code == 200


@pytest.mark.parametrize("environment", ["Production", "prod", "", "production "])
def test_an_environment_that_does_not_exist_stops_the_start(environment):
    from pydantic import ValidationError

    from classes import Settings

    with pytest.raises(ValidationError):
        Settings(environment=environment)
