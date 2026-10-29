# -*- coding: utf-8 -*-
"""
    The history of each conversation (ADR-0017, RF14): what is stored of a message, that it is stored once however
    many times Meta sends it, that only the client who owns it reaches it, and that it goes with the contact, the
    chatbot and the client. The ingestion worker writes it (I4b); here the repository is used directly.
"""
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import mongoengine
import pytest
from fastapi.testclient import TestClient

import app as app_module
from classes import ChatBot, Contact, ConversationMessage
from classes.entity_conversation_message import INCOMING, OUTGOING
from repository import NotFound, TenantRepository, delete_client
from repository.tenant import HISTORY_RETENTION

SENT = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def caller(register):
    return register("acme")


@pytest.fixture
def stranger(register):
    return register("globex")


@pytest.fixture
def repo(caller):
    return TenantRepository(caller.client_id)


@pytest.fixture
def bot(repo):
    return repo.add_bot(ChatBot(name="Bot", role="Sales Assistant", description="A test chatbot", tone="helpful"))


def contact_of(repo, bot, number) -> Contact:
    return repo.add_contact(bot.uuid, Contact(firstName="Ana", waId=number))


@pytest.fixture
def ana(repo, bot):
    return contact_of(repo, bot, "12025550143")


def record(repo, contact, message_id="wamid.A", direction=INCOMING, text="hola, quiero una cita", **more):
    return repo.record_message(contact, direction, message_id, "whatsapp", more.pop("sent_at", SENT), text=text,
                               **more)


def raw() -> str:
    """The collection as a copy of the database shows it."""
    return json.dumps(list(mongoengine.get_db()["messages"].find({})), default=str)


# --- What is stored ------------------------------------------------------------------------------------------------

def test_the_text_is_stored_encrypted_and_read_back_opened(repo, ana):
    assert record(repo, ana) is True

    assert "quiero una cita" not in raw() and "v1:" in raw()
    message, = repo.history(ana)
    assert (message["text"], message["direction"], message["messageId"], message["channel"]) == (
        "hola, quiero una cita", INCOMING, "wamid.A", "whatsapp")


def test_who_wrote_is_the_uuid_of_the_contact_never_the_number(repo, ana):
    record(repo, ana)

    stored = ConversationMessage.objects.get()
    assert stored.contactId == ana._uuid
    assert "12025550143" not in raw()


def test_of_an_attachment_only_its_type_is_kept(repo, ana):
    record(repo, ana, text=None, attachments=("image",))

    message, = repo.history(ana)
    assert (message["text"], message["attachments"]) == (None, ["image"])


def test_a_message_expires_twelve_months_after_it_is_stored(repo, ana):
    before = datetime.now(timezone.utc)
    record(repo, ana)

    expires = ConversationMessage.objects.get().expiresAt.replace(tzinfo=timezone.utc)
    assert before + HISTORY_RETENTION - timedelta(seconds=5) <= expires <= datetime.now(timezone.utc) + HISTORY_RETENTION
    assert HISTORY_RETENTION == timedelta(days=365)


def test_the_conversation_is_read_in_the_order_it_was_sent_with_its_time_zone(repo, ana):
    record(repo, ana, "wamid.SECOND", sent_at=SENT + timedelta(minutes=1))
    record(repo, ana, "wamid.FIRST")

    history = repo.history(ana)
    assert [m["messageId"] for m in history] == ["wamid.FIRST", "wamid.SECOND"]
    assert history[0]["sentAt"] == SENT


# --- Stored once ---------------------------------------------------------------------------------------------------

def test_a_message_meta_sends_again_is_stored_once(repo, ana):
    assert record(repo, ana) is True
    assert record(repo, ana, text="otra cosa") is False

    assert [m["text"] for m in repo.history(ana)] == ["hola, quiero una cita"]
    assert repo.has_message(ana.botId, INCOMING, "wamid.A")
    assert not repo.has_message(ana.botId, INCOMING, "wamid.OTHER")


def test_stored_once_does_not_depend_on_an_index(repo, ana):
    """The chatbot, the direction and the identifier are the primary key: dropping the collection keeps it."""
    record(repo, ana, "wamid.BEFORE")
    ConversationMessage.drop_collection()

    assert record(repo, ana) is True
    assert record(repo, ana) is False
    assert ConversationMessage.objects.count() == 1


def test_an_answer_with_the_identifier_of_an_incoming_message_is_another_message(repo, ana):
    record(repo, ana, direction=INCOMING)
    record(repo, ana, direction=OUTGOING, text="claro")

    assert [m["direction"] for m in repo.history(ana)] == [INCOMING, OUTGOING]


def test_the_same_identifier_in_another_chatbot_is_another_message(repo, bot, ana):
    other_bot = repo.add_bot(ChatBot(name="Other", role="Sales Assistant", description="Another", tone="helpful"))
    record(repo, ana)
    record(repo, contact_of(repo, other_bot, "12025550143"))

    assert ConversationMessage.objects.count() == 2


# --- Only its client -----------------------------------------------------------------------------------------------

def test_another_client_neither_stores_nor_reads_nor_finds_the_history(repo, stranger, ana):
    record(repo, ana)
    theirs = TenantRepository(stranger.client_id)

    with pytest.raises(NotFound):
        record(theirs, ana, "wamid.B")
    with pytest.raises(NotFound):
        theirs.history(ana)
    assert not theirs.has_message(ana.botId, INCOMING, "wamid.A")
    assert ConversationMessage.objects.count() == 1


def test_nothing_is_stored_for_a_contact_deleted_meanwhile(repo, ana):
    Contact.objects(_uuid=ana._uuid).delete()

    with pytest.raises(NotFound):
        record(repo, ana)
    assert ConversationMessage.objects.count() == 0


# --- It goes with the contact --------------------------------------------------------------------------------------

def test_the_export_of_a_contact_carries_its_conversation(caller, repo, bot, ana):
    record(repo, ana)
    record(repo, ana, "wamid.REPLY", direction=OUTGOING, text="claro", sent_at=SENT + timedelta(seconds=5))

    exported = TestClient(app_module.app).get(f"/chat/{bot.uuid}/contacts/{ana._uuid}", headers=caller.auth)

    assert exported.status_code == 200
    assert [(m["direction"], m["text"]) for m in exported.json()["data"]["messages"]] == [
        (INCOMING, "hola, quiero una cita"), (OUTGOING, "claro")]


def test_deleting_a_contact_deletes_its_conversation_and_no_other(repo, bot, ana):
    luis = contact_of(repo, bot, "12025550199")
    record(repo, ana)
    record(repo, luis, "wamid.LUIS")

    assert repo.delete_contact(bot.uuid, ana._uuid) == {"contacts": 1, "messages": 1, "outbound": 0}
    assert [m.contactId for m in ConversationMessage.objects] == [luis._uuid]


def test_deleting_a_chatbot_deletes_the_conversations_it_had(repo, bot, ana):
    record(repo, ana)

    assert repo.delete_bot(bot.uuid)["messages"] == 1
    assert ConversationMessage.objects.count() == 0


def test_deleting_a_client_deletes_the_conversations_of_its_chatbots(caller, stranger, repo, ana):
    theirs = TenantRepository(stranger.client_id)
    their_bot = theirs.add_bot(ChatBot(name="Bot", role="Sales Assistant", description="Theirs", tone="helpful"))
    record(repo, ana)
    record(theirs, contact_of(theirs, their_bot, "12025550143"))

    assert delete_client(caller.client_id)["messages"] == 1
    assert [m.clientId for m in ConversationMessage.objects] == [stranger.client_id]


def test_making_a_contact_deletion_again_takes_the_conversation_too(repo, bot, ana):
    """What the register of erasures makes again after restoring a backup (migrations.replay_erasures)."""
    record(repo, ana)

    assert repo.erase_contacts(bot.uuid, [ana._uuid, uuid4()]) == {"contacts": 1, "messages": 1, "outbound": 0}
