# -*- coding: utf-8 -*-
"""
    The models of the events that cross between the gateway and the engine (shared.contract, ADR-0023). That they agree
    with what the gateway publishes and stores is tested in test_ingestion.py; here, that they read strictly.
"""
from uuid import uuid4

import pytest
from pydantic import ValidationError

from shared.contract import SCHEMA_VERSION, IdentifiedMessage, SentReply, identified_subject, reply_subject

BOT, CLIENT, CONTACT = uuid4(), uuid4(), uuid4()


def identified(**changes) -> dict:
    event = {"schemaVersion": 2, "botId": str(BOT), "clientId": str(CLIENT), "messageId": "wamid.A",
             "message": {"messages": [{"id": "wamid.A", "type": "text", "text": {"body": "hola"}}]},
             "channel": "whatsapp", "receivedAt": "2026-10-20T09:00:00+00:00", "orderKey": f"{BOT}.{CONTACT}",
             "contact": {"uuid": str(CONTACT), "name": "Ana", "waId": "12025550143"}}
    event.update(changes)
    return event


def test_a_message_identified_reads_with_its_types():
    read = IdentifiedMessage.model_validate(identified())

    assert (read.botId, read.clientId, read.contact.uuid) == (BOT, CLIENT, CONTACT)
    assert read.receivedAt.tzinfo is not None


@pytest.mark.parametrize("changes", [{"schemaVersion": 1}, {"schemaVersion": 3}, {"channel": "telegram"},
                                     {"botId": "not-a-uuid"}, {"messageId": ""},
                                     {"contact": {"uuid": str(CONTACT), "phone": "12025550143"}}],
                         ids=["version 1", "a version from the future", "another channel", "a bot that is not a uuid",
                              "no message id", "a contact with a field the contract does not have"])
def test_a_broken_message_does_not_read(changes):
    with pytest.raises(ValidationError):
        IdentifiedMessage.model_validate(identified(**changes))


def test_a_field_added_to_the_event_later_does_not_break_who_reads_it():
    assert IdentifiedMessage.model_validate(identified(laterField=True)).messageId == "wamid.A"


def test_an_answer_longer_than_whatsapp_takes_is_refused():
    with pytest.raises(ValidationError):
        SentReply(schemaVersion=2, botId=BOT, clientId=CLIENT, contactId=CONTACT, channel="whatsapp", inReplyTo="a",
                  messageId="b", sentAt="2026-10-20T09:00:00+00:00", text="x" * 4097)


def test_the_subjects_are_the_ones_of_the_contract():
    assert SCHEMA_VERSION == 2
    assert identified_subject(7, BOT, CONTACT) == f"gateway.message.7.{BOT}.{CONTACT}"
    assert reply_subject(BOT) == f"engine.reply.sent.{BOT}"
