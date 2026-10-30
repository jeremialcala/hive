# -*- coding: utf-8 -*-
"""
    An IdP that stands for Zitadel in the tests of the people of a client (ADR-0029): its own RSA key and JWKS, the ID
    tokens it signs, and the exchange at POST /oauth/token the way the BFF makes it, with the BFF's own key.
"""
import json
import time

from jwcrypto import jwk, jws

from auth.idp import IdpUnavailable
from conftest import TEST_SERVER
from shared.auth.client import dpop_proof, new_signing_key

ISSUER, AUDIENCE = "https://idp.test", "bff-client-id"
SUBJECT = "312345678901234567"
EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
ID_TOKEN = "urn:ietf:params:oauth:token-type:id_token"
ACCESS_TOKEN = "urn:ietf:params:oauth:token-type:access_token"
TOKEN_URL = f"{TEST_SERVER}/oauth/token"
# The BFF's own key (ADR-0033, point 2): it binds every token of a person it gets.
BFF = new_signing_key()


class Zitadel:
    """Stands for the IdP: signs ID tokens with its key, and counts how often its JWKS is read."""

    def __init__(self):
        self.key = jwk.JWK.generate(kty="RSA", size=2048, kid="idp-1", use="sig")
        self.reads = 0
        self.down = False

    def jwks(self, url):
        self.reads += 1
        if self.down:
            raise IdpUnavailable("ConnectionError")
        keys = jwk.JWKSet()
        keys.add(jwk.JWK.from_json(self.key.export_public()))
        return keys

    def id_token(self, key=None, kid="idp-1", alg="RS256", **changes) -> str:
        now = int(time.time())
        claims = {"iss": ISSUER, "sub": SUBJECT, "aud": [AUDIENCE], "azp": AUDIENCE, "iat": now, "exp": now + 3600,
                  "amr": ["pwd", "mfa", "otp"], "auth_time": now}
        claims.update(changes)
        claims = {name: value for name, value in claims.items() if value is not None}
        signed = jws.JWS(json.dumps(claims).encode())
        signed.add_signature(key or self.key, alg=alg, protected={"alg": alg, "kid": kid, "typ": "JWT"})
        return signed.serialize(compact=True)


def exchange(http, id_token, key=BFF, proof=True, **form):
    body = {"grant_type": EXCHANGE, "subject_token": id_token, "subject_token_type": ID_TOKEN, **form}
    headers = {"DPoP": dpop_proof(key, "POST", TOKEN_URL)} if proof else {"X-Test-Without-DPoP": "1"}
    return http.post("/oauth/token", data={k: v for k, v in body.items() if v is not None}, headers=headers)


def auth(token) -> dict:
    return {"Authorization": f"DPoP {token}"}
