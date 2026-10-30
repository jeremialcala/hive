# -*- coding: utf-8 -*-
"""
    Authentication of everything that is not the webhook (ADR-0025): OAuth 2.0 client_credentials. A client sends an
    assertion to POST /oauth/token, a JWT it signs inside a JWE encrypted to the gateway, and gets an access token the
    gateway signs, which it presents to every other route.

    An assertion built the documented way gets a token; every way of forging, altering or replaying one gets the same
    401 `invalid_client`. An access token the gateway issued gets in for ten minutes; one it did not issue, or one
    that is not exactly as issued, gets the same 401. The keys are real, the clients are registered in the in-memory
    database, and the requests go through the application.
"""
import json
import time
from datetime import timezone
from uuid import uuid4

import mongoengine
import pytest
from fastapi.testclient import TestClient
from jwcrypto import jwe, jwk, jws
from jwcrypto.common import base64url_decode

import app as app_module
from auth import access_tokens, dpop, principal
from shared.auth import gateway_keys, tokens
from shared.auth.client import ASSERTION_TYPE, GRANT_TYPE, dpop_proof, new_signing_key, token_request
from classes import Client, SeenToken
from conftest import GATEWAY_AUDIENCE, GATEWAY_KEYS_FILE, KEY_OF, TEST_SERVER
from enums import PersonRole, Role, Status
from repository import find_signer

GATEWAY = gateway_keys.GatewayKeys.load(GATEWAY_KEYS_FILE)
GATEWAY_KEY = GATEWAY.public_set()["keys"][0]


@pytest.fixture
def http():
    return TestClient(app_module.app)


@pytest.fixture
def caller(register):
    return register("acme")


def asking(http, assertion):
    """What POST /oauth/token answers to an assertion."""
    return http.post("/oauth/token", data=token_request(assertion))


def status_of(http, assertion) -> int:
    return asking(http, assertion).status_code


def calling(http, access_token):
    """What a route answers to an access token."""
    headers = {"Authorization": f"DPoP {access_token}"} if access_token is not None else {}
    return http.get("/keys", headers=headers)


def forge(caller, sign_with: dict | None = None, signature: str = "ES256", jws_header: dict | None = None,
          encrypt_to: dict | None = None, jwe_header: dict | None = None, inner: str | None = None, **claims) -> str:
    """An assertion like the real one, with whatever a test wants changed."""
    now = int(time.time())
    body = {"iss": str(caller.client_id), "sub": str(caller.client_id), "aud": GATEWAY_AUDIENCE, "iat": now,
            "exp": now + 60, "jti": str(uuid4())}
    body.update({name: value(now) if callable(value) else value for name, value in claims.items()})
    body = {name: value for name, value in body.items() if value is not None}
    if inner is None:
        key = jwk.JWK(**(sign_with or caller.key))
        signed = jws.JWS(json.dumps(body).encode())
        signed.allowed_algs = [signature]
        signed.add_signature(key, alg=signature,
                             protected={"alg": signature, "typ": "JWT", "kid": caller.kid, **(jws_header or {})})
        inner = signed.serialize(compact=True)
    target = encrypt_to or GATEWAY_KEY
    protected = {"alg": "RSA-OAEP-256", "enc": "A256CBC-HS512", "cty": "JWT", "kid": target.get("kid", "none"),
                 **(jwe_header or {})}
    envelope = jwe.JWE(inner.encode(), protected=protected,
                       algs=["RSA-OAEP-256", "RSA-OAEP", "A256CBC-HS512", "A256GCM", "dir"])
    envelope.add_recipient(jwk.JWK(**target))
    forged = envelope.serialize(compact=True)
    # The proof of the request is the honest one of the caller's key: what is tested is the assertion.
    KEY_OF[forged] = caller.key
    return forged


def payload(token: str) -> dict:
    return json.loads(base64url_decode(token.split(".")[1]))


def header(token: str) -> dict:
    return json.loads(base64url_decode(token.split(".")[0]))


# --- The token endpoint ----------------------------------------------------------------------------------------------

def test_an_assertion_built_the_documented_way_gets_an_access_token(http, caller):
    response = asking(http, caller.assertion())

    assert response.status_code == 200
    body = response.json()
    assert (body["token_type"], body["expires_in"]) == ("DPoP", 600)
    # RFC 6749, section 5.1: nothing keeps it.
    assert (response.headers["cache-control"], response.headers["pragma"]) == ("no-store", "no-cache")
    assert calling(http, body["access_token"]).json()["data"]["uuid"] == str(caller.client_id)


def test_the_access_token_says_who_the_client_is_and_the_gateway_signs_it(http, caller):
    token = caller.access_token()

    assert header(token) == {"alg": "ES256", "typ": "at+jwt", "kid": GATEWAY.signer()[0]}
    claims = payload(token)
    assert (claims["iss"], claims["aud"]) == (GATEWAY_AUDIENCE, GATEWAY_AUDIENCE)
    assert (claims["sub"], claims["client_id"]) == (str(caller.client_id), str(caller.client_id))
    assert (claims["hive_role"], claims["hive_worker"], claims["hive_key"]) == ("client", None, caller.kid)
    assert claims["exp"] - claims["iat"] == 600


def test_the_forged_assertion_of_these_tests_is_valid_when_nothing_is_changed(http, caller):
    """Without this, every rejection below could be the helper's fault."""
    assert status_of(http, forge(caller)) == 200


def test_the_audience_may_be_one_of_several(http, caller):
    assert status_of(http, forge(caller, aud=["https://other.test", GATEWAY_AUDIENCE])) == 200


def test_a_clock_slightly_off_is_tolerated(http, caller):
    now = int(time.time())

    assert status_of(http, forge(caller, iat=now + 20, exp=now + 80)) == 200
    assert status_of(http, forge(caller, iat=now - 80, exp=now - 20)) == 200


FORM = {"Content-Type": "application/x-www-form-urlencoded"}


@pytest.mark.parametrize("body", [
    lambda assertion: "grant_type=client_credentials",
    lambda assertion: "",
    lambda assertion: "not a form at all",
    lambda assertion: (f"grant_type={GRANT_TYPE}&grant_type={GRANT_TYPE}&client_assertion_type={ASSERTION_TYPE}"
                       f"&client_assertion={assertion}"),
    lambda assertion: f"grant_type={GRANT_TYPE}&client_assertion_type={ASSERTION_TYPE}".encode() + bytes([0xFF]),
], ids=["no assertion", "empty", "not a form", "a parameter twice", "not ascii"])
def test_a_request_that_is_not_the_agreed_form_is_invalid(http, caller, body):
    response = http.post("/oauth/token", content=body(caller.assertion()), headers=FORM)

    assert (response.status_code, response.json()) == (400, {"error": "invalid_request"})


def test_a_request_that_is_not_a_form_is_invalid(http, caller):
    response = http.post("/oauth/token", json=token_request(caller.assertion()))

    assert (response.status_code, response.json()) == (400, {"error": "invalid_request"})


def test_another_grant_is_not_supported(http, caller):
    response = http.post("/oauth/token", data={**token_request(caller.assertion()), "grant_type": "password"})

    assert (response.status_code, response.json()) == (400, {"error": "unsupported_grant_type"})


def test_another_kind_of_assertion_is_an_invalid_client(http, caller):
    response = http.post("/oauth/token", data={**token_request(caller.assertion()),
                                               "client_assertion_type": "urn:ietf:params:oauth:grant-type:saml2-bearer"})

    assert (response.status_code, response.json()) == (401, {"error": "invalid_client"})


def test_an_oversized_request_is_not_parsed(http, caller):
    response = http.post("/oauth/token", data={**token_request(caller.assertion()), "padding": "x" * 20000})

    assert (response.status_code, response.json()) == (400, {"error": "invalid_request"})


def test_an_oversized_assertion_is_rejected_before_it_is_parsed(http, caller):
    # Bigger than an assertion may be, within what the endpoint reads.
    assert status_of(http, forge(caller, padding="x" * 5000)) == 401


# --- The envelope of the assertion ---------------------------------------------------------------------------------

def test_an_assertion_encrypted_to_another_key_is_rejected(http, caller):
    another = gateway_keys.public_part(gateway_keys.new_key())

    assert status_of(http, forge(caller, encrypt_to=another)) == 401
    # Not even when it claims to be for the gateway's key.
    assert status_of(http, forge(caller, encrypt_to=another, jwe_header={"kid": GATEWAY_KEY["kid"]})) == 401


@pytest.mark.parametrize("jwe_header", [
    {"alg": "RSA-OAEP"},
    {"enc": "A256GCM"},
    {"cty": "text/plain"},
    {"cty": None},
    {"kid": "another-kid"},
    {"kid": None},
], ids=["other key algorithm", "other content algorithm", "not a JWT inside", "no cty", "unknown kid", "no kid"])
def test_an_envelope_that_is_not_exactly_as_agreed_is_rejected(http, caller, jwe_header):
    changed = {name: value for name, value in jwe_header.items() if value is not None}
    assertion = forge(caller, jwe_header=changed)
    if None in jwe_header.values():
        # jwcrypto always writes what it was given, so the member is taken out of the protected header by hand:
        # the result no longer authenticates, and it has to be rejected for that or for the missing member.
        assertion = forge(caller, jwe_header={name: "" for name in jwe_header})

    assert status_of(http, assertion) == 401


def test_a_symmetric_envelope_is_rejected(http, caller):
    shared = jwk.JWK.generate(kty="oct", size=512)
    envelope = jwe.JWE(b"x", protected={"alg": "dir", "enc": "A256CBC-HS512", "cty": "JWT", "kid": GATEWAY_KEY["kid"]})
    envelope.add_recipient(shared)

    assertion = envelope.serialize(compact=True)
    KEY_OF[assertion] = caller.key

    assert status_of(http, assertion) == 401


def test_an_envelope_with_something_else_inside_is_rejected(http, caller):
    assert status_of(http, forge(caller, inner="this is not a JWT")) == 401
    assert status_of(http, forge(caller, inner="a.b.c")) == 401


# --- The signature of the assertion --------------------------------------------------------------------------------

def test_an_assertion_signed_with_another_key_is_rejected(http, caller):
    assert status_of(http, forge(caller, sign_with=new_signing_key())) == 401


def test_an_assertion_signed_by_one_client_in_the_name_of_another_is_rejected(http, caller, register):
    other = register("globex")

    assert status_of(http, forge(caller, iss=str(other.client_id), sub=str(other.client_id))) == 401
    assert status_of(http, forge(caller, iss=str(other.client_id), sub=str(other.client_id),
                                 jws_header={"kid": other.kid})) == 401


def test_an_unsigned_assertion_is_rejected(http, caller):
    head = jws.base64url_encode(json.dumps({"alg": "none", "typ": "JWT", "kid": caller.kid}))
    now = int(time.time())
    body = jws.base64url_encode(json.dumps({"iss": str(caller.client_id), "sub": str(caller.client_id),
                                            "aud": GATEWAY_AUDIENCE, "iat": now, "exp": now + 60,
                                            "jti": "unsigned"}))

    assert status_of(http, forge(caller, inner=f"{head}.{body}.")) == 401


def test_an_assertion_signed_with_the_public_key_as_a_shared_secret_is_rejected(http, caller):
    """The classic confusion: the verifier is handed an HMAC made with the key it holds, which is public."""
    public = {name: caller.key[name] for name in ("kty", "crv", "x", "y")}
    secret = jwk.JWK(kty="oct", k=jws.base64url_encode(json.dumps(public)))

    assert status_of(http, forge(caller, sign_with=json.loads(secret.export()), signature="HS256")) == 401


def test_another_signature_algorithm_is_rejected(http, caller):
    other_curve = json.loads(jwk.JWK.generate(kty="EC", crv="P-384").export_private())

    assert status_of(http, forge(caller, sign_with=other_curve, signature="ES384")) == 401


@pytest.mark.parametrize("jws_header", [{"kid": "unknown"}, {"kid": ""}, {"kid": 7}],
                         ids=["unknown kid", "empty kid", "kid that is not text"])
def test_an_assertion_that_does_not_name_a_registered_key_is_rejected(http, caller, jws_header):
    assert status_of(http, forge(caller, jws_header=jws_header)) == 401


@pytest.mark.parametrize("issuer", [None, "", "acme", str(uuid4()), 7, ["a"]],
                         ids=["missing", "empty", "a name", "unknown client", "a number", "a list"])
def test_an_assertion_from_nobody_known_is_rejected(http, caller, issuer):
    assert status_of(http, forge(caller, iss=issuer)) == 401


@pytest.mark.parametrize("subject", [None, "", str(uuid4())], ids=["missing", "empty", "somebody else"])
def test_a_client_authenticates_as_itself(http, caller, subject):
    """RFC 7523, section 3: the subject of the assertion is the client that signs it."""
    assert status_of(http, forge(caller, sub=subject)) == 401


def test_a_tampered_assertion_is_rejected(http, caller):
    assertion = caller.assertion()
    head, key, iv, text, tag = assertion.split(".")
    flipped = text[:-2] + ("AA" if text[-2:] != "AA" else "BB")
    tampered = ".".join([head, key, iv, flipped, tag])
    KEY_OF[tampered] = caller.key

    assert status_of(http, tampered) == 401


# --- The claims of the assertion -----------------------------------------------------------------------------------

def at(offset: int):
    """A moment relative to when the assertion is forged, not to when this file was read: a slow run cannot change it."""
    return lambda now: now + offset


@pytest.mark.parametrize("claims", [
    {"aud": "https://other.test"},
    {"aud": ["https://other.test"]},
    {"aud": None},
    {"exp": None},
    {"iat": None},
    {"iat": "now", "exp": "later"},
    {"iat": True, "exp": True},
    {"iat": at(0), "exp": at(61)},
    {"iat": at(0), "exp": at(3600)},
    {"iat": at(0), "exp": at(0)},
    {"iat": at(0), "exp": at(-10)},
    {"iat": at(-120), "exp": at(-60)},
    {"iat": at(120), "exp": at(180)},
    {"jti": None},
    {"jti": ""},
    {"jti": 7},
    {"jti": "x" * 129},
], ids=["another audience", "another audience in a list", "no audience", "no exp", "no iat", "dates that are text",
        "dates that are booleans", "one second too long", "an hour", "no lifetime", "expires before it is issued",
        "expired", "issued in the future", "no jti", "empty jti", "jti that is not text", "jti too long"])
def test_an_assertion_with_wrong_claims_is_rejected(http, caller, claims):
    assert status_of(http, forge(caller, **claims)) == 401


# --- Each check is the one that rejects --------------------------------------------------------------------------

OTHER_GATEWAY = gateway_keys.public_part(gateway_keys.new_key())
OTHER_CURVE = json.loads(jwk.JWK.generate(kty="EC", crv="P-384").export_private())

REASONS = {
    "not encrypted to a key of this gateway": {"encrypt_to": OTHER_GATEWAY},
    "the JWE does not open": {"encrypt_to": OTHER_GATEWAY, "jwe_header": {"kid": GATEWAY_KEY["kid"]}},
    "JWE algorithm not allowed": {"jwe_header": {"enc": "A256GCM"}},
    "the JWE does not declare a JWT inside": {"jwe_header": {"cty": "text/plain"}},
    "malformed JWT": {"inner": "this is not a JWT"},
    "JWT algorithm not allowed": {"sign_with": OTHER_CURVE, "signature": "ES384"},
    "no issuer": {"iss": "acme"},
    "unknown client or key": {"jws_header": {"kid": "unknown"}},
    "bad signature": {"sign_with": new_signing_key()},
    "the subject is not the issuer": {"sub": str(uuid4())},
    "issued for another audience": {"aud": "https://other.test"},
    "no iat or exp": {"exp": None},
    "lifetime longer than allowed": {"iat": at(0), "exp": at(61)},
    "issued in the future": {"iat": at(120), "exp": at(180)},
    "expired": {"iat": at(-120), "exp": at(-60)},
    "no jti": {"jti": ""},
}


@pytest.mark.parametrize("extra", [False, True], ids=["as it is", "with an issuer outside the signature"])
def test_a_jwt_in_json_form_is_malformed(http, caller, register, extra):
    """
        Found in the dual review of Gate 2: the JSON form was taken, and the issuer was read from bytes the
        signature does not cover. With the key of acme, this says it is acme outside and somebody else inside.
    """
    now = int(time.time())
    claims = {"iss": str(uuid4()) if extra else str(caller.client_id), "sub": str(caller.client_id),
              "aud": GATEWAY_AUDIENCE, "iat": now, "exp": now + 60, "jti": str(uuid4())}
    signed = jws.JWS(json.dumps(claims).encode())
    signed.add_signature(jwk.JWK(**caller.key), alg="ES256", protected={"alg": "ES256", "kid": caller.kid})
    inner = json.loads(signed.serialize(compact=False))
    if extra:
        issuer = jws.base64url_encode(json.dumps({"iss": str(caller.client_id)}))
        inner["a"] = f"a.{issuer}.b"
    assertion = forge(caller, inner=json.dumps(inner))

    with pytest.raises(tokens.InvalidToken) as rejection:
        tokens.verify(assertion, GATEWAY, GATEWAY_AUDIENCE,
                      lambda client_id, kid: (find_signer(client_id, kid) or (None, None))[1], time.time())

    assert str(rejection.value) == "malformed JWT"
    assert status_of(http, assertion) == 401


@pytest.mark.parametrize("reason", sorted(REASONS))
def test_an_assertion_is_rejected_for_the_reason_it_should_be(caller, reason):
    """Seen from outside every rejection is the same 401, so this looks at which check turned the assertion away."""
    def signing_key(client_id, kid):
        found = find_signer(client_id, kid)
        return found[1] if found else None

    with pytest.raises(tokens.InvalidToken) as rejection:
        tokens.verify(forge(caller, **REASONS[reason]), GATEWAY, GATEWAY_AUDIENCE, signing_key, time.time())

    assert str(rejection.value) == reason


# --- An assertion is good once ---------------------------------------------------------------------------------

def test_an_assertion_is_good_once(http, caller):
    assertion = caller.assertion()

    assert [status_of(http, assertion), status_of(http, assertion), status_of(http, assertion)] == [200, 401, 401]


def test_two_clients_may_use_the_same_jti(http, caller, register):
    other = register("globex")

    assert status_of(http, forge(caller, jti="same")) == 200
    assert status_of(http, forge(other, sign_with=other.key, jws_header={"kid": other.kid}, jti="same")) == 200
    assert status_of(http, forge(caller, jti="same")) == 401


def test_a_rejected_assertion_does_not_burn_the_jti(http, caller):
    """Otherwise anyone could invalidate a client's next assertion by sending the jti first, unsigned."""
    assert status_of(http, forge(caller, sign_with=new_signing_key(), jti="mine")) == 401
    assert status_of(http, forge(caller, aud="https://other.test", jti="mine")) == 401
    assert SeenToken.objects.count() == 0
    assert status_of(http, forge(caller, jti="mine")) == 200


def test_a_used_assertion_is_remembered_until_it_could_not_be_presented_again(http, caller):
    now = int(time.time())
    status_of(http, forge(caller, iat=now, exp=now + 60, jti="kept"))

    seen = SeenToken.objects.get()
    assert (seen.key, seen.clientId) == (f"{caller.client_id}:kept", caller.client_id)
    # exp plus the clock tolerance. MongoDB deletes the entry by itself after that.
    assert abs(seen.expiresAt.replace(tzinfo=timezone.utc).timestamp() - (now + 90)) < 2
    assert {"fields": ["expiresAt"], "expireAfterSeconds": 0} in SeenToken._meta["indexes"]


def test_the_single_use_does_not_depend_on_an_index(http, caller):
    """
        A collection that is dropped while the service runs loses its indexes, and they are not created again
        until it restarts. The single use must hold all the same.
    """
    mongoengine.get_db().drop_collection(SeenToken._get_collection_name())
    assertion = caller.assertion()

    assert [status_of(http, assertion), status_of(http, assertion)] == [200, 401]


# --- The registry decides who gets a token -------------------------------------------------------------------------

def test_a_revoked_key_gets_no_new_token_at_once(http, caller, operator):
    assert status_of(http, caller.assertion()) == 200

    revoked = http.delete(f"/clients/{caller.client_id}/keys/{caller.kid}", headers=operator.auth)

    assert revoked.status_code == 200
    assert status_of(http, caller.assertion()) == 401


def test_a_token_issued_before_the_key_was_revoked_lasts_until_it_expires(http, caller, operator):
    """ADR-0025: what revoking a key costs. The token is the gateway's, and the gateway signed it for ten minutes."""
    token = caller.access_token()
    http.delete(f"/clients/{caller.client_id}/keys/{caller.kid}", headers=operator.auth)

    assert calling(http, token).status_code == 200


def test_a_client_that_is_not_active_gets_no_token(http, caller):
    Client.objects(uuid=caller.client_id).update_one(set__status=Status.LOK.value)

    assert status_of(http, caller.assertion()) == 401


def test_the_answer_does_not_say_why(http, caller, register):
    """Wrong signature, unknown client, replay: nothing in the answer lets a caller tell them apart."""
    other = register("globex")
    used = caller.assertion()
    status_of(http, used)
    KEY_OF["not-an-assertion"] = caller.key
    answers = [asking(http, assertion) for assertion in (
        used,
        forge(caller, sign_with=new_signing_key()),
        forge(caller, iss=str(uuid4())),
        forge(caller, iss=str(other.client_id)),
        forge(caller, aud="https://other.test"),
        "not-an-assertion",
    )]

    assert {(answer.status_code, answer.text) for answer in answers} == {(401, '{"error":"invalid_client"}')}


# --- The access token ----------------------------------------------------------------------------------------------

def forge_access(caller, sign_with=None, kid: str | None = None, typ: str = "at+jwt", alg: str = "ES256",
                 **claims) -> str:
    """An access token like the gateway's, with whatever a test wants changed."""
    now = int(time.time())
    body = {"iss": GATEWAY_AUDIENCE, "aud": GATEWAY_AUDIENCE, "sub": str(caller.client_id),
            "client_id": str(caller.client_id), "iat": now, "exp": now + 600, "jti": str(uuid4()),
            "hive_role": "client", "hive_worker": None, "hive_key": caller.kid,
            "cnf": {"jkt": dpop.thumbprint(caller.key)}}
    body.update({name: value(now) if callable(value) else value for name, value in claims.items()})
    body = {name: value for name, value in body.items() if value != "drop"}
    signer_kid, key = GATEWAY.signer()
    signed = jws.JWS(json.dumps(body).encode())
    signed.allowed_algs = [alg]
    signed.add_signature(sign_with or key, alg=alg, protected={"alg": alg, "typ": typ, "kid": kid or signer_kid})
    forged = signed.serialize(compact=True)
    # Its requests carry the honest proof of the caller's key: what is tested is the token.
    KEY_OF[forged] = caller.key
    return forged


def test_an_access_token_is_good_for_many_requests(http, caller):
    token = caller.access_token()

    assert [calling(http, token).status_code for _ in range(3)] == [200, 200, 200]


def test_the_forged_access_token_of_these_tests_is_valid_when_nothing_is_changed(http, caller):
    """Without this, every rejection below could be the helper's fault."""
    assert calling(http, forge_access(caller)).status_code == 200


def test_checking_an_access_token_does_not_touch_the_database(http, caller, monkeypatch):
    token = caller.access_token()

    def no(*_args, **_kwargs):
        raise AssertionError("the database was read")

    monkeypatch.setattr("auth.principal.find_signer", no)
    monkeypatch.setattr("auth.principal.remember_token", no)

    assert http.get("/gateway/keys", headers={"Authorization": f"DPoP {token}"}).status_code == 200


@pytest.mark.parametrize("headers", [
    {},
    {"Authorization": ""},
    {"Authorization": "Bearer"},
    {"Authorization": "Bearer not-a-token"},
    {"Authorization": "Bearer a.b.c.d.e"},
    {"Authorization": "Basic dXNlcjpwYXNz"},
    {"Authorization": "DPoP"},
    {"Authorization": "DPoP not-a-token"},
    {"X-API-Key": "test-admin-api-key-0123456789abcdef"},
], ids=["nothing", "empty", "no value", "garbage", "five parts of nothing", "another scheme", "DPoP without a token",
        "DPoP with garbage", "the old admin key"])
def test_a_request_without_an_access_token_is_rejected(http, headers):
    response = http.get("/keys", headers=headers)

    assert response.status_code == 401
    assert response.json() == {"detail": "HTTP_401_UNAUTHORIZED"}
    # RFC 9449, section 7.1: what the service takes, and nothing about why.
    assert response.headers["www-authenticate"] in ('DPoP algs="ES256"', 'DPoP algs="ES256", error="invalid_token"')


def test_an_assertion_is_not_an_access_token(http, caller):
    """The token of before (ADR-0009) does not get in any more: the change is all at once."""
    assert calling(http, caller.assertion()).status_code == 401


def test_an_access_token_signed_by_somebody_else_is_rejected(http, caller):
    other = jwk.JWK.generate(kty="EC", crv="P-256")

    assert calling(http, forge_access(caller, sign_with=other)).status_code == 401
    # Not even with the kid of the gateway's signing key, nor with the client's own key.
    assert calling(http, forge_access(caller, sign_with=jwk.JWK(**caller.key))).status_code == 401
    assert calling(http, forge_access(caller, kid="unknown")).status_code == 401


def test_an_access_token_signed_with_the_public_key_as_a_shared_secret_is_rejected(http, caller):
    public = json.loads(GATEWAY.signer()[1].export_public())
    secret = jwk.JWK(kty="oct", k=jws.base64url_encode(json.dumps(public)))

    assert calling(http, forge_access(caller, sign_with=secret, alg="HS256")).status_code == 401


@pytest.mark.parametrize("typ", ["JWT", "", "key-proof+jwt", "application/at+jwt"])
def test_only_an_access_token_says_it_is_one(http, caller, typ):
    assert calling(http, forge_access(caller, typ=typ)).status_code == 401


@pytest.mark.parametrize("claims", [
    {"iss": "https://other.test"},
    {"aud": "https://other.test"},
    {"aud": [GATEWAY_AUDIENCE]},
    {"exp": "drop"},
    {"iat": "drop"},
    {"iat": at(0), "exp": at(601)},
    {"iat": at(0), "exp": at(0)},
    {"iat": at(-700), "exp": at(-100)},
    {"iat": at(-600), "exp": at(-1)},
    {"iat": at(120), "exp": at(700)},
    {"sub": "acme"},
    {"sub": "drop"},
    {"client_id": str(uuid4())},
    {"hive_role": "superuser"},
    {"hive_role": "drop"},
    {"hive_worker": "intruder"},
    {"hive_key": ""},
    {"hive_key": "drop"},
    {"jti": "drop"},
], ids=["another issuer", "another audience", "audience in a list", "no exp", "no iat", "one second too long",
        "no lifetime", "expired long ago", "expired a second ago", "issued in the future", "subject not a uuid",
        "no subject", "another client_id", "unknown role", "no role", "unknown worker", "empty key", "no key", "no jti"])
def test_an_access_token_with_wrong_claims_is_rejected(http, caller, claims):
    assert calling(http, forge_access(caller, **claims)).status_code == 401


def test_an_access_token_is_rejected_for_the_reason_it_should_be(caller):
    now = time.time()
    cases = {
        "not an access token": forge_access(caller, typ="JWT"),
        "not signed by a key of this gateway": forge_access(caller, kid="unknown"),
        "bad signature": forge_access(caller, sign_with=jwk.JWK.generate(kty="EC", crv="P-256")),
        "issued by or for another gateway": forge_access(caller, iss="https://other.test"),
        "lifetime other than allowed": forge_access(caller, iat=at(0), exp=at(601)),
        "issued in the future": forge_access(caller, iat=at(120), exp=at(700)),
        "expired": forge_access(caller, iat=at(-600), exp=at(-1)),
        "no client, role or worker": forge_access(caller, hive_role="superuser"),
        "no key or jti": forge_access(caller, hive_key=""),
    }
    for reason, token in cases.items():
        with pytest.raises(tokens.InvalidToken) as rejection:
            access_tokens.read(token, GATEWAY, GATEWAY_AUDIENCE, now)
        assert str(rejection.value) == reason


def test_the_lifetime_of_an_access_token_is_checked_to_the_second(caller):
    """Each edge on its right side (HIG-137: mutation testing found every one of them unchecked)."""
    issued = int(time.time())

    def read(iat, exp, now):
        return access_tokens.read(forge_access(caller, iat=iat, exp=exp), GATEWAY, GATEWAY_AUDIENCE, now)

    assert read(issued, issued + 1, issued).client_id == caller.client_id
    assert read(issued, issued + 600, issued + 599).client_id == caller.client_id
    # Issued by a clock ahead of this one, by as much as is tolerated.
    assert read(issued + 30, issued + 630, issued).client_id == caller.client_id
    for iat, exp, now, reason in [(issued, issued, issued - 1, "lifetime other than allowed"),
                                  (issued, issued + 601, issued, "lifetime other than allowed"),
                                  (issued + 31, issued + 631, issued, "issued in the future"),
                                  (issued, issued + 600, issued + 600, "expired")]:
        with pytest.raises(tokens.InvalidToken) as rejection:
            read(iat, exp, now)
        assert str(rejection.value) == reason


def test_a_token_of_a_person_claims_no_key_no_worker_and_not_the_engine(caller):
    """
        A token of a person is of the BFF's key, never of a key of the client nor of the response engine. One that
        says otherwise was not issued by this gateway, and is not taken (HIG-137, surviving mutants).
    """
    now = time.time()
    of_a_person = {"hive_user": str(uuid4()), "hive_user_role": "cliente-admin", "hive_key": None}
    assert access_tokens.read(forge_access(caller, **of_a_person), GATEWAY, GATEWAY_AUDIENCE, now).user_role \
        is PersonRole.ADMIN

    for claims in ({"hive_key": caller.kid}, {"hive_worker": "notifier"}, {"hive_role": "service"},
                   {"hive_user": "not-a-uuid"}, {"hive_user_role": "owner"}, {"hive_user_role": "drop"}):
        with pytest.raises(tokens.InvalidToken) as rejection:
            access_tokens.read(forge_access(caller, **{**of_a_person, **claims}), GATEWAY, GATEWAY_AUDIENCE, now)
        assert str(rejection.value) == "no person or role", claims


def test_an_access_token_of_another_client_than_its_subject_is_rejected_for_that(caller):
    with pytest.raises(tokens.InvalidToken) as rejection:
        access_tokens.read(forge_access(caller, client_id=str(uuid4())), GATEWAY, GATEWAY_AUDIENCE, time.time())

    assert str(rejection.value) == "no client, role or worker"


def test_an_access_token_is_rejected_for_the_reason_it_should_be_before_and_after_its_claims(caller):
    """
        The reasons the log gets that the test of the claims does not reach (HIG-137, surviving mutants). Some are a
        second line of defense: a token signed with HS256 would not verify either, but it is turned away before.
    """
    public = json.loads(GATEWAY.signer()[1].export_public())
    secret = jwk.JWK(kty="oct", k=jws.base64url_encode(json.dumps(public)))
    signer_kid, key = GATEWAY.signer()
    not_an_object = jws.JWS(b'["a", "list"]')
    not_an_object.add_signature(key, alg="ES256", protected={"alg": "ES256", "typ": "at+jwt", "kid": signer_kid})
    cases = {
        "not an access token": [None, 7, forge_access(caller, padding="x" * tokens.MAX_TOKEN_SIZE),
                                forge_access(caller, sign_with=secret, alg="HS256")],
        "malformed access token": ["a.b.c"],
        "bad signature": [not_an_object.serialize(compact=True)],
        "no key or jti": [forge_access(caller, jti=7), forge_access(caller, jti="")],
        "not bound to a key": [forge_access(caller, cnf="a-thumbprint"), forge_access(caller, cnf={"jkt": 7}),
                               forge_access(caller, cnf="drop")],
    }
    for reason, forged in cases.items():
        for token in forged:
            with pytest.raises(tokens.InvalidToken) as rejection:
                access_tokens.read(token, GATEWAY, GATEWAY_AUDIENCE, time.time())
            assert str(rejection.value) == reason, (reason, token)


def test_a_token_is_issued_for_a_key_or_for_a_person_with_their_role_and_at_the_time_it_is_told(caller):
    """What issue() refuses to sign, and the clock it signs with (HIG-137, surviving mutants)."""
    for kid, user_id, user_role in [(None, None, None), (caller.kid, uuid4(), PersonRole.ADMIN),
                                    (caller.kid, None, PersonRole.ADMIN), (None, uuid4(), None)]:
        with pytest.raises(ValueError) as refused:
            access_tokens.issue(GATEWAY, GATEWAY_AUDIENCE, caller.client_id, Role.CLIENT, None, kid, "a-thumbprint",
                                user_id=user_id, user_role=user_role)
        assert str(refused.value) == "a token is either of a key or of a person with their role"

    token, lifetime = access_tokens.issue(GATEWAY, GATEWAY_AUDIENCE, caller.client_id, Role.CLIENT, None,
                                          caller.kid, "a-thumbprint", now=1_000_000.7)

    assert (payload(token)["iat"], payload(token)["exp"], lifetime) == (1_000_000, 1_000_600, 600)


def test_the_log_says_why_a_token_or_a_request_was_turned_away(http, caller, caplog):
    """The reasons are fixed texts, for the log; the answers say none of them (HIG-137, surviving mutants)."""
    token_url, keys_url = f"{TEST_SERVER}/oauth/token", f"{TEST_SERVER}/keys"
    http.post("/oauth/token", data=token_request(caller.assertion()),
              headers={"DPoP": dpop_proof(new_signing_key(), "POST", token_url)})
    used = caller.assertion()
    status_of(http, used)
    status_of(http, used)
    token = caller.access_token()
    http.get("/keys", headers={"Authorization": f"DPoP {token}",
                               "DPoP": dpop_proof(new_signing_key(), "GET", keys_url, token)})

    rejected = [record.getMessage() for record in caplog.records if record.getMessage().startswith("Rejected a")]
    assert rejected == ["Rejected a request for an access token: signed with a key other than the assertion's",
                        "Rejected a request for an access token: assertion already used",
                        "Rejected a request: signed with a key other than the token's"]
    with pytest.raises(tokens.InvalidToken, match="^no assertion$"):
        principal.exchange(None, GATEWAY, GATEWAY_AUDIENCE, "a-thumbprint")
    with pytest.raises(tokens.InvalidToken, match="^no token$"):
        principal.authenticate(None, [], "GET", {keys_url}, GATEWAY, GATEWAY_AUDIENCE)


def test_the_old_signing_key_checks_the_tokens_it_signed_while_it_is_in_the_file(caller):
    """Rotating the signing key: the newest signs, every one in the file checks."""
    old, new = gateway_keys.new_signing_key(), gateway_keys.new_signing_key()
    before = gateway_keys.GatewayKeys([gateway_keys.new_key(), old])
    token, _ = access_tokens.issue(before, GATEWAY_AUDIENCE, caller.client_id, Role.CLIENT, None, caller.kid,
                                   "a-thumbprint")
    rotating = gateway_keys.GatewayKeys([gateway_keys.new_key(), old, new])
    after = gateway_keys.GatewayKeys([gateway_keys.new_key(), new])

    assert header(access_tokens.issue(rotating, GATEWAY_AUDIENCE, caller.client_id, Role.CLIENT, None,
                                      caller.kid, "a-thumbprint")[0])["kid"] == new["kid"]
    assert access_tokens.read(token, rotating, GATEWAY_AUDIENCE, time.time()).client_id == caller.client_id
    with pytest.raises(tokens.InvalidToken):
        access_tokens.read(token, after, GATEWAY_AUDIENCE, time.time())


# --- Every route ------------------------------------------------------------------------------------------------------

PUBLIC = {("GET", "/chat/{bot_id}"), ("POST", "/chat/{bot_id}"), ("GET", "/health"), ("POST", "/oauth/token")}
ROUTES = sorted({(method, route.path) for route in app_module.app.routes if hasattr(route, "methods")
                 and not route.path.startswith(("/docs", "/redoc", "/openapi")) for method in route.methods
                 if method != "HEAD"} - PUBLIC)
PLACEHOLDERS = {"bot_id": str(uuid4()), "intent_id": str(uuid4()), "client_id": str(uuid4()), "kid": "a-kid",
                "template_id": "1234567890", "person_id": str(uuid4()), "request_id": str(uuid4()),
                "contact_id": str(uuid4()), "version": "1", "rule_id": str(uuid4())}


def test_the_webhook_health_and_token_routes_are_the_only_ones_without_a_token():
    assert len(ROUTES) == 52


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer not-a-token"}], ids=["no token", "garbage"])
@pytest.mark.parametrize("method,path", ROUTES)
def test_every_other_route_asks_for_a_token(http, method, path, headers):
    response = http.request(method, path.format(**PLACEHOLDERS), headers=headers, json={})

    assert response.status_code == 401


@pytest.mark.parametrize("method,path", ROUTES)
def test_no_route_takes_an_assertion_for_an_access_token(http, caller, method, path):
    response = http.request(method, path.format(**PLACEHOLDERS),
                            headers={"Authorization": f"DPoP {caller.assertion()}"}, json={})

    assert response.status_code == 401


def test_the_grant_and_the_kind_of_assertion_are_the_standard_ones():
    assert (GRANT_TYPE, ASSERTION_TYPE) == ("client_credentials",
                                            "urn:ietf:params:oauth:client-assertion-type:jwt-bearer")
