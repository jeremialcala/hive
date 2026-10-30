# -*- coding: utf-8 -*-
"""
    The login of a person with Zitadel (ADR-0029, point 5): OpenID Connect, Authorization Code with PKCE (S256), the
    portal as a confidential client that authenticates with its secret (client_secret_basic). Each login carries a
    `state`, which ties Zitadel's answer to the browser that asked, and a `nonce`, which ties the ID token to it.

    Only the ID token is kept, for the exchange with Hive; Zitadel's access token is not used and goes nowhere. Of an
    answer of Zitadel that is not good, only its HTTP status and its `error`, a word of OAuth, are kept.
"""
import base64
import hashlib
import re
import secrets
import threading
from urllib.parse import urlencode

import requests

__all__ = ["Zitadel", "LoginFailed", "IdpUnavailable", "pkce"]

TIMEOUT = 10  # seconds
SCOPE = "openid"
OAUTH_ERROR = re.compile(r"^[a-z_]{1,40}$")


class LoginFailed(Exception):
    """Zitadel did not give an ID token for this login: it carries the kind, never what Zitadel said."""


class IdpUnavailable(Exception):
    """Zitadel could not be reached: the login may work later."""


def pkce() -> tuple[str, str]:
    """A new PKCE verifier, and its S256 challenge (RFC 7636)."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class Zitadel:
    """Zitadel, as the portal's IdP: its endpoints, its login and its logout."""
    def __init__(self, issuer: str, client_id: str, secret_file: str, redirect_uri: str, timeout: float = TIMEOUT):
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self.redirect_uri = redirect_uri
        self._secret_file = secret_file
        self._timeout = timeout
        self._lock = threading.Lock()
        self._endpoints: dict | None = None

    def endpoints(self) -> dict:
        """Zitadel's discovery, read once. Each endpoint has to be Zitadel's own, under its issuer."""
        with self._lock:
            if self._endpoints is None:
                try:
                    response = requests.get(f"{self.issuer}/.well-known/openid-configuration", timeout=self._timeout,
                                            allow_redirects=False)
                    found = response.json() if response.status_code == 200 else None
                except (requests.RequestException, OSError, ValueError):
                    found = None
                names = ("authorization_endpoint", "token_endpoint", "end_session_endpoint")
                if (not isinstance(found, dict) or found.get("issuer") != self.issuer
                        or not all(isinstance(found.get(name), str) and found[name].startswith(self.issuer + "/")
                                   for name in names)):
                    raise IdpUnavailable("the discovery of the IdP")
                self._endpoints = {name: found[name] for name in names}
            return self._endpoints

    def authorize_url(self, state: str, nonce: str, challenge: str) -> str:
        """Where the browser goes to sign in, for this login."""
        return f"{self.endpoints()['authorization_endpoint']}?" + urlencode({
            "response_type": "code", "client_id": self.client_id, "redirect_uri": self.redirect_uri,
            "scope": SCOPE, "state": state, "nonce": nonce, "code_challenge": challenge,
            "code_challenge_method": "S256"})

    def _secret(self) -> str:
        try:
            with open(self._secret_file, encoding="utf-8") as file:
                secret = file.read().strip()
        except OSError:
            raise IdpUnavailable("the client secret cannot be read") from None
        if not secret:
            raise IdpUnavailable("the client secret is empty")
        return secret

    def id_token_for(self, code: str, verifier: str) -> str:
        """The ID token Zitadel gives for the code of a login, with its verifier."""
        try:
            response = requests.post(self.endpoints()["token_endpoint"], timeout=self._timeout, allow_redirects=False,
                                     auth=(self.client_id, self._secret()),
                                     data={"grant_type": "authorization_code", "code": code,
                                           "redirect_uri": self.redirect_uri, "code_verifier": verifier})
        except (requests.RequestException, OSError):
            raise IdpUnavailable("the token endpoint of the IdP") from None
        if response.status_code >= 500 or response.status_code == 429:
            raise IdpUnavailable(f"the IdP answered {response.status_code}")
        try:
            answer = response.json()
        except ValueError:
            answer = {}
        if response.status_code != 200:
            error = answer.get("error") if isinstance(answer, dict) else None
            kind = error if isinstance(error, str) and OAUTH_ERROR.fullmatch(error) else "no error"
            raise LoginFailed(f"the IdP answered {response.status_code}: {kind}")
        id_token = answer.get("id_token") if isinstance(answer, dict) else None
        if not isinstance(id_token, str) or not id_token:
            raise LoginFailed("an answer of the IdP without an ID token")
        return id_token

    def logout_url(self, after: str) -> str:
        """
            Where the browser ends its session in Zitadel too, and comes back to the portal. By the client id, not
            by id_token_hint (OpenID Connect RP-Initiated Logout): the ID token would go to the browser in the URL.
        """
        query = {"client_id": self.client_id, "post_logout_redirect_uri": after}
        return f"{self.endpoints()['end_session_endpoint']}?" + urlencode(query)
