# -*- coding: utf-8 -*-
"""
    Who is calling (ADR-0025). A client proves it once, with an assertion its key signs, at POST /oauth/token, and gets
    an access token; every other request carries that access token, which says who the client is.

    Every request, the one for the token too, also carries a DPoP proof (ADR-0033). A machine credential signs it with
    the same registered key that signed its assertion, so the token it gets is bound to that key, and a stolen token
    is good for nothing without it.
"""
import time
from dataclasses import dataclass
from uuid import UUID

from enums import Role, Worker
from repository import find_signer, remember_token

from shared.auth.gateway_keys import GatewayKeys
from shared.auth.tokens import InvalidToken, verify

from . import dpop
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


def exchange(assertion: str | None, gateway_keys: GatewayKeys, audience: str,
             proof_jkt: str) -> tuple[str, int, Principal]:
    """
        Turns a client's assertion into an access token, how many seconds it lasts and who it is for, or raises
        InvalidToken. The assertion is good once: its jti is remembered. `proof_jkt` is the thumbprint of the key the
        request's DPoP proof was signed with, already checked: it has to be the key that signed the assertion.
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
    if dpop.thumbprint(signer["key"]) != proof_jkt:
        raise dpop.InvalidProof("signed with a key other than the assertion's")
    # Last, and only for an assertion that was valid: a forged one must not be able to burn somebody's jti.
    if not remember_token(claims.client_id, claims.jti, claims.expires_at):
        raise InvalidToken("assertion already used")
    caller = Principal(client_id=claims.client_id, role=signer["role"], kid=signer["key"]["kid"], jti=claims.jti,
                       worker=signer["worker"])
    token, lifetime = issue(gateway_keys, audience, caller.client_id, caller.role, caller.worker, caller.kid,
                            proof_jkt)
    return token, lifetime, caller


def authenticate(token: str | None, proofs: list[str], method: str, urls: set[str], gateway_keys: GatewayKeys,
                 audience: str) -> Principal:
    """
        Turns the access token of a request and its DPoP proof into its caller, or raises InvalidToken, or
        InvalidProof for the proof. It does not touch the database.
    """
    if not token:
        raise InvalidToken("no token")
    now = time.time()
    access = read(token, gateway_keys, audience, now)
    if dpop.check(proofs, method, urls, token, now) != access.jkt:
        raise dpop.InvalidProof("signed with a key other than the token's")
    return Principal(client_id=access.client_id, role=access.role, kid=access.kid, jti=access.jti,
                     worker=access.worker)
