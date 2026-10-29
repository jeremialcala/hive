# -*- coding: utf-8 -*-
"""
    DPoP (RFC 9449, ADR-0033): every access token is bound to a key, and every request proves it holds that key.

    A proof is a JWT the caller signs, one per request, in the `DPoP` header: it says the method and the URL it is
    for, when it was made, a jti of its own and, beside an access token, the hash of that token. Its public key goes
    in its header. The access token carries the thumbprint of that key (`cnf.jkt`), so a token stolen without its key
    is good for nothing.

    The jti of a proof is remembered in memory for as long as the proof could be accepted, never in the database: a
    request must not write there (ADR-0025). A restart forgets them, so a proof made before the process started is
    not taken either.

    The reasons a proof is turned away are fixed texts, never anything taken from it, so they can go to the log.
"""
import base64
import hashlib
import json
import math
import threading
import time

from jwcrypto import jwk, jws

from shared.auth.tokens import InvalidToken, is_a_number, is_compact

PROOF_TYPE = "dpop+jwt"
ALGORITHM = "ES256"
WINDOW = 60          # seconds a proof is good for after it was made
FUTURE = 5           # seconds of tolerance for a clock ahead of ours
MAX_PROOF_SIZE = 4096
MAX_JTI_SIZE = 128
# More proofs than this inside one window and new ones are turned away rather than forgetting any (it fails
# closed). Far above what midgard serves: a few thousand a minute.
MAX_REMEMBERED = 200_000

# A proof made before this process started is not taken: the jtis it remembered are gone with the last one.
STARTED = math.floor(time.time())


class InvalidProof(InvalidToken):
    """The DPoP proof is not good: RFC 9449 answers it with invalid_dpop_proof, apart from a bad token."""


def thumbprint(public_key: dict) -> str:
    """The SHA-256 thumbprint of a public JWK (RFC 7638): what an access token is bound to."""
    return jwk.JWK(**{name: public_key[name] for name in ("kty", "crv", "x", "y") if name in public_key}).thumbprint()


def token_hash(access_token: str) -> str:
    """The `ath` of a proof: the SHA-256 of the access token, base64url without padding."""
    return base64.urlsafe_b64encode(hashlib.sha256(access_token.encode("ascii")).digest()).rstrip(b"=").decode()


class Seen:
    """The jtis of the proofs taken in the last window, per key. Safe to use from the threads of the API."""

    def __init__(self, capacity: int = MAX_REMEMBERED):
        self._capacity = capacity
        self._until: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    def remember(self, jkt: str, jti: str, until: float, now: float) -> bool:
        """False if that key already used that jti, or if there is no room left for it."""
        with self._lock:
            if len(self._until) >= self._capacity:
                for key in [key for key, expiry in self._until.items() if expiry <= now]:
                    del self._until[key]
            if (jkt, jti) in self._until and self._until[(jkt, jti)] > now:
                return False
            if len(self._until) >= self._capacity:
                return False
            self._until[(jkt, jti)] = until
            return True


SEEN = Seen()


def check(values: list[str], method: str, urls: set[str], access_token: str | None, now: float,
          seen: Seen | None = None) -> str:
    """
        The thumbprint of the key that signed the request's proof, or raises InvalidProof. `values` are all the
        `DPoP` headers of the request; `urls`, the URLs this request could have been made to, without query; and
        `access_token`, the one that goes with it, or None at POST /oauth/token.
    """
    if len(values) != 1:
        raise InvalidProof("not exactly one DPoP proof")
    raw = values[0]
    if not isinstance(raw, str) or len(raw) > MAX_PROOF_SIZE or not is_compact(raw):
        raise InvalidProof("not a DPoP proof")
    signed = jws.JWS()
    signed.allowed_algs = [ALGORITHM]
    try:
        signed.deserialize(raw)
        header = signed.jose_header
    except Exception:
        raise InvalidProof("malformed DPoP proof") from None
    if not isinstance(header, dict) or header.get("typ") != PROOF_TYPE or header.get("alg") != ALGORITHM:
        raise InvalidProof("not a DPoP proof")
    public = header.get("jwk")
    if (not isinstance(public, dict) or public.get("kty") != "EC" or public.get("crv") != "P-256"
            or "d" in public):
        raise InvalidProof("no public P-256 key in the proof")
    try:
        key = jwk.JWK(**public)
        signed.verify(key)
        # From here on, only what the signature covers.
        claims = json.loads(signed.payload)
        jkt = key.thumbprint()
    except Exception:
        raise InvalidProof("bad signature") from None
    if not isinstance(claims, dict):
        raise InvalidProof("bad signature")
    if claims.get("htm") != method:
        raise InvalidProof("for another method")
    if claims.get("htu") not in urls:
        raise InvalidProof("for another URL")
    issued = claims.get("iat")
    if not is_a_number(issued) or not now - WINDOW <= issued <= now + FUTURE:
        raise InvalidProof("made outside its window")
    if issued < STARTED:
        raise InvalidProof("made before this process started")
    jti = claims.get("jti")
    if not isinstance(jti, str) or not jti or len(jti) > MAX_JTI_SIZE:
        raise InvalidProof("no jti")
    if access_token is None:
        if "ath" in claims:
            raise InvalidProof("an access token hash where there is no access token")
    elif claims.get("ath") != token_hash(access_token):
        raise InvalidProof("for another access token")
    # Last, and only for a proof that was good: a forged one must not be able to burn somebody's jti.
    if not (seen or SEEN).remember(jkt, jti, issued + WINDOW + FUTURE, now):
        raise InvalidProof("proof already used")
    return jkt
