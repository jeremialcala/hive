# -*- coding: utf-8 -*-
"""
    The traces (I5, R4, RNF09, RS19, T28): a message can be followed from the webhook to the ingestion and on to the
    message identified, and no span carries what the logs may not. The spans are kept in memory and looked at.
"""
import asyncio
import hashlib
import hmac
import json
import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import app as app_module
from shared import bus
import controller.ingestion as ingestion
from shared import tracing
import webhook as webhook_module
from classes import ChatBot, Contact
from repository import TenantRepository

META = TestClient(webhook_module.app)
HTTP = TestClient(app_module.app)
APP_SECRET = os.environ["FACEBOOK_APP_SECRET"]

NUMBER, TEXT, NAME = "12025550143", "text-marker-de-un-mensaje", "Name-Marker"
QUERY_SECRET = "verify-token-marker"
SENT = (NUMBER, TEXT, NAME, QUERY_SECRET, APP_SECRET, "wamid.TRACE")

EXPORTED = InMemorySpanExporter()
tracing.provider().add_span_processor(SimpleSpanProcessor(EXPORTED))


@pytest.fixture(autouse=True)
def spans():
    EXPORTED.clear()
    yield EXPORTED
    EXPORTED.clear()


@pytest.fixture(autouse=True)
def queue(monkeypatch):
    sent = []

    async def publish(queue, message):
        sent.append(json.loads(message.to_json()))
    monkeypatch.setattr(ingestion, "send_message_to_queue", publish)
    return sent


class KeptEvents:
    def __init__(self):
        self.events = []

    async def publish(self, subject, payload, message_id, headers=None):
        self.events.append(SimpleNamespace(subject=subject, payload=json.loads(payload), headers=headers or {}))
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
def acme(register):
    return register("acme")


@pytest.fixture
def bot(acme):
    return TenantRepository(acme.client_id).add_bot(
        ChatBot(name="Bot", role="Sales Assistant", description="A test chatbot", tone="helpful"))


def whatsapp(message_id="wamid.TRACE"):
    return {"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "12025550101", "phone_number_id": "100000000000001"},
        "contacts": [{"profile": {"name": NAME}, "wa_id": NUMBER}],
        "messages": [{"from": NUMBER, "id": message_id, "timestamp": "1760000000", "type": "text",
                      "text": {"body": TEXT}}]}}]}]}


def notify(bot_id, payload):
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return META.post(f"/chat/{bot_id}", content=body,
                     headers={"content-type": "application/json", "X-Hub-Signature-256": signature})


def assert_they_follow_the_rule_of_the_logs(spans):
    finished = spans.get_finished_spans()
    assert finished, "no span was made"
    for span in finished:
        for key, value in span.attributes.items():
            assert key in tracing.ALLOWED, (span.name, key)
            assert not any(sent in str(value) for sent in SENT), (span.name, key)
        # Exceptions are never recorded: their text can carry anything.
        assert not [event for event in span.events if event.name == "exception"], span.name
        assert not any(sent in span.name for sent in SENT)


def by_name(spans, name):
    return [span for span in spans.get_finished_spans() if span.name == name]


# --- The rule of the logs ------------------------------------------------------------------------------------------

def test_a_notification_ingested_by_the_webhook_itself_leaves_no_personal_data_in_its_spans(bot, spans):
    assert notify(bot.uuid, whatsapp()).status_code == 200

    assert {"http.request", "ingest.notification", "ingest.message"} <= {span.name for span in
                                                                         spans.get_finished_spans()}
    message, = by_name(spans, "ingest.message")
    assert message.attributes["gateway.contact_id"] == str(Contact.objects.get()._uuid)
    assert message.attributes["gateway.outcome"] == "ingested"
    assert_they_follow_the_rule_of_the_logs(spans)


def test_the_span_of_a_request_names_its_route_never_its_address(bot, spans):
    response = META.get(f"/chat/{bot.uuid}", params={"hub.mode": "subscribe", "hub.challenge": "1",
                                                     "hub.verify_token": QUERY_SECRET})

    assert response.status_code == 403
    request, = by_name(spans, "http.request")
    assert request.attributes["http.route"] == "/chat/{bot_id}"
    assert request.attributes["gateway.bot_id"] == str(bot.uuid)
    assert request.attributes["http.response.status_code"] == 403
    assert_they_follow_the_rule_of_the_logs(spans)


def test_the_management_api_and_its_answers_follow_the_rule(acme, bot, spans):
    TenantRepository(acme.client_id).add_contact(bot.uuid, Contact(firstName=NAME, waId=NUMBER))

    headers = acme.auth
    HTTP.post("/contacts/search", headers=headers, json={"waId": NUMBER})
    HTTP.get(f"/chat/{bot.uuid}/contacts", headers=headers, params={"offset": 0, "limit": 5})

    # The two requests and the one for the access token (ADR-0025).
    assert len(by_name(spans, "http.request")) == 3
    assert_they_follow_the_rule_of_the_logs(spans)


def test_a_message_that_fails_says_the_kind_of_failure_and_not_its_text(bot, spans, monkeypatch):
    def fails(*args, **kwargs):
        raise ValueError(f"{NUMBER} {TEXT} is not something to keep")
    monkeypatch.setattr(TenantRepository, "find_contact_by_wa_id", fails)

    notify(bot.uuid, whatsapp())

    message, = by_name(spans, "ingest.message")
    assert (message.attributes["error.type"], message.attributes["gateway.outcome"]) == ("ValueError", "failed")
    assert message.status.status_code.name == "ERROR" and not message.status.description
    assert_they_follow_the_rule_of_the_logs(spans)


# --- From the webhook to the engine, one trace ---------------------------------------------------------------------

def test_the_event_the_webhook_publishes_goes_on_with_the_trace_of_its_request(bot, bifrost, spans):
    notify(bot.uuid, whatsapp())

    request, = by_name(spans, "http.request")
    event, = bifrost.events
    version, trace_id, parent_id, flags = event.headers["traceparent"].split("-")
    assert trace_id == format(request.context.trace_id, "032x")
    assert parent_id == format(request.context.span_id, "016x")


def test_the_ingestion_goes_on_with_the_trace_of_the_event_and_hands_it_to_the_engine(bot, bifrost, spans):
    notify(bot.uuid, whatsapp())
    event, = bifrost.events
    request, = by_name(spans, "http.request")

    # The worker, as it gets the event from WEBHOOK.
    asyncio.run(ingestion.ingest_notification(event.payload, event.headers["traceparent"]))

    notification, = by_name(spans, "ingest.notification")
    message, = by_name(spans, "ingest.message")
    identified = bifrost.events[1]
    assert notification.context.trace_id == message.context.trace_id == request.context.trace_id
    assert notification.parent.span_id == request.context.span_id
    assert identified.headers["traceparent"].split("-")[1] == format(request.context.trace_id, "032x")
    assert identified.headers["traceparent"].split("-")[2] == format(message.context.span_id, "016x")
    assert_they_follow_the_rule_of_the_logs(spans)


# --- What may go on a span -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("key,value", [
    ("gateway.bot_id", "6f0c2c1e-0000-4000-8000-000000000001"),
    ("gateway.messages", 3),
    ("gateway.channel", "whatsapp"),
    ("http.route", "/chat/{bot_id}/contacts/{contact_id}/turns"),
    ("http.route", "/health"),
    ("messaging.destination.name", "WEBHOOK"),
    ("messaging.destination.name", "gateway.message.5.6f0c2c1e-0000-4000-8000-000000000001"
                                   ".6f0c2c1e-0000-4000-8000-000000000002"),
    ("messaging.destination.name", "gateway.contact.erased"),
    ("error.type", "ServerSelectionTimeoutError"),
])
def test_what_the_service_itself_makes_goes_on_a_span(key, value):
    assert tracing.safe(key, value)


@pytest.mark.parametrize("key,value", [
    ("gateway.bot_id", NUMBER),
    ("gateway.contact_id", "not-a-uuid"),
    ("gateway.messages", "3"),
    ("gateway.messages", True),
    ("gateway.channel", "telegram"),
    ("http.route", f"/chat/{NUMBER}"),
    ("http.route", "/chat/x?hub.verify_token=secret"),
    ("messaging.destination.name", f"webhook.meta.{NUMBER}"),
    ("error.type", f"ValueError: {NUMBER}"),
    ("http.url", "https://bots.example.com/chat/x"),
    ("enduser.id", NUMBER),
], ids=lambda value: str(value)[:30])
def test_anything_else_is_kept_out(key, value):
    assert not tracing.safe(key, value)


def test_an_attribute_kept_out_leaves_its_key_in_the_log_never_its_value(spans, caplog):
    with tracing.span("test.span", **{"enduser.id": NUMBER, "gateway.channel": "whatsapp"}) as current:
        current.set(**{"gateway.contact_id": NUMBER})

    test_span, = by_name(spans, "test.span")
    assert dict(test_span.attributes) == {"gateway.channel": "whatsapp"}
    assert NUMBER not in caplog.text
    assert "An attribute was kept out of a span: not allowed" in caplog.text
    assert "An attribute was kept out of a span: gateway.contact_id" in caplog.text


def test_outside_a_span_there_is_no_context_to_hand_on():
    assert tracing.traceparent() is None


def test_the_health_check_is_not_traced(spans):
    """Docker calls it every 30 s on each service: a trace of it would only fill Saga."""
    assert META.get("/health").status_code == 200
    assert HTTP.get("/health").status_code == 200

    assert by_name(spans, "http.request") == []


def test_a_block_that_is_not_traced_runs_the_same_and_keeps_nothing(spans):
    with tracing.span("test.untraced", traced=False, **{"gateway.channel": "whatsapp"}) as current:
        current.set(**{"gateway.messages": 1})
        current.failed("ValueError")

    assert spans.get_finished_spans() == ()
