# -*- coding: utf-8 -*-
"""
    H14 in the management API: a route that waits on MongoDB or the key service holds only its own request. The API
    runs one worker, so a route that waited inside the event loop held every other request, the health check
    included. The routes are functions, which FastAPI runs in its pool of threads; the few that stay coroutines,
    because they await the bus, hand what blocks to a thread.
"""
import asyncio
import inspect
import time
from uuid import uuid4

import httpx
import pytest
from fastapi.routing import APIRoute

import app as app_module
from repository import NotFound, TenantRepository

SLOW = 1.5
# The only coroutines left, each for a reason: the token route reads its form by hand and decrypts in a thread,
# the deletions await the notice to the engine and delete in a thread, the health check touches nothing, and a send
# to a contact awaits the bus and reads in a thread. Each change of a chatbot's configuration is made in a thread and
# then told to the engine on the bus (ADR-0035).
COROUTINES = {"oauth_token", "delete_chatbot", "delete_contact", "delete_client", "health", "send_message",
              "add_chatbot_rule", "add_chatbot_intent", "update_chatbot_intent", "delete_chatbot_intent",
              "restore_version"}


def slowly(error: Exception):
    def wait(*_args, **_kwargs):
        time.sleep(SLOW)
        raise error
    return wait


@pytest.fixture
def acme(register):
    return register("acme")


def test_only_the_routes_that_await_the_bus_or_read_by_hand_are_coroutines():
    coroutines = {route.endpoint.__name__ for route in app_module.app.routes
                  if isinstance(route, APIRoute) and inspect.iscoroutinefunction(route.endpoint)}

    assert coroutines == COROUTINES


async def health_while(method: str, path: str, headers: dict) -> tuple[float, int]:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_module.app),
                                 base_url="http://testserver") as client:
        # Timed from before the slow request: a held event loop would hold the clock too.
        started = time.monotonic()
        slow = asyncio.create_task(client.request(method, path, headers=headers))
        await asyncio.sleep(0.3)
        health = await client.get("/health")
        answered_after = time.monotonic() - started
        response = await slow
        assert health.status_code == 200
        return answered_after, response.status_code


def test_a_slow_read_of_a_chatbot_does_not_hold_the_api(acme, monkeypatch):
    headers = acme.auth
    monkeypatch.setattr(TenantRepository, "get_bot", slowly(NotFound("chatbot")))

    answered_after, code = asyncio.run(health_while("GET", f"/chat/{uuid4()}/info", headers))

    assert code == 404
    assert answered_after < 1.0


def test_a_slow_deletion_does_not_hold_the_api(acme, monkeypatch):
    headers = acme.auth
    monkeypatch.setattr(TenantRepository, "delete_bot", slowly(NotFound("chatbot")))

    answered_after, code = asyncio.run(health_while("DELETE", f"/bot/{uuid4()}", headers))

    assert code == 404
    assert answered_after < 1.0


def test_a_slow_read_of_the_registry_does_not_hold_the_api(operator, monkeypatch):
    import controller.clients as clients
    headers = operator.auth
    monkeypatch.setattr(clients, "get_client", slowly(NotFound("client")))

    answered_after, code = asyncio.run(health_while("DELETE", f"/clients/{uuid4()}", headers))

    assert code == 404
    assert answered_after < 1.0
