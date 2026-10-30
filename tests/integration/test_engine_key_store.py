# -*- coding: utf-8 -*-
"""
    Where the private keys of the engine's workers are kept, against a real OpenBao set up the way Andvari is (the
    andvari repository, motor/instalar.sh): a KV engine of version 2 at `motor/` that requires check-and-set, the
    policy of the command that issues the keys, and one policy per worker that reads only its own key.

    It needs an OpenBao in development mode to configure:

        docker run --rm -p 127.0.0.1:18200:8200 -e BAO_DEV_ROOT_TOKEN_ID=dev-root openbao/openbao:2.7.1
        OPENBAO_TEST_ADDR=http://127.0.0.1:18200 OPENBAO_TEST_TOKEN=dev-root pytest tests/integration

    Without those two variables the tests are skipped; with OPENBAO_TEST_REQUIRED set they fail instead.
"""
import os
import pathlib
from uuid import uuid4

import pytest
import requests

from shared.auth.client import new_signing_key
from shared.engine_key_store import EngineKeyStore, KeyStoreUnavailable

ADDRESS, ROOT_TOKEN = os.environ.get("OPENBAO_TEST_ADDR"), os.environ.get("OPENBAO_TEST_TOKEN")
# The same as politicas/motor-claves.hcl and politicas/motor-<worker>.hcl in the andvari repository.
COMMAND_POLICY = 'path "motor/data/clientes/+/+" { capabilities = ["create", "read"] }'
WORKER_POLICY = 'path "motor/data/clientes/+/{worker}" {{ capabilities = ["read"] }}'


def as_root(method: str, path: str, body: dict | None = None, allowed=(200, 204)) -> dict:
    response = requests.request(method, f"{ADDRESS}/v1/{path}", json=body, headers={"X-Vault-Token": ROOT_TOKEN},
                                timeout=5)
    assert response.status_code in allowed, (path, response.status_code)
    return response.json() if response.content else {}


def role(name: str, policy: str, directory: pathlib.Path) -> tuple:
    """An AppRole with that policy: its role id and the file with a new secret id."""
    as_root("PUT", f"sys/policies/acl/{name}", {"policy": policy})
    as_root("POST", f"auth/approle/role/{name}", {"token_policies": name, "token_ttl": "5m"})
    role_id = as_root("GET", f"auth/approle/role/{name}/role-id")["data"]["role_id"]
    secret_file = directory / f"{name}-secret-id"
    secret_file.write_text(as_root("POST", f"auth/approle/role/{name}/secret-id")["data"]["secret_id"] + "\n",
                           encoding="utf-8")
    return role_id, str(secret_file)


@pytest.fixture(scope="module")
def roles(tmp_path_factory):
    if not (ADDRESS and ROOT_TOKEN):
        if os.environ.get("OPENBAO_TEST_REQUIRED"):
            pytest.fail("OPENBAO_TEST_ADDR and OPENBAO_TEST_TOKEN are required here")
        pytest.skip("No OpenBao to test against: set OPENBAO_TEST_ADDR and OPENBAO_TEST_TOKEN")
    # 400 is "already there": the same server may be used for more than one run.
    as_root("POST", "sys/mounts/motor", {"type": "kv", "options": {"version": "2"}}, allowed=(200, 204, 400))
    as_root("POST", "motor/config", {"cas_required": True})
    as_root("POST", "sys/auth/approle", {"type": "approle"}, allowed=(200, 204, 400))
    directory = tmp_path_factory.mktemp("motor")
    return {"command": role("motor-claves", COMMAND_POLICY, directory),
            "notifier": role("motor-notifier", WORKER_POLICY.format(worker="notifier"), directory),
            "classifier": role("motor-classifier", WORKER_POLICY.format(worker="classifier"), directory)}


@pytest.fixture
def command(roles):
    return EngineKeyStore(ADDRESS, *roles["command"])


def test_a_key_is_kept_and_read_back(command):
    client, key = uuid4(), new_signing_key()

    assert command.read(client, "notifier") is None
    assert command.create(client, "notifier", key) is True
    assert command.read(client, "notifier") == key


def test_a_kept_key_is_never_written_over(command):
    client, key = uuid4(), new_signing_key()
    command.create(client, "notifier", key)

    assert command.create(client, "notifier", new_signing_key()) is False
    assert command.read(client, "notifier") == key


def test_the_command_can_neither_update_nor_delete_nor_list(command):
    client, key = uuid4(), new_signing_key()
    command.create(client, "notifier", key)
    headers = {"X-Vault-Token": command._token}

    for method, path, body in (
            ("POST", f"motor/data/clientes/{client}/notifier", {"options": {"cas": 1}, "data": {"jwk": {}}}),
            ("DELETE", f"motor/data/clientes/{client}/notifier", None),
            ("DELETE", f"motor/metadata/clientes/{client}/notifier", None),
            ("LIST", "motor/metadata/clientes", None),
            ("GET", "sys/mounts", None)):
        response = requests.request(method, f"{ADDRESS}/v1/{path}", headers=headers, json=body, timeout=5)
        assert response.status_code == 403, (method, path)
    assert command.read(client, "notifier") == key


def test_a_worker_reads_its_own_key_of_any_client_and_nobody_else_s(command, roles):
    client, mine, theirs = uuid4(), new_signing_key(), new_signing_key()
    command.create(client, "notifier", mine)
    command.create(client, "classifier", theirs)
    notifier = EngineKeyStore(ADDRESS, *roles["notifier"])

    assert notifier.read(client, "notifier") == mine
    with pytest.raises(KeyStoreUnavailable, match="read refused: 403"):
        notifier.read(client, "classifier")
    with pytest.raises(KeyStoreUnavailable, match="create refused: 403"):
        notifier.create(uuid4(), "notifier", new_signing_key())


def test_the_store_logs_in_again_when_its_token_is_revoked(command):
    client, key = uuid4(), new_signing_key()
    command.create(client, "notifier", key)

    as_root("POST", "auth/token/revoke", {"token": command._token})

    assert command.read(client, "notifier") == key


@pytest.mark.parametrize("problem", ["wrong secret", "no secret file", "nobody listening"])
def test_a_store_that_cannot_be_used_is_reported_and_nothing_else(roles, tmp_path, problem):
    role_id, secret_file = roles["command"]
    wrong = tmp_path / "secret-id"
    wrong.write_text("not-the-secret", encoding="utf-8")
    store = {
        "wrong secret": EngineKeyStore(ADDRESS, role_id, str(wrong)),
        "no secret file": EngineKeyStore(ADDRESS, role_id, str(tmp_path / "missing")),
        "nobody listening": EngineKeyStore("http://127.0.0.1:1", role_id, secret_file),
    }[problem]

    with pytest.raises(KeyStoreUnavailable) as failure:
        store.read(uuid4(), "notifier")

    assert "not-the-secret" not in str(failure.value) and "127.0.0.1" not in str(failure.value)
