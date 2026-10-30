# -*- coding: utf-8 -*-
"""
    The ingestion worker against a real NATS JetStream set up like Bifröst (nats-test.conf): the webhook's user
    publishes a notification, and the worker's user reads it with the consumer `ingesta`, publishes the message
    identified on MENSAJES and acknowledges the event, with the permissions Bifröst gives each of them. The database
    is the in-memory one and Hermod is replaced.

        docker run --rm -p 127.0.0.1:24222:4222 -p 127.0.0.1:28222:8222 \
            -v "$PWD/tests/integration/nats-test.conf:/etc/nats/test.conf:ro" nats:2.14.8-alpine -c /etc/nats/test.conf
        NATS_TEST_ADDR=127.0.0.1:24222 pytest tests/integration/test_ingest_worker.py

    Skipped without NATS_TEST_ADDR; with NATS_TEST_REQUIRED set it fails instead.
"""
import asyncio
import json
import os
from datetime import datetime, timezone
from uuid import uuid4

import nats
import pytest
from nats.js.api import AckPolicy, ConsumerConfig, RetentionPolicy, StreamConfig

from shared import bus
import controller.ingestion as ingestion
import ingest
from shared.bus.nats_jetstream import NatsPublisher
from classes import ChatBot, Contact, ConversationMessage
from controller.incoming import IncomingMessage
from controller.webhook_event import event_message_id, webhook_event
from repository import TenantRepository

ADDRESS = os.environ.get("NATS_TEST_ADDR")
NUMBER = "12025550143"


@pytest.fixture(scope="module")
def url():
    if not ADDRESS:
        if os.environ.get("NATS_TEST_REQUIRED"):
            pytest.fail("NATS_TEST_ADDR is required here")
        pytest.skip("No NATS to test against: set NATS_TEST_ADDR")
    return f"nats://{ADDRESS}"


async def _admin(url, work):
    connection = await nats.connect(url, user="admin", password="test")
    try:
        return await work(connection.jetstream())
    finally:
        await connection.close()


def admin(url, work):
    return asyncio.run(_admin(url, work))


@pytest.fixture
def streams(url):
    """WEBHOOK with its consumer `ingesta`, and MENSAJES, as Bifröst has them (repository bifrost, jetstream/)."""
    backoff = [30, 60, 300, 900, 1800, 3600]

    async def create(js):
        for name in ("WEBHOOK", "MENSAJES", "RESPUESTAS"):
            try:
                await js.delete_stream(name)
            except nats.js.errors.NotFoundError:
                # Not there from an earlier run: nothing to delete.
                pass
        await js.add_stream(StreamConfig(name="WEBHOOK", subjects=["webhook.meta.>"],
                                         retention=RetentionPolicy.WORK_QUEUE, max_age=7 * 24 * 3600,
                                         duplicate_window=120))
        await js.add_stream(StreamConfig(name="MENSAJES", subjects=["gateway.message.>"], max_age=24 * 3600,
                                         duplicate_window=120))
        await js.add_stream(StreamConfig(name="RESPUESTAS", subjects=["engine.reply.sent.>"],
                                         retention=RetentionPolicy.WORK_QUEUE, max_age=24 * 3600,
                                         duplicate_window=120))
        for stream, durable in (("WEBHOOK", "ingesta"), ("RESPUESTAS", "historial")):
            await js.add_consumer(stream, ConsumerConfig(durable_name=durable, ack_policy=AckPolicy.EXPLICIT,
                                                         ack_wait=backoff[0], max_deliver=1000, backoff=backoff,
                                                         max_ack_pending=100))
    admin(url, create)
    yield
    admin(url, lambda js: asyncio.gather(*(js.delete_stream(name) for name in ("WEBHOOK", "MENSAJES", "RESPUESTAS"))))


@pytest.fixture
def queue(monkeypatch):
    sent = []

    async def publish(queue, message):
        sent.append(json.loads(message.to_json()))
    monkeypatch.setattr(ingestion, "send_message_to_queue", publish)
    return sent


@pytest.fixture
def bot(register):
    return TenantRepository(register("acme").client_id).add_bot(
        ChatBot(name="Bot", role="Sales Assistant", description="A test chatbot", tone="helpful"))


def message(message_id) -> IncomingMessage:
    return IncomingMessage(message_id=message_id, sender=NUMBER, name="Ana", content={
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "12025550101", "phone_number_id": "100000000000001"},
        "contacts": [{"profile": {"name": "Ana"}, "wa_id": NUMBER}],
        "messages": [{"from": NUMBER, "id": message_id, "timestamp": "1760000000", "type": "text",
                      "text": {"body": "hola"}}]})


def as_the_webhook(url) -> NatsPublisher:
    return NatsPublisher(url, "webhook", "test", inbox_prefix="_INBOX_webhook", name=f"webhook-{uuid4()}")


def as_the_worker(url) -> NatsPublisher:
    return NatsPublisher(url, "ingesta", "test", inbox_prefix="_INBOX_ingesta", name=f"ingest-{uuid4()}")


async def _publish_and_ingest(url, bot, messages_) -> list[str]:
    webhook = as_the_webhook(url)
    event = webhook_event("whatsapp", bot, messages_, datetime.now(timezone.utc))
    await webhook.publish("webhook.meta.whatsapp", json.dumps(event).encode(), event_message_id(bot, messages_),
                          headers={"traceparent": "00-" + "a" * 32 + "-" + "b" * 16 + "-01"})
    await webhook.close()

    bus.configure(as_the_worker(url))
    connection = await ingest.connect(url, "ingesta", "test", None, "_INBOX_ingesta")
    try:
        subscription = await ingest.bind(connection, ingest.NOTIFICATIONS)
        delivered = await subscription.fetch(10, timeout=5)
        return [await ingest.handle(each, ingest.NOTIFICATIONS) for each in delivered]
    finally:
        await connection.drain()
        await bus.close()
        bus.configure(None)


def kept(url, stream) -> list:
    async def read(js):
        info = await js.stream_info(stream)
        if not info.state.messages:
            return []
        return [await js.get_msg(stream, sequence) for sequence in range(info.state.first_seq,
                                                                          info.state.last_seq + 1)]
    return admin(url, read)


def test_the_worker_ingests_what_the_webhook_published_with_the_users_bifrost_gives_them(url, streams, bot, queue):
    outcome = asyncio.run(_publish_and_ingest(url, bot, [message("wamid.A"), message("wamid.B")]))

    assert outcome == ["acknowledged"]
    contact = Contact.objects.get()
    identified = kept(url, "MENSAJES")
    assert [m.headers["Nats-Msg-Id"] for m in identified] == [f"{bot.uuid}:wamid.A", f"{bot.uuid}:wamid.B"]
    assert {m.subject.rsplit(".", 2)[1:] == [str(bot.uuid), str(contact._uuid)] for m in identified} == {True}
    # The trace of the event goes on, from a span of the ingestion.
    assert identified[0].headers["traceparent"].split("-")[1] == "a" * 32
    assert [envelope["messageId"] for envelope in queue] == ["wamid.A", "wamid.B"]
    assert ConversationMessage.objects.count() == 2


def test_an_acknowledged_event_leaves_the_work_queue(url, streams, bot, queue):
    asyncio.run(_publish_and_ingest(url, bot, [message("wamid.A")]))

    for _ in range(20):
        if not kept(url, "WEBHOOK"):
            break
        asyncio.run(asyncio.sleep(0.1))
    assert kept(url, "WEBHOOK") == []


def test_the_worker_may_not_make_a_consumer_of_its_own(url, streams):
    async def try_to_make_one():
        connection = await nats.connect(url, user="ingesta", password="test", inbox_prefix="_INBOX_ingesta")
        try:
            await connection.jetstream().add_consumer("WEBHOOK", ConsumerConfig(durable_name="another"),
                                                      timeout=2)
        finally:
            await connection.close()

    with pytest.raises((nats.errors.Error, asyncio.TimeoutError)):
        asyncio.run(try_to_make_one())


# --- The answers the engine sent, to the history -----------------------------------------------------------------

async def _answer_and_ingest(url, answer: dict) -> list[str]:
    """The notifier's user is not in the test server: the answer is published as the operator."""
    connection = await nats.connect(url, user="admin", password="test")
    await connection.jetstream().publish(f"engine.reply.sent.{answer['botId']}", json.dumps(answer).encode(),
                                         headers={"Nats-Msg-Id": f"{answer['botId']}:{answer['messageId']}"})
    await connection.close()

    worker = await ingest.connect(url, "ingesta", "test", None, "_INBOX_ingesta")
    try:
        subscription = await ingest.bind(worker, ingest.ANSWERS)
        delivered = await subscription.fetch(10, timeout=5)
        return [await ingest.handle(each, ingest.ANSWERS) for each in delivered]
    finally:
        await worker.drain()


def test_the_worker_stores_the_answer_the_engine_sent_with_the_user_bifrost_gives_it(url, streams, bot, queue):
    asyncio.run(_publish_and_ingest(url, bot, [message("wamid.A")]))
    contact = Contact.objects.get()
    answer = {"schemaVersion": 2, "botId": str(bot.uuid), "clientId": str(bot.clientId),
              "contactId": str(contact._uuid), "channel": "whatsapp", "inReplyTo": "wamid.A",
              "messageId": "wamid.ANSWER", "sentAt": datetime.now(timezone.utc).isoformat(), "text": "claro"}

    assert asyncio.run(_answer_and_ingest(url, answer)) == ["acknowledged"]
    stored = ConversationMessage.objects(direction="out").get()
    assert (stored.messageId, stored.inReplyTo, stored.contactId) == ("wamid.ANSWER", "wamid.A", contact._uuid)
    for _ in range(20):
        if not kept(url, "RESPUESTAS"):
            break
        asyncio.run(asyncio.sleep(0.1))
    assert kept(url, "RESPUESTAS") == []
