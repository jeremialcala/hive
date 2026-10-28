# -*- coding: utf-8 -*-
"""
    Default values must be computed for each object, not once when the module is imported.
    They used to be written as `default=uuid4()` and `default=datetime.now()`: every document then carried the
    process start time as its creation date, and every queue message shared one `_uuid`.
"""
import time
from datetime import datetime, timedelta, timezone

import pytest

from classes import ChatBot, ChatbotPhone, Client, Contact, Event, EventAction, Intent, Jwk, QueueMessage, ResponseData, Rule
from classes.entity_credentials import Credentials
from classes.tool_clock import as_utc

ENTITIES = [ChatBot, ChatbotPhone, Client, Contact, Event, EventAction, Intent, Jwk, QueueMessage, Rule, Credentials]
IDENTIFIER = {Contact: "_uuid", QueueMessage: "_uuid", Jwk: "kid"}


@pytest.mark.parametrize("entity", ENTITIES, ids=lambda entity: entity.__name__)
def test_each_document_gets_its_own_identifier(entity):
    field = IDENTIFIER.get(entity, "uuid")

    assert getattr(entity(), field) != getattr(entity(), field)


@pytest.mark.parametrize("entity", [e for e in ENTITIES if e is not Credentials], ids=lambda entity: entity.__name__)
def test_creation_date_is_the_moment_the_document_is_created(entity):
    before = time.time()
    time.sleep(0.002)

    document = entity()

    assert document.createdAt.timestamp() > before
    assert document.statusDate.timestamp() > before


@pytest.mark.parametrize("entity", [e for e in ENTITIES if e is not Credentials], ids=lambda entity: entity.__name__)
def test_every_document_is_dated_in_utc_and_says_so(entity):
    """
        H10: MongoDB reads every date as UTC. One without a time zone, written by a machine whose clock is not in
        UTC, lands shifted by that machine's offset: four hours early on one in Venezuela.
    """
    document = entity()

    assert document.createdAt.utcoffset() == document.statusDate.utcoffset() == timedelta(0)


def test_each_credential_gets_its_own_signing_key():
    assert Credentials()._key != Credentials()._key


def test_credentials_do_not_share_the_header_dictionary():
    first, second = Credentials(), Credentials()
    first._header["alg"] = "none"

    assert second._header == {"alg": "HS256"}


def test_response_timestamp_is_the_moment_of_the_response_in_utc():
    before = datetime.now(timezone.utc)
    time.sleep(0.002)
    timestamp = ResponseData().timestamp

    assert timestamp > before
    assert timestamp.utcoffset() == timedelta(0)


@pytest.mark.parametrize("read,utc", [
    (datetime(2026, 10, 28, 2, 0), datetime(2026, 10, 28, 2, 0, tzinfo=timezone.utc)),
    (datetime(2026, 10, 27, 22, 0, tzinfo=timezone(timedelta(hours=-4))),
     datetime(2026, 10, 28, 2, 0, tzinfo=timezone.utc)),
    (None, None),
])
def test_a_date_read_back_is_given_its_time_zone(read, utc):
    """pymongo hands UTC dates over without a time zone: read back, they are told they are UTC."""
    assert as_utc(read) == utc
    assert read is None or as_utc(read).utcoffset() == timedelta(0)
