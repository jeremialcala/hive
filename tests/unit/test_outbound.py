# -*- coding: utf-8 -*-
"""
    What a client sends to its contacts (ADR-0031, RF29, RF31): POST /chat/{bot_id}/contacts/{contact_id}/messages
    checks and publishes a PedidoDeEnvio on SALIENTES, and the engine's notifier sends it. A text only inside the
    window of 24 hours since the contact last wrote; a template only approved, with its values, and outside the
    window only with the contact's consent; never twice for the same idempotency key, never past the limits, and
    never to a contact of somebody else.
"""
import json
import logging
import pathlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

import app as app_module
from classes import ChatBot, Contact, MessageTemplate, OutboundRequest
from classes.entity_conversation_message import INCOMING
from controller import outbound, send_limits
from controller.ingestion import partition
from repository import TenantRepository
from shared import bus

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTRACT = yaml.safe_load((ROOT / "docs" / "02-design" / "contracts" / "asyncapi-v2.yaml").read_text(encoding="utf-8"))
REQUEST = Draft202012Validator({"$ref": "#/components/schemas/PedidoDeEnvio", "components": CONTRACT["components"]})
NUMBER = "12025550143"


class Bifrost:
    """Stands for Bifröst: keeps what it is handed, or refuses it."""

    def __init__(self):
        self.kept, self.refuse = [], False

    async def publish(self, subject, payload, message_id, headers=None):
        if self.refuse:
            raise bus.BusUnavailable("TimeoutError")
        self.kept.append(SimpleNamespace(subject=subject, request=json.loads(payload), message_id=message_id))
        return bus.Published(stream="SALIENTES", sequence=len(self.kept), duplicate=False)

    async def close(self):
        pass


@pytest.fixture
def bifrost(monkeypatch):
    kept = Bifrost()
    bus.configure(kept)
    # Each test counts its own sends.
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
    return repo.add_bot(ChatBot(name="Bot", role="Sales Assistant", description="A test chatbot", tone="helpful"))


@pytest.fixture
def ana(repo, bot):
    return repo.add_contact(bot.uuid, Contact(firstName="Ana", waId=NUMBER))


def wrote(repo, contact, hours_ago: float):
    """The contact wrote that long ago: it opens the window then."""
    repo.record_message(contact, INCOMING, f"wamid.{uuid4().hex}", "whatsapp",
                        datetime.now(timezone.utc) - timedelta(hours=hours_ago), text="hola")


def template(repo, bot, name="cita", status="APPROVED", category="UTILITY", parameters=1, language="es"):
    return MessageTemplate(key=MessageTemplate.key_for(bot.uuid, name, language), clientId=repo.client_id,
                           botId=bot.uuid, name=name, language=language, category=category, status=status,
                           parameters=parameters).save()


def send(http, caller, bot, contact, body, key=None):
    headers = {**caller.auth, **({"Idempotency-Key": key} if key is not False else {})}
    if key is None:
        headers["Idempotency-Key"] = f"key-{uuid4()}"
    return http.post(f"/chat/{bot.uuid}/contacts/{contact._uuid}/messages", headers=headers, json=body)


TEXT = {"type": "text", "text": "Su cita es mañana a las 10"}
TEMPLATE = {"type": "template", "template": {"name": "cita", "language": "es", "parameters": ["mañana a las 10"]}}


# --- A text --------------------------------------------------------------------------------------------------------

def test_a_text_inside_the_window_is_taken_and_published_for_the_notifier(http, caller, repo, bot, ana, bifrost):
    wrote(repo, ana, hours_ago=2)

    response = send(http, caller, bot, ana, TEXT)

    assert response.status_code == 202
    data = response.json()["data"]
    (published,) = bifrost.kept
    assert published.subject == f"gateway.outbound.{partition(bot.uuid, ana._uuid)}.{bot.uuid}.{ana._uuid}"
    assert published.message_id == data["uuid"]
    REQUEST.validate(published.request)
    assert (published.request["kind"], published.request["text"], published.request["contactId"]) == \
        ("text", TEXT["text"], str(ana._uuid))
    # The notifier looks the number up: it does not travel in the request.
    assert NUMBER not in json.dumps(published.request)


@pytest.mark.parametrize("hours_ago", [25, None], ids=["the window closed", "the contact never wrote"])
def test_a_text_outside_the_window_is_turned_away(http, caller, repo, bot, ana, bifrost, hours_ago):
    if hours_ago is not None:
        wrote(repo, ana, hours_ago)

    response = send(http, caller, bot, ana, TEXT)

    assert response.status_code == 422
    assert response.json()["message"].startswith("OUTSIDE_WINDOW")
    assert bifrost.kept == []


# --- A template ----------------------------------------------------------------------------------------------------

def test_an_approved_template_inside_the_window_needs_no_consent(http, caller, repo, bot, ana, bifrost):
    wrote(repo, ana, hours_ago=1)
    template(repo, bot)

    response = send(http, caller, bot, ana, TEMPLATE)

    assert response.status_code == 202
    REQUEST.validate(bifrost.kept[0].request)
    assert bifrost.kept[0].request["template"] == {"name": "cita", "language": "es", "category": "UTILITY",
                                                   "parameters": ["mañana a las 10"]}


@pytest.mark.parametrize("stored", ["none", "PENDING", "REJECTED", "PAUSED", "AUTHENTICATION"])
def test_only_an_approved_template_of_a_category_of_the_v1_is_sent(http, caller, repo, bot, ana, bifrost, stored):
    wrote(repo, ana, hours_ago=1)
    if stored == "AUTHENTICATION":
        template(repo, bot, category="AUTHENTICATION")
    elif stored != "none":
        template(repo, bot, status=stored)

    response = send(http, caller, bot, ana, TEMPLATE)

    assert (response.status_code, response.json()["message"].split(":")[0]) == (422, "TEMPLATE_NOT_APPROVED")
    assert bifrost.kept == []


def test_a_template_goes_with_as_many_values_as_it_takes(http, caller, repo, bot, ana, bifrost):
    wrote(repo, ana, hours_ago=1)
    template(repo, bot, parameters=2)

    response = send(http, caller, bot, ana, TEMPLATE)

    assert (response.status_code, response.json()["message"].split(":")[0]) == (422, "TEMPLATE_PARAMETERS")


def test_outside_the_window_a_template_needs_the_consent_for_its_category(http, caller, repo, bot, ana, bifrost):
    template(repo, bot)

    without = send(http, caller, bot, ana, TEMPLATE)
    other = http.put(f"/chat/{bot.uuid}/contacts/{ana._uuid}/consent", headers=caller.auth,
                     json={"categories": ["MARKETING"], "source": "web form"})
    still = send(http, caller, bot, ana, TEMPLATE)
    agreed = http.put(f"/chat/{bot.uuid}/contacts/{ana._uuid}/consent", headers=caller.auth,
                      json={"categories": ["UTILITY"], "source": "web form"})
    sent = send(http, caller, bot, ana, TEMPLATE)

    assert [r.status_code for r in (without, other, still, agreed, sent)] == [422, 200, 422, 200, 202]
    assert without.json()["message"].startswith("NO_CONSENT")
    assert len(bifrost.kept) == 1


def test_a_consent_withdrawn_stops_the_templates_outside_the_window(http, caller, repo, bot, ana, bifrost):
    template(repo, bot)
    http.put(f"/chat/{bot.uuid}/contacts/{ana._uuid}/consent", headers=caller.auth,
             json={"categories": ["UTILITY"], "source": "web form"})

    withdrawn = http.delete(f"/chat/{bot.uuid}/contacts/{ana._uuid}/consent", headers=caller.auth)
    response = send(http, caller, bot, ana, TEMPLATE)

    assert withdrawn.status_code == 200 and withdrawn.json()["data"]["consent"]["withdrawnAt"] is not None
    assert (response.status_code, response.json()["message"].split(":")[0]) == (422, "NO_CONSENT")


def test_the_consent_goes_with_the_contact_in_its_export(http, caller, repo, bot, ana):
    http.put(f"/chat/{bot.uuid}/contacts/{ana._uuid}/consent", headers=caller.auth,
             json={"categories": ["UTILITY", "MARKETING"], "source": "web form",
                   "grantedAt": "2026-10-20T10:00:00+00:00"})

    exported = http.get(f"/chat/{bot.uuid}/contacts/{ana._uuid}", headers=caller.auth).json()["data"]

    assert exported["consent"] == {"categories": ["UTILITY", "MARKETING"], "source": "web form",
                                   "grantedAt": "2026-10-20T10:00:00Z", "withdrawnAt": None}


@pytest.mark.parametrize("body", [
    {"categories": [], "source": "web form"},
    {"categories": ["AUTHENTICATION"], "source": "web form"},
    {"categories": ["UTILITY", "UTILITY"], "source": "web form"},
    {"categories": ["UTILITY"], "source": ""},
    {"categories": ["UTILITY"], "source": "web form", "grantedAt": "2026-10-20T10:00:00"},
], ids=["no category", "a category that is not of the v1", "the same twice", "no source", "a date without zone"])
def test_a_consent_is_recorded_as_it_should(http, caller, bot, ana, body):
    assert http.put(f"/chat/{bot.uuid}/contacts/{ana._uuid}/consent", headers=caller.auth,
                    json=body).status_code == 422


# --- Once, and within the limits ----------------------------------------------------------------------------------

def test_the_same_idempotency_key_sends_once(http, caller, repo, bot, ana, bifrost):
    wrote(repo, ana, hours_ago=1)

    first = send(http, caller, bot, ana, TEXT, key="the-same-key")
    again = send(http, caller, bot, ana, TEXT, key="the-same-key")
    other = send(http, caller, bot, ana, {**TEXT, "text": "otra cosa"}, key="the-same-key")

    assert (first.status_code, again.status_code) == (202, 202)
    assert first.json()["data"]["uuid"] == again.json()["data"]["uuid"]
    assert (other.status_code, other.json()["message"].split(":")[0]) == (422, "IDEMPOTENCY_KEY_REUSED")
    assert len(bifrost.kept) == 1


@pytest.mark.parametrize("key", [False, "short", "with spaces in it", "x" * 129], ids=["none", "too short",
                                                                                        "spaces", "too long"])
def test_a_send_needs_its_idempotency_key(http, caller, repo, bot, ana, bifrost, key):
    wrote(repo, ana, hours_ago=1)

    response = send(http, caller, bot, ana, TEXT, key=key)

    assert (response.status_code, response.json()["message"].split(":")[0]) == (422, "IDEMPOTENCY_KEY")


def test_past_the_limit_of_a_chatbot_a_minute_it_is_turned_away(http, caller, repo, bot, ana, bifrost, monkeypatch):
    monkeypatch.setattr(outbound, "LIMITS", send_limits.SendLimits(per_bot_a_minute=2))
    wrote(repo, ana, hours_ago=1)

    answers = [send(http, caller, bot, ana, TEXT) for _ in range(3)]

    assert [answer.status_code for answer in answers] == [202, 202, 429]
    assert 0 < int(answers[-1].headers["retry-after"]) <= 60
    assert len(bifrost.kept) == 2


def test_past_the_limit_of_a_client_a_day_it_is_turned_away(http, caller, repo, bot, ana, bifrost, monkeypatch):
    monkeypatch.setattr(outbound, "LIMITS", send_limits.SendLimits(per_client_a_day=1))
    wrote(repo, ana, hours_ago=1)

    assert [send(http, caller, bot, ana, TEXT).status_code for _ in range(2)] == [202, 429]


def test_a_bus_that_does_not_take_it_leaves_the_key_free_to_send_again(http, caller, repo, bot, ana, bifrost):
    wrote(repo, ana, hours_ago=1)
    bifrost.refuse = True

    refused = send(http, caller, bot, ana, TEXT, key="try-it-again")
    bifrost.refuse = False
    taken = send(http, caller, bot, ana, TEXT, key="try-it-again")

    assert (refused.status_code, taken.status_code) == (503, 202)
    assert len(bifrost.kept) == 1


def test_without_a_bus_nothing_is_taken(http, caller, repo, bot, ana):
    wrote(repo, ana, hours_ago=1)

    assert send(http, caller, bot, ana, TEXT).status_code == 503
    assert OutboundRequest.objects.count() == 0


# --- Only to one's own contacts ------------------------------------------------------------------------------------

def test_another_client_does_not_reach_the_contacts_of_this_one(http, caller, repo, bot, ana, bifrost, register):
    wrote(repo, ana, hours_ago=1)
    stranger = register("globex")

    assert send(http, stranger, bot, ana, TEXT).status_code == 404
    assert http.put(f"/chat/{bot.uuid}/contacts/{ana._uuid}/consent", headers=stranger.auth,
                    json={"categories": ["UTILITY"], "source": "x"}).status_code == 404
    assert bifrost.kept == []


def test_a_contact_that_was_deleted_gets_nothing(http, caller, repo, bot, ana, bifrost):
    wrote(repo, ana, hours_ago=1)
    http.delete(f"/chat/{bot.uuid}/contacts/{ana._uuid}", headers=caller.auth)

    assert send(http, caller, bot, ana, TEXT).status_code == 404
    # The bus only got the notice of the erasure.
    assert [kept.subject for kept in bifrost.kept] == ["gateway.contact.erased"]


def test_a_contact_of_another_chatbot_is_not_reached_through_this_one(http, caller, repo, bot, ana, bifrost):
    other = repo.add_bot(ChatBot(name="Other", role="Sales Assistant", description="Another", tone="helpful"))
    wrote(repo, ana, hours_ago=1)

    assert http.post(f"/chat/{other.uuid}/contacts/{ana._uuid}/messages", json=TEXT,
                     headers={**caller.auth, "Idempotency-Key": "another-bot"}).status_code == 404


@pytest.mark.parametrize("body", [
    {"type": "text"},
    {"type": "text", "text": "hola", "template": TEMPLATE["template"]},
    {"type": "template", "text": "hola"},
    {"type": "text", "text": "   "},
    {"type": "text", "text": "x" * 4097},
    {"type": "template", "template": {"name": "Cita!", "language": "es"}},
    {"type": "template", "template": {"name": "cita", "language": "es", "parameters": [""]}},
    {"type": "audio", "text": "hola"},
], ids=["a text without text", "both", "a template without template", "an empty text", "a text too long",
        "a name Meta does not take", "an empty value", "another type"])
def test_a_send_is_asked_for_as_it_should(http, caller, repo, bot, ana, bifrost, body):
    wrote(repo, ana, hours_ago=1)

    assert send(http, caller, bot, ana, body).status_code == 422
    assert bifrost.kept == []


def test_the_log_says_who_sent_what_to_whom_and_never_the_text_nor_the_number(http, caller, repo, bot, ana,
                                                                               bifrost, caplog):
    caplog.set_level(logging.DEBUG, logger=app_module.settings.environment)
    wrote(repo, ana, hours_ago=1)
    template(repo, bot)

    send(http, caller, bot, ana, TEXT)
    send(http, caller, bot, ana, TEMPLATE)

    assert f"chatbot {bot.uuid}, contact {ana._uuid}, text" in caplog.text
    assert "template cita (es)" in caplog.text
    for secret in (TEXT["text"], "mañana a las 10", NUMBER):
        assert secret not in caplog.text


def test_what_was_asked_goes_with_the_contact_when_it_is_deleted(http, caller, repo, bot, ana, bifrost):
    wrote(repo, ana, hours_ago=1)
    send(http, caller, bot, ana, TEXT)

    deleted = http.delete(f"/chat/{bot.uuid}/contacts/{ana._uuid}", headers=caller.auth).json()["data"]["deleted"]

    assert deleted["outbound"] == 1
    assert OutboundRequest.objects(contactId=ana._uuid).count() == 0
