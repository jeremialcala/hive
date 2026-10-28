# -*- coding: utf-8 -*-
"""
    H14: what waits on MongoDB, the key service or the Graph API never holds the event loop of the webhook or of the
    ingestion worker. On midgard one read that never came back left a whole process without an answer, its health
    check included. Each test makes one of those waits slow, 1.5 s, and checks that the rest of the process goes on
    meanwhile: the health check of the webhook, or another coroutine of the worker.
"""
import asyncio
import inspect
import time
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

import webhook as webhook_module
from controller import facebook, ingestion
from controller.webhook_event import SCHEMA_VERSION
from repository import NotFound, TenantRepository

SLOW = 1.5
SIGNATURE = "sha256=" + "0" * 64


def slowly(answer=None, error: Exception | None = None):
    def wait(*_args, **_kwargs):
        time.sleep(SLOW)
        if error is not None:
            raise error
        return answer
    return wait


async def health_while(request) -> tuple[float, int]:
    """Starts `request` against the webhook, asks for /health meanwhile, and says how long that took and the code."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=webhook_module.app),
                                 base_url="http://testserver") as client:
        # Timed from before the slow request: a held event loop would hold the clock too.
        started = time.monotonic()
        slow = asyncio.create_task(request(client))
        await asyncio.sleep(0.3)
        health = await client.get("/health")
        answered_after = time.monotonic() - started
        response = await slow
        assert health.status_code == 200
        return answered_after, response.status_code


def test_a_slow_read_of_the_chatbot_does_not_hold_the_webhook(monkeypatch):
    monkeypatch.setattr(webhook_module, "find_webhook_bot", slowly(error=NotFound("chatbot")))

    answered_after, code = asyncio.run(health_while(lambda client: client.post(
        f"/chat/{uuid4()}", content=b"{}", headers={"X-Hub-Signature-256": SIGNATURE})))

    assert code == 404
    assert answered_after < 1.0


def test_opening_the_secrets_slowly_does_not_hold_the_webhook(monkeypatch):
    bot = SimpleNamespace(uuid=uuid4(), clientId=uuid4())
    monkeypatch.setattr(webhook_module, "find_webhook_bot", lambda bot_id: bot)
    monkeypatch.setattr(webhook_module, "webhook_secrets", slowly(SimpleNamespace(app_secret="not this one",
                                                                                  verify_token="nor this")))

    answered_after, code = asyncio.run(health_while(lambda client: client.post(
        f"/chat/{bot.uuid}", content=b"{}", headers={"X-Hub-Signature-256": SIGNATURE})))

    assert code == 403
    assert answered_after < 1.0


def test_a_slow_verification_does_not_hold_the_webhook(monkeypatch):
    bot = SimpleNamespace(uuid=uuid4(), clientId=uuid4())
    monkeypatch.setattr(webhook_module, "find_webhook_bot", lambda bot_id: bot)
    monkeypatch.setattr(webhook_module, "webhook_secrets", slowly(SimpleNamespace(verify_token="the right one")))

    answered_after, code = asyncio.run(health_while(lambda client: client.get(
        f"/chat/{bot.uuid}", params={"hub.mode": "subscribe", "hub.challenge": "42", "hub.verify_token": "wrong"})))

    assert code == 403
    assert answered_after < 1.0


async def free_while(work) -> float:
    """Runs `work` and, meanwhile, a 0.3 s sleep: says how long the sleep took. A held loop makes it take longer."""
    # Timed from before the work starts: the work only runs once this coroutine yields, at the sleep, and if it held
    # the loop the sleep could not end before it.
    started = time.monotonic()
    slow = asyncio.create_task(work)
    await asyncio.sleep(0.3)
    took = time.monotonic() - started
    with pytest.raises(ingestion.Unprocessable):
        await slow
    return took


def test_a_slow_read_does_not_hold_the_ingestion_of_a_notification(monkeypatch):
    monkeypatch.setattr(TenantRepository, "get_bot", slowly(error=NotFound("chatbot")))
    event = {"schemaVersion": SCHEMA_VERSION, "clientId": str(uuid4()), "botId": str(uuid4())}

    assert asyncio.run(free_while(ingestion.ingest_notification(event))) < 1.0


def test_a_slow_read_does_not_hold_the_storage_of_an_answer(monkeypatch):
    monkeypatch.setattr(TenantRepository, "get_contact", slowly(error=NotFound("contact")))
    event = {"schemaVersion": SCHEMA_VERSION, "clientId": str(uuid4()), "botId": str(uuid4()),
             "contactId": str(uuid4()), "sentAt": "2026-10-28T00:00:00+00:00", "messageId": "wamid.x",
             "channel": "whatsapp"}

    assert asyncio.run(free_while(ingestion.ingest_reply(event))) < 1.0


@pytest.mark.parametrize("function", [webhook_module.get_webhook_bot, webhook_module.verify, ingestion._identify,
                                      ingestion._store_reply, facebook.ctr_identify_contact_by_wa_id,
                                      facebook.ctr_identify_fb_contact, facebook.ctr_get_contact_from_facebook_id])
def test_what_waits_on_the_database_is_a_function_and_not_a_coroutine(function):
    """FastAPI runs a function in its pool of threads, and the ingestion hands it to one: a coroutine would not be."""
    assert not inspect.iscoroutinefunction(function)
