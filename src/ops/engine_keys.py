# -*- coding: utf-8 -*-
"""
    Issues the keys of the response engine for a client: one per worker (ADR-0022), six in all. For each worker it
    makes a new private key, keeps it in Andvari, where only that worker reads it, and registers its public part
    with the management API as the operator. The private keys are never printed nor written to disk.

        python -m ops.engine_keys <client id>

    Run it again whenever it stops halfway: a worker whose key is kept and registered is left as it is, one whose
    key is kept but not registered gets it registered, and only a worker with no key gets a new one. A kept key is
    never written over. It prints, for each worker, the kid and what was done.

    It reads its settings from the environment (or config.env):

        GATEWAY_URL                 the management API, e.g. http://gestion:5002
        GATEWAY_AUDIENCE            what tokens for that API carry in their aud
        GATEWAY_PUBLIC_KEYS_FILE    the gateway's public keys, as {"keys": [...]}: what GET /gateway/keys gives
        OPERATOR_KEY_FILE           the operator's private signing key
        OPERATOR_CLIENT_ID          the operator's client id
        ANDVARI_ADDR, ANDVARI_CA_FILE
        ENGINE_KEYS_ROLE_ID         the AppRole of this command in Andvari, `motor-claves`
        ENGINE_KEYS_SECRET_ID_FILE  the file with its secret id
"""
import json
import sys
import time
from typing import Callable
from uuid import UUID

import requests
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from shared.auth.client import TOKEN_PATH, build_assertion, dpop_proof, new_signing_key, public_part, token_request
from shared.engine_key_store import EngineKeyStore, KeyStoreUnavailable
from enums import Worker

TIMEOUT = 10  # seconds

# What a call to the management API comes back as: its status and its body.
Call = Callable[[str, str, dict | None], tuple]


class EngineKeysSettings(BaseSettings):
    gateway_url: str = Field(min_length=8)
    gateway_audience: str = Field(min_length=8)
    gateway_public_keys_file: str = Field(min_length=1)
    operator_key_file: str = Field(min_length=1)
    operator_client_id: UUID
    andvari_addr: str = Field(min_length=8)
    andvari_ca_file: str | None = None
    engine_keys_role_id: str = Field(min_length=1)
    engine_keys_secret_id_file: str = Field(min_length=1)

    model_config = SettingsConfigDict(env_file="config.env", hide_input_in_errors=True, extra="ignore")


def _read(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def management_api(settings: EngineKeysSettings) -> Call:
    """
        Calls the management API as the operator (ADR-0025): it gets an access token at POST /oauth/token and uses it
        until a minute before it expires, or until the gateway turns it away. Each request carries a DPoP proof the
        operator's key signs (ADR-0033).
    """
    operator_key = _read(settings.operator_key_file)
    # The last gateway key is the newest: while the gateway rotates, that is the one to move to.
    gateway_key = _read(settings.gateway_public_keys_file)["keys"][-1]
    base = settings.gateway_url.rstrip("/")
    access = {}

    def token(again: bool = False) -> str:
        if again or not access or time.monotonic() >= access["until"]:
            assertion = build_assertion(operator_key, settings.operator_client_id, settings.gateway_audience,
                                        gateway_key)
            response = requests.post(f"{base}{TOKEN_PATH}", data=token_request(assertion), timeout=TIMEOUT,
                                     headers={"DPoP": dpop_proof(operator_key, "POST", f"{base}{TOKEN_PATH}")})
            if response.status_code != 200:
                raise SystemExit(f"The gateway gave no access token: {response.status_code}")
            given = response.json()
            access.update(token=given["access_token"], until=time.monotonic() + given["expires_in"] - 60)
        return access["token"]

    def bound(given: str, method: str, url: str) -> dict:
        return {"Authorization": f"DPoP {given}", "DPoP": dpop_proof(operator_key, method, url, given)}

    def call(method: str, path: str, body: dict | None = None) -> tuple:
        response = requests.request(method, f"{base}{path}", json=body, timeout=TIMEOUT,
                                    headers=bound(token(), method, f"{base}{path}"))
        if response.status_code == 401:
            response = requests.request(method, f"{base}{path}", json=body, timeout=TIMEOUT,
                                        headers=bound(token(again=True), method, f"{base}{path}"))
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, {}

    return call


def registered_keys(call: Call, client_id: UUID) -> dict:
    """The kids of the engine's keys registered for the client, with the worker of each."""
    status, body = call("GET", "/clients", None)
    if status != 200:
        raise SystemExit(f"The list of clients could not be read: {status}")
    for client in body.get("data", []):
        if client.get("uuid") == str(client_id):
            return {key["kid"]: key.get("worker") for key in client.get("serviceKeys", [])}
    raise SystemExit(f"There is no client {client_id}")


def issue(client_id: UUID, call: Call, store: EngineKeyStore) -> list:
    """
        Issues the key of every worker that lacks one and registers what is kept and not yet registered. Its
        order is what makes it safe to stop at any point: the key is kept before it is registered, so a key that
        is registered always has its private part somewhere.
    """
    registered = registered_keys(call, client_id)
    done = []
    for worker in Worker:
        entry = {"worker": worker.value}
        try:
            key = store.read(client_id, worker.value)
            entry["kept"] = "already"
            if key is None:
                key = new_signing_key()
                if store.create(client_id, worker.value, key):
                    entry["kept"] = "new"
                else:
                    # Another run kept one in between: that is the one, not this.
                    key = store.read(client_id, worker.value)
            entry["kid"] = key["kid"]
        except KeyStoreUnavailable as e:
            done.append({**entry, "error": f"Andvari: {e}"})
            continue
        except (KeyError, TypeError):
            done.append({**entry, "error": "Andvari: the kept key has no kid"})
            continue
        if registered.get(key["kid"]) == worker.value:
            entry["registered"] = "already"
        else:
            status, body = call("POST", f"/clients/{client_id}/service-keys",
                                {"signingKey": public_part(key), "worker": worker.value})
            if status == 200:
                entry["registered"] = "new"
            else:
                entry["error"] = f"API: {status} {body.get('detail', '')}".strip()
        done.append(entry)
    return done


def main(arguments: list) -> int:
    if len(arguments) != 1:
        raise SystemExit(__doc__)
    try:
        client_id = UUID(arguments[0])
    except ValueError:
        raise SystemExit("The client id must be a UUID") from None
    settings = EngineKeysSettings()
    store = EngineKeyStore(settings.andvari_addr, settings.engine_keys_role_id, settings.engine_keys_secret_id_file,
                           settings.andvari_ca_file)
    done = issue(client_id, management_api(settings), store)
    print(json.dumps(done, indent=2))
    return 1 if any("error" in entry for entry in done) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
