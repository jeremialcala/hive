# -*- coding: utf-8 -*-
"""
    The ID tokens of Zitadel, the IdP of the people of a client (ADR-0029). The BFF hands one over at POST /oauth/token,
    to exchange it for an access token of Hive (RFC 8693), and here it is checked:

        - its signature, with a public key of Zitadel's JWKS, RS256 or ES256;
        - its `iss`, Zitadel's, and its `aud`, the BFF's client id there (with `azp`, if it is for more than one);
        - its lifetime, with the tolerance of the assertions;
        - that its `amr` says there was more than one factor: `mfa` (RFC 8176). Zitadel's policy asks for it, and Hive
          asks again.

    It only says who the person is, by `iss` and `sub`: the client and the role are Hive's (the gateway's
    principal.py). The BFF (hive-portal) checks the same ID token when Zitadel hands it over, with the `nonce` of its
    login besides; the gateway, when the BFF exchanges it.

    The keys are kept for an hour. A token signed by a key that is not among them makes them be read again, at most
    once a minute: Zitadel rotates its keys, and a token with a made-up kid must not make Hive ask Zitadel each time.
    The reasons a token is turned away are fixed texts, never anything of the token.
"""
import hmac
import json
import threading
import time
from dataclasses import dataclass

import requests
from jwcrypto import jwk, jws

from shared.auth.tokens import CLOCK_SKEW, InvalidToken, is_a_number, is_compact

__all__ = ["IdentityProvider", "Identity", "InvalidIdToken", "IdpUnavailable", "ALGORITHMS", "MFA"]

ALGORITHMS = {"RS256": "RSA", "ES256": "EC"}
MFA = "mfa"
MAX_ID_TOKEN = 16 * 1024
KEPT_FOR = 3600        # seconds the keys of Zitadel are kept
READ_AGAIN_AFTER = 60  # seconds at least between two reads, for a kid that is not among them
TIMEOUT = 5            # seconds
MAX_SUBJECT = 255


class InvalidIdToken(InvalidToken):
    """The ID token is not good: who handed it over does not get an access token."""


class IdpUnavailable(Exception):
    """Zitadel's keys could not be read: whether the ID token is good cannot be said now. It carries the kind."""


@dataclass(frozen=True)
class Identity:
    """Who the person is in the IdP."""
    issuer: str
    subject: str


def _read_keys(url: str) -> jwk.JWKSet:
    try:
        response = requests.get(url, timeout=TIMEOUT, allow_redirects=False)
    except (requests.RequestException, OSError) as e:
        raise IdpUnavailable(type(e).__name__) from None
    if response.status_code != 200:
        raise IdpUnavailable(f"HTTP {response.status_code}")
    try:
        return jwk.JWKSet.from_json(response.text)
    except Exception:
        raise IdpUnavailable("not a JWKS") from None


class IdentityProvider:
    def __init__(self, issuer: str, audience: str, jwks_url: str | None = None, read_keys=_read_keys,
                 clock=time.time):
        self.issuer = issuer.rstrip("/")
        self.audience = audience
        self._url = jwks_url or f"{self.issuer}/oauth/v2/keys"
        self._read_keys = read_keys
        self._clock = clock
        self._lock = threading.Lock()
        self._keys: jwk.JWKSet | None = None
        self._read_at = float("-inf")

    def _key(self, kid: str) -> jwk.JWK | None:
        with self._lock:
            now = self._clock()
            if self._keys is None or now - self._read_at > KEPT_FOR:
                self._keys, self._read_at = self._read_keys(self._url), now
            key = self._keys.get_key(kid)
            if key is None and now - self._read_at > READ_AGAIN_AFTER:
                self._keys, self._read_at = self._read_keys(self._url), now
                key = self._keys.get_key(kid)
            return key

    def verify(self, raw, nonce: str | None = None) -> Identity:
        """
            Who the person of a good ID token is, or InvalidIdToken; IdpUnavailable if Zitadel's keys cannot be read.
            With `nonce`, the token has to carry that same one: it is the answer to that login, and no other.
        """
        if not isinstance(raw, str) or len(raw) > MAX_ID_TOKEN or not is_compact(raw):
            raise InvalidIdToken("not an ID token")
        signed = jws.JWS()
        signed.allowed_algs = list(ALGORITHMS)
        try:
            signed.deserialize(raw)
            header = signed.jose_header
        except Exception:
            raise InvalidIdToken("malformed ID token") from None
        if (not isinstance(header, dict) or header.get("alg") not in ALGORITHMS
                or not isinstance(header.get("kid"), str)):
            raise InvalidIdToken("ID token algorithm or key not allowed")
        key = self._key(header["kid"])
        if key is None or key.get("kty") != ALGORITHMS[header["alg"]] or key.has_private:
            raise InvalidIdToken("not signed by a key of the IdP")
        if key.get("use") not in (None, "sig") or (header["alg"] == "ES256" and key.get("crv") != "P-256"):
            raise InvalidIdToken("not signed by a key of the IdP")
        try:
            signed.verify(key)
            claims = json.loads(signed.payload)
        except Exception:
            raise InvalidIdToken("bad signature") from None
        if not isinstance(claims, dict):
            raise InvalidIdToken("bad signature")
        return self._check(claims, nonce)

    def _check(self, claims: dict, nonce: str | None = None) -> Identity:
        if claims.get("iss") != self.issuer:
            raise InvalidIdToken("issued by another IdP")
        audiences = claims.get("aud")
        if isinstance(audiences, str):
            audiences = [audiences]
        if not isinstance(audiences, list) or self.audience not in audiences:
            raise InvalidIdToken("issued for another client")
        # OpenID Connect Core, 3.1.3.7: for more than one audience, the one it was issued to says so.
        if len(audiences) > 1 and claims.get("azp") != self.audience:
            raise InvalidIdToken("issued for another client")
        now = self._clock()
        issued, expires = claims.get("iat"), claims.get("exp")
        if not is_a_number(issued) or not is_a_number(expires) or expires <= issued:
            raise InvalidIdToken("no lifetime")
        if issued > now + CLOCK_SKEW:
            raise InvalidIdToken("issued in the future")
        if expires <= now - CLOCK_SKEW:
            raise InvalidIdToken("expired")
        if nonce is not None and (not isinstance(claims.get("nonce"), str)
                                  or not hmac.compare_digest(claims["nonce"], nonce)):
            raise InvalidIdToken("the nonce of another login")
        methods = claims.get("amr")
        if not isinstance(methods, list) or MFA not in methods:
            raise InvalidIdToken("no second factor")
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject or len(subject) > MAX_SUBJECT:
            raise InvalidIdToken("no subject")
        return Identity(issuer=self.issuer, subject=subject)
