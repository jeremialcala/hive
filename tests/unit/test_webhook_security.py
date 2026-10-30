# -*- coding: utf-8 -*-
import asyncio
import hashlib
import hmac
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock
from uuid import uuid4

import pika
import pytest
from fastapi.testclient import TestClient

import webhook as webhook_module
import controller.amqp_broker as broker
import controller.ingestion as ingestion
import controller.messages as messages
from classes import FacebookRequest
from enums import Status
from controller import BrokerUnavailable
from utils import is_valid_signature

APP_SECRET = os.environ["FACEBOOK_APP_SECRET"]

BOT_ID = str(uuid4())
WEBHOOK = f"/chat/{BOT_ID}"
VERIFY_TOKEN = "the-verify-token-of-this-bot"

WHATSAPP_PAYLOAD = {
    "object": "whatsapp_business_account",
    "entry": [{
        "id": "101831186329557",
        "changes": [{
            "field": "messages",
            "value": {
                "messaging_product": "whatsapp",
                "metadata": {"display_phone_number": "15550001111", "phone_number_id": "100000000000001"},
                "contacts": [{"profile": {"name": "Ana"}, "wa_id": "584120000000"}],
                "messages": [{"from": "584120000000", "id": "wamid.TEST", "type": "text", "text": {"body": "hola"}}],
            },
        }],
    }],
}
BODY = json.dumps(WHATSAPP_PAYLOAD).encode()
JSON = {"content-type": "application/json"}


def sign(body: bytes, secret: str = APP_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def client():
    return TestClient(webhook_module.app)


@pytest.fixture
def bot(monkeypatch):
    _bot = SimpleNamespace(uuid=BOT_ID, clientId=uuid4(), verifyToken=VERIFY_TOKEN, appSecret=None,
                           status=Status.ACT.value)
    monkeypatch.setattr(webhook_module, "find_webhook_bot", lambda bot_id: _bot)
    # Read when asked for, not now: the tests change the secrets of the bot after this.
    monkeypatch.setattr(webhook_module, "webhook_secrets",
                        lambda bot: SimpleNamespace(verify_token=bot.verifyToken, app_secret=bot.appSecret))
    return _bot


@pytest.fixture
def process(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(webhook_module, "ctr_process_messages", mock)
    return mock


# --- X-Hub-Signature-256 ---------------------------------------------------------------------------------------------

def test_signature_matches_only_the_signed_body():
    assert is_valid_signature(APP_SECRET, BODY, sign(BODY))
    assert not is_valid_signature(APP_SECRET, BODY + b" ", sign(BODY))
    assert not is_valid_signature("another-secret", BODY, sign(BODY))


@pytest.mark.parametrize("header", [None, "", "sha256=", sign(BODY)[len("sha256="):], "sha1=" + "0" * 40, "sha256=ñ"])
def test_signature_rejects_malformed_headers(header):
    assert not is_valid_signature(APP_SECRET, BODY, header)


@pytest.mark.parametrize("secret", [None, ""])
def test_signature_is_never_valid_without_a_secret(secret):
    assert not is_valid_signature(secret, BODY, sign(BODY, secret=""))


# --- POST /chat/{bot_id}: inbound notifications ------------------------------------------------------------------------

def test_signed_notification_is_processed(client, bot, process):
    response = client.post(WEBHOOK, content=BODY, headers={**JSON, "X-Hub-Signature-256": sign(BODY)})

    assert response.status_code == 200
    process.assert_awaited_once()


@pytest.mark.parametrize("headers", [
    {},
    {"X-Hub-Signature-256": sign(BODY, secret="not-the-app-secret")},
    {"X-Hub-Signature-256": sign(b'{"object": "page", "entry": []}')},
    {"Authorization": "Bearer a-token-is-not-a-signature"},
], ids=["unsigned", "wrong secret", "signature of another body", "a token is not a signature"])
def test_notification_without_a_valid_signature_is_rejected(client, bot, process, headers):
    response = client.post(WEBHOOK, content=BODY, headers={**JSON, **headers})

    assert response.status_code == 403
    process.assert_not_awaited()


def test_bot_app_secret_overrides_the_global_one(client, bot, process):
    bot.appSecret = "secret-of-this-bot"

    rejected = client.post(WEBHOOK, content=BODY, headers={**JSON, "X-Hub-Signature-256": sign(BODY)})
    accepted = client.post(WEBHOOK, content=BODY,
                           headers={**JSON, "X-Hub-Signature-256": sign(BODY, secret="secret-of-this-bot")})

    assert (rejected.status_code, accepted.status_code) == (403, 200)
    process.assert_awaited_once()


def test_notification_is_rejected_when_no_secret_is_configured(client, bot, process, monkeypatch):
    monkeypatch.setattr(webhook_module.settings, "facebook_app_secret", None)

    response = client.post(WEBHOOK, content=BODY, headers={**JSON, "X-Hub-Signature-256": sign(BODY, secret="")})

    assert response.status_code == 403
    process.assert_not_awaited()


def test_notification_for_an_unknown_bot_is_not_found(client, process):
    response = client.post(WEBHOOK, content=BODY, headers={**JSON, "X-Hub-Signature-256": sign(BODY)})

    assert response.status_code == 404
    process.assert_not_awaited()


def test_notification_for_a_malformed_bot_id_is_not_found(client, process):
    response = client.post("/chat/not-a-uuid", content=BODY, headers={**JSON, "X-Hub-Signature-256": sign(BODY)})

    assert response.status_code == 404
    process.assert_not_awaited()


def test_broker_failure_asks_meta_to_retry(client, bot, process):
    process.side_effect = BrokerUnavailable("llm")

    response = client.post(WEBHOOK, content=BODY, headers={**JSON, "X-Hub-Signature-256": sign(BODY)})

    assert response.status_code == 503


# --- GET /chat/{bot_id}: webhook verification ------------------------------------------------------------------------

def test_verification_echoes_the_challenge(client, bot):
    response = client.get(WEBHOOK, params={"hub.mode": "subscribe", "hub.verify_token": VERIFY_TOKEN,
                                           "hub.challenge": "1158201444"})

    assert response.status_code == 200
    assert response.text == "1158201444"
    assert response.headers["content-type"].startswith("text/plain")


@pytest.mark.parametrize("params", [
    {"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "1"},
    {"hub.mode": "subscribe", "hub.challenge": "1"},
    {"hub.mode": "unsubscribe", "hub.verify_token": VERIFY_TOKEN, "hub.challenge": "1"},
    {"hub.verify_token": VERIFY_TOKEN, "hub.challenge": "1"},
    {"hub.mode": "subscribe", "hub.verify_token": VERIFY_TOKEN},
    {},
], ids=["wrong token", "no token", "wrong mode", "no mode", "no challenge", "no params"])
def test_verification_is_rejected(client, bot, params):
    assert client.get(WEBHOOK, params=params).status_code == 403


@pytest.mark.parametrize("token", ["", "None"])
def test_verification_never_passes_for_a_bot_without_verify_token(client, bot, token):
    bot.verifyToken = None

    response = client.get(WEBHOOK, params={"hub.mode": "subscribe", "hub.verify_token": token, "hub.challenge": "1"})

    assert response.status_code == 403


# --- The health check ------------------------------------------------------------------------------------------------

def test_health_answers_without_token_nor_database(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# --- No silent message loss ------------------------------------------------------------------------------------------

@pytest.fixture
def whatsapp_message(monkeypatch):
    monkeypatch.setattr(ingestion, "ctr_identify_contact_by_wa_id",
                        Mock(return_value=SimpleNamespace(firstName="Ana", waId="584120000000")))
    return FacebookRequest(**WHATSAPP_PAYLOAD)


# A live chatbot: its notification does not also activate it (HIG-75).
LIVE = SimpleNamespace(uuid=BOT_ID, clientId=BOT_ID, status=Status.ACT.value)


def test_processing_waits_for_the_broker(whatsapp_message, monkeypatch):
    send = AsyncMock()
    monkeypatch.setattr(ingestion, "send_message_to_queue", send)

    asyncio.run(messages.ctr_process_messages(req=whatsapp_message, _bot=LIVE))

    send.assert_awaited_once()
    assert send.await_args.kwargs["queue"] == "llm"


def test_processing_does_not_swallow_a_broker_failure(whatsapp_message, monkeypatch):
    monkeypatch.setattr(ingestion, "send_message_to_queue", AsyncMock(side_effect=BrokerUnavailable("llm")))

    with pytest.raises(BrokerUnavailable):
        asyncio.run(messages.ctr_process_messages(req=whatsapp_message, _bot=LIVE))


def publish(monkeypatch, connection):
    monkeypatch.setattr(broker.pika, "BlockingConnection", connection)
    message = SimpleNamespace(to_json=lambda: "{}")
    asyncio.run(broker.send_message_to_queue(queue="llm", message=message))


def test_publish_is_confirmed_by_the_broker(monkeypatch):
    connection = MagicMock()
    channel = connection.return_value.channel.return_value

    publish(monkeypatch, connection)

    channel.confirm_delivery.assert_called_once()
    assert channel.basic_publish.call_args.kwargs["mandatory"] is True
    connection.return_value.close.assert_called_once()


def test_a_published_message_is_persistent_and_expires_after_24_hours(monkeypatch):
    connection = MagicMock()

    publish(monkeypatch, connection)

    properties = connection.return_value.channel.return_value.basic_publish.call_args.kwargs["properties"]
    assert (properties.delivery_mode, properties.expiration) == (2, "86400000")


def test_unreachable_broker_is_reported(monkeypatch):
    with pytest.raises(BrokerUnavailable):
        publish(monkeypatch, MagicMock(side_effect=pika.exceptions.AMQPConnectionError()))


def test_message_refused_by_the_broker_is_reported(monkeypatch):
    connection = MagicMock()
    connection.return_value.channel.return_value.basic_publish.side_effect = pika.exceptions.NackError([])

    with pytest.raises(BrokerUnavailable):
        publish(monkeypatch, connection)

    connection.return_value.close.assert_called_once()
