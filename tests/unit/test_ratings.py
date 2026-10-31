# -*- coding: utf-8 -*-
"""
    The people of the client rate the answers of the bots and see the compliance of each (RF28, HIG-111): who rates
    (a person, either role; a machine credential does not), what can be rated (an answer of the bot in the contact's
    history), that a rating says who and keeps its comment encrypted, that it goes with the contact, and the metrics.
"""
import json
import logging
from datetime import datetime, timedelta, timezone

import mongoengine
import pytest
from fastapi.testclient import TestClient

import app as app_module
from auth.idp import IdentityProvider
from classes import ChatBot, Contact, Rating
from classes.entity_conversation_message import INCOMING, OUTGOING
from conftest import KEY_OF
from enums import PersonRole, PersonStatus
from fake_idp import AUDIENCE, BFF, ISSUER, Zitadel, auth, exchange
from repository import TenantRepository

NUMBER = "12025550143"
SENT = datetime.now(timezone.utc) - timedelta(hours=1)


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
def bot(repo):
    return repo.add_bot(ChatBot(name="Bot", role="Sales Assistant", description="A test chatbot", tone="helpful"))


@pytest.fixture
def ana(repo, bot):
    contact = repo.add_contact(bot.uuid, Contact(firstName="Ana", waId=NUMBER))
    repo.record_message(contact, INCOMING, "wamid.IN", "whatsapp", SENT, text="¿abren mañana?")
    repo.record_message(contact, OUTGOING, "wamid.OUT", "whatsapp", SENT + timedelta(seconds=5),
                        text="Sí, de 9 a 18.", in_reply_to="wamid.IN", origin="engine",
                        evaluation={"verdict": "rewritten", "criterion": "rule", "rule": 1, "model": "gemma",
                                    "seconds": 1.2})
    repo.record_message(contact, OUTGOING, "wamid.CLIENT", "whatsapp", SENT + timedelta(minutes=1),
                        text="Le recordamos su cita.", origin="client", sent_by="person x")
    return contact


@pytest.fixture
def zitadel(monkeypatch):
    idp = Zitadel()
    monkeypatch.setattr(app_module, "IDP", IdentityProvider(ISSUER, AUDIENCE, read_keys=idp.jwks))
    return idp


@pytest.fixture
def logged(caplog):
    caplog.set_level(logging.WARNING)
    return lambda: "\n".join(record.getMessage() for record in caplog.records)


def person(http, repo, zitadel, role: PersonRole, subject: str):
    """A person of the client with that role, and the headers of their token."""
    made = repo.add_person(f"{subject}@acme.test", ISSUER, subject, role, invited_by="test", status=PersonStatus.ACTIVE)
    token = exchange(http, zitadel.id_token(sub=subject)).json()["access_token"]
    KEY_OF[token] = BFF
    return made, auth(token)


@pytest.fixture
def analyst(http, repo, zitadel):
    return person(http, repo, zitadel, PersonRole.ANALYST, "analyst-subject")


@pytest.fixture
def admin(http, repo, zitadel):
    return person(http, repo, zitadel, PersonRole.ADMIN, "admin-subject")


def ref(bot, message_id="wamid.OUT", direction=OUTGOING):
    """The reference the people see of a message, in place of Meta's id (HIG-149)."""
    return TenantRepository(bot.clientId).ref_of(bot.uuid, direction, message_id)


def rate(http, headers, bot, contact, message_id="wamid.OUT", score="down", comment=None):
    body = {"ref": ref(bot, message_id), "score": score, **({"comment": comment} if comment is not None else {})}
    return http.put(f"/chat/{bot.uuid}/contacts/{contact._uuid}/ratings", headers=headers, json=body)


# --- Who rates, and what -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("who", ["analyst", "admin"])
def test_a_person_of_either_role_rates_an_answer_and_the_rating_says_who(http, bot, ana, request, who, logged):
    made, headers = request.getfixturevalue(who)

    response = rate(http, headers, bot, ana, comment="Demasiado largo")

    assert response.status_code == 200
    rating = response.json()["data"]["rating"]
    assert (rating["score"], rating["comment"], rating["ratedBy"]) == ("down", "Demasiado largo", f"person {made.uuid}")
    assert f"Audit: person {made.uuid} rated an answer to contact {ana._uuid}" in logged()
    assert "wamid.OUT" not in logged() and "Demasiado" not in logged()


def test_rating_again_changes_the_rating_of_that_person(http, repo, bot, ana, analyst):
    rate(http, analyst[1], bot, ana, score="down", comment="Mal")
    rate(http, analyst[1], bot, ana, score="up")

    assert Rating.objects.count() == 1
    mine = repo.ratings_of(ana)[ref(bot)][0]
    assert mine["score"] == "up" and mine["comment"] is None


def test_two_people_rate_the_same_answer_each_their_own(http, repo, bot, ana, analyst, admin):
    rate(http, analyst[1], bot, ana, score="down")
    rate(http, admin[1], bot, ana, score="up")

    assert sorted(r["score"] for r in repo.ratings_of(ana)[ref(bot)]) == ["down", "up"]


def test_the_comment_is_stored_encrypted(http, bot, ana, analyst):
    rate(http, analyst[1], bot, ana, comment="Le dio a Ana un horario equivocado")

    stored = json.dumps(list(mongoengine.get_db()["ratings"].find({})), default=str)
    assert "Ana" not in stored and "horario" not in stored


def test_a_machine_credential_does_not_rate(http, caller, bot, ana):
    response = rate(http, caller.auth, bot, ana)

    assert response.status_code == 403 and Rating.objects.count() == 0


@pytest.mark.parametrize("message_id", ["wamid.IN", "wamid.CLIENT", "wamid.NOPE"],
                         ids=["what the person wrote", "what the client sent", "not in the history"])
def test_only_an_answer_of_the_bot_is_rated(http, bot, ana, analyst, message_id):
    response = rate(http, analyst[1], bot, ana, message_id=message_id)

    assert response.status_code == 404 and Rating.objects.count() == 0


def test_an_answer_to_another_contact_is_not_rated_through_this_one(http, repo, bot, ana, analyst):
    other = repo.add_contact(bot.uuid, Contact(firstName="Luis", waId="12025550199"))

    assert rate(http, analyst[1], bot, other).status_code == 404


@pytest.mark.parametrize("body", [
    {"ref": "0" * 24, "score": "meh"},
    {"ref": "0" * 24, "score": "up", "comment": "x" * 501},
    {"ref": "0" * 24, "score": "up", "comment": "a\x00b"},
    {"ref": "0" * 24, "score": "up", "extra": True},
    {"messageId": "wamid.OUT", "score": "up"},
], ids=["unknown score", "comment too long", "control character", "unknown field", "Meta's id"])
def test_a_rating_off_the_contract_is_refused(http, bot, ana, analyst, body):
    response = http.put(f"/chat/{bot.uuid}/contacts/{ana._uuid}/ratings", headers=analyst[1], json=body)

    assert response.status_code == 422 and Rating.objects.count() == 0


def test_the_rating_expires_with_the_answer(http, bot, ana, analyst):
    rate(http, analyst[1], bot, ana)

    rating = Rating.objects.first()
    answer = mongoengine.get_db()["messages"].find_one({"_id": f"{bot.uuid}:out:wamid.OUT"})
    assert rating.expiresAt == answer["expiresAt"]


# --- It goes with the contact, the chatbot and the client --------------------------------------------------------

def test_deleting_the_contact_deletes_its_ratings_and_no_other(http, repo, bot, ana, analyst):
    other = repo.add_contact(bot.uuid, Contact(firstName="Luis", waId="12025550199"))
    repo.record_message(other, OUTGOING, "wamid.OTHER", "whatsapp", SENT, text="Hola", origin="engine")
    rate(http, analyst[1], bot, ana)
    rate(http, analyst[1], bot, other, message_id="wamid.OTHER")

    repo.delete_contact(bot.uuid, ana._uuid)

    assert [r.messageId for r in Rating.objects] == ["wamid.OTHER"]


def test_deleting_the_chatbot_deletes_its_ratings(http, repo, bot, ana, analyst):
    rate(http, analyst[1], bot, ana)

    repo.delete_bot(bot.uuid)

    assert Rating.objects.count() == 0


# --- The conversation, for rating ------------------------------------------------------------------------------------

def test_the_conversation_carries_the_evaluation_and_the_ratings_without_the_number(http, bot, ana, analyst, logged):
    rate(http, analyst[1], bot, ana, comment="Bien, pero largo")

    response = http.get(f"/chat/{bot.uuid}/contacts/{ana._uuid}/conversation", headers=analyst[1])

    assert response.status_code == 200 and NUMBER not in response.text
    # Nor Meta's ids, which in WhatsApp carry the number (HIG-149): opaque references instead.
    assert "wamid" not in response.text
    messages = {m["ref"]: m for m in response.json()["data"]["messages"]}
    out, incoming = messages[ref(bot)], messages[ref(bot, "wamid.IN", INCOMING)]
    assert out["evaluation"] == {"verdict": "rewritten", "criterion": "rule", "rule": 1, "model": "gemma",
                                 "seconds": 1.2}
    assert out["inReplyToRef"] == incoming["ref"]
    assert [(r["score"], r["comment"]) for r in out["ratings"]] == [("down", "Bien, pero largo")]
    assert incoming["ratings"] == [] and incoming["evaluation"] is None
    # Opening it to rate leaves its line of audit, as reading it always did.
    assert f"Audit: person {analyst[0].uuid} read the conversation of contact {ana._uuid}" in logged()


# --- The metrics -----------------------------------------------------------------------------------------------------

def test_the_metrics_count_the_evaluator_the_fixed_texts_and_the_ratings(http, repo, bot, ana, analyst, admin):
    repo.record_message(ana, OUTGOING, "wamid.FIX", "whatsapp", SENT, text="Lo siento.", origin="engine",
                        evaluation={"verdict": "fixed"})
    rate(http, analyst[1], bot, ana, score="down")
    rate(http, admin[1], bot, ana, score="up")

    response = http.get(f"/chat/{bot.uuid}/compliance", headers=analyst[1])

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["answers"] == 2 and data["verdicts"]["rewritten"] == 1 and data["fixedTexts"] == 1
    assert data["criteria"] == {"rule": 1} and data["rules"] == {"1": 1}
    assert data["ratings"] == {"up": 1, "down": 1, "answers": 1} and data["since"] is None
    assert NUMBER not in response.text and "de 9 a 18" not in response.text


def test_the_metrics_can_start_at_a_date(http, caller, bot, ana):
    later = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

    data = http.get(f"/chat/{bot.uuid}/compliance", headers=caller.auth, params={"since": later}).json()["data"]

    assert data["answers"] == 0 and data["since"] is not None


def test_another_client_does_not_see_the_metrics(http, bot, ana, register):
    stranger = register("globex")

    assert http.get(f"/chat/{bot.uuid}/compliance", headers=stranger.auth).status_code == 404


# --- The answers to rate (HIG-149) -----------------------------------------------------------------------------------

def test_the_answers_to_rate_come_newest_first_without_text_number_nor_meta_id(http, repo, bot, ana, analyst):
    repo.record_message(ana, OUTGOING, "wamid.LATER", "whatsapp", SENT + timedelta(minutes=5), text="Hasta luego.",
                        origin="engine", evaluation={"verdict": "approved"})

    response = http.get(f"/chat/{bot.uuid}/compliance/answers", headers=analyst[1])

    assert response.status_code == 200
    answers = response.json()["data"]["answers"]
    assert [a["ref"] for a in answers] == [ref(bot, "wamid.LATER"), ref(bot)]
    assert answers[1]["evaluation"]["verdict"] == "rewritten" and answers[1]["flagged"] is True
    assert answers[0]["contact"] == {"uuid": str(ana._uuid), "firstName": "Ana"}
    # Only what the bot sent and the evaluator judged: not what the client sent.
    assert len(answers) == 2
    for part in (NUMBER, "wamid", "Hasta luego", "de 9 a 18"):
        assert part not in response.text


@pytest.mark.parametrize("which,expected", [("unrated", ["wamid.LATER"]), ("flagged", ["wamid.OUT", "wamid.LATER"]),
                                            ("all", ["wamid.LATER", "wamid.OUT"])])
def test_the_answers_to_rate_can_be_filtered(http, repo, bot, ana, analyst, which, expected):
    repo.record_message(ana, OUTGOING, "wamid.LATER", "whatsapp", SENT + timedelta(minutes=5), text="Ok.",
                        origin="engine", evaluation={"verdict": "approved"})
    # The answer that was rewritten is rated; the later one, approved, is rated bad only in the flagged case.
    rate(http, analyst[1], bot, ana, score="up")
    if which == "flagged":
        rate(http, analyst[1], bot, ana, message_id="wamid.LATER", score="down")

    answers = http.get(f"/chat/{bot.uuid}/compliance/answers", headers=analyst[1],
                       params={"filter": which}).json()["data"]["answers"]

    assert sorted(a["ref"] for a in answers) == sorted(ref(bot, message_id) for message_id in expected)


def test_an_unknown_filter_is_refused(http, caller, bot):
    assert http.get(f"/chat/{bot.uuid}/compliance/answers", headers=caller.auth,
                    params={"filter": "everything"}).status_code == 422


def test_another_client_does_not_see_the_answers(http, bot, ana, register):
    assert http.get(f"/chat/{bot.uuid}/compliance/answers", headers=register("globex").auth).status_code == 404
