# -*- coding: utf-8 -*-
"""
    The people of a client (ADR-0029, HIG-92), against an IdP that stands for Zitadel, with a JWKS of its own. The BFF
    exchanges the ID token of a person at POST /oauth/token (RFC 8693) for an access token of Hive, bound to the BFF's
    own key; the client and the role come from Hive, and each route lets in what the role × action matrix says.

    The abuse scenarios of the PRD: AB23, an analyst who tries to configure; AB24, a person who tries another client;
    AB26, a person blocked or removed; AB27, an account of the IdP with no person in Hive, or without a second factor.
"""
import time
from uuid import uuid4

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from jwcrypto import jwk

import app as app_module
from auth import access_tokens, token_limits
from auth.idp import IdentityProvider
from classes import ChatBot, Client, ClientUser, Contact
from conftest import GATEWAY_AUDIENCE, KEY_OF
from enums import PersonRole, PersonStatus, Role, Status
from repository import TenantRepository
from shared.auth.client import new_signing_key
from shared.auth.gateway_keys import GatewayKeys

from fake_idp import ACCESS_TOKEN, AUDIENCE, BFF, ID_TOKEN, ISSUER, SUBJECT, Zitadel, auth, exchange

@pytest.fixture
def zitadel(monkeypatch):
    idp = Zitadel()
    monkeypatch.setattr(app_module, "IDP", IdentityProvider(ISSUER, AUDIENCE, read_keys=idp.jwks))
    return idp


@pytest.fixture
def http():
    return TestClient(app_module.app)


@pytest.fixture
def acme(register):
    return register("acme")


def person(caller, role=PersonRole.ADMIN, status=PersonStatus.ACTIVE, subject=SUBJECT, email="ana@acme.test"):
    return TenantRepository(caller.client_id).add_person(email, ISSUER, subject, role, invited_by="test",
                                                          status=status)


def token_of(http, zitadel, **form) -> str:
    response = exchange(http, zitadel.id_token(), **form)
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    KEY_OF[token] = BFF
    return token


@pytest.fixture
def bot(acme):
    return TenantRepository(acme.client_id).add_bot(ChatBot(name="Bot", role="r", description="d", tone="t"))


# --- The exchange -----------------------------------------------------------------------------------------------------

def test_a_person_with_a_second_factor_gets_a_token_of_their_client_and_role(http, zitadel, acme, bot):
    ana = person(acme)

    response = exchange(http, zitadel.id_token())

    assert response.status_code == 200
    body = response.json()
    assert (body["token_type"], body["issued_token_type"], body["expires_in"]) == ("DPoP", ACCESS_TOKEN, 600)
    assert response.headers["Cache-Control"] == "no-store"
    access = access_tokens.read(body["access_token"], GatewayKeys.load(app_module.settings.gateway_keys_file),
                                GATEWAY_AUDIENCE, time.time())
    assert (access.client_id, access.user_id, access.user_role, access.kid) == (acme.client_id, ana.uuid,
                                                                                 PersonRole.ADMIN, None)
    KEY_OF[body["access_token"]] = BFF
    assert http.get(f"/chat/{bot.uuid}/info", headers=auth(body["access_token"])).status_code == 200


def test_an_invited_person_is_active_once_they_got_their_first_token(http, zitadel, acme):
    ana = person(acme, status=PersonStatus.PENDING)

    assert exchange(http, zitadel.id_token()).status_code == 200

    assert ClientUser.objects(uuid=ana.uuid).first().status == PersonStatus.ACTIVE.value


def test_the_token_of_a_person_is_bound_to_the_key_of_the_bff(http, zitadel, acme, bot):
    person(acme)
    token = token_of(http, zitadel)
    KEY_OF[token] = new_signing_key()

    assert http.get(f"/chat/{bot.uuid}/info", headers=auth(token)).status_code == 401


def test_an_exchange_without_a_proof_is_turned_away(http, zitadel, acme):
    person(acme)

    response = exchange(http, zitadel.id_token(), proof=False)

    assert (response.status_code, response.json()) == (400, {"error": "invalid_dpop_proof"})


def test_without_an_idp_configured_the_grant_is_not_taken(http, zitadel, acme, monkeypatch):
    person(acme)
    monkeypatch.setattr(app_module, "IDP", None)

    response = exchange(http, zitadel.id_token())

    assert (response.status_code, response.json()) == (400, {"error": "unsupported_grant_type"})


def test_when_the_keys_of_the_idp_cannot_be_read_it_is_for_later(http, zitadel, acme):
    person(acme)
    zitadel.down = True

    response = exchange(http, zitadel.id_token())

    assert (response.status_code, response.json()) == (503, {"error": "temporarily_unavailable"})


@pytest.mark.parametrize("form", [
    {"subject_token_type": ACCESS_TOKEN},
    {"requested_token_type": ID_TOKEN},
    {"hive_client": "not-a-uuid"},
    {"client_assertion": "x"},
    {"actor_token": "x"},
    {"subject_token": None},
], ids=["other token type", "other requested type", "client not a uuid", "an assertion too", "actor", "no token"])
def test_an_exchange_not_of_the_shape_of_rfc_8693_is_an_invalid_request(http, zitadel, acme, form):
    person(acme)
    token = zitadel.id_token()
    if "subject_token" in form:
        token = form.pop("subject_token")

    response = exchange(http, token, **form)

    assert (response.status_code, response.json()) == (400, {"error": "invalid_request"})


# --- AB27: an ID token that is not good, or a person Hive does not have ---------------------------------------------

def test_an_account_of_the_idp_with_no_person_in_hive_gets_nothing(http, zitadel, acme):
    person(acme, subject="another-subject")

    assert exchange(http, zitadel.id_token()).json() == {"error": "invalid_grant"}


@pytest.mark.parametrize("amr", [["pwd"], [], None, "mfa", ["otp"]], ids=["password", "empty", "none", "a string",
                                                                          "otp alone"])
def test_without_the_second_factor_in_the_amr_there_is_no_token(http, zitadel, acme, amr):
    person(acme)
    token = zitadel.id_token(amr=amr) if amr is not None else zitadel.id_token(amr=None)

    response = exchange(http, token)

    assert (response.status_code, response.json()) == (400, {"error": "invalid_grant"})


@pytest.mark.parametrize("changes", [
    {"iss": "https://other-idp.test"},
    {"aud": ["another-client"], "azp": "another-client"},
    {"aud": [AUDIENCE, "another-client"], "azp": "another-client"},
    {"aud": [AUDIENCE, "another-client"], "azp": None},
    {"exp": int(time.time()) - 120},
    {"iat": int(time.time()) + 600, "exp": int(time.time()) + 3600},
    {"sub": ""},
    {"sub": "x" * 300},
], ids=["other issuer", "other audience", "azp of another", "no azp", "expired", "from the future", "empty subject",
        "long subject"])
def test_an_id_token_issued_by_or_for_someone_else_gets_nothing(http, zitadel, acme, changes):
    person(acme)

    assert exchange(http, zitadel.id_token(**changes)).json() == {"error": "invalid_grant"}


def test_an_id_token_signed_by_another_key_with_the_same_kid_gets_nothing(http, zitadel, acme):
    person(acme)
    forger = jwk.JWK.generate(kty="RSA", size=2048, kid="idp-1")

    assert exchange(http, zitadel.id_token(key=forger)).json() == {"error": "invalid_grant"}


def test_an_id_token_without_signature_or_signed_with_a_secret_gets_nothing(http, zitadel, acme):
    person(acme)
    header, payload, _ = zitadel.id_token().split(".")
    secret = jwk.JWK.generate(kty="oct", size=256, kid="idp-1")

    assert exchange(http, f"{header}.{payload}.").json() == {"error": "invalid_grant"}
    assert exchange(http, zitadel.id_token(key=secret, alg="HS256")).json() == {"error": "invalid_grant"}


def test_a_made_up_kid_reads_the_keys_of_the_idp_once_at_most(http, zitadel, acme):
    person(acme)
    assert exchange(http, zitadel.id_token()).status_code == 200
    reads = zitadel.reads

    for _ in range(3):
        assert exchange(http, zitadel.id_token(kid=f"made-up-{uuid4()}")).json() == {"error": "invalid_grant"}

    assert zitadel.reads == reads


# --- AB26: blocked, removed, or of a client that is gone ------------------------------------------------------------

@pytest.mark.parametrize("status", [PersonStatus.BLOCKED, PersonStatus.REMOVED])
def test_a_person_blocked_or_removed_gets_no_new_token(http, zitadel, acme, status):
    ana = person(acme)
    assert exchange(http, zitadel.id_token()).status_code == 200

    TenantRepository(acme.client_id).set_person_status(ana, status)

    assert exchange(http, zitadel.id_token()).json() == {"error": "invalid_grant"}


def test_a_person_of_a_client_that_is_not_active_gets_no_token(http, zitadel, acme):
    person(acme)
    Client.objects(uuid=acme.client_id).update_one(set__status=Status.DIS.value)

    assert exchange(http, zitadel.id_token()).json() == {"error": "invalid_grant"}


def test_a_client_deleted_takes_its_people_with_it(http, zitadel, acme, operator):
    person(acme)

    deleted = http.delete(f"/clients/{acme.client_id}", headers=operator.auth)

    assert deleted.status_code == 200
    assert ClientUser.objects(clientId=acme.client_id).count() == 0
    assert exchange(http, zitadel.id_token()).json() == {"error": "invalid_grant"}


# --- A person of more than one client, and AB24 -----------------------------------------------------------------------

def test_a_person_of_two_clients_says_which_one(http, zitadel, acme, register):
    globex = register("globex")
    person(acme)
    in_globex = person(globex, role=PersonRole.ANALYST)

    assert exchange(http, zitadel.id_token()).json() == {"error": "invalid_target"}

    response = exchange(http, zitadel.id_token(), hive_client=str(globex.client_id))
    access = access_tokens.read(response.json()["access_token"],
                                GatewayKeys.load(app_module.settings.gateway_keys_file), GATEWAY_AUDIENCE, time.time())
    assert (access.client_id, access.user_id, access.user_role) == (globex.client_id, in_globex.uuid,
                                                                     PersonRole.ANALYST)


def test_the_log_says_why_an_exchange_was_turned_away(http, zitadel, acme, register, caplog):
    """The reasons are fixed texts, for the log; the answer says none of them (HIG-137, surviving mutants)."""
    assert exchange(http, zitadel.id_token()).json() == {"error": "invalid_grant"}
    person(acme)
    person(register("globex"))
    assert exchange(http, zitadel.id_token()).json() == {"error": "invalid_target"}

    rejected = [record.getMessage() for record in caplog.records
                if record.getMessage().startswith("Rejected an exchange")]
    assert rejected == ["Rejected an exchange for an access token: no person in Hive who may get a token",
                        "Rejected an exchange for an access token: a person of more than one client"]


def test_a_person_cannot_ask_for_a_client_they_are_not_of(http, zitadel, acme, register):
    globex = register("globex")
    person(acme)

    assert exchange(http, zitadel.id_token(), hive_client=str(globex.client_id)).json() == {"error": "invalid_grant"}


def test_the_token_of_a_person_reaches_nothing_of_another_client(http, zitadel, acme, register):
    globex = register("globex")
    theirs = TenantRepository(globex.client_id).add_bot(ChatBot(name="Theirs", role="r", description="d", tone="t"))
    person(acme)
    token = token_of(http, zitadel)

    assert http.get(f"/chat/{theirs.uuid}/info", headers=auth(token)).status_code == 404
    assert http.get(f"/chat/{theirs.uuid}/contacts", headers=auth(token)).status_code == 404


# --- AB23: what each role reaches -------------------------------------------------------------------------------------

PUBLIC = {("GET", "/health"), ("POST", "/oauth/token")}
ROUTES = sorted((method, route.path) for route in app_module.app.routes if isinstance(route, APIRoute)
                for method in route.methods if (method, route.path) not in PUBLIC)
NOT_FOR_THE_ANALYST = [route for route in ROUTES if route not in app_module.ANALYST_ROUTES]
PLACES = {"bot_id": str(uuid4()), "intent_id": str(uuid4()), "contact_id": str(uuid4()), "client_id": str(uuid4()),
          "kid": "a-kid", "template_id": "1234567890", "person_id": str(uuid4()), "request_id": str(uuid4()),
          "version": "1", "rule_id": str(uuid4())}


@pytest.mark.parametrize("method,path", NOT_FOR_THE_ANALYST)
def test_an_analyst_gets_403_on_everything_the_matrix_does_not_give_them(http, zitadel, acme, bot, method, path):
    person(acme, role=PersonRole.ANALYST)
    token = token_of(http, zitadel)
    places = {**PLACES, "bot_id": str(bot.uuid)}

    response = http.request(method, path.format(**places), headers=auth(token), json={})

    assert response.status_code == 403


def test_an_analyst_sees_the_card_of_a_bot_without_its_verify_token(http, zitadel, acme, bot):
    person(acme, role=PersonRole.ANALYST)
    token = token_of(http, zitadel)

    card = http.get(f"/chat/{bot.uuid}/info", headers=auth(token))

    assert card.status_code == 200
    assert "verifyToken" not in card.json()["data"]
    assert "credentials" in card.json()["data"]


def test_an_admin_sees_the_card_with_its_verify_token(http, zitadel, acme, bot):
    person(acme, role=PersonRole.ADMIN)
    token = token_of(http, zitadel)

    assert "verifyToken" in http.get(f"/chat/{bot.uuid}/info", headers=auth(token)).json()["data"]


@pytest.mark.parametrize("path", ["/chat/{bot}/phones", "/chat/{bot}/rules", "/chat/{bot}/intents",
                                  "/chat/{bot}/tools", "/chat/{bot}/contacts", "/chat/{bot}/templates"])
def test_an_analyst_reads_what_their_client_has(http, zitadel, acme, bot, path):
    person(acme, role=PersonRole.ANALYST)
    token = token_of(http, zitadel)

    assert http.get(path.format(bot=bot.uuid), headers=auth(token)).status_code == 200


def test_an_analyst_does_not_send_text_and_a_template_gets_past_the_role(http, zitadel, acme, bot):
    contact = TenantRepository(acme.client_id).add_contact(bot.uuid, Contact(firstName="Ana", waId="12025550143"))
    person(acme, role=PersonRole.ANALYST)
    token = token_of(http, zitadel)
    address = f"/chat/{bot.uuid}/contacts/{contact._uuid}/messages"
    headers = {**auth(token), "Idempotency-Key": f"analyst-{uuid4()}"}

    text = http.post(address, headers=headers, json={"type": "text", "text": "Hola"})
    template = http.post(address, headers={**auth(token), "Idempotency-Key": f"analyst-{uuid4()}"},
                         json={"type": "template", "template": {"name": "order_update", "language": "es"}})

    assert text.status_code == 403
    # Past the role: it is turned away for the template, which the chatbot does not have, not for who sends it.
    assert template.status_code == 422


def test_an_admin_configures(http, zitadel, acme, bot):
    person(acme, role=PersonRole.ADMIN)
    token = token_of(http, zitadel)

    assert http.post(f"/chat/{bot.uuid}/rules", headers=auth(token), json={"rules": ["Be kind"]}).status_code == 200


def test_an_admin_of_the_operator_reaches_the_registry_and_an_analyst_of_it_does_not(http, zitadel, operator):
    operators = TenantRepository(operator.client_id)
    operators.add_person("op@hive.test", ISSUER, SUBJECT, PersonRole.ADMIN, invited_by="test",
                         status=PersonStatus.ACTIVE)
    operators.add_person("an@hive.test", ISSUER, "analyst-of-the-operator", PersonRole.ANALYST, invited_by="test",
                         status=PersonStatus.ACTIVE)
    admin = token_of(http, zitadel)
    response = exchange(http, zitadel.id_token(sub="analyst-of-the-operator"))
    analyst = response.json()["access_token"]
    KEY_OF[analyst] = BFF

    assert http.get("/clients", headers=auth(admin)).status_code == 200
    assert http.get("/clients", headers=auth(analyst)).status_code == 403


def test_an_admin_of_a_client_does_not_reach_the_registry(http, zitadel, acme):
    person(acme)

    assert http.get("/clients", headers=auth(token_of(http, zitadel))).status_code == 403


# --- What stays as it was ---------------------------------------------------------------------------------------------

def test_the_assertion_of_a_machine_credential_still_gets_its_token(http, zitadel, acme, bot):
    response = http.get(f"/chat/{bot.uuid}/info", headers=acme.auth)

    assert response.status_code == 200
    assert "verifyToken" in response.json()["data"]


def test_the_tokens_of_a_person_are_counted_like_those_of_a_key(http, zitadel, acme, monkeypatch):
    monkeypatch.setattr(token_limits, "KEY_TOKENS", 2)
    monkeypatch.setattr(token_limits, "LIMITS", token_limits.TokenLimits())
    person(acme)

    answers = [exchange(http, zitadel.id_token()).status_code for _ in range(3)]

    assert answers == [200, 200, 429]


def test_the_email_of_a_person_is_stored_encrypted_and_found_by_its_index(acme):
    ana = person(acme, email="  Ana@Acme.Test ")
    repo = TenantRepository(acme.client_id)

    assert "ana@acme.test" not in ClientUser.objects(uuid=ana.uuid).first().to_json().lower()
    assert repo.person_email(ana) == "ana@acme.test"
    assert repo.find_person_by_email("ANA@acme.test").uuid == ana.uuid


def test_the_same_person_of_the_idp_is_once_in_a_client(acme):
    from repository import Conflict
    person(acme)

    with pytest.raises(Conflict):
        person(acme, email="other@acme.test")


def test_a_machine_credential_is_neither_a_person_nor_an_analyst():
    from auth.principal import Principal
    machine = Principal(client_id=uuid4(), role=Role.CLIENT, kid="k", jti="j")

    assert (machine.is_person, machine.is_analyst, machine.actor) == (False, False, "key k")
