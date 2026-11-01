# -*- coding: utf-8 -*-
"""What the bucket of sessions holds (ADR-0034): sealed values under hashed names, nothing a reader can use."""
import asyncio
import os

import pytest

from src.sessions import MemoryStore, Sealer, Sessions


@pytest.fixture
def store():
    return MemoryStore()


@pytest.fixture
def sessions(store):
    return Sessions(store, Sealer(os.urandom(32)))


def test_a_session_is_stored_sealed_and_under_a_hash_of_its_id(store, sessions):
    session_id = asyncio.run(sessions.create({"id_token": "the-id-token", "token": "the-hive-token"}))

    (name, value), = store.values.items()
    assert session_id not in name and name.startswith("session.") and len(name) == len("session.") + 64
    assert b"the-id-token" not in value and b"the-hive-token" not in value
    assert asyncio.run(sessions.load(session_id))["token"] == "the-hive-token"


def test_a_value_tampered_with_or_moved_to_another_name_opens_to_nothing(store, sessions):
    first = asyncio.run(sessions.create({"token": "one"}))
    second = asyncio.run(sessions.create({"token": "two"}))
    names = list(store.values)
    store.values[names[0]], store.values[names[1]] = store.values[names[1]], store.values[names[0]]

    assert asyncio.run(sessions.load(first)) is None and asyncio.run(sessions.load(second)) is None

    third = asyncio.run(sessions.create({"token": "three"}))
    name = next(name for name in store.values if name not in names)
    store.values[name] = store.values[name][:-1] + bytes([store.values[name][-1] ^ 1])
    assert asyncio.run(sessions.load(third)) is None


def test_another_key_opens_nothing(store):
    session_id = asyncio.run(Sessions(store, Sealer(os.urandom(32))).create({"token": "x"}))

    assert asyncio.run(Sessions(store, Sealer(os.urandom(32))).load(session_id)) is None


def test_the_key_is_32_bytes():
    with pytest.raises(ValueError):
        Sealer(os.urandom(16))


def test_an_id_that_is_none_or_empty_is_no_session(sessions):
    assert asyncio.run(sessions.load(None)) is None and asyncio.run(sessions.load("")) is None
    assert asyncio.run(sessions.finish_login("", "x")) is None and asyncio.run(sessions.finish_login("x", None)) is None


# --- A session written again is the one that was read (HIG-153) ---------------------------------------------------

def test_a_renewal_that_finishes_after_the_logout_does_not_bring_the_session_back(store, sessions):
    async def run():
        session_id = await sessions.create({"id_token": "the-id-token", "token": "old"})
        read = await sessions.load(session_id)
        await sessions.end(session_id)                      # the logout, while the token is renewed
        renewed = await sessions.renewed(session_id, {**read, "token": "new"})
        return renewed, await sessions.load(session_id)

    renewed, after = asyncio.run(run())

    assert renewed is None and after is None and not store.values


def test_two_renewals_at_once_keep_one_and_the_session(store, sessions):
    async def run():
        session_id = await sessions.create({"id_token": "the-id-token", "token": "old"})
        first, second = await sessions.load(session_id), await sessions.load(session_id)
        one = await sessions.renewed(session_id, {**first, "token": "one"})
        other = await sessions.renewed(session_id, {**second, "token": "other"})
        return one, other

    one, other = asyncio.run(run())

    # The second read an older revision: the first renewal stays, and the second request goes on with it.
    assert one["token"] == "one" and other["token"] == "one"


def test_the_revision_a_session_was_read_at_is_never_sealed(store, sessions):
    async def run():
        session_id = await sessions.create({"id_token": "the-id-token", "token": "old"})
        read = await sessions.load(session_id)
        await sessions.renewed(session_id, {**read, "token": "new"})
        return session_id

    session_id = asyncio.run(run())

    (name, value), = store.values.items()
    sealed = sessions._sealer.open(name, value)  # pylint: disable=protected-access
    assert "_revision" not in sealed and sealed["token"] == "new" and session_id


def test_the_memory_store_swaps_only_at_the_revision_it_was_read_at(store):
    async def run():
        await store.put("k", b"a")
        _, revision = await store.read("k")
        fresh = await store.swap("k", b"b", revision)
        stale = await store.swap("k", b"c", revision)
        await store.delete("k")
        _, gone = await store.read("k")
        deleted = await store.swap("k", b"d", gone)
        return fresh, stale, deleted

    swapped = asyncio.run(run())

    assert swapped == (True, False, False)
    assert "k" not in store.values
