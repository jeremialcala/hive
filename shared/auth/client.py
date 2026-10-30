# -*- coding: utf-8 -*-
"""
    The client's side of the authentication, as a reference: what a client of the service has to do to call it
    (ADR-0025).

    1. Build an assertion: a JWT signed with its key, inside a JWE for the gateway. It is good once and for 60 s.
    2. Send it to POST /oauth/token as the body `token_request` gives, with a `DPoP` header that `dpop_proof` signs
       with the same key, and get an access token back, bound to that key (ADR-0033).
    3. Call the API with `Authorization: DPoP <access token>` until it expires, 10 minutes later, and in each request
       a new `DPoP` header for its method and URL, with the token.

    The tests build their assertions with this, so what is documented for clients is what is verified.
"""
import base64
import hashlib
import json
import time
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from jwcrypto import jwe, jwk, jws
from jwcrypto.common import base64url_decode

from .key_proof import PROOF_TYPE
from .tokens import CONTENT_ENCRYPTION, KEY_ENCRYPTION, MAX_LIFETIME, SIGNATURE

TOKEN_PATH = "/oauth/token"
DPOP_TYPE = "dpop+jwt"
GRANT_TYPE = "client_credentials"
ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"


def new_signing_key() -> dict:
    """A new private P-256 key as a JWK. It never leaves the client: only its public part is registered."""
    key = jwk.JWK.generate(kty="EC", crv="P-256")
    return {**json.loads(key.export_private()), "kid": key.thumbprint()}


def public_part(private: dict) -> dict:
    return {name: private[name] for name in ("kty", "crv", "x", "y") if name in private}


def build_assertion(signing_key: dict, client_id, audience: str, gateway_key: dict,
                    now: float | None = None, lifetime: int = MAX_LIFETIME, jti: str | None = None) -> str:
    """
        An assertion to get one access token: sign first, then encrypt. `signing_key` is the client's private JWK
        and `gateway_key` one of the gateway's public ones. Its issuer and its subject are the client.
    """
    issued = int(time.time() if now is None else now)
    claims = {"iss": str(client_id), "sub": str(client_id), "aud": audience, "iat": issued,
              "exp": issued + lifetime, "jti": jti or str(uuid4())}

    signed = jws.JWS(json.dumps(claims).encode())
    signed.add_signature(jwk.JWK(**signing_key), alg=SIGNATURE,
                         protected={"alg": SIGNATURE, "typ": "JWT", "kid": jwk.JWK(**signing_key).thumbprint()})

    envelope = jwe.JWE(signed.serialize(compact=True).encode(),
                       protected={"alg": KEY_ENCRYPTION, "enc": CONTENT_ENCRYPTION, "cty": "JWT",
                                  "kid": gateway_key["kid"]})
    envelope.add_recipient(jwk.JWK(**gateway_key))
    return envelope.serialize(compact=True)


def dpop_proof(signing_key: dict, method: str, url: str, access_token: str | None = None,
               now: float | None = None, jti: str | None = None) -> str:
    """
        The `DPoP` header of one request (RFC 9449, ADR-0033): a JWT the client's key signs, with its public part in
        the header, for that method and that URL without query or fragment. Beside an access token it carries the
        token's hash, so it is good for no other token. The gateway takes each one once, and for 60 seconds.
    """
    private = jwk.JWK(**signing_key)
    public = {name: value for name, value in json.loads(private.export_public()).items()
              if name in ("kty", "crv", "x", "y")}
    scheme, host, path, _, _ = urlsplit(url)
    claims = {"jti": jti or str(uuid4()), "htm": method.upper(), "htu": urlunsplit((scheme, host, path, "", "")),
              "iat": int(time.time() if now is None else now)}
    if access_token is not None:
        digest = hashlib.sha256(access_token.encode("ascii")).digest()
        claims["ath"] = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    signed = jws.JWS(json.dumps(claims).encode())
    signed.add_signature(private, alg=SIGNATURE, protected={"alg": SIGNATURE, "typ": DPOP_TYPE, "jwk": public})
    return signed.serialize(compact=True)


def token_request(assertion: str) -> dict:
    """The body of POST /oauth/token, to send as application/x-www-form-urlencoded."""
    return {"grant_type": GRANT_TYPE, "client_assertion_type": ASSERTION_TYPE, "client_assertion": assertion}


def access_token_jti(access_token: str) -> str:
    """
        The jti of an access token, read without checking it: the client trusts the gateway that gave it. A proof
        for a new key is bound to it (key_proof).
    """
    return json.loads(base64url_decode(access_token.split(".")[1]))["jti"]


def key_proof(new_key: dict, client_id, audience: str, jti: str, now: float | None = None,
              lifetime: int = MAX_LIFETIME) -> str:
    """
        What registering a new key with POST /keys asks for besides its public part: a JWT the new private key
        signs, for the jti of the access token that goes with that same request. The access token comes from a key
        the client already has; the proof, from the one it is adding.
    """
    issued = int(time.time() if now is None else now)
    claims = {"iss": str(client_id), "aud": audience, "iat": issued, "exp": issued + lifetime, "jti": jti}
    signed = jws.JWS(json.dumps(claims).encode())
    signed.add_signature(jwk.JWK(**new_key), alg=SIGNATURE, protected={"alg": SIGNATURE, "typ": PROOF_TYPE})
    return signed.serialize(compact=True)
