# -*- coding: utf-8 -*-
"""
    The contract of the bus, version 2 (asyncapi-v2.yaml). The file is kept coherent: every reference points
    somewhere, and every schema is a valid JSON Schema. Of its events, the webhook's is published since I3, and what
    the code builds is validated against it here; the rest are still a design.
"""
import pathlib
from datetime import datetime, timezone
from uuid import uuid4

import pytest
import yaml
from jsonschema import Draft202012Validator

from classes import ChatBot
from controller.incoming import IncomingMessage
from controller.webhook_event import CHANNELS, SCHEMA_VERSION, event_message_id, traceparent, webhook_event

CONTRACT = pathlib.Path(__file__).resolve().parents[2] / "docs" / "02-design" / "contracts" / "asyncapi-v2.yaml"
DOCUMENT = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))


def references(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref":
                yield value
            else:
                yield from references(value)
    elif isinstance(node, list):
        for value in node:
            yield from references(value)


def resolve(pointer: str):
    assert pointer.startswith("#/"), pointer
    node = DOCUMENT
    for part in pointer[2:].split("/"):
        node = node[part]
    return node


def test_it_is_version_2_of_the_bus():
    assert (DOCUMENT["asyncapi"], DOCUMENT["info"]["version"]) == ("3.0.0", "2.0.0")
    assert DOCUMENT["servers"]["bifrost"]["protocol"] == "nats"


@pytest.mark.parametrize("pointer", sorted(set(references(DOCUMENT))))
def test_every_reference_points_somewhere(pointer):
    assert resolve(pointer) is not None


@pytest.mark.parametrize("name", sorted(DOCUMENT["components"]["schemas"]))
def test_every_schema_is_valid(name):
    Draft202012Validator.check_schema(DOCUMENT["components"]["schemas"][name])


def test_every_channel_parameter_is_declared():
    for name, channel in DOCUMENT["channels"].items():
        named = {part[1:-1] for part in channel["address"].split(".") if part.startswith("{")}
        assert named == set(channel.get("parameters", {})), name


# --- The webhook's event, as the code builds it ----------------------------------------------------------------------

def validator(name: str) -> Draft202012Validator:
    return Draft202012Validator({"$ref": f"#/components/schemas/{name}", "components": DOCUMENT["components"]})


BOT = ChatBot(uuid=uuid4(), clientId=uuid4(), name="Bot", role="Sales Assistant", description="A bot", tone="helpful")
MESSAGES = [IncomingMessage(message_id="wamid.A", sender="12025550143", name="Ana", content={"messages": []})]


def built() -> dict:
    return webhook_event("whatsapp", BOT, MESSAGES, datetime.now(timezone.utc))


def test_the_event_the_webhook_builds_is_the_one_of_the_contract():
    assert [error.message for error in validator("EventoWebhook").iter_errors(built())] == []


def test_its_headers_are_the_ones_of_the_contract():
    headers = {"Nats-Msg-Id": event_message_id(BOT, MESSAGES), "traceparent": traceparent()}

    assert [error.message for error in validator("Cabeceras").iter_errors(headers)] == []


@pytest.mark.parametrize("broken", [
    lambda event: event.pop("receivedAt"),
    lambda event: event.update(messages=[]),
    lambda event: event.update(schemaVersion=1),
    lambda event: event.update(channel="telegram"),
])
def test_an_event_that_strays_from_the_contract_is_caught(broken):
    """The validation above would also pass on nothing: this is what shows that it does not."""
    event = built()
    broken(event)

    assert list(validator("EventoWebhook").iter_errors(event))


def test_the_channels_and_the_version_are_the_ones_of_the_contract():
    assert set(CHANNELS.values()) <= set(DOCUMENT["components"]["parameters"]["canal"]["enum"])
    assert SCHEMA_VERSION == DOCUMENT["components"]["schemas"]["Base"]["properties"]["schemaVersion"]["const"]
