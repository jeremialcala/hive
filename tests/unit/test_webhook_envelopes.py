# -*- coding: utf-8 -*-
"""
    From a signed notification to what is handed to the broker. One envelope goes out for each message a person
    sent, whatever way Meta packed them, and none for anything else. Only the broker and the Graph API are replaced.
"""
import copy
import hashlib
import hmac
import json
import os
import pathlib
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

import webhook as webhook_module
import controller.facebook as facebook
import controller.ingestion as ingestion
from classes import ChatBot, Contact
from controller import BrokerUnavailable
from repository import TenantRepository

# What Meta calls is its own service, apart from the management API.
META = TestClient(webhook_module.app)

APP_SECRET = os.environ["FACEBOOK_APP_SECRET"]
ROOT = pathlib.Path(__file__).resolve().parents[2]
ASYNCAPI = yaml.safe_load((ROOT / "docs" / "02-design" / "contracts" / "asyncapi.yaml").read_text(encoding="utf-8"))
ENVELOPE = Draft202012Validator({"$ref": "#/components/schemas/SobreMensaje", "components": ASYNCAPI["components"]})

METADATA = {"display_phone_number": "12025550101", "phone_number_id": "100000000000001"}
PAGE = "987654321"


def text(message_id, number="12025550143"):
    return {"from": number, "id": message_id, "timestamp": "1760000000", "type": "text", "text": {"body": "hola"}}


def value(*message_ids):
    return {"messaging_product": "whatsapp", "metadata": METADATA,
            "contacts": [{"profile": {"name": "Ana"}, "wa_id": "12025550143"}],
            "messages": [text(message_id) for message_id in message_ids]}


def whatsapp(*values):
    return {"object": "whatsapp_business_account",
            "entry": [{"id": "1", "changes": [{"field": "messages", "value": each}]} for each in values]}


def event(mid, sender="123456789", **message):
    return {"sender": {"id": sender}, "recipient": {"id": PAGE}, "timestamp": 1760000000000,
            "message": {"mid": mid, "text": "hola", **message}}


def messenger(*events):
    return {"object": "page", "entry": [{"id": "1", "time": 1760000000000, "messaging": list(events)}]}


@pytest.fixture
def queue(monkeypatch):
    """What the broker was handed, in order. `queue.fail_at` makes it refuse from that message on."""
    sent = []

    async def publish(queue, message):
        if len(sent) + 1 == publish.fail_at:
            raise BrokerUnavailable(queue)
        sent.append(json.loads(message.to_json()))

    publish.fail_at, publish.sent = None, sent
    monkeypatch.setattr(ingestion, "send_message_to_queue", publish)
    return publish


@pytest.fixture
def graph_api(monkeypatch):
    profile = {"id": "123456789", "first_name": "Ana", "last_name": "Diaz", "profile_pic": "https://example.com/a.jpg"}
    get = lambda url, timeout: SimpleNamespace(status_code=200, text="{}", json=lambda: profile)  # noqa: E731
    monkeypatch.setattr(facebook.requests, "get", get)


@pytest.fixture
def bot(register):
    owner = TenantRepository(register("acme").client_id)
    return owner.add_bot(ChatBot(name="Bot", role="Sales Assistant", description="A test chatbot", tone="helpful",
                                 facebookToken="page-token"))


def notify(bot_id, payload):
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return META.post(f"/chat/{bot_id}", content=body,
                                           headers={"content-type": "application/json",
                                                    "X-Hub-Signature-256": signature})


def identifiers(queue):
    return [envelope["messageId"] for envelope in queue.sent]


def assert_they_match_the_contract(queue):
    assert [error.message for envelope in queue.sent for error in ENVELOPE.iter_errors(envelope)] == []


# --- One envelope for each message ---------------------------------------------------------------------------------

def test_two_whatsapp_messages_in_one_notification_are_two_envelopes(bot, queue):
    assert notify(bot.uuid, whatsapp(value("wamid.A", "wamid.B"))).status_code == 200

    assert identifiers(queue) == ["wamid.A", "wamid.B"]
    assert [envelope["jwe_body"]["message"]["messages"] for envelope in queue.sent] == [[text("wamid.A")],
                                                                                         [text("wamid.B")]]
    assert_they_match_the_contract(queue)


def test_the_messages_of_every_entry_of_a_whatsapp_notification_are_queued(bot, queue):
    assert notify(bot.uuid, whatsapp(value("wamid.A"), value("wamid.B"))).status_code == 200

    assert identifiers(queue) == ["wamid.A", "wamid.B"]


def test_two_messenger_messages_in_one_notification_are_two_envelopes(bot, queue, graph_api):
    assert notify(bot.uuid, messenger(event("m_A"), event("m_B"))).status_code == 200

    assert identifiers(queue) == ["m_A", "m_B"]
    assert [envelope["jwe_body"]["message"] for envelope in queue.sent] == [event("m_A"), event("m_B")]
    assert_they_match_the_contract(queue)


def test_each_envelope_of_a_notification_has_its_own_identifier_and_names_the_chatbot(bot, queue):
    notify(bot.uuid, whatsapp(value("wamid.A", "wamid.B")))

    first, second = queue.sent
    assert first["_uuid"] != second["_uuid"]
    assert {envelope["jwe_body"]["botId"] for envelope in queue.sent} == {str(bot.uuid)}


def test_the_one_who_wrote_several_messages_is_stored_once(bot, queue):
    notify(bot.uuid, whatsapp(value("wamid.A", "wamid.B")))

    assert Contact.objects.count() == 1


# --- Nothing for what is not a message from a person ---------------------------------------------------------------

def test_a_delivery_receipt_is_acknowledged_and_nothing_is_queued_or_stored(bot, queue):
    receipt = {"messaging_product": "whatsapp", "metadata": METADATA,
               "statuses": [{"id": "wamid.SENT", "status": "delivered", "recipient_id": "12025550143"}]}

    assert notify(bot.uuid, whatsapp(receipt)).status_code == 200
    assert (queue.sent, Contact.objects.count()) == ([], 0)


def test_the_echo_of_what_the_page_sent_is_not_queued_and_the_page_does_not_become_a_contact(bot, queue, graph_api):
    echo = event("m_ECHO", sender=PAGE, is_echo=True)

    assert notify(bot.uuid, messenger(echo)).status_code == 200
    assert (queue.sent, Contact.objects.count()) == ([], 0)


def test_a_button_press_in_messenger_is_acknowledged_and_not_queued(bot, queue, graph_api):
    press = event("m_unused")
    del press["message"]
    press["postback"] = {"title": "Empezar", "payload": "GET_STARTED", "mid": "m_PB"}

    assert notify(bot.uuid, messenger(press, event("m_A"))).status_code == 200
    assert identifiers(queue) == ["m_A"]


def test_a_whatsapp_message_without_the_number_it_was_sent_to_is_not_queued(bot, queue):
    without = value("wamid.A")
    del without["metadata"]

    assert notify(bot.uuid, whatsapp(without)).status_code == 200
    assert (queue.sent, Contact.objects.count()) == ([], 0)


def test_a_whatsapp_contact_without_a_name_is_queued_without_one(bot, queue):
    nameless = value("wamid.A")
    nameless["contacts"] = [{"wa_id": "12025550143"}]

    assert notify(bot.uuid, whatsapp(nameless)).status_code == 200

    assert queue.sent[0]["jwe_body"]["contact"] == {"name": None, "waId": "12025550143"}
    assert_they_match_the_contract(queue)


# --- The broker fails halfway --------------------------------------------------------------------------------------

def test_meta_is_asked_to_send_the_notification_again_if_one_of_its_messages_was_not_taken(bot, queue):
    queue.fail_at = 2

    assert notify(bot.uuid, whatsapp(value("wamid.A", "wamid.B"))).status_code == 503
    assert identifiers(queue) == ["wamid.A"]


def test_the_notification_sent_again_only_hands_over_what_was_not_handed_over(bot, queue):
    notification = whatsapp(value("wamid.A", "wamid.B"))
    queue.fail_at = 2
    notify(bot.uuid, copy.deepcopy(notification))
    queue.fail_at = None

    assert notify(bot.uuid, notification).status_code == 200

    # wamid.A was handed over and stored in the history the first time: sent again, it is not handed over twice.
    assert identifiers(queue) == ["wamid.A", "wamid.B"]
