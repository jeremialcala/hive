# -*- coding: utf-8 -*-
"""
    The notices of erasure against a real NATS JetStream set up like Bifröst (nats-test.conf): the management API's
    user publishes them on SUPRESIONES, and nothing else.

        NATS_TEST_ADDR=127.0.0.1:24222 pytest tests/integration/test_erasure_notices_bus.py

    Skipped without NATS_TEST_ADDR; with NATS_TEST_REQUIRED set it fails instead.
"""
import asyncio
import json
import os
from uuid import uuid4

import nats
import pytest
from nats.js.api import StreamConfig

from shared import bus
from shared.bus.nats_jetstream import NatsPublisher
from controller import erasure_notices

ADDRESS = os.environ.get("NATS_TEST_ADDR")
SUBJECTS = ["gateway.contact.erased", "gateway.bot.erased", "gateway.client.erased"]


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
def stream(url):
    """SUPRESIONES as Bifröst has it (repository bifrost, jetstream/stream-supresiones.json)."""
    async def create(js):
        try:
            await js.delete_stream("SUPRESIONES")
        except nats.js.errors.NotFoundError:
            # Not there from an earlier run: nothing to delete.
            pass
        await js.add_stream(StreamConfig(name="SUPRESIONES", subjects=SUBJECTS, max_age=7 * 24 * 3600,
                                         duplicate_window=120))
    admin(url, create)
    yield
    admin(url, lambda js: js.delete_stream("SUPRESIONES"))


def kept(url) -> list:
    async def read(js):
        info = await js.stream_info("SUPRESIONES")
        if not info.state.messages:
            return []
        return [await js.get_msg("SUPRESIONES", sequence)
                for sequence in range(info.state.first_seq, info.state.last_seq + 1)]
    return admin(url, read)


def as_the_management_api(url) -> NatsPublisher:
    return NatsPublisher(url, "gestion", "test", inbox_prefix="_INBOX_gestion", name=f"gestion-{uuid4()}")


async def _announce_all(url, client, bot, contacts):
    bus.configure(as_the_management_api(url))
    try:
        return [await erasure_notices.announce(erasure_notices.CONTACT, client, bot, contacts),
                await erasure_notices.announce(erasure_notices.CHATBOT, client, bot),
                await erasure_notices.announce(erasure_notices.CLIENT, client)]
    finally:
        await bus.close()
        bus.configure(None)


def test_the_management_api_publishes_each_notice_with_the_user_bifrost_gives_it(url, stream):
    client, bot, contacts = uuid4(), uuid4(), [uuid4(), uuid4()]

    assert asyncio.run(_announce_all(url, client, bot, contacts)) == [2, 1, 1]

    notices = kept(url)
    assert [m.subject for m in notices] == ["gateway.contact.erased"] * 2 + ["gateway.bot.erased",
                                                                           "gateway.client.erased"]
    assert [json.loads(m.data)["uuid"] for m in notices] == [m.headers["Nats-Msg-Id"] for m in notices]
    assert {json.loads(m.data).get("contactId") for m in notices[:2]} == {str(c) for c in contacts}


def test_the_management_api_may_publish_nothing_but_notices(url, stream):
    async def try_elsewhere():
        publisher = as_the_management_api(url)
        try:
            await publisher.publish("webhook.meta.whatsapp", b"{}", f"not-mine-{uuid4()}")
        finally:
            await publisher.close()

    with pytest.raises(bus.BusUnavailable):
        asyncio.run(try_elsewhere())
