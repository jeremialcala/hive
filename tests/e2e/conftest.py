# -*- coding: utf-8 -*-
"""The fixtures of the end-to-end test (HIG-135, HIG-141): the stack, and a client with its chatbot (e2e_stack.py)."""
import os
import secrets
import subprocess
import tempfile
import time
from pathlib import Path

import pytest
import requests

from .e2e_stack import API, PORTAL, ROOT, WEBHOOK, Hive, Stack, until, wanted

WABA_ID = "100000000000009"
# The stack that is up, so that a failing test carries the log of every service in its report.
UP: list = []


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):  # pylint: disable=unused-argument
    report = (yield).get_result()
    if report.when == "call" and report.failed and UP:
        report.sections.append(("docker compose logs", UP[-1].logs()))


@pytest.fixture(scope="session")
def stack():
    if not wanted():
        pytest.skip("The end-to-end test needs Docker and Bifröst: E2E=1 BIFROST_DIR=<higerotech/bifrost>")
    if not os.environ.get("E2E_NO_BUILD"):
        for args in (["-t", "hive-gateway:e2e", "."], ["-f", "engine/Dockerfile", "-t", "hive-engine:e2e", "."],
                     ["-f", "portal/Dockerfile", "-t", "hive-portal:e2e", "."]):
            subprocess.run(["docker", "build", "-q", *args], cwd=ROOT, check=True, capture_output=True)
    with tempfile.TemporaryDirectory(prefix="hive-e2e-") as directory:
        # It is made 0700, and the containers' users must get into it, as into its files (Stack.write). Docker Desktop
        # does not look; Linux, as in the CI, does.
        Path(directory).chmod(0o755)
        built = Stack(Path(directory))
        built.make_secrets()
        built.compose("down", "-v", "--remove-orphans", check=False)
        try:
            built.compose("up", "-d", "openbao", "nats", "mongo", "rabbitmq", "fakes")
            gateway_role, engine_keys_role = built.andvari()
            built.gateway_env(gateway_role, engine_keys_role)
            built.bifrost()
            built.compose("up", "-d", "hive-api", "webhook", "ingest", "portal")
            until("hive-api", lambda: requests.get(f"{API}/health", timeout=3).ok)
            until("the webhook", lambda: requests.get(f"{WEBHOOK}/health", timeout=3).ok)
            until("the portal", lambda: requests.get(f"{PORTAL}/health", timeout=3).ok)
            UP.append(built)
            yield built
            UP.remove(built)
        except BaseException:
            print(built.logs())
            raise
        finally:
            if not os.environ.get("E2E_KEEP"):
                built.compose("down", "-v", "--remove-orphans", check=False)


@pytest.fixture(scope="session")
def client(stack):
    """The operator's key registered; a client, its chatbot with its credentials; and the engine with its keys."""
    from shared.auth.client import new_signing_key, public_part  # pylint: disable=import-outside-toplevel

    operator_id = stack.ops("ops.operator_key", "/run/e2e/operador-publica.json")["client"]
    operator = Hive(stack.operator_key, operator_id, stack.gateway_public[-1])
    key = new_signing_key()
    made = operator.call("POST", "/clients", {"name": "acme-e2e", "signingKey": public_part(key)})
    acme = Hive(key, made["uuid"], stack.gateway_public[-1])

    bot = acme.call("POST", "/bot", {"name": "Tienda e2e", "role": "Sales Assistant", "tone": "amable",
                                     "description": "Una tienda de prueba de Hive"})
    app_secret = secrets.token_urlsafe(24)
    # And its WhatsApp Business Account: the PWA manages the chatbot's templates there (HIG-141).
    acme.call("PUT", f"/chat/{bot['uuid']}/credentials", {"whatsappToken": "EAAG-e2e-token", "appSecret": app_secret,
                                                          "wabaId": WABA_ID})
    acme.call("POST", f"/chat/{bot['uuid']}/rules", {"rules": ["Responde siempre en español"]})

    stack.ops("ops.engine_keys", made["uuid"], extra_env={"OPERATOR_CLIENT_ID": operator_id})
    stack.compose("up", "-d", "classifier", "coordinator", "responder", "evaluator", "notifier")
    time.sleep(5)
    stopped = {name: state for name, state in stack.running().items() if state != "running"}
    assert not stopped, f"Not running: {stopped}\n{stack.logs()}"
    return {"hive": acme, "bot": bot["uuid"], "app_secret": app_secret,
            "llm_key": (stack.dir / "llm-api-key").read_text(encoding="utf-8")}
