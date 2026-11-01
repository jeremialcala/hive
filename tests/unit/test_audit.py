# -*- coding: utf-8 -*-
"""
    The audit of a client (RF18, HIG-95): every request that changes something leaves an entry with who, what, on which
    resources, when and what came of it, denied attempts included; so do the reads of personal data and what the
    response engine reads. Only identifiers. The cliente-admin reads it, by pages, with filters and as CSV; the
    cliente-analista, the engine and another client do not. It goes with the client.
"""
import csv
import io
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app as app_module
from auth.idp import IdentityProvider
from classes import AuditEntry, Client, Contact
from classes.entity_conversation_message import INCOMING
from auth.client_keys import public_signing_key
from conftest import KEY_OF, bearer, signed_assertion
from datetime import datetime, timezone
from enums import PersonRole, PersonStatus
from fake_idp import AUDIENCE, BFF, ISSUER, Zitadel, auth, exchange
from repository import TenantRepository
from shared.auth.client import new_signing_key, public_part

NUMBER = "12025550143"
BOT = {"name": "Mine", "role": "Sales Assistant", "description": "A test chatbot", "tone": "helpful"}


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
def bot(http, caller):
    return http.post("/bot", headers=caller.auth, json=BOT).json()["data"]["uuid"]


@pytest.fixture
def ana(repo, bot):
    contact = repo.add_contact(bot, Contact(firstName="Ana", waId=NUMBER))
    repo.record_message(contact, INCOMING, "wamid.IN", "whatsapp", datetime.now(timezone.utc), text="¿abren mañana?")
    return contact


@pytest.fixture
def people(http, repo, monkeypatch):
    """A cliente-admin and a cliente-analista of acme, each with the headers of their token."""
    idp = Zitadel()
    monkeypatch.setattr(app_module, "IDP", IdentityProvider(ISSUER, AUDIENCE, read_keys=idp.jwks))
    made = {}
    for role, subject in ((PersonRole.ADMIN, "admin-subject"), (PersonRole.ANALYST, "analyst-subject")):
        person = repo.add_person(f"{subject}@acme.test", ISSUER, subject, role, invited_by="test",
                                 status=PersonStatus.ACTIVE)
        token = exchange(http, idp.id_token(sub=subject)).json()["access_token"]
        KEY_OF[token] = BFF
        made[role] = SimpleNamespace(uuid=person.uuid, auth=auth(token))
    return SimpleNamespace(admin=made[PersonRole.ADMIN], analyst=made[PersonRole.ANALYST])


def audit(http, who, **params):
    return http.get("/audit", headers=who.auth, params=params)


def entries(http, who, **params) -> list:
    response = audit(http, who, **params)
    assert response.status_code == 200, response.text
    return response.json()["data"]["entries"]


def test_a_change_leaves_who_what_on_which_resource_when_and_how_it_came_out(http, caller, bot):
    rule = http.post(f"/chat/{bot}/rules", headers=caller.auth, json={"rules": ["Be kind"]})
    assert rule.status_code == 200

    found = entries(http, caller)

    latest = found[0]
    assert (latest["action"], latest["method"], latest["route"], latest["category"]) == (
        "add_chatbot_rule", "POST", "/chat/{bot_id}/rules", "config")
    assert (latest["actor"], latest["actorKind"], latest["result"], latest["status"]) == (
        f"key {caller.kid}", "machine", "ok", 200)
    assert latest["resource"] == {"bot_id": bot}
    assert datetime.fromisoformat(latest["at"]).tzinfo is not None
    # The chatbot was created first: newest first.
    assert [each["action"] for each in found] == ["add_chatbot_rule", "create_chatbot"]


def test_a_person_is_written_as_the_person(http, bot, people):
    http.put(f"/chat/{bot}/persona", headers=people.admin.auth,
             json={**BOT, "name": "Ana"})

    latest = entries(http, people.admin)[0]

    assert (latest["actor"], latest["actorKind"], latest["action"]) == (
        f"person {people.admin.uuid}", "person", "set_chatbot_persona")


def test_a_denied_attempt_is_kept_with_its_result(http, bot, people):
    denied = http.post(f"/chat/{bot}/rules", headers=people.analyst.auth, json={"rules": ["Be rude"]})
    assert denied.status_code == 403

    latest = entries(http, people.admin, result="denied")[0]

    assert (latest["actor"], latest["action"], latest["result"], latest["status"]) == (
        f"person {people.analyst.uuid}", "add_chatbot_rule", "denied", 403)


def test_reading_personal_data_is_kept_and_other_reads_are_not(http, caller, bot, ana):
    before = len(entries(http, caller))

    http.get(f"/chat/{bot}/contacts/{ana._uuid}/conversation", headers=caller.auth)
    http.get(f"/chat/{bot}/contacts/{ana._uuid}", headers=caller.auth)
    http.get(f"/chat/{bot}/rules", headers=caller.auth)
    http.get("/bots", headers=caller.auth)

    found = entries(http, caller, category="data")
    assert [each["action"] for each in found] == ["export_contact", "get_conversation"]
    assert all(each["resource"] == {"bot_id": bot, "contact_id": str(ana._uuid)} for each in found)
    assert len(entries(http, caller)) == before + 2


def test_what_the_engine_reads_is_kept_with_its_worker(http, caller, bot):
    key = new_signing_key()
    registered = public_signing_key(public_part(key))
    registered["worker"] = "coordinator"
    Client.objects(uuid=caller.client_id).update_one(push__serviceKeys=registered)

    read = http.get(f"/chat/{bot}/rules", headers=bearer(signed_assertion(key, caller.client_id)))
    assert read.status_code == 200, read.text

    latest = entries(http, caller, category="engine")[0]
    assert (latest["actor"], latest["actorKind"], latest["worker"], latest["action"]) == (
        f"key {registered['kid']}", "engine", "coordinator", "get_chatbot_rules")


def test_no_body_text_number_nor_name_goes_into_an_entry(http, caller, bot, ana):
    http.put(f"/chat/{bot}/persona", headers=caller.auth, json={**BOT, "name": "persona-marker"})
    http.post(f"/chat/{bot}/rules", headers=caller.auth, json={"rules": ["rule-marker"]})
    http.get(f"/chat/{bot}/contacts/{ana._uuid}/conversation", headers=caller.auth)
    http.post("/contacts/search", headers=caller.auth, json={"waId": NUMBER})

    stored = str([entry.to_mongo().to_dict() for entry in AuditEntry.objects])

    for marker in ("persona-marker", "rule-marker", NUMBER, "Ana", "abren"):
        assert marker not in stored


def test_what_the_address_carries_that_is_not_an_identifier_is_not_kept(http, caller):
    http.delete("/chat/address-marker%0AFORGED/rules/x", headers=caller.auth)

    latest = entries(http, caller)[0]

    assert latest["resource"]["bot_id"] == "?" and "address-marker" not in str(latest)


def test_the_audit_is_filtered_by_category_action_actor_resource_and_date(http, caller, bot, people):
    other = http.post("/bot", headers=caller.auth, json=BOT).json()["data"]["uuid"]
    http.post(f"/chat/{bot}/rules", headers=caller.auth, json={"rules": ["Be kind"]})
    http.put(f"/chat/{bot}/persona", headers=people.admin.auth, json={**BOT, "name": "Ana"})

    assert {each["resource"].get("bot_id") for each in entries(http, caller, resource=bot)} == {bot}
    # Creating a chatbot carries no id in its address: the entry says what was done, not on which.
    assert len(entries(http, caller, action="create_chatbot")) == 2 and other
    assert [each["action"] for each in entries(http, caller, actor=f"person {people.admin.uuid}")] == [
        "set_chatbot_persona"]
    assert entries(http, caller, category="people") == []
    assert entries(http, caller, since="2999-01-01T00:00:00Z") == []
    assert len(entries(http, caller, until="2999-01-01T00:00:00Z")) == len(entries(http, caller))


@pytest.mark.parametrize("params", [{"category": "everything"}, {"result": "maybe"}, {"since": "yesterday"},
                                    {"resource": "not-an-id"}, {"format": "xml"}],
                         ids=["category", "result", "date", "resource", "format"])
def test_a_filter_out_of_its_values_is_refused(http, caller, params):
    assert audit(http, caller, **params).status_code == 422


def test_the_audit_comes_by_pages(http, caller, bot):
    for number in range(4):
        http.post(f"/chat/{bot}/rules", headers=caller.auth, json={"rules": [f"Rule {number}"]})

    first = audit(http, caller, limit=2).json()["data"]
    second = audit(http, caller, limit=2, offset=2).json()["data"]

    assert first["total"] == second["total"] == 5 and first["keptFor"] == 365
    assert len(first["entries"]) == len(second["entries"]) == 2
    assert [each["action"] for each in first["entries"] + second["entries"]] == ["add_chatbot_rule"] * 4


def test_the_audit_comes_as_csv(http, caller, bot):
    response = audit(http, caller, format="csv")

    assert response.status_code == 200 and response.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(response.text)))
    assert rows[0][:5] == ["at", "actor", "actorKind", "worker", "action"]
    assert len(rows) == 2 and (rows[1][4], rows[1][8], rows[1][9]) == ("create_chatbot", "", "ok")


def test_the_analyst_the_engine_and_another_client_do_not_read_it(http, caller, bot, people, register):
    key = new_signing_key()
    registered = public_signing_key(public_part(key))
    registered["worker"] = "coordinator"
    Client.objects(uuid=caller.client_id).update_one(push__serviceKeys=registered)
    globex = register("globex")

    assert audit(http, people.analyst).status_code == 403
    assert http.get("/audit", headers=bearer(signed_assertion(key, caller.client_id))).status_code == 403
    assert entries(http, globex) == []


def test_the_audit_goes_with_the_client(http, caller, bot, repo):
    assert AuditEntry.objects(clientId=caller.client_id).count() > 0

    deleted = repo.erase_all()

    assert deleted["audit"] > 0 and AuditEntry.objects(clientId=caller.client_id).count() == 0


def test_an_entry_expires_after_twelve_months(http, caller, bot):
    entry = AuditEntry.objects(clientId=caller.client_id).first()

    assert 364 <= (entry.expiresAt - entry.at).days <= 365
    assert any(index.get("expireAfterSeconds") == 0 for index in AuditEntry._meta["indexes"])
