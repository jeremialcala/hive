# -*- coding: utf-8 -*-
"""
    Verification of a client's assertion: a JWT signed by the client (ES256), inside a JWE encrypted to the gateway
    (RSA-OAEP-256 with A256CBC-HS512). One algorithm per layer and nothing else is accepted. Since ADR-0025 it is
    only presented to POST /oauth/token, to get an access token (RFC 7523, section 2.2): its `iss` and its `sub` are
    the client.

    Nothing here touches the database: the caller says how to find a client's key, and remembers the jti.
"""
import json
import string
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from uuid import UUID

from jwcrypto import jwe, jwk, jws
from jwcrypto.common import base64url_decode

from .gateway_keys import GatewayKeys

KEY_ENCRYPTION = "RSA-OAEP-256"
CONTENT_ENCRYPTION = "A256CBC-HS512"
SIGNATURE = "ES256"

MAX_LIFETIME = 60   # seconds between iat and exp
CLOCK_SKEW = 30     # seconds of tolerance on iat and exp
MAX_TOKEN_SIZE = 8192
MAX_JTI_SIZE = 128
# What each part of a JWS in compact form is written with: base64url.
BASE64URL = frozenset(string.ascii_letters + string.digits + "-_")


class InvalidToken(Exception):
    """
        Why a token was turned away. The reasons are fixed texts written here, never anything taken from the
        token, so they can go to the log; the caller answers 401 whichever it was.
    """


@dataclass(frozen=True)
class Claims:
    client_id: UUID
    jti: str
    expires_at: datetime


def open_envelope(raw: str, gateway_keys: GatewayKeys) -> str:
    """Decrypts the JWE and returns what is inside: the signed JWT, still unverified."""
    if not isinstance(raw, str) or len(raw) > MAX_TOKEN_SIZE or raw.count(".") != 4:
        raise InvalidToken("not a compact JWE")
    envelope = jwe.JWE(algs=[KEY_ENCRYPTION, CONTENT_ENCRYPTION])
    try:
        envelope.deserialize(raw)
        header = envelope.jose_header
    except Exception:
        raise InvalidToken("malformed JWE") from None
    if header.get("alg") != KEY_ENCRYPTION or header.get("enc") != CONTENT_ENCRYPTION:
        raise InvalidToken("JWE algorithm not allowed")
    if header.get("cty") != "JWT":
        raise InvalidToken("the JWE does not declare a JWT inside")
    key = gateway_keys.private(header.get("kid"))
    if key is None:
        raise InvalidToken("not encrypted to a key of this gateway")
    try:
        envelope.decrypt(key)
        return envelope.payload.decode("ascii")
    except Exception:
        raise InvalidToken("the JWE does not open") from None


def read_signed(inner: str) -> tuple:
    """
        Parses the JWT without trusting it yet. The issuer is read before the signature is checked because it
        says which key checks it; nothing else is taken from an unverified token.
    """
    # Only the compact form: three parts, and the issuer read here is the one the signature covers. The library
    # also takes the JSON form, where the bytes this would read the issuer from are not the ones signed.
    if not is_compact(inner):
        raise InvalidToken("malformed JWT")
    signed = jws.JWS()
    signed.allowed_algs = [SIGNATURE]
    try:
        signed.deserialize(inner)
        header = signed.jose_header
        issuer = json.loads(base64url_decode(inner.split(".")[1])).get("iss")
    except Exception:
        raise InvalidToken("malformed JWT") from None
    if not isinstance(header, dict) or header.get("alg") != SIGNATURE:
        raise InvalidToken("JWT algorithm not allowed")
    return signed, header, issuer


def is_compact(value) -> bool:
    """
        A JWS in compact form: header, payload and signature, base64url each. Checked without a regular
        expression: it also reads what a client sends in a request body, and it takes one pass whatever comes.
    """
    if not isinstance(value, str):
        return False
    parts = value.split(".")
    return len(parts) == 3 and all(part and BASE64URL.issuperset(part) for part in parts)


def is_a_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def check_claims(claims: dict, client_id: UUID, audience: str, now: float) -> Claims:
    audiences = claims.get("aud")
    if audiences != audience and not (isinstance(audiences, list) and audience in audiences):
        raise InvalidToken("issued for another audience")
    issued, expires = claims.get("iat"), claims.get("exp")
    if not is_a_number(issued) or not is_a_number(expires):
        raise InvalidToken("no iat or exp")
    if not 0 < expires - issued <= MAX_LIFETIME:
        raise InvalidToken("lifetime longer than allowed")
    if issued > now + CLOCK_SKEW:
        raise InvalidToken("issued in the future")
    if expires <= now - CLOCK_SKEW:
        raise InvalidToken("expired")
    jti = claims.get("jti")
    if not isinstance(jti, str) or not 0 < len(jti) <= MAX_JTI_SIZE:
        raise InvalidToken("no jti")
    return Claims(
        client_id=client_id,
        jti=jti,
        # Kept until nobody could present it again, tolerance included.
        expires_at=datetime.fromtimestamp(expires + CLOCK_SKEW, tz=timezone.utc)
    )


def verify(raw: str, gateway_keys: GatewayKeys, audience: str,
           signing_key: Callable[[UUID, str], dict | None], now: float) -> Claims:
    """
        Returns who signed the token, or raises InvalidToken. `signing_key` gives the public key a client has
        registered under a kid, or None. The single use of the jti is left to the caller.
    """
    signed, header, issuer = read_signed(open_envelope(raw, gateway_keys))
    try:
        client_id = UUID(issuer)
    except (ValueError, TypeError, AttributeError):
        raise InvalidToken("no issuer") from None
    kid = header.get("kid")
    public = signing_key(client_id, kid) if isinstance(kid, str) else None
    if public is None:
        raise InvalidToken("unknown client or key")
    try:
        signed.verify(jwk.JWK(**public))
        # From here on, only what the signature covers.
        claims = json.loads(signed.payload)
    except Exception:
        raise InvalidToken("bad signature") from None
    if not isinstance(claims, dict) or not _same_client(claims.get("iss"), client_id):
        raise InvalidToken("bad signature")
    if not _same_client(claims.get("sub"), client_id):
        # RFC 7523, section 3: a client authenticates as itself, so the subject is the issuer.
        raise InvalidToken("the subject is not the issuer")
    return check_claims(claims, client_id, audience, now)


def _same_client(issuer, client_id: UUID) -> bool:
    try:
        return UUID(issuer) == client_id
    except (ValueError, TypeError, AttributeError):
        return False
