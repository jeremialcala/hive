# -*- coding: utf-8 -*-
"""
    hive-api, as the portal calls it (ADR-0029, ADR-0033). The portal holds a P-256 key of its own, made when it starts
    and kept only in memory: every token of Hive it gets is bound to it, and every request carries a DPoP proof it
    signs. After a restart, each session's token is good for nothing, and the portal exchanges its ID token again.

        POST {hive}/oauth/token   token-exchange (RFC 8693): the ID token of the person for a token of Hive
        *    {hive}/<route>       the request of the PWA, with that token and a proof

    The browser never sees either token: they stay in the session, server side.
"""
import base64
import json
import time

import requests

from shared.auth.client import dpop_proof, new_signing_key

__all__ = ["Hive", "NoAccess", "ClientNeeded", "HiveUnavailable", "EXCHANGE", "ID_TOKEN", "REQUEST_HEADERS",
           "ANSWER_HEADERS", "claims_of"]

TIMEOUT = 30  # seconds
EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
ID_TOKEN = "urn:ietf:params:oauth:token-type:id_token"
# What of the PWA's request goes on to hive-api, and of hive-api's answer comes back. Nothing else: not the cookie,
# not an Authorization of the browser's, not a Set-Cookie.
REQUEST_HEADERS = ("accept", "content-type", "idempotency-key")
ANSWER_HEADERS = ("content-type", "retry-after")


class NoAccess(Exception):
    """Hive gives this person no token: not in Hive, blocked, removed, without a second factor, or a bad ID token."""


class ClientNeeded(NoAccess):
    """The person is of more than one client, and the login did not say which."""


class HiveUnavailable(Exception):
    """hive-api could not be reached, or is too busy."""


def claims_of(token: str) -> dict:
    """
        What a token of Hive says, read without checking it: it comes straight from hive-api, which checks it on every
        request. Only for the portal to say who is in, never to decide anything.
    """
    try:
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        return {}


class Hive:
    """The calls of the portal to hive-api, each with a proof of the portal's own key."""
    def __init__(self, api_url: str, timeout: float = TIMEOUT):
        self._api = api_url.rstrip("/")
        self._timeout = timeout
        # The portal's own key (ADR-0033, point 2): never written anywhere.
        self._key = new_signing_key()

    def exchange(self, id_token: str, client: str | None = None) -> tuple[str, float]:
        """A token of Hive for the ID token of a person, and when it expires (seconds since 1970)."""
        url = f"{self._api}/oauth/token"
        form = {"grant_type": EXCHANGE, "subject_token": id_token, "subject_token_type": ID_TOKEN}
        if client:
            form["hive_client"] = client
        try:
            response = requests.post(url, data=form, timeout=self._timeout, allow_redirects=False,
                                     headers={"DPoP": dpop_proof(self._key, "POST", url)})
        except (requests.RequestException, OSError) as e:
            raise HiveUnavailable(type(e).__name__) from None
        if response.status_code >= 500 or response.status_code == 429:
            raise HiveUnavailable(f"hive-api answered {response.status_code}")
        try:
            answer = response.json()
        except ValueError:
            answer = {}
        if response.status_code != 200:
            error = answer.get("error") if isinstance(answer, dict) else None
            raise (ClientNeeded if error == "invalid_target" else NoAccess)(f"{response.status_code} {error}")
        token, lifetime = answer.get("access_token"), answer.get("expires_in")
        if not isinstance(token, str) or not isinstance(lifetime, int) or lifetime <= 0:
            raise HiveUnavailable("an answer of hive-api without a token")
        return token, time.time() + lifetime

    def forward(self, token: str, method: str, route: str, query: str, body: bytes, headers: dict) -> requests.Response:
        """The PWA's request to a route of hive-api, as the person, with a new proof of the portal's key."""
        url = f"{self._api}/{route}"
        sent = {name: value for name, value in headers.items() if name.lower() in REQUEST_HEADERS}
        sent.update({"Authorization": f"DPoP {token}", "DPoP": dpop_proof(self._key, method, url, token)})
        try:
            return requests.request(method, url + (f"?{query}" if query else ""), data=body or None, headers=sent,
                                    timeout=self._timeout, allow_redirects=False)
        except (requests.RequestException, OSError) as e:
            raise HiveUnavailable(type(e).__name__) from None
