# -*- coding: utf-8 -*-
"""
    The notices of Meta that a template changed its state (HIG-86): the webhook takes the signed
    `message_template_status_update`, publishes it on `webhook.meta.templates` (CambioDePlantilla, asyncapi-v2.yaml),
    and the ingestion applies it to the copy of its template. Only of the chatbot's WhatsApp Business Account, never
    to a template of another client, and never one delivered late over one that came after it.
"""
import asyncio
import hashlib
import hmac
import json
import os
import pathlib
import time
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker

import webhook as webhook_module
from classes import ChatBot, MessageTemplate
from controller.incoming import template_changes
from controller.ingestion import Unprocessable, ingest_notification
from repository import TenantRepository
from shared import bus

META = TestClient(webhook_module.app)
APP_SECRET = os.environ["FACEBOOK_APP_SECRET"]
ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTRACT = yaml.safe_load((ROOT / "docs" / "02-design" / "contracts" / "asyncapi-v2.yaml").read_text(encoding="utf-8"))
WABA, META_ID = "2391387114704072", "594425479261596"


def schema(name: str) -> Draft202012Validator:
    return Draft202012Validator({"$ref": f"#/components/schemas/{name}", "components": CONTRACT["components"]},
                                format_checker=FormatChecker())


NOTICE, HEADERS = schema("CambioDePlantilla"), schema("Cabeceras")


class KeptEvents:
    def __init__(self):
        self.events = []

    async def publish(self, subject, payload, message_id, headers=None):
        self.events.append(SimpleNamespace(subject=subject, payload=json.loads(payload), message_id=message_id,
                                           headers={**(headers or {}), "Nats-Msg-Id": message_id}))
        return bus.Published(stream="WEBHOOK", sequence=len(self.events), duplicate=False)

    async def close(self):
        pass


@pytest.fixture
def bifrost():
    kept = KeptEvents()
    bus.configure(kept)
    yield kept
    bus.configure(None)


@pytest.fixture
def repo(register):
    return TenantRepository(register("acme").client_id)


@pytest.fixture
def bot(repo):
    made = repo.add_bot(ChatBot(name="Bot", role="Sales Assistant", description="A test chatbot", tone="helpful"))
    return repo.set_bot_credentials(made.uuid, wabaId=WABA)


def stored(repo, bot, status="PENDING", meta_id=META_ID, name="order_update"):
    return repo.save_template(bot, MessageTemplate(name=name, language="es", category="UTILITY", status=status,
                                                   metaId=meta_id, components=[], parameters=0))


def copy(repo, bot, meta_id=META_ID):
    return repo.template_by_meta_id(bot.uuid, meta_id)


def change(event, template_id=META_ID, reason="NONE", **more):
    return {"field": "message_template_status_update",
            "value": {"event": event, "message_template_id": int(template_id), "message_template_name": "order_update",
                      "message_template_language": "es", "reason": reason, **more}}


def notification(*changes, account=WABA, at=None):
    return {"object": "whatsapp_business_account",
            "entry": [{"id": account, "time": at or int(time.time()), "changes": list(changes)}]}


def notify(bot_id, payload, signed=True):
    body = json.dumps(payload).encode()
    headers = {"content-type": "application/json"}
    if signed:
        headers["X-Hub-Signature-256"] = "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return META.post(f"/chat/{bot_id}", content=body, headers=headers)


# --- Each state -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("event,reason,status,kept_reason", [
    ("APPROVED", "NONE", "APPROVED", None),
    ("REJECTED", "INVALID_FORMAT", "REJECTED", "INVALID_FORMAT"),
    ("PAUSED", "NONE", "PAUSED", None),
    ("DISABLED", "SCAM", "DISABLED", "SCAM"),
    ("PENDING_DELETION", "NONE", "PENDING_DELETION", None),
    ("REINSTATED", "NONE", "APPROVED", None),
])
def test_a_signed_notice_leaves_the_copy_in_its_state(repo, bot, event, reason, status, kept_reason):
    stored(repo, bot, status="PAUSED" if event == "REINSTATED" else "PENDING")

    assert notify(bot.uuid, notification(change(event, reason=reason))).status_code == 200

    template = copy(repo, bot)
    assert (template.status, template.statusReason) == (status, kept_reason)


def test_a_template_flagged_by_its_quality_is_still_sent(repo, bot):
    stored(repo, bot, status="APPROVED")

    assert notify(bot.uuid, notification(change("FLAGGED"))).status_code == 200

    assert copy(repo, bot).status == "APPROVED"


def test_the_reason_is_in_the_list_of_templates(repo, bot, register):
    stored(repo, bot)
    notify(bot.uuid, notification(change("REJECTED", reason="INCORRECT_CATEGORY")))
    from controller.templates import ctr_list_templates

    listed = json.loads(ctr_list_templates(repo, str(bot.uuid)).body)["data"]["templates"]

    assert [(t["status"], t["reason"]) for t in listed] == [("REJECTED", "INCORRECT_CATEGORY")]


def test_every_notice_of_a_notification_is_applied(repo, bot):
    stored(repo, bot)
    stored(repo, bot, meta_id="111111111", name="welcome")

    notify(bot.uuid, notification(change("APPROVED"), change("REJECTED", template_id="111111111")))

    assert (copy(repo, bot).status, copy(repo, bot, "111111111").status) == ("APPROVED", "REJECTED")


# --- What is not applied ----------------------------------------------------------------------------------------------

def test_a_notice_without_a_signature_is_turned_away(repo, bot):
    stored(repo, bot)

    assert notify(bot.uuid, notification(change("APPROVED")), signed=False).status_code == 403
    assert copy(repo, bot).status == "PENDING"


def test_a_notice_of_a_template_hive_does_not_have_changes_nothing(repo, bot):
    stored(repo, bot)

    assert notify(bot.uuid, notification(change("APPROVED", template_id="999999999"))).status_code == 200
    assert copy(repo, bot).status == "PENDING"
    assert repo.template_by_meta_id(bot.uuid, "999999999") is None


def test_a_notice_of_another_whatsapp_account_is_not_applied(repo, bot):
    stored(repo, bot)

    assert notify(bot.uuid, notification(change("APPROVED"), account="102290129340398")).status_code == 200
    assert copy(repo, bot).status == "PENDING"


def test_a_chatbot_without_its_whatsapp_account_applies_no_notice(repo):
    made = repo.add_bot(ChatBot(name="Bot", role="r", description="d", tone="t"))
    stored(repo, made)

    assert notify(made.uuid, notification(change("APPROVED"))).status_code == 200
    assert copy(repo, made).status == "PENDING"


def test_a_notice_never_reaches_a_template_of_another_client(repo, bot, register):
    stored(repo, bot)
    theirs = TenantRepository(register("globex").client_id)
    their_bot = theirs.set_bot_credentials(
        theirs.add_bot(ChatBot(name="Theirs", role="r", description="d", tone="t")).uuid, wabaId=WABA)
    stored(theirs, their_bot)

    assert notify(bot.uuid, notification(change("DISABLED"))).status_code == 200

    assert copy(repo, bot).status == "DISABLED"
    assert copy(theirs, their_bot).status == "PENDING"


@pytest.mark.parametrize("value", [
    {"event": "APPROVED", "message_template_name": "order_update"},
    {"event": "approved", "message_template_id": int(META_ID)},
    {"event": "APPROVED", "message_template_id": "59442x"},
    {"event": "APPROVED", "message_template_id": True},
    {"message_template_id": int(META_ID)},
    "not a value",
], ids=["no id", "lower case", "id with letters", "boolean id", "no event", "not an object"])
def test_a_notice_that_does_not_say_which_template_or_what_is_left_out(repo, bot, bifrost, value):
    stored(repo, bot)

    response = notify(bot.uuid, notification({"field": "message_template_status_update", "value": value}))

    assert response.status_code == 200
    assert bifrost.events == []


# --- Order ------------------------------------------------------------------------------------------------------------

def test_a_notice_delivered_late_does_not_undo_one_that_came_after(repo, bot):
    stored(repo, bot)
    now = int(time.time())

    notify(bot.uuid, notification(change("PAUSED"), at=now + 10))
    notify(bot.uuid, notification(change("APPROVED"), at=now + 5))

    assert copy(repo, bot).status == "PAUSED"


def test_a_notice_from_before_what_hive_did_in_meta_does_not_undo_it(repo, bot):
    stored(repo, bot, status="PENDING")

    notify(bot.uuid, notification(change("APPROVED"), at=int(time.time()) - 3600))

    assert copy(repo, bot).status == "PENDING"


def test_meta_may_approve_a_template_just_before_its_creation_is_answered(repo, bot):
    stored(repo, bot, status="PENDING")

    notify(bot.uuid, notification(change("APPROVED"), at=int(time.time()) - 5))

    assert copy(repo, bot).status == "APPROVED"


def test_an_edit_in_hive_clears_the_reason_of_the_state_before(repo, bot):
    stored(repo, bot)
    notify(bot.uuid, notification(change("REJECTED", reason="INVALID_FORMAT")))
    assert copy(repo, bot).statusReason == "INVALID_FORMAT"

    template = copy(repo, bot)
    template.status, template.statusReason = "PENDING", None
    repo.save_template(bot, template)

    assert (copy(repo, bot).status, copy(repo, bot).statusReason) == ("PENDING", None)


# --- On the bus -------------------------------------------------------------------------------------------------------

def test_with_the_bus_the_notices_are_one_event_of_the_contract(repo, bot, bifrost):
    stored(repo, bot)
    at = int(time.time())

    response = notify(bot.uuid, notification(change("APPROVED"), change("PAUSED", template_id="111"), at=at))

    assert response.status_code == 200

    event, = bifrost.events
    assert event.subject == "webhook.meta.templates"
    assert event.payload["changes"] == [
        {"account": WABA, "templateId": META_ID, "event": "APPROVED", "name": "order_update", "language": "es",
         "at": at},
        {"account": WABA, "templateId": "111", "event": "PAUSED", "name": "order_update", "language": "es", "at": at}]
    assert [e.message for e in NOTICE.iter_errors(event.payload)] == []
    assert [e.message for e in HEADERS.iter_errors(event.headers)] == []
    # The webhook only publishes: the copy changes when the ingestion reads the event.
    assert copy(repo, bot).status == "PENDING"

    outcome = asyncio.run(ingest_notification(event.payload))

    assert outcome == {"applied": 1, "unknown": 1}
    assert copy(repo, bot).status == "APPROVED"


def test_a_notification_with_messages_and_notices_is_two_events(repo, bot, bifrost):
    payload = notification(change("APPROVED"))
    payload["entry"].append({"id": WABA, "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"display_phone_number": "1", "phone_number_id": "100000000000001"},
        "contacts": [{"profile": {"name": "Ana"}, "wa_id": "12025550143"}],
        "messages": [{"from": "12025550143", "id": "wamid.A", "timestamp": "1760000000", "type": "text",
                      "text": {"body": "hola"}}]}}]})

    assert notify(bot.uuid, payload).status_code == 200

    assert sorted(event.subject for event in bifrost.events) == ["webhook.meta.templates", "webhook.meta.whatsapp"]


def test_the_same_notice_sent_again_is_dropped_by_the_bus_and_a_new_one_is_not(repo, bot, bifrost):
    at = int(time.time())
    notify(bot.uuid, notification(change("APPROVED"), at=at))
    notify(bot.uuid, notification(change("APPROVED"), at=at))
    notify(bot.uuid, notification(change("APPROVED"), at=at + 60))

    first, again, later = (event.message_id for event in bifrost.events)
    assert first == again != later
    assert first.startswith(f"{bot.uuid}:templates:")


def test_an_event_of_notices_without_its_shape_cannot_be_ingested(bot):
    event = {"schemaVersion": 2, "botId": str(bot.uuid), "clientId": str(bot.clientId), "channel": "templates",
             "changes": []}

    with pytest.raises(Unprocessable):
        asyncio.run(ingest_notification(event))


def test_messenger_has_no_notices_of_templates():
    assert template_changes("page", notification(change("APPROVED"))["entry"]) == []
