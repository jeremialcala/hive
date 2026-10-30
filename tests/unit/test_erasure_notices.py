# -*- coding: utf-8 -*-
"""
    The notices of erasure (ADR-0017, RF15): once a contact, a chatbot or a client is deleted, the management API says
    so on SUPRESIONES, one Supresion of asyncapi-v2.yaml per thing deleted, with identifiers only. A notice the bus does
    not take does not stop the deletion. The bus is replaced by a publisher that keeps what it is handed.
"""
import json
import logging
import pathlib
from types import SimpleNamespace
from uuid import UUID

import pytest
import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

import app as app_module
from shared import bus
from classes import ChatBot, Contact
from repository import TenantRepository

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTRACT = yaml.safe_load((ROOT / "docs" / "02-design" / "contracts" / "asyncapi-v2.yaml").read_text(encoding="utf-8"))
NOTICE = Draft202012Validator({"$ref": "#/components/schemas/Supresion", "components": CONTRACT["components"]})
# The subjects of SUPRESIONES: gateway.{entidad}.erased, with each entity of the contract.
SUBJECTS = {f"gateway.{entity}.erased" for entity in CONTRACT["components"]["parameters"]["entidad"]["enum"]}


class Notices:
    """Stands for Bifröst: keeps what it is handed, or refuses it."""

    def __init__(self):
        self.kept, self.refuse = [], False

    async def publish(self, subject, payload, message_id, headers=None):
        if self.refuse:
            raise bus.BusUnavailable("TimeoutError")
        self.kept.append(SimpleNamespace(subject=subject, notice=json.loads(payload), message_id=message_id,
                                         headers=headers or {}))
        return bus.Published(stream="SUPRESIONES", sequence=len(self.kept), duplicate=False)

    async def close(self):
        pass


@pytest.fixture
def bifrost():
    kept = Notices()
    bus.configure(kept)
    yield kept
    bus.configure(None)


@pytest.fixture
def http():
    return TestClient(app_module.app)


@pytest.fixture
def acme(register):
    return register("acme")


@pytest.fixture
def bot(acme):
    return TenantRepository(acme.client_id).add_bot(
        ChatBot(name="Bot", role="Sales Assistant", description="A test chatbot", tone="helpful"))


def contact(acme, bot, number="12025550143"):
    return TenantRepository(acme.client_id).add_contact(bot.uuid, Contact(firstName="Ana", waId=number))


def assert_they_are_the_contracts(notices):
    for each in notices:
        assert [error.message for error in NOTICE.iter_errors(each.notice)] == []
        assert each.subject in SUBJECTS
        assert each.message_id == each.notice["uuid"]
        assert each.headers["traceparent"].startswith("00-")


# --- One notice per thing deleted ------------------------------------------------------------------------------------

def test_deleting_a_contact_says_so_with_its_identifiers_only(http, acme, bot, bifrost):
    ana = contact(acme, bot)

    deleted = http.delete(f"/chat/{bot.uuid}/contacts/{ana._uuid}", headers=acme.auth)
    assert deleted.status_code == 200

    sent, = bifrost.kept
    assert sent.subject == "gateway.contact.erased"
    assert (sent.notice["entity"], sent.notice["clientId"], sent.notice["botId"], sent.notice["contactId"]) == (
        "contact", str(acme.client_id), str(bot.uuid), str(ana._uuid))
    assert "12025550143" not in json.dumps(sent.notice) and "Ana" not in json.dumps(sent.notice)
    assert_they_are_the_contracts(bifrost.kept)


def test_every_copy_of_the_person_gets_its_own_notice(http, acme, bot, bifrost):
    """Each copy has a context of its own in the engine: one notice each."""
    first, second = contact(acme, bot), contact(acme, bot)

    http.delete(f"/chat/{bot.uuid}/contacts/{first._uuid}", headers=acme.auth)

    assert {sent.notice["contactId"] for sent in bifrost.kept} == {str(first._uuid), str(second._uuid)}
    assert len({sent.notice["uuid"] for sent in bifrost.kept}) == 2


def test_deleting_a_chatbot_says_so(http, acme, bot, bifrost):
    contact(acme, bot)

    deleted = http.delete(f"/bot/{bot.uuid}", headers=acme.auth)
    assert deleted.status_code == 200

    sent, = bifrost.kept
    assert (sent.subject, sent.notice["entity"], sent.notice["botId"]) == ("gateway.bot.erased", "bot", str(bot.uuid))
    assert "contactId" not in sent.notice
    assert_they_are_the_contracts(bifrost.kept)


def test_deleting_a_client_says_so(http, operator, acme, bot, bifrost):
    deleted = http.delete(f"/clients/{acme.client_id}", headers=operator.auth)
    assert deleted.status_code == 200

    sent, = bifrost.kept
    assert (sent.subject, sent.notice["entity"], sent.notice["clientId"]) == ("gateway.client.erased", "client",
                                                                            str(acme.client_id))
    assert "botId" not in sent.notice
    assert_they_are_the_contracts(bifrost.kept)


# --- What is not said ------------------------------------------------------------------------------------------------

def test_a_deletion_of_something_that_does_not_exist_says_nothing(http, acme, bot, bifrost):
    deleted = http.delete(f"/chat/{bot.uuid}/contacts/{UUID(int=1)}", headers=acme.auth)
    assert deleted.status_code == 404
    deleted = http.delete(f"/bot/{UUID(int=1)}", headers=acme.auth)
    assert deleted.status_code == 404

    assert bifrost.kept == []


def test_without_a_bus_a_deletion_says_nothing_and_still_happens(http, acme, bot):
    ana = contact(acme, bot)

    deleted = http.delete(f"/chat/{bot.uuid}/contacts/{ana._uuid}", headers=acme.auth)
    assert deleted.status_code == 200
    assert Contact.objects.count() == 0


# --- When the bus does not take it -----------------------------------------------------------------------------------

def test_a_notice_the_bus_does_not_take_does_not_stop_the_deletion(http, acme, bot, bifrost, caplog):
    ana = contact(acme, bot)
    bifrost.refuse = True
    caplog.set_level(logging.WARNING)

    response = http.delete(f"/chat/{bot.uuid}/contacts/{ana._uuid}", headers=acme.auth)

    assert response.status_code == 200 and Contact.objects.count() == 0
    assert "The bus did not take the notice of erasure of a contact" in caplog.text
    assert "expires by itself within 24 hours" in caplog.text
