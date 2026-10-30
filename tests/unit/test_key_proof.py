# -*- coding: utf-8 -*-
"""
    RD-04 of the dual review of Gate 2: a client that adds a signing key proves it holds the private part, with a
    proof signed by the new key for the access token of the same request (ADR-0025). Everything goes through the application.
"""
import json
import logging
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from jwcrypto import jws

import app as app_module
from shared.auth.client import access_token_jti, key_proof, new_signing_key, public_part
from shared.auth.key_proof import PROOF_TYPE
from conftest import GATEWAY_AUDIENCE
from repository import get_client


@pytest.fixture
def http():
    return TestClient(app_module.app)


def signed(key: dict, claims: dict, typ: str = PROOF_TYPE) -> str:
    """A proof made by hand, to change what key_proof always gets right."""
    from jwcrypto import jwk

    proof = jws.JWS(json.dumps(claims).encode())
    proof.add_signature(jwk.JWK(**key), alg="ES256", protected={"alg": "ES256", "typ": typ})
    return proof.serialize(compact=True)


def claims_for(caller, jti: str, **changes) -> dict:
    now = int(time.time())
    return {"iss": str(caller.client_id), "aud": GATEWAY_AUDIENCE, "iat": now, "exp": now + 60, "jti": jti,
            **changes}


# Each one gets the caller, the new key and the jti of the token it goes with, and gives back the proof.
BAD_PROOFS = {
    "signed-by-another-key": lambda caller, new, jti: key_proof(new_signing_key(), caller.client_id,
                                                                GATEWAY_AUDIENCE, jti),
    "signed-by-the-key-it-already-has": lambda caller, new, jti: key_proof(caller.key, caller.client_id,
                                                                           GATEWAY_AUDIENCE, jti),
    "for-another-client": lambda caller, new, jti: key_proof(new, uuid4(), GATEWAY_AUDIENCE, jti),
    "for-another-gateway": lambda caller, new, jti: key_proof(new, caller.client_id, "https://elsewhere.test", jti),
    "for-another-request": lambda caller, new, jti: key_proof(new, caller.client_id, GATEWAY_AUDIENCE,
                                                              str(uuid4())),
    "expired": lambda caller, new, jti: key_proof(new, caller.client_id, GATEWAY_AUDIENCE, jti,
                                                  now=time.time() - 300),
    "too-long-lived": lambda caller, new, jti: key_proof(new, caller.client_id, GATEWAY_AUDIENCE, jti,
                                                         lifetime=3600),
    "a-token-and-not-a-proof": lambda caller, new, jti: signed(new, claims_for(caller, jti), typ="JWT"),
    "without-a-jti": lambda caller, new, jti: signed(new, {k: v for k, v in claims_for(caller, jti).items()
                                                           if k != "jti"}),
    "not-a-jws": lambda caller, new, jti: "not.a.proof",
    "json-form": lambda caller, new, jti: json.dumps({"payload": "e30", "signatures": []}),
}


def test_a_client_adds_a_key_it_proves_it_holds(http, register):
    caller = register("acme")
    new = new_signing_key()

    added = http.post("/keys", **caller.adding(new))

    assert (added.status_code, added.json()["data"]["keys"]) == (200, [caller.kid, new["kid"]])


@pytest.mark.parametrize("kind", sorted(BAD_PROOFS))
def test_without_a_good_proof_the_key_is_not_added(http, register, caplog, kind):
    caller = register("acme")
    new = new_signing_key()
    request = caller.adding(new)
    token = caller.access_token()
    request = {"headers": {"Authorization": f"DPoP {token}"},
               "json": {**request["json"], "proof": BAD_PROOFS[kind](caller, new, access_token_jti(token))}}

    with caplog.at_level(logging.WARNING):
        response = http.post("/keys", **request)

    assert (response.status_code, response.json()) == (422, {"detail": "INVALID_KEY_PROOF"})
    assert [key["kid"] for key in get_client(caller.client_id).signingKeys] == [caller.kid]
    # Why goes to the log, in a fixed text: nothing of what was sent.
    assert f"Rejected a new signing key for client {caller.client_id}" in caplog.text
    assert request["json"]["proof"] not in caplog.text


def test_without_a_proof_the_key_is_not_added(http, register):
    caller = register("acme")

    response = http.post("/keys", headers=caller.auth, json={"signingKey": public_part(new_signing_key())})

    assert response.status_code == 422
    assert len(get_client(caller.client_id).signingKeys) == 1


def test_a_proof_does_not_serve_twice(http, register):
    caller = register("acme")
    new = new_signing_key()
    request = caller.adding(new)
    assert http.post("/keys", **request).status_code == 200
    http.delete(f"/keys/{new['kid']}", headers=caller.auth)

    # With a new access token the proof is for another one; with the same one, which still serves for ten minutes
    # (ADR-0025), the proof is already used.
    again_with_a_new_token = http.post("/keys", headers=caller.auth, json=request["json"])
    again_as_it_was = http.post("/keys", **request)

    assert again_with_a_new_token.status_code == 422
    assert (again_as_it_was.status_code, again_as_it_was.json()) == (422, {"detail": "INVALID_KEY_PROOF"})
    assert [key["kid"] for key in get_client(caller.client_id).signingKeys] == [caller.kid]


def test_the_operator_lets_a_client_back_in_without_a_proof(http, operator, register):
    """It is the way back for a client that lost its key: it has no key left to sign anything with."""
    caller = register("acme")
    new = new_signing_key()

    added = http.post(f"/clients/{caller.client_id}/keys", headers=operator.auth,
                      json={"signingKey": public_part(new)})

    assert (added.status_code, added.json()["data"]["keys"]) == (200, [caller.kid, new["kid"]])
