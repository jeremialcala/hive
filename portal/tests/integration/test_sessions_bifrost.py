# -*- coding: utf-8 -*-
"""
    The sessions in a real bucket of NATS, with the portal's user as Bifröst gives it (nats-test.conf): it reads,
    writes and forgets its own sessions, and reaches nothing else.

        NATS_TEST_ADDR=127.0.0.1:24222 pytest tests/integration

    Without NATS_TEST_ADDR it is skipped; with NATS_TEST_REQUIRED, it fails.
"""
import asyncio
import os

import nats
import pytest
from nats.js.api import KeyValueConfig

from src.sessions import BUCKET, LIFETIME, KvStore, Sealer, Sessions

ADDRESS = os.environ.get("NATS_TEST_ADDR")


@pytest.fixture(scope="module")
def url():
    if not ADDRESS:
        if os.environ.get("NATS_TEST_REQUIRED"):
            pytest.fail("NATS_TEST_ADDR is needed here")
        pytest.skip("No NATS to test against: NATS_TEST_ADDR")
    return f"nats://{ADDRESS}"


async def _bucket(url: str):
    connection = await nats.connect(url, user="admin", password="test")
    jetstream = connection.jetstream()
    try:
        await jetstream.delete_key_value(BUCKET)
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    await jetstream.create_key_value(KeyValueConfig(bucket=BUCKET, history=1, ttl=LIFETIME, max_value_size=16384))
    await connection.close()


def test_the_portal_s_user_keeps_reads_and_forgets_its_sessions(url):
    async def run():
        await _bucket(url)
        sessions = Sessions(KvStore(url, "portal", "test", None, "_INBOX_portal"), Sealer(os.urandom(32)))
        session_id = await sessions.create({"id_token": "the-id-token", "token": "the-hive-token"})
        loaded = await sessions.load(session_id)
        await sessions.end(session_id)
        return loaded, await sessions.load(session_id)

    loaded, after = asyncio.run(run())

    assert loaded["token"] == "the-hive-token"
    assert after is None


def test_what_the_bucket_holds_is_sealed(url):
    async def run():
        await _bucket(url)
        sessions = Sessions(KvStore(url, "portal", "test", None, "_INBOX_portal"), Sealer(os.urandom(32)))
        await sessions.create({"id_token": "the-id-token", "token": "the-hive-token"})
        connection = await nats.connect(url, user="admin", password="test")
        kv = await connection.jetstream().key_value(BUCKET)
        values = [(await kv.get(key)).value for key in await kv.keys()]
        await connection.close()
        return values

    values = asyncio.run(run())

    assert len(values) == 1 and b"the-id-token" not in values[0] and b"the-hive-token" not in values[0]
