# -*- coding: utf-8 -*-
"""
    What the engine's evaluator decided of each answer, kept with the message (HIG-147, RF28): the ingestion stores it
    from the RespuestaEnviada, without any text, and the metrics of compliance of a chatbot count it. It expires and
    is deleted with the message, and an answer delivered again does not count twice.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import mongoengine
import pytest

from classes import ChatBot, Contact, ConversationMessage
from controller.ingestion import ingest_reply
from repository import TenantRepository
from shared.contract import Evaluation, SentReply


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
    return repo.add_contact(bot.uuid, Contact(firstName="Ana", waId="12025550143"))


def answer(caller, bot, contact, message_id="wamid.SENT", evaluation=None, sent_at=None, **more) -> dict:
    event = {"schemaVersion": 2, "botId": str(bot.uuid), "clientId": str(caller.client_id),
             "contactId": str(contact._uuid), "channel": "whatsapp", "messageId": message_id,
             "inReplyTo": "wamid.IN", "sentAt": (sent_at or datetime.now(timezone.utc)).isoformat(),
             "text": "Abrimos de 9 a 18.", "origin": "engine", **more}
    if evaluation is not None:
        event["evaluation"] = evaluation
    return event


def stored(bot, message_id="wamid.SENT") -> dict:
    return mongoengine.get_db()["messages"].find_one({"_id": f"{bot.uuid}:out:{message_id}"})


def test_the_decision_of_the_evaluator_is_stored_with_the_answer(caller, bot, ana):
    evaluation = {"verdict": "rewritten", "criterion": "rule", "rule": 2, "model": "gemma-4-26b-a4b", "seconds": 1.3}

    assert asyncio.run(ingest_reply(answer(caller, bot, ana, evaluation=evaluation))) == {"stored": True}

    assert stored(bot)["evaluation"] == evaluation


def test_the_event_follows_the_contract_and_carries_no_text():
    event = SentReply(schemaVersion=2, botId=uuid4(), clientId=uuid4(), contactId=uuid4(), channel="whatsapp",
                      messageId="wamid.SENT", sentAt=datetime.now(timezone.utc), text="Hola",
                      evaluation=Evaluation(verdict="approved", model="gemma-4-26b-a4b"))

    assert set(json.loads(event.model_dump_json())["evaluation"]) == {"verdict", "criterion", "rule", "model",
                                                                         "seconds"}


def test_an_answer_without_evaluation_is_stored_as_before(caller, bot, ana):
    asyncio.run(ingest_reply(answer(caller, bot, ana)))

    assert "evaluation" not in stored(bot) or stored(bot)["evaluation"] is None


@pytest.mark.parametrize("evaluation", [
    {"verdict": "maybe"},
    {"verdict": "approved", "rule": 99},
    {"verdict": "approved", "text": "Abrimos de 9 a 18."},
], ids=["unknown verdict", "rule out of range", "with a text"])
def test_an_evaluation_off_the_contract_is_left_out_and_the_answer_kept(caller, bot, ana, evaluation, caplog):
    assert asyncio.run(ingest_reply(answer(caller, bot, ana, evaluation=evaluation))) == {"stored": True}

    assert not stored(bot).get("evaluation")
    assert "does not follow the contract" in caplog.text and "Abrimos" not in caplog.text


def test_a_message_of_the_client_is_not_judged(caller, bot, ana):
    asyncio.run(ingest_reply(answer(caller, bot, ana, evaluation={"verdict": "approved"}, origin="client",
                                    sentBy="key kid-1")))

    assert not stored(bot).get("evaluation")


def test_an_answer_delivered_again_counts_once(caller, repo, bot, ana):
    event = answer(caller, bot, ana, evaluation={"verdict": "approved"})

    asyncio.run(ingest_reply(event))
    assert asyncio.run(ingest_reply(event)) == {"stored": False}

    assert repo.compliance(bot.uuid)["verdicts"]["approved"] == 1


def test_the_evaluation_goes_with_the_contact(caller, repo, bot, ana):
    asyncio.run(ingest_reply(answer(caller, bot, ana, evaluation={"verdict": "approved"})))

    repo.delete_contact(bot.uuid, ana._uuid)

    assert stored(bot) is None and repo.compliance(bot.uuid)["answers"] == 0


def test_the_evaluation_expires_with_the_message(caller, bot, ana):
    asyncio.run(ingest_reply(answer(caller, bot, ana, evaluation={"verdict": "approved"})))

    message = ConversationMessage.objects(key=f"{bot.uuid}:out:wamid.SENT").first()
    assert message.evaluation.verdict == "approved" and message.expiresAt > datetime.now()


# --- The metrics of compliance --------------------------------------------------------------------------------------

def test_the_metrics_count_each_verdict_and_what_failed(caller, repo, bot, ana):
    for n, evaluation in enumerate([
            {"verdict": "approved"}, {"verdict": "approved"}, {"verdict": "fixed"},
            {"verdict": "rewritten", "criterion": "rule", "rule": 2},
            {"verdict": "replaced", "criterion": "rule", "rule": 2},
            {"verdict": "replaced", "criterion": "language"},
            {"verdict": "unevaluated"}]):
        asyncio.run(ingest_reply(answer(caller, bot, ana, f"wamid.{n}", evaluation)))
    # An answer from before the evaluator, and a message of the client, do not count.
    asyncio.run(ingest_reply(answer(caller, bot, ana, "wamid.OLD")))

    metrics = repo.compliance(bot.uuid)

    assert metrics == {"answers": 7,
                       "verdicts": {"approved": 2, "rewritten": 1, "replaced": 2, "fixed": 1, "unevaluated": 1},
                       "criteria": {"rule": 2, "language": 1}, "rules": {2: 2}}


def test_the_metrics_can_start_at_a_date(caller, repo, bot, ana):
    now = datetime.now(timezone.utc)
    asyncio.run(ingest_reply(answer(caller, bot, ana, "wamid.OLD", {"verdict": "replaced", "criterion": "tone"},
                                    sent_at=now - timedelta(days=40))))
    asyncio.run(ingest_reply(answer(caller, bot, ana, "wamid.NEW", {"verdict": "approved"})))

    metrics = repo.compliance(bot.uuid, since=now - timedelta(days=30))

    assert metrics["answers"] == 1 and metrics["criteria"] == {}


def test_the_metrics_are_of_the_chatbot_and_the_client_that_own_them(caller, repo, bot, ana, register):
    asyncio.run(ingest_reply(answer(caller, bot, ana, evaluation={"verdict": "approved"})))
    other_bot = repo.add_bot(ChatBot(name="Otro", role="Sales Assistant", description="Another", tone="helpful"))

    assert repo.compliance(other_bot.uuid)["answers"] == 0
    assert TenantRepository(register("globex").client_id).compliance(bot.uuid)["answers"] == 0
