# -*- coding: utf-8 -*-
"""
    The event publisher without a bus: when it connects, when it does not try again, and what each failure becomes.
    What a real NATS keeps and refuses is in tests/integration/test_nats_publisher.py.
"""
import asyncio
from types import SimpleNamespace

import nats.errors
import pytest

from shared import bus
from shared.bus import nats_jetstream


class FakeClient:
    """Stands for the NATS client: counts the connections and does with each publication what the test says."""
    made = []

    def __init__(self):
        self.is_closed = False
        self.published = []
        self.outcome = SimpleNamespace(stream="WEBHOOK", seq=7, duplicate=None)
        self.refuse = None
        FakeClient.made.append(self)

    async def connect(self, *_args, **options):
        self.options = options
        if FakeClient.unreachable:
            raise nats.errors.NoServersError()

    def jetstream(self):
        return self

    async def publish(self, subject, payload, timeout=None, headers=None):
        self.published.append((subject, payload, headers, timeout))
        if self.refuse:
            raise self.refuse
        return self.outcome

    async def close(self):
        self.is_closed = True

    async def drain(self):
        self.is_closed = True


@pytest.fixture(autouse=True)
def client(monkeypatch):
    FakeClient.made, FakeClient.unreachable = [], False
    monkeypatch.setattr(nats_jetstream, "Client", FakeClient)
    yield
    bus.configure(None)


def publish(publisher, message_id="bot:wamid.ONE"):
    return asyncio.run(publisher.publish("webhook.meta.whatsapp", b"{}", message_id))


def test_an_event_goes_with_its_message_id_and_waits_for_the_acknowledgement():
    published = publish(nats_jetstream.NatsPublisher("nats://bus"))

    subject, payload, headers, timeout = FakeClient.made[0].published[0]
    assert (subject, payload, headers) == ("webhook.meta.whatsapp", b"{}", {"Nats-Msg-Id": "bot:wamid.ONE"})
    assert timeout == nats_jetstream.ACK_TIMEOUT
    assert published == bus.Published(stream="WEBHOOK", sequence=7, duplicate=False)


def test_an_event_without_a_message_id_is_not_published():
    with pytest.raises(ValueError):
        publish(nats_jetstream.NatsPublisher("nats://bus"), message_id="")

    assert FakeClient.made == []


def test_the_connection_is_kept_for_the_next_event():
    publisher = nats_jetstream.NatsPublisher("nats://bus")

    async def two():
        await publisher.publish("webhook.meta.whatsapp", b"1", "bot:wamid.ONE")
        await publisher.publish("webhook.meta.whatsapp", b"2", "bot:wamid.TWO")
    asyncio.run(two())

    assert len(FakeClient.made) == 1
    assert len(FakeClient.made[0].published) == 2


def test_the_webhook_connects_with_its_user_and_its_own_prefix_of_reply_subjects():
    publish(nats_jetstream.NatsPublisher("nats://bus", "webhook", "secret", inbox_prefix="_INBOX_webhook"))

    options = FakeClient.made[0].options
    assert (options["user"], options["password"], options["inbox_prefix"]) == ("webhook", "secret", "_INBOX_webhook")
    # Once in, the client reconnects on its own for as long as it takes.
    assert (options["allow_reconnect"], options["max_reconnect_attempts"]) == (True, -1)


def test_tls_is_used_for_a_tls_address_only():
    assert nats_jetstream.NatsPublisher("tls://bifrost:4222")._tls() is not None
    assert nats_jetstream.NatsPublisher("nats://127.0.0.1:4222")._tls() is None


def test_a_new_event_loop_gets_a_new_connection():
    """A connection belongs to the event loop that opened it: one opened by another loop cannot be used."""
    publisher = nats_jetstream.NatsPublisher("nats://bus")

    publish(publisher)
    publish(publisher)

    assert len(FakeClient.made) == 2


def test_a_closed_connection_is_opened_again():
    publisher = nats_jetstream.NatsPublisher("nats://bus")

    async def after_closing():
        await publisher.publish("webhook.meta.whatsapp", b"1", "bot:wamid.ONE")
        FakeClient.made[0].is_closed = True
        await publisher.publish("webhook.meta.whatsapp", b"2", "bot:wamid.TWO")
    asyncio.run(after_closing())

    assert len(FakeClient.made) == 2


def test_a_bus_that_cannot_be_reached_is_unavailable_and_not_tried_again_at_once():
    FakeClient.unreachable = True
    publisher = nats_jetstream.NatsPublisher("nats://bus")

    async def twice():
        for _ in range(2):
            with pytest.raises(bus.BusUnavailable):
                await publisher.publish("webhook.meta.whatsapp", b"{}", "bot:wamid.ONE")
    asyncio.run(twice())

    assert len(FakeClient.made) == 1
    # The attempt that failed is stopped, or it would keep retrying in the background.
    assert FakeClient.made[0].is_closed


def test_once_the_wait_is_over_the_bus_is_tried_again(monkeypatch):
    FakeClient.unreachable = True
    publisher = nats_jetstream.NatsPublisher("nats://bus")
    with pytest.raises(bus.BusUnavailable):
        publish(publisher)
    monkeypatch.setattr(nats_jetstream, "RETRY_AFTER", 0)
    FakeClient.unreachable = False

    publish(publisher)

    assert len(FakeClient.made) == 2


@pytest.mark.parametrize("failure", [nats.errors.TimeoutError(), nats.errors.NoRespondersError(),
                                     nats.errors.OutboundBufferLimitError(), asyncio.TimeoutError(), OSError()])
def test_an_event_the_bus_does_not_acknowledge_is_unavailable(failure, monkeypatch):
    publisher = nats_jetstream.NatsPublisher("nats://bus")

    async def refused():
        await publisher._connect()
        FakeClient.made[0].refuse = failure
        await publisher.publish("webhook.meta.whatsapp", b"{}", "bot:wamid.ONE")

    with pytest.raises(bus.BusUnavailable) as raised:
        asyncio.run(refused())
    # Only the kind of failure: the text of these can carry the address of the bus.
    assert str(raised.value) == type(failure).__name__


def test_a_duplicate_is_reported_as_kept_once():
    publisher = nats_jetstream.NatsPublisher("nats://bus")

    async def duplicate():
        await publisher._connect()
        FakeClient.made[0].outcome = SimpleNamespace(stream="WEBHOOK", seq=3, duplicate=True)
        return await publisher.publish("webhook.meta.whatsapp", b"{}", "bot:wamid.ONE")

    assert asyncio.run(duplicate()) == bus.Published(stream="WEBHOOK", sequence=3, duplicate=True)


def test_closing_lets_go_of_the_connection():
    publisher = nats_jetstream.NatsPublisher("nats://bus")

    async def publish_and_close():
        await publisher.publish("webhook.meta.whatsapp", b"{}", "bot:wamid.ONE")
        await publisher.close()
    asyncio.run(publish_and_close())

    assert FakeClient.made[0].is_closed


def test_without_a_configured_publisher_there_is_none_to_use_and_nothing_to_close():
    with pytest.raises(RuntimeError):
        bus.publisher()
    asyncio.run(bus.close())


def test_the_configured_publisher_is_the_one_used_and_closed():
    publisher = nats_jetstream.NatsPublisher("nats://bus")
    bus.configure(publisher)

    async def use_and_close():
        await bus.publisher().publish("webhook.meta.whatsapp", b"{}", "bot:wamid.ONE")
        await bus.close()
    asyncio.run(use_and_close())

    assert bus.publisher() is publisher
    assert FakeClient.made[0].is_closed
