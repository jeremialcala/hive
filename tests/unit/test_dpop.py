# -*- coding: utf-8 -*-
"""
    DPoP (RFC 9449, ADR-0033): an access token is bound to the key that asked for it, and every request proves it
    holds that key. A stolen token is good for nothing without the key; a proof is good once, for one method, one URL
    and one token, and for 60 seconds.
"""
import json
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from jwcrypto import jwk, jws
from jwcrypto.common import base64url_decode

import app as app_module
from auth import dpop
from conftest import GATEWAY_AUDIENCE, KEY_OF, TEST_SERVER, WITHOUT_PROOF
from shared.auth.client import dpop_proof, new_signing_key, token_request


@pytest.fixture
def http():
    return TestClient(app_module.app)


@pytest.fixture
def caller(register):
    return register("acme")


def keys_with(http, token, proof=None, method="GET", **headers):
    """What GET /keys answers to that token with that proof; without a proof at all if `proof` is None."""
    sent = {"Authorization": f"DPoP {token}", **headers}
    sent.update({"DPoP": proof} if proof is not None else {WITHOUT_PROOF: "1"})
    return http.request(method, "/keys", headers=sent)


def forged_proof(key: dict, token: str | None = None, alg: str = "ES256", typ: str = "dpop+jwt",
                 public: dict | None = None, **claims) -> str:
    """A proof like a client's, with whatever a test wants changed. A claim set to None is left out."""
    body = {"jti": str(uuid4()), "htm": "GET", "htu": f"{TEST_SERVER}/keys", "iat": int(time.time()),
            **({"ath": dpop.token_hash(token)} if token else {})}
    body.update(claims)
    body = {name: value for name, value in body.items() if value is not None}
    private = jwk.JWK(**key)
    header = {"alg": alg, "typ": typ,
              "jwk": public if public is not None else json.loads(private.export_public())}
    signed = jws.JWS(json.dumps(body).encode())
    signed.allowed_algs = [alg]
    signed.add_signature(private, alg=alg, protected=header)
    return signed.serialize(compact=True)


def claims_of(token: str) -> dict:
    return json.loads(base64url_decode(token.split(".")[1]))


# --- The token endpoint ----------------------------------------------------------------------------------------------

def test_the_token_is_bound_to_the_key_that_signed_the_assertion(http, caller):
    response = http.post("/oauth/token", data=token_request(caller.assertion()))

    assert response.status_code == 200
    assert response.json()["token_type"] == "DPoP"
    assert claims_of(response.json()["access_token"])["cnf"] == {"jkt": dpop.thumbprint(caller.key)}


@pytest.mark.parametrize("proof", ["none", "another key", "with ath", "another url", "another method", "garbage"])
def test_a_token_request_without_a_good_proof_of_the_assertion_key_is_turned_away(http, caller, proof):
    url = f"{TEST_SERVER}/oauth/token"
    headers = {
        "none": {WITHOUT_PROOF: "1"},
        "another key": {"DPoP": dpop_proof(new_signing_key(), "POST", url)},
        "with ath": {"DPoP": dpop_proof(caller.key, "POST", url, "some.access.token")},
        "another url": {"DPoP": dpop_proof(caller.key, "POST", f"{TEST_SERVER}/keys")},
        "another method": {"DPoP": dpop_proof(caller.key, "GET", url)},
        "garbage": {"DPoP": "a.b.c"},
    }[proof]

    response = http.post("/oauth/token", data=token_request(caller.assertion()), headers=headers)

    assert (response.status_code, response.json()) == (400, {"error": "invalid_dpop_proof"})


def test_a_proof_of_the_token_request_is_good_once(http, caller):
    proof = dpop_proof(caller.key, "POST", f"{TEST_SERVER}/oauth/token")

    first = http.post("/oauth/token", data=token_request(caller.assertion()), headers={"DPoP": proof})
    again = http.post("/oauth/token", data=token_request(caller.assertion()), headers={"DPoP": proof})

    assert (first.status_code, again.status_code) == (200, 400)


# --- Every other request ---------------------------------------------------------------------------------------------

def test_a_token_with_its_proof_is_good_for_many_requests(http, caller):
    token = caller.access_token()

    assert [keys_with(http, token, dpop_proof(caller.key, "GET", f"{TEST_SERVER}/keys", token)).status_code
            for _ in range(3)] == [200, 200, 200]


def test_a_token_without_its_proof_is_turned_away(http, caller):
    response = keys_with(http, caller.access_token())

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'DPoP algs="ES256", error="invalid_dpop_proof"'


def test_a_stolen_token_is_good_for_nothing_with_a_proof_of_another_key(http, caller):
    token = caller.access_token()

    assert keys_with(http, token, dpop_proof(new_signing_key(), "GET", f"{TEST_SERVER}/keys", token)).status_code \
        == 401


def test_a_proof_is_good_once(http, caller):
    token = caller.access_token()
    proof = dpop_proof(caller.key, "GET", f"{TEST_SERVER}/keys", token)

    assert [keys_with(http, token, proof).status_code for _ in range(2)] == [200, 401]


@pytest.mark.parametrize("method,url", [("GET", f"{TEST_SERVER}/bot"), ("POST", f"{TEST_SERVER}/keys"),
                                        ("GET", "https://other.test/keys"), ("GET", f"{TEST_SERVER}/keys/")],
                         ids=["another route", "another method", "another host", "another path"])
def test_a_proof_for_another_request_is_turned_away(http, caller, method, url):
    token = caller.access_token()

    assert keys_with(http, token, dpop_proof(caller.key, method, url, token)).status_code == 401


def test_the_public_address_and_the_internal_one_are_both_good(http, caller):
    token = caller.access_token()

    for base in (TEST_SERVER, GATEWAY_AUDIENCE):
        assert keys_with(http, token, dpop_proof(caller.key, "GET", f"{base}/keys", token)).status_code == 200


def test_a_proof_for_another_token_is_turned_away(http, caller):
    token, other = caller.access_token(), caller.access_token()

    assert keys_with(http, token, dpop_proof(caller.key, "GET", f"{TEST_SERVER}/keys", other)).status_code == 401
    assert keys_with(http, token, dpop_proof(caller.key, "GET", f"{TEST_SERVER}/keys")).status_code == 401


@pytest.mark.parametrize("offset", [-61, 6], ids=["a minute old", "from the future"])
def test_a_proof_outside_its_window_is_turned_away(http, caller, offset):
    token = caller.access_token()
    proof = dpop_proof(caller.key, "GET", f"{TEST_SERVER}/keys", token, now=time.time() + offset)

    assert keys_with(http, token, proof).status_code == 401


def test_a_clock_a_little_off_is_tolerated(http, caller, monkeypatch):
    token = caller.access_token()
    # A test run can be younger than 50 seconds: here only the window is tested, not the start.
    monkeypatch.setattr(dpop, "STARTED", 0)

    for offset in (-50, 4):
        proof = dpop_proof(caller.key, "GET", f"{TEST_SERVER}/keys", token, now=time.time() + offset)
        assert keys_with(http, token, proof).status_code == 200


def test_a_proof_made_before_the_api_started_is_turned_away(http, caller, monkeypatch):
    """After a restart the jtis it remembered are gone: a proof from before cannot be taken again."""
    token = caller.access_token()
    proof = dpop_proof(caller.key, "GET", f"{TEST_SERVER}/keys", token, now=time.time() - 10)
    monkeypatch.setattr(dpop, "STARTED", int(time.time()) - 5)

    assert keys_with(http, token, proof).status_code == 401


def test_the_old_scheme_is_not_taken_any_more(http, caller):
    token = caller.access_token()
    proof = dpop_proof(caller.key, "GET", f"{TEST_SERVER}/keys", token)

    assert http.get("/keys", headers={"Authorization": f"Bearer {token}", "DPoP": proof}).status_code == 401


def test_two_proofs_are_one_too_many(http, caller):
    token = caller.access_token()
    url = f"{TEST_SERVER}/keys"
    response = http.get("/keys", headers=[("Authorization", f"DPoP {token}"),
                                          ("DPoP", dpop_proof(caller.key, "GET", url, token)),
                                          ("DPoP", dpop_proof(caller.key, "GET", url, token))])

    assert response.status_code == 401


def test_a_proof_is_turned_away_for_the_reason_it_should_be(caller):
    token = "the.access.token"
    url = f"{TEST_SERVER}/keys"
    private_in_header = json.loads(jwk.JWK(**caller.key).export_private())
    other_curve = json.loads(jwk.JWK.generate(kty="EC", crv="P-384").export_private())
    cases = {
        "not exactly one DPoP proof": [],
        "not a DPoP proof": [forged_proof(caller.key, token, typ="JWT")],
        "malformed DPoP proof": ["a.b.c"],
        "no public P-256 key in the proof": [forged_proof(caller.key, token, public=private_in_header)],
        "bad signature": [forged_proof(caller.key, token, public=json.loads(
            jwk.JWK(**new_signing_key()).export_public()))],
        "for another method": [forged_proof(caller.key, token, htm="POST")],
        "for another URL": [forged_proof(caller.key, token, htu=f"{url}?query")],
        "made outside its window": [forged_proof(caller.key, token, iat="now")],
        "no jti": [forged_proof(caller.key, token, jti=None)],
        "for another access token": [forged_proof(caller.key, "another.access.token")],
    }
    for reason, proofs in cases.items():
        with pytest.raises(dpop.InvalidProof) as rejection:
            dpop.check(proofs, "GET", {url}, token, time.time(), seen=dpop.Seen())
        assert str(rejection.value) == reason, reason
    with pytest.raises(dpop.InvalidProof, match="not a DPoP proof"):
        dpop.check([forged_proof(other_curve, token, alg="ES384")], "GET", {url}, token, time.time(),
                   seen=dpop.Seen())
    with pytest.raises(dpop.InvalidProof, match="an access token hash where there is no access token"):
        dpop.check([forged_proof(caller.key, token)], "GET", {url}, None, time.time(), seen=dpop.Seen())


def test_a_forged_proof_does_not_burn_a_jti(caller):
    """Only a proof that is good is remembered: somebody else's bad one cannot spend a jti before it is used."""
    seen, url, token = dpop.Seen(), f"{TEST_SERVER}/keys", "the.access.token"
    jti = str(uuid4())

    with pytest.raises(dpop.InvalidProof):
        dpop.check([forged_proof(caller.key, "another.token", jti=jti)], "GET", {url}, token, time.time(), seen)
    assert dpop.check([forged_proof(caller.key, token, jti=jti)], "GET", {url}, token, time.time(), seen) \
        == dpop.thumbprint(caller.key)


def test_when_the_memory_of_jtis_is_full_new_proofs_are_turned_away(caller):
    """It fails closed: forgetting a jti to make room would let its proof in again."""
    seen = dpop.Seen(capacity=2)
    now = time.time()

    assert [seen.remember("k", jti, now + 60, now) for jti in ("a", "b", "c")] == [True, True, False]
    # Once the window of the first ones is over, there is room again.
    assert seen.remember("k", "c", now + 200, now + 100)


def test_every_route_but_the_public_ones_asks_for_the_proof(http, caller):
    token = caller.access_token()

    for path in ("/keys", "/gateway/keys", "/bot"):
        method = "POST" if path == "/bot" else "GET"
        assert http.request(method, path, headers={"Authorization": f"DPoP {token}", WITHOUT_PROOF: "1"},
                            json={}).status_code == 401
    assert KEY_OF[token] == caller.key
