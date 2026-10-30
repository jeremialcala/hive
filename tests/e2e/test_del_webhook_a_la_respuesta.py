# -*- coding: utf-8 -*-
"""
    A person writes to a chatbot and gets its answer, through every piece of production (HIG-135, Gate 3):

        Meta ──signed──▶ webhook ──WEBHOOK──▶ ingestion ──MENSAJES──▶ classifier ──▶ coordinator ──▶ responder
             ◀──text── notifier ◀──MOTOR──────────────────────────────────────────────────────────┘
        notifier ──RESPUESTAS──▶ ingestion ──▶ the history

    Meta and the LLM are tests/e2e/fakes.py, which keeps what it was asked. The stack is tests/e2e/conftest.py.
"""
import hashlib
import hmac
import json
import time

import pytest
import requests

from .e2e_stack import FAKES, WEBHOOK, until, wanted

if not wanted():
    pytest.skip("The end-to-end test needs Docker and Bifröst: E2E=1 BIFROST_DIR=<higerotech/bifrost>",
                allow_module_level=True)

NUMBER, PHONE_ID = "12025550143", "100000000000001"
REPLY = "Hola Ana, sí: abrimos mañana a las 9."


def notification(message_id: str, text: str) -> bytes:
    return json.dumps({"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{
        "field": "messages", "value": {
            "messaging_product": "whatsapp",
            "metadata": {"display_phone_number": "12025550101", "phone_number_id": PHONE_ID},
            "contacts": [{"profile": {"name": "Ana"}, "wa_id": NUMBER}],
            "messages": [{"from": NUMBER, "id": message_id, "timestamp": str(int(time.time())), "type": "text",
                          "text": {"body": text}}]}}]}]}).encode()


def seen() -> dict:
    return requests.get(f"{FAKES}/_seen", timeout=5).json()


def texts_to(number: str) -> list:
    return [call for call in seen()["meta"] if call["body"].get("type") == "text" and call["body"].get("to") == number]


def test_a_person_writes_and_gets_the_answer_of_the_engine_through_every_piece(client):
    hive, bot = client["hive"], client["bot"]
    raw = notification("wamid.E2E-IN-1", "Hola, ¿abren mañana?")
    signature = "sha256=" + hmac.new(client["app_secret"].encode(), raw, hashlib.sha256).hexdigest()

    answer = requests.post(f"{WEBHOOK}/chat/{bot}", data=raw, timeout=10,
                           headers={"Content-Type": "application/json", "X-Hub-Signature-256": signature})
    assert answer.status_code == 200, answer.text

    # Meta got the answer the LLM wrote, from the chatbot's number, with the chatbot's token.
    (sent,) = until("the answer at Meta", lambda: texts_to(NUMBER), timeout=120)
    assert sent["phoneId"] == PHONE_ID and sent["authorization"] == "Bearer EAAG-e2e-token"
    assert sent["body"]["text"] == {"body": REPLY, "preview_url": False}
    kinds = [call["kind"] for call in seen()["llm"]]
    assert kinds.count("classifier") == 1 and kinds.count("responder") == 1
    assert all(call["authorization"] == f"Bearer {client['llm_key']}" for call in seen()["llm"])

    # The history has both, the answer as the answer to that message.
    (contact,) = hive.call("GET", f"/chat/{bot}/contacts")["contacts"]
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
    raw = notification("wamid.E2E-FORGED", "Hola")
    signature = "sha256=" + hmac.new(b"not-the-app-secret", raw, hashlib.sha256).hexdigest()

    answer = requests.post(f"{WEBHOOK}/chat/{client['bot']}", data=raw, timeout=10,
                           headers={"Content-Type": "application/json", "X-Hub-Signature-256": signature})

    assert answer.status_code == 403
    time.sleep(5)
    assert len(texts_to(NUMBER)) == before
