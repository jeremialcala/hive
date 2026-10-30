# -*- coding: utf-8 -*-
"""
    The proof that whoever registers a new signing key holds its private part: a JWT the new key signs, bound to
    the client, to this gateway and to the token of the very request that carries it. Without it, a key of a
    client registers any public key in its name, even one nobody can sign with.

    It does not stop whoever stole a key of the client: they make a key of their own and prove they hold it.
    What stops them is the operator, who can take away every key the client does not recognise.

    Nothing here touches the database, like the verification of the token itself.
"""
import json
from uuid import UUID

from jwcrypto import jwk

from .tokens import MAX_TOKEN_SIZE, InvalidToken, _same_client, check_claims, read_signed

# Not "JWT": a token signed by the new key for something else is not taken as a proof, nor a proof as a token.
PROOF_TYPE = "key-proof+jwt"


def verify_key_proof(proof, public_key: dict, client_id: UUID, audience: str, jti: str, now: float) -> None:
    """
        Raises InvalidToken unless `proof` is signed by `public_key` and was made for this client, this gateway
        and the request whose token has `jti`. The reasons are fixed texts, so they can go to the log.
    """
    if not isinstance(proof, str) or len(proof) > MAX_TOKEN_SIZE:
        raise InvalidToken("malformed key proof")
    signed, header, _ = read_signed(proof)
    if header.get("typ") != PROOF_TYPE:
        raise InvalidToken("not a key proof")
    try:
        signed.verify(jwk.JWK(**public_key))
        # From here on, only what the signature covers.
        claims = json.loads(signed.payload)
    except Exception:
        raise InvalidToken("the key proof is not signed by the new key") from None
    if not isinstance(claims, dict) or not _same_client(claims.get("iss"), client_id):
        raise InvalidToken("key proof for another client")
    # The same rules as a token: this gateway, short-lived, and with a jti.
    check_claims(claims, client_id, audience, now)
    if claims["jti"] != jti:
        # A proof is only good once: for the request it came with, whose token is only good once too.
        raise InvalidToken("key proof for another request")
