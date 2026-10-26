# -*- coding: utf-8 -*-
"""
    Who is calling (ADR-0025). A client proves it once, with an assertion its key signs, at POST /oauth/token, and gets
    an access token; every other request carries that access token, which says who the client is.
"""
import time
from dataclasses import dataclass
from uuid import UUID

from enums import Role, Worker
from repository import find_signer, remember_token

from shared.auth.gateway_keys import GatewayKeys
from shared.auth.tokens import InvalidToken, verify

from .access_tokens import issue, read


@dataclass(frozen=True)
class Principal:
    """Who is calling. What it may do comes from its registry entry, as it was when its access token was issued."""
    client_id: UUID
    role: Role
    # The registered key the assertion behind the access token was signed with.
    kid: str
    # The access token's: what a request that needs a proof binds it to.
    jti: str
    # For the response engine, which of its workers signs: it decides the routes it reaches. None for a key
    # registered before keys had a worker (ADR-0022).
    worker: Worker | None = None

    @property
    def worker_name(self) -> str:
        """The worker, as the log writes it."""
        return self.worker.value if self.worker else "none"


def exchange(assertion: str | None, gateway_keys: GatewayKeys, audience: str) -> tuple[str, int, Principal]:
    """
        Turns a client's assertion into an access token, how many seconds it lasts and who it is for, or raises
        InvalidToken. The assertion is good once: its jti is remembered.
    """
    if not assertion:
        raise InvalidToken("no assertion")
    signer = {}

    def signing_key(client_id: UUID, kid: str) -> dict | None:
        found = find_signer(client_id, kid)
        if found is None:
            return None
        _, key, signer["role"], signer["worker"] = found
        signer["key"] = key
        return key

    claims = verify(assertion, gateway_keys, audience, signing_key, time.time())
    # Last, and only for an assertion that was valid: a forged one must not be able to burn somebody's jti.
    if not remember_token(claims.client_id, claims.jti, claims.expires_at):
        raise InvalidToken("assertion already used")
    caller = Principal(client_id=claims.client_id, role=signer["role"], kid=signer["key"]["kid"], jti=claims.jti,
                       worker=signer["worker"])
    token, lifetime = issue(gateway_keys, audience, caller.client_id, caller.role, caller.worker, caller.kid)
    return token, lifetime, caller


def authenticate(token: str | None, gateway_keys: GatewayKeys, audience: str) -> Principal:
    """Turns the access token of a request into its caller, or raises InvalidToken. It does not touch the database."""
    if not token:
        raise InvalidToken("no token")
    access = read(token, gateway_keys, audience, time.time())
    return Principal(client_id=access.client_id, role=access.role, kid=access.kid, jti=access.jti,
                     worker=access.worker)
