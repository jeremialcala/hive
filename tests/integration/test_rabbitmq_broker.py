# -*- coding: utf-8 -*-
"""
    Publishing against a real RabbitMQ, the same version as the one in production: what the broker keeps, when it
    lets go of it, and that the gateway and the response engine can both open the same queue.

    It needs a RabbitMQ with the user the tests are configured with:

        docker run --rm -p 127.0.0.1:25672:5672 -e RABBITMQ_DEFAULT_USER=test -e RABBITMQ_DEFAULT_PASS=test rabbitmq:4.3.5
        RABBITMQ_TEST_ADDR=127.0.0.1:25672 pytest tests/integration

    Without that variable the tests are skipped; with RABBITMQ_TEST_REQUIRED set they fail instead, which is how
    the pipeline makes sure they are not skipped there. Every test works on a queue of its own and deletes it.
"""
import asyncio
import functools
import hashlib
import hmac
import json
import os
import pathlib
import time
from uuid import uuid4

import pika
import pytest
import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

import webhook as webhook_module
import controller.amqp_broker as broker
import controller.ingestion as ingestion
from classes import ChatBot, QueueMessage
from controller import BrokerUnavailable
from repository import TenantRepository

# What Meta calls is its own service, apart from the management API.
META = TestClient(webhook_module.app)

ADDRESS = os.environ.get("RABBITMQ_TEST_ADDR")
APP_SECRET = os.environ["FACEBOOK_APP_SECRET"]
ROOT = pathlib.Path(__file__).resolve().parents[2]
ASYNCAPI = yaml.safe_load((ROOT / "docs" / "02-design" / "contracts" / "asyncapi.yaml").read_text(encoding="utf-8"))
ENVELOPE = Draft202012Validator({"$ref": "#/components/schemas/SobreMensaje", "components": ASYNCAPI["components"]})
DAY = str(ASYNCAPI["operations"]["publicarMensaje"]["bindings"]["amqp"]["expiration"])


@pytest.fixture(scope="module")
def address():
    if not ADDRESS:
        if os.environ.get("RABBITMQ_TEST_REQUIRED"):
            pytest.fail("RABBITMQ_TEST_ADDR is required here")
        pytest.skip("No RabbitMQ to test against: set RABBITMQ_TEST_ADDR")
    host, _, port = ADDRESS.partition(":")
    return host, int(port or 5672)


@pytest.fixture
def rabbit(address, monkeypatch):
    """Points the gateway at the test broker and returns a connection of the test's own to look at it with."""
    host, port = address
    parameters = functools.partial(pika.ConnectionParameters, port=port)
    monkeypatch.setattr(broker.settings, "qms_server", host)
    # The gateway has no setting for the port: it is added to whatever parameters it asks for.
    monkeypatch.setattr(broker.pika, "ConnectionParameters", parameters)
    credentials = pika.PlainCredentials(broker.settings.qms_user, broker.settings.qms_password)
    connection = pika.BlockingConnection(parameters(host=host, credentials=credentials))
    yield connection
    connection.close()


@pytest.fixture
def queue(rabbit):
    name = f"test-{uuid4()}"
    yield name
    # On a channel of its own: a test may have had its channel closed by the broker.
    rabbit.channel().queue_delete(queue=name)


def envelope(message_id="wamid.TEST") -> QueueMessage:
    return QueueMessage(messageId=message_id, jwe_body={"botId": str(uuid4())})


def publish(queue, message=None):
    asyncio.run(broker.send_message_to_queue(queue=queue, message=message or envelope()))


def waiting(rabbit, queue) -> int:
    return rabbit.channel().queue_declare(queue=queue, passive=True).method.message_count


def take(rabbit, queue):
    _, properties, body = rabbit.channel().basic_get(queue, auto_ack=True)
    return properties, body and json.loads(body)


# --- What the broker keeps -------------------------------------------------------------------------------------------

def test_the_broker_keeps_the_envelope_as_published_persistent_and_with_a_day_to_live(rabbit, queue):
    message = envelope()

    publish(queue, message)

    properties, body = take(rabbit, queue)
    assert body == json.loads(message.to_json())
    assert (properties.delivery_mode, properties.expiration) == (2, DAY) == (2, "86400000")


def test_a_message_nobody_took_in_time_is_gone_and_one_still_in_time_stays(rabbit, queue, monkeypatch):
    with monkeypatch.context() as short:
        short.setattr(broker, "MESSAGE_TTL", "300")
        publish(queue, envelope("wamid.EXPIRES"))
    publish(queue, envelope("wamid.STAYS"))
    assert waiting(rabbit, queue) == 2

    deadline = time.monotonic() + 10
    while waiting(rabbit, queue) != 1 and time.monotonic() < deadline:
        time.sleep(0.1)

    assert waiting(rabbit, queue) == 1
    assert take(rabbit, queue)[1]["messageId"] == "wamid.STAYS"


# --- One queue, two services -----------------------------------------------------------------------------------------

def test_the_response_engine_can_open_the_queue_the_gateway_published_to(rabbit, queue):
    publish(queue)

    # Declared the way ms-python-metamsghandler does it. A mismatch closes the channel with PRECONDITION_FAILED.
    declared = rabbit.channel().queue_declare(queue=queue, auto_delete=False, durable=True)

    assert declared.method.message_count == 1


def test_the_gateway_can_publish_to_a_queue_the_response_engine_opened_first(rabbit, queue):
    rabbit.channel().queue_declare(queue=queue, auto_delete=False, durable=True)

    publish(queue)

    assert waiting(rabbit, queue) == 1


def test_an_expiration_set_on_the_queue_would_not_fit_the_queue_that_already_exists(rabbit, queue):
    """Why the expiration goes on each message: neither service has to declare the queue again."""
    publish(queue)

    with pytest.raises(pika.exceptions.ChannelClosedByBroker) as refused:
        rabbit.channel().queue_declare(queue=queue, durable=True, arguments={"x-message-ttl": int(DAY)})

    assert refused.value.reply_code == 406


# --- When the broker says no -----------------------------------------------------------------------------------------

def test_a_broker_that_does_not_let_the_gateway_in_is_reported_as_unavailable(rabbit, queue, monkeypatch):
    monkeypatch.setattr(broker.settings, "qms_password", "not-the-password")

    with pytest.raises(BrokerUnavailable):
        publish(queue)


def test_nobody_listening_at_the_address_is_reported_as_unavailable(rabbit, queue, monkeypatch):
    monkeypatch.setattr(broker.pika, "ConnectionParameters", functools.partial(pika.ConnectionParameters, port=1))

    with pytest.raises(BrokerUnavailable):
        publish(queue)


# --- From the notification to the queue ------------------------------------------------------------------------------

def test_a_signed_notification_with_two_messages_ends_up_as_two_envelopes_in_the_queue(rabbit, queue, register,
                                                                                         monkeypatch):
    monkeypatch.setattr(ingestion, "QUEUE", queue)
    owner = TenantRepository(register("acme").client_id)
    bot = owner.add_bot(ChatBot(name="Bot", role="Sales Assistant", description="A test chatbot", tone="helpful"))
    payload = {"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "12025550101", "phone_number_id": "100000000000001"},
        "contacts": [{"profile": {"name": "Ana"}, "wa_id": "12025550143"}],
        "messages": [{"from": "12025550143", "id": message_id, "timestamp": "1760000000", "type": "text",
                      "text": {"body": "hola"}} for message_id in ("wamid.A", "wamid.B")]}}]}]}
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()

    response = META.post(f"/chat/{bot.uuid}", content=body, headers={
        "content-type": "application/json", "X-Hub-Signature-256": signature})

    assert response.status_code == 200
    taken = [take(rabbit, queue) for _ in range(2)]
    assert waiting(rabbit, queue) == 0
    assert [(properties.expiration, received["messageId"]) for properties, received in taken] == [
        (DAY, "wamid.A"), (DAY, "wamid.B")]
    assert [error.message for _, received in taken for error in ENVELOPE.iter_errors(received)] == []
    assert {received["jwe_body"]["botId"] for _, received in taken} == {str(bot.uuid)}
