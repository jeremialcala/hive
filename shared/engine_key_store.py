# -*- coding: utf-8 -*-
"""
    Where the private keys of the workers of the response engine are kept: a KV engine of Andvari (ADR-0022, I6b).
    Each worker holds one key per client, at

        motor/data/clientes/<client id>/<worker>     {"jwk": <the private JWK>}

    and reads only its own with an AppRole of its own. The command that issues them (ops.engine_keys) creates them
    and reads them back to finish an issue left halfway, and nothing else: a key that is kept is never written
    over, so a worker never loses the one it signs with.
"""
import time
from uuid import UUID

import requests

TIMEOUT = 5         # seconds
RENEW_BEFORE = 30   # seconds before the token expires at which a new one is asked for
MOUNT = "motor"


class KeyStoreUnavailable(Exception):
    """Andvari did not answer, or refused. What it carries is the kind of failure, never what was sent."""


class EngineKeyStore:

    def __init__(self, address: str, role_id: str, secret_id_file: str, ca_file: str | None = None,
                 mount: str = MOUNT):
        self._address = address.rstrip("/")
        self._role_id = role_id
        self._secret_id_file = secret_id_file
        self._verify = ca_file or True
        self._mount = mount
        self._token = None
        self._token_expires = 0.0

    def _request(self, method: str, path: str, body: dict | None = None,
                 token: str | None = None) -> requests.Response:
        headers = {"X-Vault-Token": token} if token else {}
        try:
            return requests.request(method, f"{self._address}/v1/{path}", json=body, headers=headers,
                                    timeout=TIMEOUT, verify=self._verify)
        except (requests.RequestException, OSError) as e:
            # Only the kind of failure: the text of these exceptions can carry the address and more.
            raise KeyStoreUnavailable(type(e).__name__) from None

    def _login(self) -> str:
        try:
            with open(self._secret_id_file, "r", encoding="utf-8") as file:
                secret_id = file.read().strip()
        except OSError as e:
            raise KeyStoreUnavailable(type(e).__name__) from None
        response = self._request("POST", "auth/approle/login", {"role_id": self._role_id, "secret_id": secret_id})
        if response.status_code != 200:
            raise KeyStoreUnavailable(f"login refused: {response.status_code}")
        try:
            auth = response.json()["auth"]
            self._token = auth["client_token"]
            lease = auth.get("lease_duration", 0)
        except (ValueError, KeyError, TypeError):
            raise KeyStoreUnavailable("unexpected answer") from None
        self._token_expires = time.monotonic() + (lease if isinstance(lease, (int, float)) else 0) - RENEW_BEFORE
        return self._token

    def _call(self, method: str, path: str, body: dict | None = None) -> requests.Response:
        cached = self._token if self._token and time.monotonic() < self._token_expires else None
        response = self._request(method, path, body, cached or self._login())
        if response.status_code == 403 and cached:
            # The token may have been revoked before its time: once more with a new one.
            response = self._request(method, path, body, self._login())
        return response

    def _path(self, client_id: UUID, worker: str) -> str:
        return f"{self._mount}/data/clientes/{client_id}/{worker}"

    def read(self, client_id: UUID, worker: str) -> dict | None:
        """The private key that worker holds for the client, or None if there is none yet."""
        response = self._call("GET", self._path(client_id, worker))
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise KeyStoreUnavailable(f"read refused: {response.status_code}")
        try:
            key = response.json()["data"]["data"]["jwk"]
        except (ValueError, KeyError, TypeError):
            raise KeyStoreUnavailable("unexpected answer") from None
        if not isinstance(key, dict):
            raise KeyStoreUnavailable("unexpected answer")
        return key

    def create(self, client_id: UUID, worker: str, key: dict) -> bool:
        """
            Keeps a new private key for that worker and client, only if there is none: False if there already
            was one. The check-and-set of version 0 is what makes it so, in the write itself.
        """
        response = self._call("POST", self._path(client_id, worker), {"options": {"cas": 0}, "data": {"jwk": key}})
        if response.status_code == 200:
            return True
        # 400 is the check-and-set failing. The policy of the command does not even allow writing over a key, and
        # that is a 403. Either way the key may be there: reading it tells.
        if response.status_code in (400, 403) and self.read(client_id, worker) is not None:
            return False
        raise KeyStoreUnavailable(f"create refused: {response.status_code}")
