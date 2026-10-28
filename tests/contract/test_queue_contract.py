# -*- coding: utf-8 -*-
"""
    What the gateway really publishes to the `llm` queue must match the AsyncAPI contract the response engine reads.
    The message is produced by the real processing code; only the database and the broker are replaced.
"""
import asyncio
import copy
import json
import pathlib
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml
from jsonschema import Draft202012Validator

import controller.ingestion as ingestion
import controller.messages as messages
from classes import FacebookRequest

ROOT = pathlib.Path(__file__).resolve().parents[2]
ASYNCAPI = yaml.safe_load((ROOT / "docs" / "02-design" / "contracts" / "asyncapi.yaml").read_text(encoding="utf-8"))
ENVELOPE = Draft202012Validator({"$ref": "#/components/schemas/SobreMensaje", "components": ASYNCAPI["components"]})

BOT_ID = "11111111-1111-4111-8111-111111111111"
CLIENT_ID = "22222222-2222-4222-8222-222222222222"
PAYLOADS = {
    "whatsapp": {"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "15550001111", "phone_number_id": "100000000000001"},
        "contacts": [{"profile": {"name": "Ana"}, "wa_id": "584120000000"}],
        "messages": [{"from": "584120000000", "id": "wamid.TEST", "timestamp": "1760000000", "type": "text",
                      "text": {"body": "hola"}}]}}]}]},
    "messenger": {"object": "page", "entry": [{"id": "1", "time": 1760000000000, "messaging": [{
        "sender": {"id": "123456789"}, "recipient": {"id": "987654321"}, "timestamp": 1760000000000,
        "message": {"mid": "m_TEST", "text": "hola"}}]}]},
}
# What Meta called each of those messages.
MESSAGE_ID = {"whatsapp": "wamid.TEST", "messenger": "m_TEST"}


@pytest.fixture
def published(monkeypatch):
    """Returns a function that runs a webhook payload through the gateway and gives back the queued message."""
    captured = []

    async def capture(queue, message):
        captured.append({"queue": queue, "body": json.loads(message.to_json())})

    contact = SimpleNamespace(firstName="Ana", waId="584120000000", fbId="123456789")
    monkeypatch.setattr(ingestion, "send_message_to_queue", capture)
    monkeypatch.setattr(ingestion, "ctr_identify_contact_by_wa_id", Mock(return_value=contact))
    monkeypatch.setattr(ingestion, "ctr_identify_fb_contact", Mock(return_value=contact))

    def run(payload):
        captured.clear()
        asyncio.run(messages.ctr_process_messages(req=FacebookRequest(**payload), _bot=SimpleNamespace(uuid=BOT_ID, clientId=CLIENT_ID)))
        (only,) = captured
        return dict(only)

    return run


@pytest.mark.parametrize("channel", sorted(PAYLOADS))
def test_published_message_matches_the_contract(published, channel):
    result = published(PAYLOADS[channel])

    assert result["queue"] == ASYNCAPI["channels"]["llm"]["address"]
    assert [error.message for error in ENVELOPE.iter_errors(result["body"])] == []


@pytest.mark.parametrize("channel", sorted(PAYLOADS))
def test_published_message_carries_the_target_bot(published, channel):
    assert published(PAYLOADS[channel])["body"]["jwe_body"]["botId"] == BOT_ID


@pytest.mark.parametrize("channel", sorted(PAYLOADS))
def test_published_message_says_whose_bot_it_is(published, channel):
    """The response engine holds one credential per client: this is how it knows which one to use."""
    assert published(PAYLOADS[channel])["body"]["jwe_body"]["clientId"] == CLIENT_ID


@pytest.mark.parametrize("channel", sorted(PAYLOADS))
def test_published_message_is_identified_by_what_meta_called_it(published, channel):
    body = published(PAYLOADS[channel])["body"]

    assert (body["schemaVersion"], body["messageId"]) == (1, MESSAGE_ID[channel])


def test_a_message_delivered_twice_keeps_its_identifier_in_two_different_envelopes(published):
    first, second = published(PAYLOADS["whatsapp"])["body"], published(PAYLOADS["whatsapp"])["body"]

    assert first["messageId"] == second["messageId"]
    assert first["_uuid"] != second["_uuid"]


def test_the_envelope_is_dated_when_it_is_created_whatever_the_time_zone_of_the_machine(published):
    before = time.time() * 1000
    body = published(PAYLOADS["whatsapp"])["body"]
    after = time.time() * 1000

    # The envelope keeps whole milliseconds.
    for field in ("createdAt", "statusDate"):
        assert before - 1 <= body[field]["$date"] <= after


def test_the_number_the_message_was_sent_to_is_published_as_an_object(published):
    content = published(PAYLOADS["whatsapp"])["body"]["jwe_body"]["message"]

    assert content["metadata"] == {"display_phone_number": "15550001111", "phone_number_id": "100000000000001"}


def invalid(published, change) -> bool:
    """Whether the contract turns down a published message once `change` has been applied to it."""
    body = copy.deepcopy(published(PAYLOADS["whatsapp"])["body"])
    change(body)
    return not ENVELOPE.is_valid(body)


def test_contract_rejects_a_message_without_the_target_bot(published):
    def change(body):
        del body["jwe_body"]["botId"]

    assert invalid(published, change)


def test_contract_rejects_a_message_that_does_not_say_whose_bot_it_is(published):
    def change(body):
        del body["jwe_body"]["clientId"]

    assert invalid(published, change)


def test_contract_rejects_an_unknown_channel(published):
    def change(body):
        body["jwe_body"]["type"] = "instagram"

    assert invalid(published, change)


def test_contract_rejects_a_message_without_the_identifier_of_meta(published):
    def change(body):
        del body["messageId"]

    assert invalid(published, change)


@pytest.mark.parametrize("version", [None, 0, 2, "1"])
def test_contract_rejects_any_other_version_of_the_envelope(published, version):
    def change(body):
        if version is None:
            del body["schemaVersion"]
        else:
            body["schemaVersion"] = version

    assert invalid(published, change)


def test_contract_rejects_the_metadata_as_the_list_of_pairs_it_used_to_be(published):
    def change(body):
        body["jwe_body"]["message"]["metadata"] = [["display_phone_number", "15550001111"],
                                                   ["phone_number_id", "100000000000001"]]

    assert invalid(published, change)


def test_contract_rejects_two_messages_in_one_envelope(published):
    def change(body):
        body["jwe_body"]["message"]["messages"] *= 2

    assert invalid(published, change)


def test_contract_accepts_the_message_those_changes_start_from(published):
    assert not invalid(published, lambda body: None)
