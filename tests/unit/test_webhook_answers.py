# -*- coding: utf-8 -*-
"""
    What Meta is answered, and why it matters: anything but a 200 makes it deliver the notification again, and a
    webhook that keeps failing is unsubscribed (ADR-0015).

    - Without a good signature: 403, and nothing of the body is read.
    - Larger than anything Meta sends: 413, whoever sends it and to whatever route.
    - Signed, but not something the service understands or can process: 200 and a line in the log.
    - Something the service needs is not there (the database, the broker, the key service): 503.
"""
import hashlib
import hmac
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import requests
import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from mongoengine.errors import NotUniqueError, OperationError
from pymongo.errors import AutoReconnect, DuplicateKeyError, OperationFailure, ServerSelectionTimeoutError

import app as app_module
import service
import webhook as webhook_module
import controller.facebook as facebook
import controller.ingestion as ingestion
from classes import ChatBot, Contact
from enums import Status
from controller.incoming import MESSENGER, WHATSAPP, NotUnderstood, incoming_messages, read_notification
from repository import NotFound, TenantRepository, data_keys, database_failed
from utils import BodySizeLimit

# What Meta calls is its own service, apart from the management API.
META = TestClient(webhook_module.app)

BOT = {"name": "Mine", "role": "Sales Assistant", "description": "A test chatbot", "tone": "helpful"}
APP_SECRET = "app-secret-of-this-test"
SECRETS = {"facebookToken": "EAAG-page-token-0123456789", "appSecret": APP_SECRET}
METADATA = {"display_phone_number": "12025550101", "phone_number_id": "100000000000001"}
ANA, LUIS, EVA = "12025550143", "12025550177", "12025550190"
PSID = "123456789"
PROFILE = {"id": PSID, "first_name": "Carla", "last_name": "Diaz", "profile_pic": "https://example.com/c.jpg"}
LIMIT = service.MAX_BODY_BYTES
ASYNCAPI = Path(__file__).resolve().parents[2] / "docs" / "02-design" / "contracts" / "asyncapi.yaml"


def text(number: str, message_id: str | None = None) -> dict:
    return {"from": number, "id": message_id or f"wamid.{number}", "timestamp": "1760000000", "type": "text",
            "text": {"body": "body-marker"}}


def whatsapp(*numbers: str, metadata=METADATA, messages_=None) -> dict:
    """One notification with a message from each number, each in its own entry."""
    values = [{"messaging_product": "whatsapp", "metadata": metadata,
               "contacts": [{"profile": {"name": "name-marker"}, "wa_id": number}], "messages": [text(number)]}
              for number in numbers]
    if messages_ is not None:
        values = messages_
    return {"object": WHATSAPP, "entry": [{"id": "1", "changes": [{"field": "messages", "value": value}]}
                                          for value in values]}


def messenger(psid: str = PSID, **message) -> dict:
    return {"object": MESSENGER, "entry": [{"id": "1", "time": 1760000000000, "messaging": [{
        "sender": {"id": psid}, "recipient": {"id": "987654321"}, "timestamp": 1760000000000,
        "message": {"mid": f"m_{psid}", "text": "body-marker", **message}}]}]}


@pytest.fixture
def http():
    return TestClient(webhook_module.app)


@pytest.fixture(autouse=True)
def queue(monkeypatch):
    sent = []

    async def capture(queue, message):
        sent.append(json.loads(message.to_json()))

    monkeypatch.setattr(ingestion, "send_message_to_queue", capture)
    return sent


@pytest.fixture(autouse=True)
def graph_api(monkeypatch):
    """The Graph API, answering with a profile. Returns what it answers and how many times it was asked."""
    api = SimpleNamespace(status_code=200, text="{}", json=lambda: PROFILE, asked=0)

    def get(url, timeout):
        api.asked += 1
        return api

    monkeypatch.setattr(facebook.requests, "get", get)
    return api


@pytest.fixture
def caller(register):
    return register("acme")


@pytest.fixture
def bot(caller):
    # Created through the management API, which is the other service.
    management = TestClient(app_module.app)
    created = management.post("/bot", headers=caller.auth, json=BOT).json()["data"]
    assert management.put(f"/chat/{created['uuid']}/credentials", headers=caller.auth,
                          json=SECRETS).status_code == 200
    # Live, as a chatbot Meta already sends to: its first notification does not activate it (HIG-75).
    ChatBot.objects(uuid=created["uuid"]).update(set__status=Status.ACT.value)
    return created["uuid"]


def signed(raw: bytes, secret: str = APP_SECRET) -> dict:
    return {"content-type": "application/json",
            "X-Hub-Signature-256": "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()}


def notify(http, bot_id, payload, secret: str = APP_SECRET):
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return META.post(f"/chat/{bot_id}", content=raw, headers=signed(raw, secret))


def queued(queue) -> list:
    return [envelope["messageId"] for envelope in queue]


def warnings(caplog) -> list:
    return [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]


# --- Without a good signature, nothing of the body is read -----------------------------------------------------------

BODIES = {
    "a notification": json.dumps(whatsapp(ANA)).encode(),
    "not json": b"{not json body-marker",
    "nothing": b"",
    "a list": b"[1, 2, 3]",
    "another shape": b'{"object": 7, "entry": "body-marker"}',
    "nested a thousand deep": b"[" * 1000 + b"]" * 1000,
}


@pytest.mark.parametrize("body", sorted(BODIES))
@pytest.mark.parametrize("signature", ["none", "of another secret", "not a signature"])
def test_without_a_good_signature_the_answer_is_403_and_the_body_is_not_read(http, bot, queue, monkeypatch, body,
                                                                             signature):
    read = []
    monkeypatch.setattr(webhook_module, "read_notification", lambda raw: read.append(raw))
    raw = BODIES[body]
    headers = {"content-type": "application/json"}
    if signature == "of another secret":
        headers = signed(raw, "another-secret")
    elif signature == "not a signature":
        headers["X-Hub-Signature-256"] = "sha256=body-marker"

    response = META.post(f"/chat/{bot}", content=raw, headers=headers)

    assert response.status_code == 403
    assert "body-marker" not in response.text
    assert read == [] and queue == [] and Contact.objects.count() == 0


@pytest.mark.parametrize("header", [None, "", "sha256=body-marker", "sha1=" + "0" * 40, "sha256=" + "A" * 64,
                                    "sha256=" + "0" * 63], ids=["none", "empty", "not hex", "sha1", "upper case",
                                                                 "one digit short"])
def test_without_anything_like_a_signature_the_key_service_is_not_asked(http, bot, queue, key_service, monkeypatch,
                                                                        header):
    """Found in the dual review of Gate 2: an unsigned request opened the secrets of the chatbot first."""
    opened = []
    monkeypatch.setattr(webhook_module, "webhook_secrets", lambda bot_: opened.append(bot_))
    key_service.down = True
    raw = json.dumps(whatsapp(ANA)).encode()
    headers = {"content-type": "application/json"}
    if header is not None:
        headers["X-Hub-Signature-256"] = header

    response = META.post(f"/chat/{bot}", content=raw, headers=headers)

    assert response.status_code == 403
    assert opened == [] and queue == []


# --- Signed, and not something the service understands: 200 and a line in the log ----------------------------------

NOT_UNDERSTOOD = {
    "not json": b"{not json body-marker",
    "nothing": b"",
    "a list": b'["body-marker"]',
    "a text": b'"body-marker"',
    "nested a thousand deep": b"[" * 1000 + b"]" * 1000,
    "no object": json.dumps({"entry": [], "marker": "body-marker"}).encode(),
    "no entry": json.dumps({"object": WHATSAPP}).encode(),
    "an object that is not text": json.dumps({"object": 7, "entry": []}).encode(),
    "an empty object": json.dumps({"object": "", "entry": []}).encode(),
    "an object too long": json.dumps({"object": "x" * 65, "entry": []}).encode(),
    "an entry that is not a list": json.dumps({"object": WHATSAPP, "entry": "body-marker"}).encode(),
    "entries that are not objects": json.dumps({"object": WHATSAPP, "entry": ["body-marker", 7]}).encode(),
    "more entries than meta sends": json.dumps({"object": WHATSAPP, "entry": [{}] * 1001}).encode(),
}


@pytest.mark.parametrize("body", sorted(NOT_UNDERSTOOD))
def test_what_meta_signed_and_is_not_understood_is_accepted_and_said_in_the_log(http, bot, queue, caplog, body):
    response = notify(http, bot, NOT_UNDERSTOOD[body])

    assert response.status_code == 200
    assert queue == [] and Contact.objects.count() == 0
    (warning,) = warnings(caplog)
    assert warning.startswith(f"A signed notification for chatbot {bot} was not understood: ")
    assert "body-marker" not in caplog.text


@pytest.mark.parametrize("raw,reason", [(b"{not json", "it is not JSON"), (b"", "it is not JSON"),
                                        (b"[]", "it does not have the shape of one"),
                                        (b'{"object": "page"}', "it does not have the shape of one")])
def test_why_a_notification_is_not_understood_is_said_in_words_of_the_service(raw, reason):
    with pytest.raises(NotUnderstood) as failure:
        read_notification(raw)

    assert str(failure.value) == reason


def test_nothing_is_filled_in_for_what_a_notification_lacks():
    """It used to be: a notification without entries was given a made-up one."""
    notification = read_notification(json.dumps({"object": WHATSAPP, "entry": []}).encode())

    assert (notification.object, notification.entry) == (WHATSAPP, [])
    for raw in (b"{}", b'{"object": "page"}', b'{"entry": []}'):
        with pytest.raises(NotUnderstood):
            read_notification(raw)


def test_what_meta_adds_to_a_notification_is_ignored(http, bot, queue, caplog):
    payload = {**whatsapp(ANA), "a_field_meta_added": {"anything": [1, 2]}, "another": "body-marker"}

    assert notify(http, bot, payload).status_code == 200
    assert queued(queue) == [f"wamid.{ANA}"] and warnings(caplog) == []


def test_as_many_entries_as_meta_sends_are_read(http, bot, queue, caplog):
    payload = whatsapp(ANA)
    payload["entry"] = [{}] * 999 + payload["entry"]

    assert notify(http, bot, payload).status_code == 200
    assert queued(queue) == [f"wamid.{ANA}"] and warnings(caplog) == []


def test_a_kind_of_object_the_service_does_not_handle_is_accepted_and_said_in_the_log(http, bot, queue, caplog):
    response = notify(http, bot, {"object": "kind-marker", "entry": [{"id": "1", "messaging": []}]})

    assert response.status_code == 200 and queue == []
    assert warnings(caplog) == [
        f"A notification for chatbot {bot} is about a kind of object the service does not handle"]
    assert "kind-marker" not in caplog.text


@pytest.mark.parametrize("payload", [
    whatsapp(messages_=[{"messaging_product": "whatsapp", "metadata": METADATA,
                         "statuses": [{"id": "wamid.SENT", "status": "delivered", "recipient_id": ANA}]}]),
    messenger(is_echo=True),
    {"object": MESSENGER, "entry": [{"id": "1", "messaging": [{"sender": {"id": PSID}, "delivery": {"mids": ["m"]}}]}]},
    {"object": WHATSAPP, "entry": []},
], ids=["a delivery receipt", "an echo", "a messenger receipt", "no entries"])
def test_what_is_not_a_message_from_a_person_is_accepted_without_a_word(http, bot, queue, caplog, payload):
    assert notify(http, bot, payload).status_code == 200
    assert queue == [] and warnings(caplog) == []


def test_the_messages_that_can_be_read_are_queued_and_the_others_are_counted(http, bot, queue, caplog):
    no_number = {"messaging_product": "whatsapp", "metadata": {"display_phone_number": "12025550101"},
                 "contacts": [], "messages": [text(LUIS), text(EVA)]}
    good_and_bad = {"messaging_product": "whatsapp", "metadata": METADATA,
                    "contacts": [{"profile": {"name": "name-marker"}, "wa_id": ANA}],
                    "messages": [text(ANA), {"from": ANA, "type": "text"}, {"id": "wamid.NOBODY", "type": "text"}]}

    response = notify(http, bot, whatsapp(messages_=[no_number, good_and_bad]))

    assert response.status_code == 200
    assert queued(queue) == [f"wamid.{ANA}"]
    assert warnings(caplog) == [f"Messages of a notification for chatbot {bot} were left out: "
                                "{'sent to no number': 2, 'without identifier or sender': 2}"]
    assert "name-marker" not in caplog.text and LUIS not in caplog.text


def test_a_messenger_message_without_its_identifier_is_counted(http, bot, queue, caplog):
    payload = messenger()
    payload["entry"][0]["messaging"].append({"sender": {"id": PSID}, "message": {"text": "body-marker"}})
    payload["entry"][0]["messaging"].append({"sender": {}, "message": {"mid": "m_NOBODY"}})

    assert notify(http, bot, payload).status_code == 200
    assert queued(queue) == [f"m_{PSID}"]
    assert warnings(caplog) == [f"Messages of a notification for chatbot {bot} were left out: "
                                "{'without identifier or sender': 2}"]


def test_reading_the_messages_says_nothing_of_what_was_left_out_unless_asked():
    payload = whatsapp(messages_=[{"metadata": {}, "messages": [text(ANA)]}])
    left_out = []

    assert incoming_messages(WHATSAPP, payload["entry"]) == []
    assert incoming_messages(WHATSAPP, payload["entry"], left_out) == [] and left_out == ["sent to no number"]


# --- Larger than anything Meta sends: 413 --------------------------------------------------------------------------

def too_large() -> bytes:
    return b'{"object": "page", "entry": [], "pad": "' + b"x" * LIMIT + b'"}'


@pytest.mark.parametrize("route", ["the webhook, signed", "the webhook, unsigned", "a management route, with a token",
                                   "a management route, without one", "a route that does not exist"])
def test_a_body_over_the_limit_is_turned_away_whoever_sends_it(http, caller, bot, queue, monkeypatch, route):
    checked, read = [], []
    monkeypatch.setattr(webhook_module, "is_valid_signature", lambda *args: checked.append(args) or True)
    monkeypatch.setattr(webhook_module, "read_notification", lambda raw: read.append(raw))
    raw = too_large()
    path, headers = {
        "the webhook, signed": (f"/chat/{bot}", signed(raw)),
        "the webhook, unsigned": (f"/chat/{bot}", {}),
        "a management route, with a token": ("/bot", caller.auth),
        "a management route, without one": ("/bot", {}),
        "a route that does not exist": ("/nowhere", {}),
    }[route]

    # Each service has the limit: the management routes are sent to the management API.
    service_for_it = TestClient(app_module.app) if path == "/bot" else http
    response = service_for_it.post(path, content=raw, headers={"content-type": "application/json", **headers})

    assert (response.status_code, response.json()) == (413, {"code": 413, "message": "PAYLOAD TOO LARGE"})
    assert checked == [] and read == [] and queue == []


def test_a_body_that_does_not_say_how_long_it_is_is_counted_as_it_arrives(http, bot, queue):
    def pieces():
        for _ in range(LIMIT // 65536 + 2):
            yield b"x" * 65536

    response = META.post(f"/chat/{bot}", content=pieces(), headers={"content-type": "application/json"})

    assert response.status_code == 413 and queue == []


def test_the_limit_is_the_largest_notification_meta_sends():
    """3 MB, says its documentation. Anything above that is not from Meta."""
    assert LIMIT == 3 * 1024 * 1024


@pytest.mark.parametrize("declared", [True, False], ids=["saying how long it is", "without saying it"])
def test_one_byte_over_the_limit_is_too_much(http, bot, queue, declared):
    raw = b"x" * (LIMIT + 1)

    response = META.post(f"/chat/{bot}", content=raw if declared else iter([raw[:LIMIT], raw[LIMIT:]]),
                         headers={"content-type": "application/json"})

    assert response.status_code == 413 and queue == []


def test_a_notification_exactly_as_large_as_the_limit_is_read(http, bot, queue):
    body = json.dumps(whatsapp(ANA))[:-1].encode() + b', "pad": "'
    raw = body + b"x" * (LIMIT - len(body) - 2) + b'"}'
    assert len(raw) == LIMIT

    assert notify(http, bot, raw).status_code == 200
    assert queued(queue) == [f"wamid.{ANA}"]


def test_what_is_turned_away_for_its_size_leaves_its_line_in_the_log(http, bot, caplog):
    caplog.set_level(logging.INFO)

    META.post(f"/chat/{bot}", content=too_large(), headers={"content-type": "application/json"})

    assert any(record.getMessage().startswith("POST no route code:413") for record in caplog.records)


def run(coroutine):
    import asyncio

    return asyncio.run(coroutine)


def test_the_limit_leaves_alone_what_is_not_a_request():
    seen = []

    async def application(scope, receive, send):
        seen.append(scope["type"])

    run(BodySizeLimit(application, limit=10)({"type": "lifespan"}, None, None))

    assert seen == ["lifespan"]


def test_a_caller_that_goes_away_halfway_does_not_reach_the_application():
    reached, messages_ = [], [{"type": "http.request", "body": b"12345", "more_body": True}, {"type": "http.disconnect"}]

    async def application(scope, receive, send):
        reached.append(scope)

    async def receive():
        return messages_.pop(0)

    run(BodySizeLimit(application, limit=10)({"type": "http", "headers": []}, receive, None))

    assert reached == []


def test_a_body_that_arrives_in_pieces_reaches_the_application_whole():
    got, pieces = [], [{"type": "http.request", "body": b"12345", "more_body": True},
                       {"type": "http.request", "body": b"678", "more_body": False}, {"type": "http.disconnect"}]

    async def application(scope, receive, send):
        got.extend([await receive(), await receive()])

    async def receive():
        return pieces.pop(0)

    run(BodySizeLimit(application, limit=10)({"type": "http", "headers": []}, receive, None))

    assert got == [{"type": "http.request", "body": b"12345678", "more_body": False}, {"type": "http.disconnect"}]


# --- The database is not there: 503, and Meta sends it again ---------------------------------------------------------

DOWN = {
    "it does not answer": lambda: ServerSelectionTimeoutError("127.0.0.1:27017: connection refused failure-marker"),
    "the connection dropped": lambda: AutoReconnect("connection closed failure-marker"),
    "it refuses the credentials": lambda: OperationFailure("Authentication failed. failure-marker", 18),
    "a write does not go through": lambda: OperationError("Could not save document (failure-marker)"),
}
WHERE = {
    "finding the chatbot": (webhook_module, "find_webhook_bot", whatsapp(ANA)),
    "reading its secrets": (TenantRepository, "bot_secrets", whatsapp(ANA)),
    "finding a whatsapp contact": (TenantRepository, "find_contact_by_wa_id", whatsapp(ANA)),
    "storing a whatsapp contact": (TenantRepository, "add_contact", whatsapp(ANA)),
    "finding a messenger contact": (TenantRepository, "find_contact_by_fb_id", messenger()),
    "storing a messenger contact": (TenantRepository, "add_contact", messenger()),
}


@pytest.mark.parametrize("failure", sorted(DOWN))
@pytest.mark.parametrize("where", sorted(WHERE))
def test_while_the_database_is_not_there_meta_is_asked_to_send_it_again(http, bot, queue, caplog, monkeypatch,
                                                                         where, failure):
    target, name, payload = WHERE[where]

    def fails(*args, **kwargs):
        raise DOWN[failure]()

    monkeypatch.setattr(target, name, fails)

    response = notify(http, bot, payload)

    assert response.status_code == 503
    assert queue == [] and Contact.objects.count() == 0
    assert "The database is unavailable" in caplog.text
    assert "failure-marker" not in caplog.text and "failure-marker" not in response.text


def test_once_the_database_is_back_the_same_notification_is_queued(http, bot, queue, monkeypatch):
    find = TenantRepository.find_contact_by_wa_id

    def fails(*args, **kwargs):
        raise ServerSelectionTimeoutError("no servers")

    monkeypatch.setattr(TenantRepository, "find_contact_by_wa_id", fails)
    assert notify(http, bot, whatsapp(ANA)).status_code == 503

    monkeypatch.setattr(TenantRepository, "find_contact_by_wa_id", find)

    assert notify(http, bot, whatsapp(ANA)).status_code == 200
    assert queued(queue) == [f"wamid.{ANA}"]


def test_a_failure_of_the_database_halfway_asks_for_the_whole_notification_again(http, bot, queue, monkeypatch):
    """
        What was already queued is in the history and is not queued again: only the rest of the notification is.
    """
    find = TenantRepository.find_contact_by_wa_id
    down = [True]

    def fails_for_luis(self, bot_id, wa_id):
        if wa_id == LUIS and down[0]:
            raise ServerSelectionTimeoutError("no servers")
        return find(self, bot_id, wa_id)

    monkeypatch.setattr(TenantRepository, "find_contact_by_wa_id", fails_for_luis)

    assert notify(http, bot, whatsapp(ANA, LUIS, EVA)).status_code == 503
    assert queued(queue) == [f"wamid.{ANA}"]

    down[0] = False

    assert notify(http, bot, whatsapp(ANA, LUIS, EVA)).status_code == 200
    assert queued(queue) == [f"wamid.{ANA}", f"wamid.{LUIS}", f"wamid.{EVA}"]


def test_the_webhook_is_not_verified_while_the_database_is_not_there(http, bot, monkeypatch):
    def fails(*args, **kwargs):
        raise ServerSelectionTimeoutError("no servers")

    monkeypatch.setattr(webhook_module, "find_webhook_bot", fails)

    response = META.get(f"/chat/{bot}", params={"hub.mode": "subscribe", "hub.challenge": "1",
                                                "hub.verify_token": "x"})

    assert response.status_code == 503


@pytest.mark.parametrize("error,failed", [
    (ServerSelectionTimeoutError("x"), True), (AutoReconnect("x"), True), (OperationFailure("x", 18), True),
    (OperationError("x"), True), (DuplicateKeyError("x"), False), (NotUniqueError("x"), False),
    (ValueError("x"), False), (NotFound("x"), False), (KeyError("x"), False),
])
def test_what_counts_as_a_failure_of_the_database(error, failed):
    assert database_failed(error) is failed


# --- A message that fails by itself: 200, and the others go on -----------------------------------------------------

@pytest.mark.parametrize("failure", [ValueError("failure-marker"), NotUniqueError("failure-marker"),
                                     DuplicateKeyError("failure-marker"), NotFound("failure-marker")],
                         ids=lambda failure: type(failure).__name__)
def test_a_message_that_fails_by_itself_does_not_take_the_others_with_it(http, bot, queue, caplog, monkeypatch,
                                                                          failure):
    """Sent again it would fail again, and a webhook that keeps failing is unsubscribed."""
    add = TenantRepository.add_contact

    def fails_for_luis(self, bot_id, contact):
        if contact.waId == LUIS:
            raise failure
        return add(self, bot_id, contact)

    monkeypatch.setattr(TenantRepository, "add_contact", fails_for_luis)

    response = notify(http, bot, whatsapp(ANA, LUIS, EVA))

    assert response.status_code == 200
    assert queued(queue) == [f"wamid.{ANA}", f"wamid.{EVA}"]
    assert f"A message for chatbot {bot} could not be ingested: {type(failure).__name__} at " in caplog.text
    assert "failure-marker" not in caplog.text


# --- The Graph API does not give the profile: the message goes on without the name --------------------------------

@pytest.fixture(params=["it refuses", "it cannot be reached", "it answers something else"])
def no_profile(request, graph_api, monkeypatch):
    if request.param == "it refuses":
        graph_api.status_code, graph_api.text = 400, '{"error": {"message": "graph-marker", "code": 100}}'
    elif request.param == "it answers something else":
        graph_api.json = lambda: {"first_name": ["graph-marker"], "id": {"nested": PSID}}
    else:
        def unreachable(url, timeout):
            graph_api.asked += 1
            raise requests.ConnectionError(f"graph-marker {url}")

        monkeypatch.setattr(facebook.requests, "get", unreachable)
    return graph_api


def test_without_the_profile_the_message_is_queued_without_the_name(http, bot, queue, no_profile, caplog):
    """A profile is refused for reasons that asking again does not change: the message must not wait for it."""
    response = notify(http, bot, messenger())

    assert response.status_code == 200
    (envelope,) = queue
    assert envelope["jwe_body"]["contact"] == {"name": None, "fbId": PSID}
    assert envelope["messageId"] == f"m_{PSID}"
    (contact,) = Contact.objects
    assert (contact.fbId, contact.firstName, contact.lastName, contact.profilePic) == (PSID, None, None, None)
    assert "No profile from the Graph API" in caplog.text and "graph-marker" not in caplog.text


def test_the_envelope_of_a_message_without_the_name_keeps_the_contract(http, bot, queue, no_profile):
    asyncapi = yaml.safe_load(ASYNCAPI.read_text(encoding="utf-8"))
    contract = Draft202012Validator({"$ref": "#/components/schemas/SobreMensaje", "components": asyncapi["components"]})

    notify(http, bot, messenger())

    assert [error.message for error in contract.iter_errors(queue[0])] == []


def test_the_graph_api_is_not_asked_again_for_somebody_already_stored(http, bot, queue, no_profile, another):
    notify(http, bot, messenger())
    asked = no_profile.asked

    assert notify(http, bot, another(messenger())).status_code == 200
    assert no_profile.asked == asked == 1
    assert len(queue) == 2 and Contact.objects.count() == 1


def test_with_the_profile_the_name_goes_in_the_envelope(http, bot, queue):
    assert notify(http, bot, messenger()).status_code == 200
    assert queue[0]["jwe_body"]["contact"] == {"name": "Carla", "fbId": PSID}


def test_the_database_failing_while_the_token_of_the_page_is_read_is_not_a_missing_profile(http, bot, queue,
                                                                                            monkeypatch):
    secrets = TenantRepository.bot_secrets
    calls = []

    def fails_the_second_time(self, bot_):
        calls.append(bot_)
        if len(calls) > 1:
            raise ServerSelectionTimeoutError("no servers")
        return secrets(self, bot_)

    monkeypatch.setattr(TenantRepository, "bot_secrets", fails_the_second_time)

    assert notify(http, bot, messenger()).status_code == 503
    assert queue == [] and Contact.objects.count() == 0


def test_the_key_service_failing_while_the_token_of_the_page_is_read_asks_meta_to_send_again(http, bot, queue,
                                                                                              key_service,
                                                                                              monkeypatch):
    secrets = TenantRepository.bot_secrets
    calls = []

    def key_gone_the_second_time(self, bot_):
        calls.append(bot_)
        if len(calls) > 1:
            key_service.down = True
            data_keys.forget()
        return secrets(self, bot_)

    monkeypatch.setattr(TenantRepository, "bot_secrets", key_gone_the_second_time)

    assert notify(http, bot, messenger()).status_code == 503
    assert queue == [] and Contact.objects.count() == 0


def test_a_message_for_a_chatbot_that_does_not_exist_is_not_found(http, queue):
    raw = json.dumps(whatsapp(ANA)).encode()

    assert META.post(f"/chat/{uuid4()}", content=raw, headers=signed(raw)).status_code == 404
    assert queue == []
