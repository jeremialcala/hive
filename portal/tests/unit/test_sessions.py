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
