# -*- coding: utf-8 -*-
"""
    The gateway runs as two services (RD-05 of the dual review of Gate 2): the webhook Meta calls and the management
    API. They are separate processes, so that a slow management request never holds a notification up. These tests
    check what each one serves, and that the webhook starts with nothing of what only the management API needs.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import app as app_module
import webhook as webhook_module

SRC = Path(__file__).resolve().parents[2] / "src"
# The service imports `shared` from the root of the repository (ADR-0023).
PATH = os.pathsep.join([str(SRC), str(SRC.parent)])
# What only the management API needs: the gateway's key pair, the audience of its tokens and the register of
# erasures.
MANAGEMENT_ONLY = ("GATEWAY_KEYS_FILE", "GATEWAY_AUDIENCE", "ERASURES_DIR")


def routes(service) -> set:
    return {(method, route.path) for route in service.routes if isinstance(route, APIRoute)
            for method in route.methods}


def test_the_webhook_serves_what_meta_calls_and_nothing_else():
    assert routes(webhook_module.app) == {("GET", "/chat/{bot_id}"), ("POST", "/chat/{bot_id}"),
                                          ("GET", "/health")}


def test_the_management_api_does_not_serve_the_webhook():
    management = TestClient(app_module.app)
    some_bot = "6f1d3b52-7c5e-4d8e-9a1b-2c3d4e5f6a7b"

    assert ("POST", "/chat/{bot_id}") not in routes(app_module.app)
    assert ("GET", "/chat/{bot_id}") not in routes(app_module.app)
    assert management.post(f"/chat/{some_bot}", content=b"{}").status_code == 404
    assert management.get(f"/chat/{some_bot}", params={"hub.mode": "subscribe"}).status_code == 404


def test_both_have_the_health_check():
    for service in (webhook_module.app, app_module.app):
        assert TestClient(service).get("/health").json() == {"status": "ok"}


def start(module: str, tmp_path, without=()) -> subprocess.CompletedProcess:
    """Imports one service in a process of its own, with the test settings minus those named."""
    env = {name: value for name, value in os.environ.items() if name not in without}
    env["PYTHONPATH"] = PATH
    probe = (f"import sys, {module}; from repository import erasures; "
             "print('loaded:', ','.join(sorted(m for m in ('app', 'webhook') if m in sys.modules))); "
             "print('erasures:', erasures._directory)")
    # cwd has no config.env, so what is left out of the environment is not picked up from it.
    return subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, cwd=str(tmp_path),
                          env=env, timeout=120)


def test_the_webhook_starts_without_what_only_the_management_api_needs(tmp_path):
    started = start("webhook", tmp_path, without=MANAGEMENT_ONLY)

    assert started.returncode == 0, started.stderr
    # The management API is not loaded, there is no key pair to load, and no register to delete with.
    assert "loaded: webhook\n" in started.stdout
    assert "erasures: None" in started.stdout


@pytest.mark.parametrize("missing", ["GATEWAY_KEYS_FILE", "GATEWAY_AUDIENCE"])
def test_the_management_api_does_not_start_without_its_keys(tmp_path, missing):
    started = start("app", tmp_path, without=(missing,))

    assert started.returncode != 0
    assert f"{missing} is required" in started.stderr
