# -*- coding: utf-8 -*-
"""
    A02 of the OWASP matrix (.ai-dlc/owasp-mapping.md; HIG-138): every answer of hive-api and of the webhook carries
    the headers of a JSON API, whatever it was: a good one, a 404, a 403, a 413, a 422 or a 500.
"""
import pytest
from fastapi.testclient import TestClient

import app as app_module
import service
import webhook as webhook_module

WANTED = {
    "strict-transport-security": "max-age=31536000; includeSubDomains",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
    "cache-control": "no-store",
}
API, WEBHOOK = TestClient(app_module.app, raise_server_exceptions=False), TestClient(webhook_module.app)
ANSWERS = {
    "health of the API": lambda: API.get("/health"),
    "health of the webhook": lambda: WEBHOOK.get("/health"),
    "a route that is not there": lambda: API.get("/nothing-here"),
    "without a token": lambda: API.get("/bots"),
    "a token request that is not one": lambda: API.post("/oauth/token", data={"grant_type": "password"}),
    "a body too big": lambda: API.post("/bot", content=b"x" * (3 * 1024 * 1024 + 1),
                                       headers={"Content-Type": "application/json"}),
    "a verification that fails": lambda: WEBHOOK.get(f"/chat/{'0' * 8}-0000-4000-8000-{'0' * 12}",
                                                     params={"hub.mode": "subscribe", "hub.challenge": "1",
                                                             "hub.verify_token": "no"}),
    "a notification without a signature": lambda: WEBHOOK.post(f"/chat/{'0' * 8}-0000-4000-8000-{'0' * 12}",
                                                               json={"object": "whatsapp_business_account"}),
}


@pytest.mark.parametrize("answer", ANSWERS.values(), ids=ANSWERS.keys())
def test_every_answer_carries_the_headers_of_a_json_api(answer):
    response = answer()

    assert {name: response.headers.get(name) for name in WANTED} == WANTED, response.status_code


def test_a_failure_carries_them_too(register, monkeypatch):
    def fails(*_args, **_kwargs):
        raise RuntimeError("forced")
    monkeypatch.setattr(app_module, "ctr_list_bots", fails)

    response = API.get("/bots", headers=register("acme").auth)

    assert response.status_code == 500
    assert {name: response.headers.get(name) for name in WANTED} == WANTED


def test_outside_development_it_loads_nothing_and_is_shown_in_no_frame():
    assert service.security_headers("production")["Content-Security-Policy"] == "default-src 'none'; frame-ancestors 'none'"
    assert service.security_headers("staging")["Content-Security-Policy"] == "default-src 'none'; frame-ancestors 'none'"
    # In development the interactive documentation is a page with its scripts: no CSP that would leave it blank.
    assert "Content-Security-Policy" not in service.security_headers("development")


def test_an_answer_that_already_says_it_is_not_said_twice():
    """POST /oauth/token sets its own Cache-Control (RFC 6749, 5.1): it stays one header, not two."""
    response = API.post("/oauth/token", data={"grant_type": "password"})

    assert response.headers.get_list("cache-control") == ["no-store"]
