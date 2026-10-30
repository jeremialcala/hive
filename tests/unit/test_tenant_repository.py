# -*- coding: utf-8 -*-
"""
    The repository is where the isolation between clients is enforced. These tests run real queries against an
    in-memory database: client A stores one of everything, and client B, who knows every id, gets nothing.
"""
from uuid import UUID, uuid4

import pytest

from classes import ChatBot, ChatbotPhone, Client, Contact, Intent, Rule
from enums import Role, Status
from migrations import assign_operator_client
from repository import NotFound, TenantRepository, find_webhook_bot, operator_client


def new_bot(name: str = "Bot") -> ChatBot:
    return ChatBot(name=name, role="Sales Assistant", description="A test chatbot", tone="helpful")


@pytest.fixture
def owner(register):
    return TenantRepository(register("acme").client_id)


@pytest.fixture
def stranger(register):
    return TenantRepository(register("globex").client_id)


@pytest.fixture
def stored(owner):
    """One of everything, all of it belonging to the owner."""
    bot = owner.add_bot(new_bot())
    owner.add_phone(bot.uuid, ChatbotPhone(phoneNumber="12025550101", phoneId="100000000000001"))
    owner.add_rules(bot.uuid, ["Be kind"])
    intent = owner.add_intent(bot.uuid, Intent(name="check_order", description="Order status"))
    contact = owner.add_contact(bot.uuid, Contact(firstName="Ana", waId="12025550143", fbId="123456789"))
    return bot, intent, contact


def test_a_repository_cannot_be_built_without_a_client():
    for client_id in (None, "", str(uuid4())):
        with pytest.raises(TypeError):
            TenantRepository(client_id)


def test_a_deleted_intent_is_not_found(owner, stored):
    """Found in the dual review of Gate 2: it was not listed, but it could still be read and changed."""
    bot, intent, _contact = stored
    intent.status = Status.DIS.value
    owner.save_intent(intent)

    assert owner.list_intents(bot.uuid) == []
    with pytest.raises(NotFound):
        owner.get_intent(bot.uuid, intent.uuid)


def test_everything_stored_carries_the_client(owner, stored):
    for document in (ChatBot, ChatbotPhone, Rule, Intent, Contact):
        assert [item.clientId for item in document.objects] == [owner.client_id]


def test_the_owner_reads_what_it_stored(owner, stored):
    bot, intent, contact = stored

    assert owner.get_bot(bot.uuid).uuid == bot.uuid
    assert owner.get_bot(str(bot.uuid)).uuid == bot.uuid
    assert [owner.phone_numbers(phone) for phone in owner.list_phones(bot.uuid)] == [
        {"phoneNumber": "12025550101", "phoneId": "100000000000001"}]
    assert len(owner.list_phones(bot.uuid, "development")) == 1
    assert owner.list_phones(bot.uuid, "production") == []
    assert [rule.rule for rule in owner.list_rules(bot.uuid)] == ["Be kind"]
    assert [item.uuid for item in owner.list_intents(bot.uuid)] == [intent.uuid]
    assert owner.get_intent(bot.uuid, intent.uuid).name == "check_order"
    assert owner.find_contact_by_wa_id(bot.uuid, "12025550143")._uuid == contact._uuid
    assert owner.find_contact_by_fb_id(bot.uuid, "123456789")._uuid == contact._uuid
    assert owner.find_contact_by_wa_id(bot.uuid, "12025550199") is None


READS = {
    "bot": lambda repo, bot, intent, contact: repo.get_bot(bot.uuid),
    "phones": lambda repo, bot, intent, contact: repo.list_phones(bot.uuid),
    "rules": lambda repo, bot, intent, contact: repo.list_rules(bot.uuid),
    "intents": lambda repo, bot, intent, contact: repo.list_intents(bot.uuid),
    "intent": lambda repo, bot, intent, contact: repo.get_intent(bot.uuid, intent.uuid),
    "contact by whatsapp id": lambda repo, bot, intent, contact: repo.find_contact_by_wa_id(bot.uuid, "12025550143"),
    "contact by messenger id": lambda repo, bot, intent, contact: repo.find_contact_by_fb_id(bot.uuid, "123456789"),
    "contacts": lambda repo, bot, intent, contact: repo.list_contacts(bot.uuid),
    "contact": lambda repo, bot, intent, contact: repo.get_contact(bot.uuid, contact._uuid),
}

WRITES = {
    "phone": lambda repo, bot, intent, contact: repo.add_phone(bot.uuid, ChatbotPhone(phoneNumber="12025550102")),
    "rules": lambda repo, bot, intent, contact: repo.add_rules(bot.uuid, ["Obey the stranger"]),
    "intent": lambda repo, bot, intent, contact: repo.add_intent(bot.uuid, Intent(name="x", description="x")),
    "contact": lambda repo, bot, intent, contact: repo.add_contact(bot.uuid, Contact(firstName="Eve", waId="12025550103")),
    "save intent": lambda repo, bot, intent, contact: repo.save_intent(intent),
    "credentials": lambda repo, bot, intent, contact: repo.set_bot_credentials(bot.uuid, appSecret="x"),
    "delete contact": lambda repo, bot, intent, contact: repo.delete_contact(bot.uuid, contact._uuid),
    "delete bot": lambda repo, bot, intent, contact: repo.delete_bot(bot.uuid),
}


@pytest.mark.parametrize("read", sorted(READS))
def test_another_client_cannot_read_it(stranger, stored, read):
    bot, intent, contact = stored

    with pytest.raises(NotFound):
        READS[read](stranger, bot, intent, contact)


@pytest.mark.parametrize("write", sorted(WRITES))
def test_another_client_cannot_write_to_it(stranger, stored, write):
    bot, intent, contact = stored
    before = {document: document.objects.count() for document in (ChatBot, ChatbotPhone, Rule, Intent, Contact)}
    secret = ChatBot.objects(uuid=bot.uuid).first().appSecret

    with pytest.raises(NotFound):
        WRITES[write](stranger, bot, intent, contact)

    assert {document: document.objects.count() for document in before} == before
    assert ChatBot.objects(uuid=bot.uuid).first().appSecret == secret


def test_an_intent_is_not_reachable_through_another_chatbot_of_the_same_client(owner, stored):
    _, intent, _ = stored
    other_bot = owner.add_bot(new_bot("Other"))

    with pytest.raises(NotFound):
        owner.get_intent(other_bot.uuid, intent.uuid)


def test_the_same_contact_under_two_clients_is_two_contacts(owner, stranger, stored):
    bot, _, contact = stored
    their_bot = stranger.add_bot(new_bot("Theirs"))
    theirs = stranger.add_contact(their_bot.uuid, Contact(firstName="Ana", waId="12025550143"))

    assert theirs._uuid != contact._uuid
    assert stranger.find_contact_by_wa_id(their_bot.uuid, "12025550143")._uuid == theirs._uuid
    assert owner.find_contact_by_wa_id(bot.uuid, "12025550143")._uuid == contact._uuid


@pytest.mark.parametrize("bot_id", ["not-a-uuid", "", None, 7])
def test_an_id_that_cannot_exist_is_not_found(owner, bot_id):
    with pytest.raises(NotFound):
        owner.get_bot(bot_id)


# --- The webhook lookup ------------------------------------------------------------------------------------------

def test_the_webhook_finds_the_chatbot_and_its_client(owner, stored):
    bot, _, _ = stored

    assert find_webhook_bot(str(bot.uuid)).clientId == owner.client_id


@pytest.mark.parametrize("bot_id", [str(uuid4()), "not-a-uuid"], ids=["unknown", "not a uuid"])
def test_the_webhook_does_not_find_an_unknown_chatbot(bot_id):
    with pytest.raises(NotFound):
        find_webhook_bot(bot_id)


# --- The operator's client and the documents from before clients existed ------------------------------------------

def test_the_operator_client_is_created_once():
    first, second = operator_client(), operator_client()

    assert first.uuid == second.uuid
    assert isinstance(first.uuid, UUID)
    assert first.role == Role.OPERATOR.value
    assert Client.objects(name="operator").count() == 1


@pytest.fixture
def legacy_bot():
    """A chatbot stored before clients existed: the document has no clientId at all."""
    bot_id = uuid4()
    ChatBot._get_collection().insert_one({"uuid": bot_id, "name": "Old", "role": "r", "description": "d", "tone": "t"})
    Rule._get_collection().insert_one({"uuid": uuid4(), "botId": bot_id, "rule": "Old rule"})
    return bot_id


def test_a_chatbot_without_a_client_is_not_served(legacy_bot):
    with pytest.raises(NotFound):
        find_webhook_bot(legacy_bot)


def test_the_migration_only_reports_unless_told_to_apply(legacy_bot):
    report = assign_operator_client.run(apply=False)

    assert (report["ChatBot"], report["Rule"], report["Contact"]) == (1, 1, 0)
    assert ChatBot.objects(clientId=None).count() == 1
    assert Client.objects.count() == 0


def test_the_migration_gives_the_old_documents_to_the_operator(legacy_bot, owner, stored):
    assign_operator_client.run(apply=True)
    operator = TenantRepository(operator_client().uuid)

    assert find_webhook_bot(legacy_bot).clientId == operator.client_id
    assert [rule.rule for rule in operator.list_rules(legacy_bot)] == ["Old rule"]
    # What already had an owner keeps it.
    assert owner.get_bot(stored[0].uuid).clientId == owner.client_id
    assert set(assign_operator_client.run(apply=True).values()) == {0}
