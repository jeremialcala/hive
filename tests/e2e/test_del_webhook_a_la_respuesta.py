# -*- coding: utf-8 -*-
"""
    A person writes to a chatbot and gets its answer, through every piece of production (HIG-135, Gate 3):

        Meta ──signed──▶ webhook ──WEBHOOK──▶ ingestion ──MENSAJES──▶ classifier ──▶ coordinator ──▶ responder
             ◀──text── notifier ◀──MOTOR──────────────────────────────────────────────────────────┘
        notifier ──RESPUESTAS──▶ ingestion ──▶ the history

    Meta and the LLM are tests/e2e/fakes.py, which keeps what it was asked. The stack is tests/e2e/conftest.py.
"""
import time

import pytest

from .e2e_stack import PHONE_ID, deliver, notification, seen, until, wanted

if not wanted():
    pytest.skip("The end-to-end test needs Docker and Bifröst: E2E=1 BIFROST_DIR=<higerotech/bifrost>",
                allow_module_level=True)

NUMBER = "12025550143"
REPLY = "Hola Ana, sí: abrimos mañana a las 9."


def texts_to(number: str) -> list:
    return [call for call in seen()["meta"] if call["body"].get("type") == "text" and call["body"].get("to") == number]


def test_a_person_writes_and_gets_the_answer_of_the_engine_through_every_piece(client):
    hive, bot = client["hive"], client["bot"]
    llm_before = len(seen()["llm"])

    answer = deliver(bot, client["app_secret"], notification(NUMBER, "Ana", "wamid.E2E-IN-1", "Hola, ¿abren mañana?"))
    assert answer.status_code == 200, answer.text

    # Meta got the answer the LLM wrote, from the chatbot's number, with the chatbot's token.
    (sent,) = until("the answer at Meta", lambda: texts_to(NUMBER), timeout=120)
    assert sent["phoneId"] == PHONE_ID and sent["authorization"] == "Bearer EAAG-e2e-token"
    assert sent["body"]["text"] == {"body": REPLY, "preview_url": False}
    kinds = [call["kind"] for call in seen()["llm"][llm_before:]]
    assert kinds.count("classifier") == 1 and kinds.count("responder") == 1
    assert all(call["authorization"] == f"Bearer {client['llm_key']}" for call in seen()["llm"])

    # The history has both, the answer as the answer to that message.
    (contact,) = [each for each in hive.call("GET", f"/chat/{bot}/contacts")["contacts"] if each["firstName"] == "Ana"]
    def both():
        messages = hive.call("GET", f"/chat/{bot}/contacts/{contact['uuid']}/conversation")["messages"]
        return messages if len(messages) == 2 else None
    incoming, outgoing = until("the answer in the history", both, timeout=60)
    assert (incoming["direction"], incoming["text"]) == ("in", "Hola, ¿abren mañana?")
    assert (outgoing["direction"], outgoing["text"], outgoing["inReplyTo"]) == ("out", REPLY, "wamid.E2E-IN-1")

    # And the chatbot is live: Meta reached it (HIG-75).
    assert hive.call("GET", f"/chat/{bot}/info")["status"] == "ACT"


def test_a_notification_signed_with_another_secret_reaches_nothing(client):
    before = len(texts_to(NUMBER))

    answer = deliver(client["bot"], "not-the-app-secret", notification(NUMBER, "Ana", "wamid.E2E-FORGED", "Hola"))

    assert answer.status_code == 403
    time.sleep(5)
    assert len(texts_to(NUMBER)) == before
