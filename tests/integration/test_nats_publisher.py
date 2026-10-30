# -*- coding: utf-8 -*-
"""
    Publishing against a real NATS JetStream, the same version as Bifröst and set up like it (nats-test.conf): what
    the stream keeps, what it drops as a duplicate, what the webhook's user may not do, and that a process keeps a
    single connection however many events it publishes.

        docker run --rm -p 127.0.0.1:24222:4222 -p 127.0.0.1:28222:8222 \
            -v "$PWD/tests/integration/nats-test.conf:/etc/nats/test.conf:ro" nats:2.14.8-alpine -c /etc/nats/test.conf
        NATS_TEST_ADDR=127.0.0.1:24222 NATS_TEST_MONITOR=127.0.0.1:28222 pytest tests/integration

    Without NATS_TEST_ADDR the tests are skipped; with NATS_TEST_REQUIRED set they fail instead, which is how the
    pipeline makes sure they are not skipped there. The stream is made for the tests and emptied before each one.
"""
import asyncio
import json
import os
import urllib.request
from uuid import uuid4

import nats
import pytest
from nats.js.api import RetentionPolicy, StreamConfig

from shared import bus
from shared.bus.nats_jetstream import NatsPublisher

BusUnavailable = bus.BusUnavailable
ADDRESS = os.environ.get("NATS_TEST_ADDR")
MONITOR = os.environ.get("NATS_TEST_MONITOR")
STREAM = "WEBHOOK"
SUBJECT = "webhook.meta.whatsapp"
# The stream remembers message ids for its whole window, also after it is emptied: every run uses its own.
RUN = uuid4().hex


def run(coroutine):
    return asyncio.run(coroutine)


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


@pytest.fixture(scope="module")
def stream(url):
    """WEBHOOK as Bifröst has it (repository bifrost, jetstream/stream-webhook.json): a work queue, 2 minutes of window."""
    config = StreamConfig(name=STREAM, subjects=["webhook.meta.>"], retention=RetentionPolicy.WORK_QUEUE,
                          max_age=7 * 24 * 3600, duplicate_window=120)

    async def create(js):
        try:
            await js.delete_stream(STREAM)
        except nats.js.errors.NotFoundError:
            # Stream may not exist yet; ignore to keep setup idempotent.
            pass
        await js.add_stream(config)
    run(_admin(url, create))
    yield
    run(_admin(url, lambda js: js.delete_stream(STREAM)))


@pytest.fixture
def kept(url, stream):
    """Empties the stream, and returns what reads it back: the events in it, oldest first."""
    run(_admin(url, lambda js: js.purge_stream(STREAM)))

    async def read(js):
        info = await js.stream_info(STREAM)
        messages = []
        for sequence in range(info.state.first_seq, info.state.last_seq + 1) if info.state.messages else []:
            messages.append(await js.get_msg(STREAM, sequence))
        return messages
    return lambda: run(_admin(url, read))


def webhook(url, **overrides) -> NatsPublisher:
    """The publisher as the webhook gets it: its user, its prefix of reply subjects."""
    options = {"user": "webhook", "password": "test", "inbox_prefix": "_INBOX_webhook", "name": f"test-{uuid4()}"}
    options.update(overrides)
    return NatsPublisher(url, **options)


async def _publish(publisher, events):
    try:
        return [await publisher.publish(subject, payload, message_id) for subject, payload, message_id in events]
    finally:
        await publisher.close()


def publish(publisher, *events):
    return run(_publish(publisher, events))


# --- What the stream keeps -------------------------------------------------------------------------------------------

def test_the_stream_keeps_the_event_as_published_with_its_message_id(url, kept):
    body = b'{"object":"whatsapp_business_account"}'

    published, = publish(webhook(url), (SUBJECT, body, f"bot:wamid.ONE.{RUN}"))

    message, = kept()
    assert (published.stream, published.sequence, published.duplicate) == (STREAM, message.seq, False)
    assert (message.subject, message.data) == (SUBJECT, body)
    assert message.headers["Nats-Msg-Id"] == f"bot:wamid.ONE.{RUN}"


def test_the_same_message_id_within_the_window_is_kept_once_and_reported_as_a_duplicate(url, kept):
    same = f"bot:wamid.SAME.{RUN}"

    first, again = publish(webhook(url), (SUBJECT, b"one", same), (SUBJECT, b"again", same))

    assert (first.duplicate, again.duplicate) == (False, True)
    assert again.sequence == first.sequence
    assert [m.data for m in kept()] == [b"one"]


def test_another_message_id_is_another_event(url, kept):
    publish(webhook(url), (SUBJECT, b"one", f"bot:wamid.FIRST.{RUN}"), (SUBJECT, b"two", f"bot:wamid.SECOND.{RUN}"))

    assert [m.data for m in kept()] == [b"one", b"two"]


def test_the_stream_still_drops_a_message_id_it_kept_before_being_emptied(url, kept):
    """Emptying the stream does not let an event in again: the window of message ids outlives what is kept."""
    same = f"bot:wamid.PURGED.{RUN}"
    publish(webhook(url), (SUBJECT, b"one", same))
    run(_admin(url, lambda js: js.purge_stream(STREAM)))

    again, = publish(webhook(url), (SUBJECT, b"again", same))

    assert again.duplicate
    assert kept() == []


def test_the_largest_notification_meta_sends_is_kept(url, kept):
    """Meta sends up to 3 MB, and the webhook publishes a notification whole (ADR-0021)."""
    payload = b"x" * (3 * 1024 * 1024)

    publish(webhook(url), (SUBJECT, payload, f"bot:wamid.LARGE.{RUN}"))

    assert len(kept()[0].data) == len(payload)


# --- One connection per process --------------------------------------------------------------------------------------

def test_a_process_keeps_one_connection_however_many_events_it_publishes(url, kept):
    if not MONITOR:
        pytest.skip("Set NATS_TEST_MONITOR to count the connections")
    name = f"test-{uuid4()}"

    async def many():
        publisher = webhook(url, name=name)
        try:
            for i in range(20):
                await publisher.publish(SUBJECT, b"x", f"bot:wamid.{i}.{RUN}")
            with urllib.request.urlopen(f"http://{MONITOR}/connz?limit=1024", timeout=5) as answer:
                connections = json.load(answer)["connections"]
            return [c for c in connections if c.get("name") == name]
        finally:
            await publisher.close()

    assert len(run(many())) == 1
    assert len(kept()) == 20


def test_the_publisher_of_the_process_is_the_one_configured(url, kept):
    publisher = webhook(url)
    bus.configure(publisher)
    try:
        run(_publish(bus.publisher(), [(SUBJECT, b"x", f"bot:wamid.CONFIGURED.{RUN}")]))
    finally:
        bus.configure(None)

    assert len(kept()) == 1


# --- When the bus says no --------------------------------------------------------------------------------------------

def test_a_subject_the_webhook_may_not_publish_to_is_reported_as_unavailable(url, kept):
    with pytest.raises(BusUnavailable):
        publish(webhook(url), ("gateway.message.0.bot.contact", b"x", f"bot:wamid.NOT-MINE.{RUN}"))

    assert kept() == []


def test_without_its_own_prefix_of_reply_subjects_the_acknowledgement_never_arrives(url, kept):
    """Bifröst only lets the webhook read _INBOX_webhook: with the default prefix the event is kept, unconfirmed."""
    with pytest.raises(BusUnavailable):
        publish(webhook(url, inbox_prefix="_INBOX"), (SUBJECT, b"x", f"bot:wamid.NO-ACK.{RUN}"))


def test_a_wrong_password_is_reported_as_unavailable(url, kept):
    with pytest.raises(BusUnavailable):
        publish(webhook(url, password="not-the-password"), (SUBJECT, b"x", f"bot:wamid.LOCKED-OUT.{RUN}"))

    assert kept() == []


def test_nobody_listening_at_the_address_is_reported_as_unavailable():
    with pytest.raises(BusUnavailable):
        publish(NatsPublisher("nats://127.0.0.1:1"), (SUBJECT, b"x", f"bot:wamid.NOBODY.{RUN}"))
