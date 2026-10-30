# -*- coding: utf-8 -*-
"""
    From a signed notification to the event on the bus (ADR-0021, I3): one event per notification that brings any
    message from a person, with all of them, validated against the version 2 contract (asyncapi-v2.yaml). The bus
    is replaced by a publisher that keeps what it is handed; the broker of version 1 and the Graph API too.
"""
import hashlib
import hmac
import json
import os
import pathlib
import re
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker

from shared import bus
import controller.facebook as facebook
import controller.ingestion as ingestion
import webhook as webhook_module
from classes import ChatBot, Contact, ConversationMessage
from controller.incoming import IncomingMessage
from controller.webhook_event import event_message_id
from repository import TenantRepository

META = TestClient(webhook_module.app)

APP_SECRET = os.environ["FACEBOOK_APP_SECRET"]
ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTRACT = yaml.safe_load((ROOT / "docs" / "02-design" / "contracts" / "asyncapi-v2.yaml").read_text(encoding="utf-8"))


def schema(name: str) -> Draft202012Validator:
    return Draft202012Validator({"$ref": f"#/components/schemas/{name}", "components": CONTRACT["components"]},
                                format_checker=FormatChecker())


EVENT, HEADERS = schema("EventoWebhook"), schema("Cabeceras")
METADATA = {"display_phone_number": "12025550101", "phone_number_id": "100000000000001"}


def text(message_id, number="12025550143"):
    return {"from": number, "id": message_id, "timestamp": "1760000000", "type": "text", "text": {"body": "hola"}}


def value(*message_ids, statuses=None):
    content = {"messaging_product": "whatsapp", "metadata": METADATA,
               "contacts": [{"profile": {"name": "Ana"}, "wa_id": "12025550143"}],
               "messages": [text(message_id) for message_id in message_ids]}
    if statuses:
        content = {"messaging_product": "whatsapp", "metadata": METADATA, "statuses": statuses}
    return content


def whatsapp(*values):
    return {"object": "whatsapp_business_account",
            "entry": [{"id": "1", "changes": [{"field": "messages", "value": each}]} for each in values]}


def messenger_event(mid, **message):
    return {"sender": {"id": "123456789"}, "recipient": {"id": "987654321"}, "timestamp": 1760000000000,
            "message": {"mid": mid, "text": "hola", **message}}


def messenger(*events):
    return {"object": "page", "entry": [{"id": "1", "time": 1760000000000, "messaging": list(events)}]}


class KeptEvents:
    """Stands for Bifröst: keeps each event it is handed, or refuses them all."""

    def __init__(self):
        self.events = []
        self.refuse = False

    async def publish(self, subject, payload, message_id, headers=None):
        if self.refuse:
            raise bus.BusUnavailable("TimeoutError")
        self.events.append(SimpleNamespace(subject=subject, payload=json.loads(payload), message_id=message_id,
                                           headers={**(headers or {}), "Nats-Msg-Id": message_id}))
        return bus.Published(stream="WEBHOOK", sequence=len(self.events), duplicate=False)

    async def close(self):
        pass


@pytest.fixture
def bifrost():
    kept = KeptEvents()
    bus.configure(kept)
    yield kept
    bus.configure(None)


@pytest.fixture
def queue(monkeypatch):
    """The envelopes of version 1, which still go to Hermod until I4."""
    sent = []

    async def publish(queue, message):
        sent.append(json.loads(message.to_json()))
    monkeypatch.setattr(ingestion, "send_message_to_queue", publish)
    return sent


@pytest.fixture
def graph_api(monkeypatch):
    profile = {"id": "123456789", "first_name": "Ana", "last_name": "Diaz", "profile_pic": "https://example.com/a.jpg"}
    monkeypatch.setattr(facebook.requests, "get",
                        lambda url, timeout: SimpleNamespace(status_code=200, text="{}", json=lambda: profile))


@pytest.fixture
def bot(register):
    owner = TenantRepository(register("acme").client_id)
    return owner.add_bot(ChatBot(name="Bot", role="Sales Assistant", description="A test chatbot", tone="helpful",
                                 facebookToken="page-token"))


def notify(bot_id, payload):
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return META.post(f"/chat/{bot_id}", content=body,
                     headers={"content-type": "application/json", "X-Hub-Signature-256": signature})


def violations(event) -> list[str]:
    return ([error.message for error in EVENT.iter_errors(event.payload)]
            + [error.message for error in HEADERS.iter_errors(event.headers)])


# --- One event per notification --------------------------------------------------------------------------------------

def test_a_whatsapp_notification_with_two_messages_is_one_event_with_both(bot, bifrost, queue):
    assert notify(bot.uuid, whatsapp(value("wamid.A", "wamid.B"))).status_code == 200

    event, = bifrost.events
    assert event.subject == "webhook.meta.whatsapp"
    assert [m["messageId"] for m in event.payload["messages"]] == ["wamid.A", "wamid.B"]
    # Each message as the engine gets it today: the value of the change, reduced to that message.
    assert event.payload["messages"][1]["message"]["messages"] == [text("wamid.B")]
    assert (event.payload["botId"], event.payload["clientId"]) == (str(bot.uuid), str(bot.clientId))
    assert violations(event) == []


def test_the_messages_of_every_entry_are_in_the_same_event(bot, bifrost, queue):
    notify(bot.uuid, whatsapp(value("wamid.A"), value("wamid.B")))

    event, = bifrost.events
    assert [m["messageId"] for m in event.payload["messages"]] == ["wamid.A", "wamid.B"]


def test_a_messenger_notification_is_an_event_of_its_channel(bot, bifrost, queue, graph_api):
    assert notify(bot.uuid, messenger(messenger_event("m_A"), messenger_event("m_B"))).status_code == 200

    event, = bifrost.events
    assert (event.subject, event.payload["channel"]) == ("webhook.meta.messenger", "messenger")
    assert [m["message"] for m in event.payload["messages"]] == [messenger_event("m_A"), messenger_event("m_B")]
    assert violations(event) == []


def test_the_event_says_when_it_was_received_with_its_time_zone(bot, bifrost, queue):
    notify(bot.uuid, whatsapp(value("wamid.A")))

    assert re.search(r"\+00:00$", bifrost.events[0].payload["receivedAt"])


def test_with_the_bus_the_webhook_only_publishes(bot, bifrost, queue):
    """Who wrote, the history and the queue of the engine are the ingestion worker's (ADR-0021, I4)."""
    assert notify(bot.uuid, whatsapp(value("wamid.A", "wamid.B"))).status_code == 200

    assert len(bifrost.events) == 1
    assert queue == []
    assert Contact.objects.count() == ConversationMessage.objects.count() == 0


# --- What is not published -------------------------------------------------------------------------------------------

def test_a_notification_without_a_message_from_a_person_is_not_published(bot, bifrost, queue):
    receipt = [{"id": "wamid.A", "status": "read", "timestamp": "1760000000", "recipient_id": "12025550143"}]

    assert notify(bot.uuid, whatsapp(value(statuses=receipt))).status_code == 200

    assert bifrost.events == []


def test_an_echo_of_the_page_is_not_published(bot, bifrost, queue):
    assert notify(bot.uuid, messenger(messenger_event("m_ECHO", is_echo=True))).status_code == 200

    assert bifrost.events == []


def test_without_a_bus_the_webhook_ingests_the_notification_itself(bot, queue):
    """Development and the tests: the same ingestion as the worker's, before answering."""
    assert not bus.configured()

    assert notify(bot.uuid, whatsapp(value("wamid.A"))).status_code == 200

    assert [envelope["messageId"] for envelope in queue] == ["wamid.A"]
    assert Contact.objects.count() == ConversationMessage.objects.count() == 1


# --- When the bus does not keep it -----------------------------------------------------------------------------------

def test_an_event_the_bus_does_not_keep_is_a_503(bot, bifrost, queue):
    bifrost.refuse = True

    assert notify(bot.uuid, whatsapp(value("wamid.A"))).status_code == 503

    assert queue == []


# --- What the bus drops a second copy by -----------------------------------------------------------------------------

def message(message_id):
    return IncomingMessage(message_id=message_id, sender="12025550143", name=None, content={})


def test_the_same_notification_sent_again_has_the_same_message_id(bot, bifrost, queue):
    notify(bot.uuid, whatsapp(value("wamid.A", "wamid.B")))
    notify(bot.uuid, whatsapp(value("wamid.A", "wamid.B")))

    first, again = bifrost.events
    assert first.message_id == again.message_id


def test_the_message_id_names_the_chatbot_and_digests_the_identifiers_in_any_order(bot):
    one = event_message_id(bot, [message("wamid.A"), message("wamid.B")])

    assert one == event_message_id(bot, [message("wamid.B"), message("wamid.A")])
    assert one == f"{bot.uuid}:{hashlib.sha256(b'wamid.A,wamid.B').hexdigest()}"
    assert one != event_message_id(bot, [message("wamid.A")])


def test_the_message_id_carries_no_identifier_of_meta_in_clear(bot, bifrost, queue):
    notify(bot.uuid, whatsapp(value("wamid.A")))

    assert "wamid" not in bifrost.events[0].message_id


def test_each_event_has_a_trace_context_of_its_own(bot, bifrost, queue):
    notify(bot.uuid, whatsapp(value("wamid.A")))
    notify(bot.uuid, whatsapp(value("wamid.B")))

    first, second = (event.headers["traceparent"] for event in bifrost.events)
    assert first != second
