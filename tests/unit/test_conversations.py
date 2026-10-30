# -*- coding: utf-8 -*-
"""
    What the PWA reads (HIG-87): the chatbots of the client, a contact's conversation with Meta's window and without its
    number, and what came of each request to send, as the ingestion marks it with what the notifier says.
"""
import asyncio
import json
import logging
import pathlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker

import app as app_module
from auth.idp import IdentityProvider
from classes import ChatBot, ChatbotPhone, Contact, OutboundRequest
from classes.entity_conversation_message import INCOMING
from conftest import KEY_OF
from controller import outbound, send_limits
from controller.ingestion import Unprocessable, ingest_reply
from enums import PersonRole, PersonStatus
from fake_idp import AUDIENCE, BFF, ISSUER, Zitadel, auth, exchange
from repository import TenantRepository
from shared import bus
from shared.contract import FailedSend

NUMBER, PHONE_ID = "12025550143", "100000000000001"


class Bifrost:
    def __init__(self):
        self.kept = []

    async def publish(self, subject, payload, message_id, headers=None):
        self.kept.append(SimpleNamespace(subject=subject, request=json.loads(payload)))
        return bus.Published(stream="SALIENTES", sequence=len(self.kept), duplicate=False)

    async def close(self):
        pass


@pytest.fixture
def bifrost(monkeypatch):
    kept = Bifrost()
    bus.configure(kept)
    monkeypatch.setattr(outbound, "LIMITS", send_limits.SendLimits())
    yield kept
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
def bot(repo):
    made = repo.add_bot(ChatBot(name="Bot", role="Sales Assistant", description="A test chatbot", tone="helpful"))
    repo.add_phone(made.uuid, ChatbotPhone(phoneNumber="12025550101", phoneId=PHONE_ID))
    return made


@pytest.fixture
def ana(repo, bot):
    return repo.add_contact(bot.uuid, Contact(firstName="Ana", waId=NUMBER))


@pytest.fixture
def logged(caplog):
    caplog.set_level(logging.WARNING)
    return lambda: "\n".join(record.getMessage() for record in caplog.records)


def wrote(repo, contact, hours_ago: float, text="hola"):
    repo.record_message(contact, INCOMING, f"wamid.{uuid4().hex}", "whatsapp",
                        datetime.now(timezone.utc) - timedelta(hours=hours_ago), text=text)


def send(http, caller, bot, contact, text="Su cita es mañana"):
    return http.post(f"/chat/{bot.uuid}/contacts/{contact._uuid}/messages", json={"type": "text", "text": text},
                     headers={**caller.auth, "Idempotency-Key": f"key-{uuid4()}"})


def status_of(http, caller, bot, contact, request_id):
    return http.get(f"/chat/{bot.uuid}/contacts/{contact._uuid}/messages/{request_id}", headers=caller.auth)


def reply(caller, bot, contact, request_id, message_id="wamid.SENT"):
    return {"schemaVersion": 2, "botId": str(bot.uuid), "clientId": str(caller.client_id),
            "contactId": str(contact._uuid), "channel": "whatsapp", "messageId": message_id,
            "sentAt": datetime.now(timezone.utc).isoformat(), "text": "Su cita es mañana", "origin": "client",
            "sentBy": f"key {caller.kid}", "requestId": str(request_id)}


def failure(caller, bot, contact, request_id, code=131047, reason="rejected"):
    return {"schemaVersion": 2, "botId": str(bot.uuid), "clientId": str(caller.client_id),
            "contactId": str(contact._uuid), "channel": "whatsapp", "requestId": str(request_id), "code": code,
            "reason": reason, "failedAt": datetime.now(timezone.utc).isoformat()}


# --- The chatbots -----------------------------------------------------------------------------------------------------

def test_the_list_has_the_chatbots_of_the_client_and_none_of_their_secrets(http, caller, repo, bot, register):
    other = TenantRepository(register("globex").client_id)
    other.add_bot(ChatBot(name="Theirs", role="r", description="d", tone="t"))
    repo.set_bot_credentials(bot.uuid, whatsappToken="EAAG-a-token-of-the-test", appSecret="a-secret-of-the-test")

    response = http.get("/bots", headers=caller.auth)

    assert response.status_code == 200
    (listed,) = response.json()["data"]["bots"]
    assert (listed["uuid"], listed["name"], listed["status"]) == (str(bot.uuid), "Bot", "REG")
    assert listed["credentials"]["whatsappToken"] is True
    for secret in ("EAAG-a-token-of-the-test", "a-secret-of-the-test", "verifyToken"):
        assert secret not in response.text


# --- The conversation -----------------------------------------------------------------------------------------------

def test_the_conversation_has_the_messages_and_the_window_but_not_the_number(http, caller, repo, bot, ana):
    wrote(repo, ana, hours_ago=2, text="¿Tienen zapatos rojos?")

    response = http.get(f"/chat/{bot.uuid}/contacts/{ana._uuid}/conversation", headers=caller.auth)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["contact"]["firstName"] == "Ana" and data["contact"]["channel"] == "whatsapp"
    assert [message["text"] for message in data["messages"]] == ["¿Tienen zapatos rojos?"]
    assert data["window"]["open"] is True
    closes = datetime.fromisoformat(data["window"]["closesAt"])
    assert timedelta(hours=21) < closes - datetime.now(timezone.utc) < timedelta(hours=23)
    assert NUMBER not in response.text


@pytest.mark.parametrize("hours_ago,open_", [(25, False), (None, False), (0.1, True)])
def test_the_window_is_open_for_24_hours_after_the_contact_wrote(http, caller, repo, bot, ana, hours_ago, open_):
    if hours_ago is not None:
        wrote(repo, ana, hours_ago=hours_ago)

    answer = http.get(f"/chat/{bot.uuid}/contacts/{ana._uuid}/conversation", headers=caller.auth)
    window = answer.json()["data"]["window"]

    assert window["open"] is open_
    assert (window["lastIncomingAt"] is None) == (hours_ago is None)


def test_the_conversation_has_the_last_fifty_messages_oldest_first(http, caller, repo, bot, ana):
    for minutes in range(60, 0, -1):
        wrote(repo, ana, hours_ago=minutes / 60, text=f"m{60 - minutes}")

    messages = http.get(f"/chat/{bot.uuid}/contacts/{ana._uuid}/conversation",
                        headers=caller.auth).json()["data"]["messages"]

    assert [message["text"] for message in messages] == [f"m{n}" for n in range(10, 60)]


def test_a_contact_of_another_chatbot_or_client_is_not_found(http, caller, repo, ana, register):
    other = repo.add_bot(ChatBot(name="Other", role="r", description="d", tone="t"))
    stranger = register("globex")

    assert http.get(f"/chat/{other.uuid}/contacts/{ana._uuid}/conversation", headers=caller.auth).status_code == 404
    assert http.get(f"/chat/{ana.botId}/contacts/{ana._uuid}/conversation", headers=stranger.auth).status_code == 404


@pytest.fixture
def zitadel(monkeypatch):
    idp = Zitadel()
    monkeypatch.setattr(app_module, "IDP", IdentityProvider(ISSUER, AUDIENCE, read_keys=idp.jwks))
    return idp


def test_an_analyst_reads_the_conversation_and_the_log_says_who(http, caller, repo, bot, ana, zitadel, logged):
    analyst = repo.add_person("an@acme.test", ISSUER, "analyst-subject", PersonRole.ANALYST, invited_by="test",
                              status=PersonStatus.ACTIVE)
    token = exchange(http, zitadel.id_token(sub="analyst-subject")).json()["access_token"]
    KEY_OF[token] = BFF

    response = http.get(f"/chat/{bot.uuid}/contacts/{ana._uuid}/conversation", headers=auth(token))

    assert response.status_code == 200 and NUMBER not in response.text
    assert f"Audit: person {analyst.uuid} read the conversation of contact {ana._uuid}" in logged()


def test_a_machine_credential_reading_leaves_no_line_of_audit(http, caller, bot, ana, logged):
    http.get(f"/chat/{bot.uuid}/contacts/{ana._uuid}/conversation", headers=caller.auth)

    assert "read the conversation" not in logged()


# --- What came of a send ----------------------------------------------------------------------------------------

def test_a_send_is_accepted_then_sent_when_the_history_has_it(http, caller, repo, bot, ana, bifrost):
    wrote(repo, ana, hours_ago=1)
    request_id = send(http, caller, bot, ana).json()["data"]["uuid"]

    assert status_of(http, caller, bot, ana, request_id).json()["data"]["status"] == "accepted"

    asyncio.run(ingest_reply(reply(caller, bot, ana, request_id)))

    data = status_of(http, caller, bot, ana, request_id).json()["data"]
    assert (data["status"], data["messageId"], data["failure"]) == ("sent", "wamid.SENT", None)
    assert data["sentAt"] is not None


@pytest.mark.parametrize("code,reason,said", [
    (131047, "rejected", "window_closed"), (132015, "rejected", "template_paused"),
    (132016, "rejected", "template_disabled"), (132000, "rejected", "template_parameters"),
    (131049, "rejected", "marketing_limit"), (999999, "rejected", "rejected"),
    (None, "no_token", "no_token"), (None, "gave_up", "gave_up")])
def test_a_send_that_failed_says_why(http, caller, repo, bot, ana, bifrost, logged, code, reason, said):
    wrote(repo, ana, hours_ago=1)
    request_id = send(http, caller, bot, ana).json()["data"]["uuid"]

    assert asyncio.run(ingest_reply(failure(caller, bot, ana, request_id, code, reason))) == {"failed": True}

    data = status_of(http, caller, bot, ana, request_id).json()["data"]
    assert (data["status"], data["failure"]["code"], data["failure"]["reason"]) == ("failed", code, said)
    assert f"Request {request_id} of chatbot {bot.uuid} was not sent: {reason}" in logged()


def test_a_send_already_sent_is_not_undone_by_a_late_failure_nor_the_other_way(http, caller, repo, bot, ana,
                                                                                  bifrost):
    wrote(repo, ana, hours_ago=1)
    sent = send(http, caller, bot, ana).json()["data"]["uuid"]
    failed = send(http, caller, bot, ana, text="otro").json()["data"]["uuid"]
    asyncio.run(ingest_reply(reply(caller, bot, ana, sent, "wamid.A")))
    asyncio.run(ingest_reply(failure(caller, bot, ana, failed)))

    asyncio.run(ingest_reply(failure(caller, bot, ana, sent)))
    asyncio.run(ingest_reply(reply(caller, bot, ana, failed, "wamid.B")))

    assert status_of(http, caller, bot, ana, sent).json()["data"]["status"] == "sent"
    assert status_of(http, caller, bot, ana, failed).json()["data"]["status"] == "failed"


def test_the_status_of_a_send_is_only_found_through_its_contact(http, caller, repo, bot, ana, bifrost, register):
    wrote(repo, ana, hours_ago=1)
    request_id = send(http, caller, bot, ana).json()["data"]["uuid"]
    bob = repo.add_contact(bot.uuid, Contact(firstName="Bob", waId="12025550199"))

    assert status_of(http, caller, bot, bob, request_id).status_code == 404
    assert status_of(http, register("globex"), bot, ana, request_id).status_code == 404
    assert status_of(http, caller, bot, ana, uuid4()).status_code == 404
    assert status_of(http, caller, bot, ana, "not-a-uuid").status_code == 404


def test_a_failure_of_another_client_changes_nothing(http, caller, repo, bot, ana, bifrost, register):
    wrote(repo, ana, hours_ago=1)
    request_id = send(http, caller, bot, ana).json()["data"]["uuid"]
    stranger = register("globex")

    event = {**failure(caller, bot, ana, request_id), "clientId": str(stranger.client_id)}
    assert asyncio.run(ingest_reply(event)) == {"failed": False}

    assert status_of(http, caller, bot, ana, request_id).json()["data"]["status"] == "accepted"


@pytest.mark.parametrize("change", [{"reason": "whatever"}, {"requestId": "x"}, {"schemaVersion": 1}])
def test_a_failure_without_its_shape_cannot_be_ingested(caller, bot, ana, change):
    with pytest.raises(Unprocessable):
        asyncio.run(ingest_reply({**failure(caller, bot, ana, uuid4()), **change}))


def test_a_request_is_remembered_with_its_status_for_24_hours(caller, repo, bot, ana, bifrost, http):
    wrote(repo, ana, hours_ago=1)
    request_id = send(http, caller, bot, ana).json()["data"]["uuid"]

    stored = OutboundRequest.objects(uuid=request_id).first()

    assert stored.status == "accepted"
    assert timedelta(hours=23) < stored.expiresAt.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc) \
        <= timedelta(hours=24)


CONTRACT = yaml.safe_load((pathlib.Path(__file__).resolve().parents[2] / "docs" / "02-design" / "contracts"
                           / "asyncapi-v2.yaml").read_text(encoding="utf-8"))


def test_a_failed_send_of_the_notifier_is_the_one_of_the_contract(caller, bot, ana):
    schema = Draft202012Validator({"$ref": "#/components/schemas/EnvioFallido", "components": CONTRACT["components"]},
                                  format_checker=FormatChecker())
    event = FailedSend.model_validate(failure(caller, bot, ana, uuid4())).model_dump(mode="json")

    assert [error.message for error in schema.iter_errors(event)] == []
    assert list(schema.iter_errors({**event, "reason": "whatever"}))
