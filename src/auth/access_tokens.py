# -*- coding: utf-8 -*-
"""
    The access tokens the gateway issues at POST /oauth/token (ADR-0025): JWTs it signs itself (RFC 9068), good for
    10 minutes and for as many requests as the client makes in them. Each is bound to the key that asked for it
    (`cnf.jkt`, RFC 9449, ADR-0033): every request with it brings a proof signed with that key (dpop.py).

    A token says who the client is, so a request is checked without going to the database: the signature with a
    signing key of the gateway, its type, its issuer and audience, and its lifetime. Revoking a key of a client stops
    it getting a new token at once; one it already has is good until it expires.

    A token of a person of a client (ADR-0029), issued for an ID token of the IdP, says also who the person is and their
    role there (`hive_user`, `hive_user_role`), and has no key of the client: the key it is bound to is the BFF's.

    Nothing here touches the database. The reasons a token is turned away are fixed texts, never anything taken from
    the token, so they can go to the log.
"""
import json
import time
from dataclasses import dataclass
from uuid import UUID, uuid4

from jwcrypto import jws

from enums import PersonRole, Role, Worker
from shared.auth.gateway_keys import SIGNING_ALGORITHM, GatewayKeys
from shared.auth.tokens import CLOCK_SKEW, MAX_TOKEN_SIZE, InvalidToken, is_a_number, is_compact

# RFC 9068, section 2.1: an access token says it is one, so it is never taken for anything else, nor anything else
# for it.
ACCESS_TYPE = "at+jwt"
LIFETIME = 600  # seconds


@dataclass(frozen=True)
class Access:
    """What a valid access token says, all of it signed by the gateway."""
    client_id: UUID
    role: Role
    # The registered key the client signed its assertion with. None in the token of a person.
    kid: str | None
    jti: str
    # The thumbprint of the key every proof that goes with this token is signed with (RFC 9449, section 6).
    jkt: str
    worker: Worker | None = None
    # The person of the client, and their role there, in the token of a person.
    user_id: UUID | None = None
    user_role: PersonRole | None = None


def issue(gateway_keys: GatewayKeys, audience: str, client_id: UUID, role: Role, worker: Worker | None,
          kid: str | None, jkt: str, now: float | None = None, user_id: UUID | None = None,
          user_role: PersonRole | None = None) -> tuple[str, int]:
    """
        A new access token for that client, bound to the key with thumbprint `jkt` and signed with the newest signing
        key of the gateway, and how long it lasts. For a person, `user_id` and `user_role` instead of `kid`.
    """
    if (kid is None) == (user_id is None) or (user_id is None) != (user_role is None):
        raise ValueError("a token is either of a key or of a person with their role")
    issued = int(time.time() if now is None else now)
    claims = {"iss": audience, "aud": audience, "sub": str(client_id), "client_id": str(client_id),
              "iat": issued, "exp": issued + LIFETIME, "jti": str(uuid4()),
              "hive_role": role.value, "hive_worker": worker.value if worker else None, "hive_key": kid,
              "cnf": {"jkt": jkt}}
    if user_id is not None:
        claims.update(hive_user=str(user_id), hive_user_role=user_role.value)
    signer_kid, key = gateway_keys.signer()
    signed = jws.JWS(json.dumps(claims).encode())
    signed.add_signature(key, alg=SIGNING_ALGORITHM,
                         protected={"alg": SIGNING_ALGORITHM, "typ": ACCESS_TYPE, "kid": signer_kid})
    return signed.serialize(compact=True), LIFETIME


def read(raw, gateway_keys: GatewayKeys, audience: str, now: float) -> Access:
    """Returns what a valid access token says, or raises InvalidToken."""
    if not isinstance(raw, str) or len(raw) > MAX_TOKEN_SIZE or not is_compact(raw):
        raise InvalidToken("not an access token")
    signed = jws.JWS()
    signed.allowed_algs = [SIGNING_ALGORITHM]
    try:
        signed.deserialize(raw)
        header = signed.jose_header
    except Exception:
        raise InvalidToken("malformed access token") from None
    if not isinstance(header, dict) or header.get("alg") != SIGNING_ALGORITHM or header.get("typ") != ACCESS_TYPE:
        raise InvalidToken("not an access token")
    key = gateway_keys.verifier(header.get("kid"))
    if key is None:
        raise InvalidToken("not signed by a key of this gateway")
    try:
        signed.verify(key)
        # From here on, only what the signature covers.
        claims = json.loads(signed.payload)
    except Exception:
        raise InvalidToken("bad signature") from None
    if not isinstance(claims, dict):
        raise InvalidToken("bad signature")
    return _check(claims, audience, now)


def _check(claims: dict, audience: str, now: float) -> Access:
    if claims.get("iss") != audience or claims.get("aud") != audience:
        raise InvalidToken("issued by or for another gateway")
    issued, expires = claims.get("iat"), claims.get("exp")
    if not is_a_number(issued) or not is_a_number(expires) or not 0 < expires - issued <= LIFETIME:
        raise InvalidToken("lifetime other than allowed")
    if issued > now + CLOCK_SKEW:
        raise InvalidToken("issued in the future")
    # The gateway's own clock signed it: no tolerance on the way out.
    if expires <= now:
        raise InvalidToken("expired")
    try:
        client_id = UUID(claims.get("sub"))
        role = Role(claims.get("hive_role"))
        worker = Worker(claims["hive_worker"]) if claims.get("hive_worker") is not None else None
    except (ValueError, TypeError, KeyError):
        raise InvalidToken("no client, role or worker") from None
    if claims.get("client_id") != str(client_id):
        raise InvalidToken("no client, role or worker")
    kid, jti = claims.get("hive_key"), claims.get("jti")
    user_id, user_role = None, None
    if claims.get("hive_user") is not None:
        try:
            user_id, user_role = UUID(claims["hive_user"]), PersonRole(claims.get("hive_user_role"))
        except (ValueError, TypeError):
            raise InvalidToken("no person or role") from None
        # A person is not the response engine, nor signs with a key of the client.
        if kid is not None or worker is not None or role is Role.SERVICE:
            raise InvalidToken("no person or role")
    elif not isinstance(kid, str) or not kid:
        raise InvalidToken("no key or jti")
    if not isinstance(jti, str) or not jti:
        raise InvalidToken("no key or jti")
    confirmation = claims.get("cnf")
    jkt = confirmation.get("jkt") if isinstance(confirmation, dict) else None
    if not isinstance(jkt, str) or not jkt:
        raise InvalidToken("not bound to a key")
    return Access(client_id=client_id, role=role, kid=kid, jti=jti, jkt=jkt, worker=worker, user_id=user_id,
                  user_role=user_role)
