# -*- coding: utf-8 -*-
"""
    The state of a chatbot (HIG-74, HIG-75; controller/bot_state.py), moved only by what Meta says:

    - REG → ACT when Meta verifies its webhook, or sends it a signed notification;
    - REG, ACT → ERR when Meta turns its token or its permissions away;
    - ERR → ACT with a new token, or when Meta takes a send or a call on templates.

    Nothing takes a chatbot out of LOK or DIS, and the surfaces read the state by its name.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pymongo.errors import ServerSelectionTimeoutError

import app as app_module
import controller.ingestion as ingestion
import controller.templates as templates
import webhook as webhook_module
from classes import ChatBot, ChatbotPhone, Contact
from classes.tool_clock import as_utc
from controller.bot_state import TOKEN_OR_PERMISSION, bot_accepted, bot_refused, bot_verified
from controller.conversations import REASONS
from controller.incoming import IncomingMessage
from controller.meta_templates import MetaPermission, MetaRefused
from controller.webhook_event import webhook_event
from enums import Status
from repository import TenantRepository
from shared import bus

META = TestClient(webhook_module.app)
NUMBER, PHONE_ID = "12025550143", "100000000000001"


class Bifrost:
    def __init__(self):
        self.kept = []

    async def publish(self, subject, payload, message_id, headers=None):
        self.kept.append(subject)
        return bus.Published(stream="TEST", sequence=len(self.kept), duplicate=False)

    async def close(self):
        pass


@pytest.fixture
def bifrost():
    kept = Bifrost()
    bus.configure(kept)
    yield kept
    bus.configure(None)


@pytest.fixture
def http():
    return TestClient(app_module.app)


@pytest.fixture
def caller(register):
    return register("acme")


@pytest.fixture
def repo(caller):
    return TenantRepository(caller.client_id)


@pytest.fixture
def bot(http, caller, repo):
    made = http.post("/bot", headers=caller.auth, json={"name": "Bot", "role": "Sales Assistant",
                                                        "description": "A test chatbot", "tone": "helpful"})
    bot_id = made.json()["data"]["uuid"]
    repo.add_phone(bot_id, ChatbotPhone(phoneNumber="12025550101", phoneId=PHONE_ID))
    return SimpleNamespace(uuid=bot_id, verify_token=made.json()["data"]["verifyToken"])


def state(repo, bot) -> str:
    return Status(repo.get_bot(bot.uuid).status).name


def put_in(bot, status: Status):
    ChatBot.objects(uuid=bot.uuid).update(set__status=status.value)


def verify(bot, token=None):
    return META.get(f"/chat/{bot.uuid}", params={"hub.mode": "subscribe", "hub.challenge": "42",
                                                 "hub.verify_token": token or bot.verify_token})


# --- What the surfaces read (HIG-74) ----------------------------------------------------------------------------------

def test_the_card_and_the_list_say_the_state_by_its_name_and_since_when(http, caller, bot):
    info = http.get(f"/chat/{bot.uuid}/info", headers=caller.auth).json()["data"]
    (listed,) = http.get("/bots", headers=caller.auth).json()["data"]["bots"]

    assert info["status"] == listed["status"] == "REG"
    assert info["statusDate"] is not None and listed["statusDate"] == info["statusDate"]


# --- REG → ACT (HIG-75) -----------------------------------------------------------------------------------------------

def test_meta_verifying_the_webhook_makes_the_chatbot_live(http, caller, repo, bot, caplog):
    # An hour back: the database keeps milliseconds, and both could fall in the same one.
    an_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    ChatBot.objects(uuid=bot.uuid).update(set__statusDate=an_hour_ago)

    response = verify(bot)

    assert (response.status_code, response.text) == (200, "42")
    assert state(repo, bot) == "ACT"
    assert as_utc(repo.get_bot(bot.uuid).statusDate) > an_hour_ago + timedelta(minutes=59)
    assert http.get(f"/chat/{bot.uuid}/info", headers=caller.auth).json()["data"]["status"] == "ACT"
    assert f"Chatbot {bot.uuid} of client {caller.client_id} is now ACT: Meta verified its webhook" in caplog.text


def test_a_verification_that_fails_changes_nothing(repo, bot):
    assert verify(bot, token="not-the-token").status_code == 403
    assert state(repo, bot) == "REG"


@pytest.mark.parametrize("held", [Status.LOK, Status.DIS, Status.ERR], ids=["locked", "disabled", "in error"])
def test_a_verification_takes_no_chatbot_out_of_where_it_is_held(repo, bot, held):
    put_in(bot, held)

    assert verify(bot).status_code == 200
    assert state(repo, bot) == held.name


def test_meta_gets_its_challenge_even_if_the_state_cannot_be_written(repo, bot, monkeypatch):
    def fails(*_args, **_kwargs):
        raise ServerSelectionTimeoutError("no servers")
    monkeypatch.setattr(TenantRepository, "move_bot", fails)

    response = verify(bot)

    assert (response.status_code, response.text) == (200, "42")
    assert state(repo, bot) == "REG"


def notification(caller, bot, message_id="wamid.FIRST"):
    message = IncomingMessage(message_id=message_id, sender=NUMBER, name="Ana", content={
        "messaging_product": "whatsapp", "metadata": {"display_phone_number": "12025550101", "phone_number_id": PHONE_ID},
        "contacts": [{"profile": {"name": "Ana"}, "wa_id": NUMBER}],
        "messages": [{"from": NUMBER, "id": message_id, "timestamp": "1760000000", "type": "text",
                      "text": {"body": "hola"}}]})
    chatbot = SimpleNamespace(uuid=bot.uuid, clientId=caller.client_id)
    return json.loads(json.dumps(webhook_event("whatsapp", chatbot, [message], datetime.now(timezone.utc))))


def test_the_first_signed_notification_makes_a_chatbot_that_was_verified_long_ago_live(caller, repo, bot, bifrost,
                                                                                        monkeypatch):
    monkeypatch.setattr(ingestion, "send_message_to_queue", lambda *a, **k: asyncio.sleep(0))

    assert asyncio.run(ingestion.ingest_notification(notification(caller, bot)))["ingested"] == 1
    assert state(repo, bot) == "ACT"


def test_a_signed_notification_does_not_take_a_chatbot_out_of_err(caller, repo, bot, bifrost, monkeypatch):
    monkeypatch.setattr(ingestion, "send_message_to_queue", lambda *a, **k: asyncio.sleep(0))
    put_in(bot, Status.ERR)

    asyncio.run(ingestion.ingest_notification(notification(caller, bot)))

    assert state(repo, bot) == "ERR"


# --- → ERR, and back (HIG-74) -----------------------------------------------------------------------------------------

def failed(caller, bot, code, reason="rejected"):
    return {"schemaVersion": 2, "botId": str(bot.uuid), "clientId": str(caller.client_id), "contactId": str(uuid4()),
            "channel": "whatsapp", "requestId": str(uuid4()), "code": code, "reason": reason,
            "failedAt": datetime.now(timezone.utc).isoformat()}


@pytest.mark.parametrize("start", [Status.REG, Status.ACT])
@pytest.mark.parametrize("code,reason", [(190, "gave_up"), (0, "gave_up"), (10, "gave_up"), (131005, "rejected")])
def test_meta_turning_the_token_or_its_permissions_away_puts_the_chatbot_in_err(caller, repo, bot, start, code,
                                                                                 reason):
    put_in(bot, start)

    asyncio.run(ingestion.ingest_reply(failed(caller, bot, code, reason)))

    assert state(repo, bot) == "ERR"


@pytest.mark.parametrize("code", [131047, 132015, 131026, None])
def test_what_is_not_the_token_leaves_the_state_alone(caller, repo, bot, code):
    put_in(bot, Status.ACT)

    asyncio.run(ingestion.ingest_reply(failed(caller, bot, code, "rejected" if code else "gave_up")))

    assert state(repo, bot) == "ACT"


def test_a_locked_chatbot_stays_locked_whatever_meta_says(caller, repo, bot):
    put_in(bot, Status.LOK)

    asyncio.run(ingestion.ingest_reply(failed(caller, bot, 190, "gave_up")))

    assert state(repo, bot) == "LOK"


def test_a_send_meta_takes_brings_a_chatbot_in_err_back(caller, repo, bot):
    contact = repo.add_contact(bot.uuid, Contact(firstName="Ana", waId=NUMBER))
    put_in(bot, Status.ERR)
    sent = {"schemaVersion": 2, "botId": str(bot.uuid), "clientId": str(caller.client_id),
            "contactId": str(contact._uuid), "channel": "whatsapp", "messageId": "wamid.SENT",
            "sentAt": datetime.now(timezone.utc).isoformat(), "text": "Hola", "origin": "engine"}

    assert asyncio.run(ingestion.ingest_reply(sent)) == {"stored": True}
    assert state(repo, bot) == "ACT"


def test_a_new_token_brings_a_chatbot_in_err_back_and_the_account_alone_does_not(http, caller, repo, bot):
    put_in(bot, Status.ERR)
    assert http.put(f"/chat/{bot.uuid}/credentials", headers=caller.auth,
                    json={"wabaId": "2391387114704072"}).status_code == 200
    assert state(repo, bot) == "ERR"

    assert http.put(f"/chat/{bot.uuid}/credentials", headers=caller.auth,
                    json={"whatsappToken": "EAAG-a-new-token-of-the-test"}).status_code == 200
    assert state(repo, bot) == "ACT"


class Meta:
    def __init__(self, error=None):
        self.error = error

    def every(self, *_args):
        if self.error:
            raise self.error
        return [], True


@pytest.mark.parametrize("error,after", [
    (MetaPermission(190), "ERR"), (MetaPermission(10), "ERR"),
    # 200 is the permission to manage templates: the chatbot may still send, and it stays.
    (MetaPermission(200), "ACT"), (MetaRefused(100, 33), "ACT"),
], ids=["token", "permission", "template permission only", "another refusal"])
def test_what_meta_says_of_the_token_on_templates_moves_the_chatbot(http, caller, repo, bot, monkeypatch, error,
                                                                     after):
    http.put(f"/chat/{bot.uuid}/credentials", headers=caller.auth,
             json={"wabaId": "2391387114704072", "whatsappToken": "EAAG-a-token-of-the-test"})
    put_in(bot, Status.ACT)
    monkeypatch.setattr(templates, "meta", Meta(error))

    http.get(f"/chat/{bot.uuid}/templates?sync=true", headers=caller.auth)

    assert state(repo, bot) == after


def test_a_call_on_templates_meta_takes_brings_a_chatbot_in_err_back(http, caller, repo, bot, monkeypatch):
    http.put(f"/chat/{bot.uuid}/credentials", headers=caller.auth,
             json={"wabaId": "2391387114704072", "whatsappToken": "EAAG-a-token-of-the-test"})
    put_in(bot, Status.ERR)
    monkeypatch.setattr(templates, "meta", Meta())

    assert http.get(f"/chat/{bot.uuid}/templates?sync=true", headers=caller.auth).status_code == 200
    assert state(repo, bot) == "ACT"


def test_every_code_that_puts_a_chatbot_in_err_has_its_reason_for_the_pwa():
    assert {REASONS[code] for code in TOKEN_OR_PERMISSION} == {"token_rejected", "permission"}


# --- Each move by itself (HIG-137: what mutation testing found unchecked) --------------------------------------------

class Moves:
    """A repository that only writes down the moves it is asked for, and says whether each matched."""
    client_id = "the-client"

    def __init__(self, matched=True):
        self.matched, self.asked = matched, []

    def move_bot(self, bot_uuid, start, to):
        self.asked.append((bot_uuid, start, to))
        return self.matched


def test_each_move_says_whether_it_happened_and_asks_nothing_when_it_cannot_happen():
    bot_uuid = uuid4()
    live, registered = (SimpleNamespace(uuid=bot_uuid, status=status.value) for status in (Status.ACT, Status.REG))

    nothing = Moves()
    assert not bot_verified(nothing, live, "Meta verified its webhook")
    assert not bot_refused(nothing, bot_uuid, 131047)
    assert not bot_accepted(nothing, bot_uuid, "Meta took a send", Status.ACT.value)
    assert nothing.asked == []

    asked = Moves()
    assert bot_verified(asked, registered, "Meta verified its webhook")
    assert bot_refused(asked, bot_uuid, 190)
    assert bot_accepted(asked, bot_uuid, "Meta took a send", Status.ERR.value)
    assert bot_accepted(asked, bot_uuid, "Meta took a send")
    assert asked.asked == [(bot_uuid, (Status.REG,), Status.ACT), (bot_uuid, (Status.REG, Status.ACT), Status.ERR),
                           (bot_uuid, (Status.ERR,), Status.ACT), (bot_uuid, (Status.ERR,), Status.ACT)]

    # The update matched nothing: another process moved it first.
    assert not bot_refused(Moves(matched=False), bot_uuid, 190)


def test_the_warning_of_a_move_says_why_and_only_a_move_writes_one(caplog):
    """The WARNING is the audit of a move, the one production keeps: it says what moved the chatbot."""
    bot_uuid = uuid4()

    bot_refused(Moves(), bot_uuid, 131005)
    bot_accepted(Moves(), bot_uuid, "the client loaded a new token")
    bot_refused(Moves(matched=False), bot_uuid, 190)

    moves = [record.getMessage() for record in caplog.records if record.levelname == "WARNING"]
    assert moves == [f"Chatbot {bot_uuid} of client the-client is now ERR: Meta turned its token away, 131005",
                     f"Chatbot {bot_uuid} of client the-client is now ACT: the client loaded a new token"]
