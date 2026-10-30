# -*- coding: utf-8 -*-
"""
    The users of Zitadel that Hive invites (ADR-0029, point 7; HIG-94), with a service account of Zitadel that may
    manage the users of Hive's organization there. Its token is read from a file, like the secret id of Andvari, and
    goes nowhere but the Authorization header.

        POST {api}/v2/users                         find a user of the organization by their email
        POST {api}/v2/users/human                   create one, with the email not verified and its code returned,
                                                    so that Zitadel sends only the invitation
        POST {api}/v2/users/{user_id}/invite_code   Zitadel sends the invitation: the person sets up their
                                                    password or passkey, and the MFA its policy asks for

    (zitadel.com/docs, User Service v2, read on 2026-10-30; to be checked against the version on Nidavellir.)

    Of an answer that is not good, only its HTTP status is kept: its message may carry the email.
"""
import pathlib
import re
from urllib.parse import quote

import requests

__all__ = ["ZitadelUsers", "ZitadelUnavailable", "ZitadelRefused", "USER_ID"]

TIMEOUT = 10  # seconds
APPLICATION = "Hive"
# What Zitadel's ids look like: anything else is not put into an address, nor stored.
USER_ID = re.compile(r"^[0-9A-Za-z_-]{1,64}$")


class ZitadelUnavailable(Exception):
    """Zitadel could not be reached, or is too busy, or the service account is not set up: it may work later."""


class ZitadelRefused(Exception):
    """Zitadel said no: it carries the HTTP status, never the message."""

    def __init__(self, status: int):
        super().__init__(f"Zitadel {status}")
        self.status = status


class ZitadelUsers:
    def __init__(self, api_url: str, organization_id: str, token_file: str, timeout: float = TIMEOUT):
        self._api = api_url.rstrip("/")
        self._organization = organization_id
        self._token_file = pathlib.Path(token_file)
        self._timeout = timeout

    def _token(self) -> str:
        # Read on each call: a token rotated in the file is used at once, and it is never kept in memory for long.
        try:
            token = self._token_file.read_text(encoding="utf-8").strip()
        except OSError:
            raise ZitadelUnavailable("the token of the service account cannot be read") from None
        if not token:
            raise ZitadelUnavailable("the token of the service account is empty")
        return token

    def _post(self, path: str, body: dict) -> dict:
        try:
            response = requests.post(f"{self._api}/{path}", json=body, timeout=self._timeout, allow_redirects=False,
                                     headers={"Authorization": f"Bearer {self._token()}"})
        except (requests.RequestException, OSError) as e:
            # Only the kind: the text of these exceptions carries the address.
            raise ZitadelUnavailable(type(e).__name__) from None
        if response.status_code >= 500 or response.status_code == 429:
            raise ZitadelUnavailable(f"Zitadel {response.status_code}")
        if response.status_code in (401, 403):
            raise ZitadelUnavailable(f"the service account was turned away: {response.status_code}")
        if response.status_code != 200:
            raise ZitadelRefused(response.status_code)
        try:
            answer = response.json()
        except ValueError:
            raise ZitadelUnavailable("an answer that is not JSON") from None
        if not isinstance(answer, dict):
            raise ZitadelUnavailable("an answer that is not an object")
        return answer

    def find_by_email(self, email: str) -> str | None:
        """The id of the user of the organization with that email, or None."""
        answer = self._post("v2/users", {
            "query": {"limit": 2},
            "queries": [{"emailQuery": {"emailAddress": email, "method": "TEXT_QUERY_METHOD_EQUALS_IGNORE_CASE"}},
                        {"organizationIdQuery": {"organizationId": self._organization}}]})
        found = [user.get("userId") for user in answer.get("result") or [] if isinstance(user, dict)]
        found = [user_id for user_id in found if isinstance(user_id, str) and USER_ID.fullmatch(user_id)]
        if len(found) > 1:
            # Two users with one email: which one is the person cannot be told from here.
            raise ZitadelRefused(409)
        return found[0] if found else None

    def create(self, email: str, given_name: str, family_name: str) -> str:
        """A new user of the organization, not initialized yet, and their id."""
        answer = self._post("v2/users/human", {
            "organization": {"orgId": self._organization}, "username": email,
            "profile": {"givenName": given_name, "familyName": family_name},
            # The code is returned and dropped here: the invitation is what verifies the email.
            "email": {"email": email, "returnCode": {}}})
        user_id = answer.get("userId")
        if not isinstance(user_id, str) or not USER_ID.fullmatch(user_id):
            raise ZitadelUnavailable("a user created without an id")
        return user_id

    def invite(self, user_id: str) -> None:
        """Zitadel sends the person the email to set up their account."""
        self._post(f"v2/users/{quote(user_id, safe='')}/invite_code", {"sendCode": {"applicationName": APPLICATION}})
