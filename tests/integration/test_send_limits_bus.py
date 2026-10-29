# -*- coding: utf-8 -*-
"""
    The counters of the limits of sending (ADR-0031) against a real NATS JetStream set up like Bifröst
    (nats-test.conf): the management API's user counts in the bucket `limites`, two at once do not lose a count, it
    reaches no other bucket, and it publishes the requests to send on SALIENTES.

    Without NATS_TEST_ADDR the tests are skipped; with NATS_TEST_REQUIRED set they fail instead.
"""
import asyncio
import os
from uuid import uuid4

import nats
import pytest
from nats.js.api import KeyValueConfig, RetentionPolicy, StreamConfig

from shared import bus
from shared.bus.nats_jetstream import NatsPublisher

ADDRESS = os.environ.get("NATS_TEST_ADDR")


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
def buckets(url):
    """`limites` as Bifröst has it (repository bifrost, jetstream/aprovisionar.sh), and one it may not reach."""
    async def create(js):
        for name in ("limites", "contexto"):
            try:
                await js.delete_key_value(name)
            except nats.js.errors.NotFoundError:
                pass
            await js.create_key_value(KeyValueConfig(bucket=name, ttl=24 * 3600, history=1, max_value_size=64))
    run(_admin(url, create))
    yield
    run(_admin(url, lambda js: asyncio.gather(js.delete_key_value("limites"), js.delete_key_value("contexto"))))


@pytest.fixture(scope="module")
def outgoing(url):
    """SALIENTES as Bifröst has it (repository bifrost, jetstream/stream-salientes.json)."""
    config = StreamConfig(name="SALIENTES", subjects=["gateway.outbound.>"], retention=RetentionPolicy.WORK_QUEUE,
                          max_age=24 * 3600, duplicate_window=120)

    async def create(js):
        try:
            await js.delete_stream("SALIENTES")
        except nats.js.errors.NotFoundError:
            pass
        await js.add_stream(config)
    run(_admin(url, create))
    yield
    run(_admin(url, lambda js: js.delete_stream("SALIENTES")))


def management(url) -> NatsPublisher:
    return NatsPublisher(url, user="gestion", password="test", inbox_prefix="_INBOX_gestion", name=f"test-{uuid4()}")


async def _counted(publisher, keys):
    try:
        return [await publisher.count("limites", key) for key in keys]
    finally:
        await publisher.close()


def test_a_counter_starts_at_one_and_adds_one_each_time(url, buckets):
    key = f"bot.{uuid4()}.202610291200"

    assert run(_counted(management(url), [key, key, key])) == [1, 2, 3]


def test_two_at_once_do_not_lose_a_count(url, buckets):
    key = f"client.{uuid4()}.20261029"

    async def both():
        first, second = management(url), management(url)
        try:
            return await asyncio.gather(*[(first if n % 2 else second).count("limites", key) for n in range(10)])
        finally:
            await first.close()
            await second.close()

    assert sorted(run(both())) == list(range(1, 11))


def test_the_management_api_reaches_no_other_bucket(url, buckets):
    async def other():
        publisher = management(url)
        try:
            return await publisher.count("contexto", f"bot.{uuid4()}")
        finally:
            await publisher.close()

    with pytest.raises(bus.BusUnavailable):
        run(other())


def test_the_management_api_publishes_the_requests_to_send(url, outgoing):
    async def publish():
        publisher = management(url)
        try:
            return await publisher.publish(f"gateway.outbound.3.{uuid4()}.{uuid4()}", b"{}", str(uuid4()))
        finally:
            await publisher.close()

    assert run(publish()).stream == "SALIENTES"
