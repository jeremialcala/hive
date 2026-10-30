# -*- coding: utf-8 -*-
"""
    The people of a client, managed by its cliente-admin (RF16, HIG-94), against a local HTTP server that stands for
    Zitadel's User Service v2: it checks the service account's token, the organization and the body, and keeps the
    users it creates and the invitations it sends. An invitation never gives access to another client; the last active
    cliente-admin stays one; and a removed person's email is erased.
"""
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app as app_module
from auth.idp import IdentityProvider
from classes import ClientUser
from controller import people
from controller.zitadel import ZitadelUsers
from enums import PersonRole, PersonStatus
from repository import TenantRepository
from conftest import KEY_OF
from fake_idp import AUDIENCE, BFF, ISSUER, Zitadel as IdP, auth, exchange

ORGANIZATION, SERVICE_TOKEN = "312000000000000001", "service-account-token-of-the-test"
EMAIL = "ana@acme.test"


class FakeZitadel(BaseHTTPRequestHandler):
    users: dict = {}      # email -> user id
    invited: list = []
    seen: list = []
    fail: tuple | None = None

    def log_message(self, *_args):
        pass

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):  # pylint: disable=invalid-name
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        FakeZitadel.seen.append((self.path, self.headers.get("Authorization"), body))
        if self.headers.get("Authorization") != f"Bearer {SERVICE_TOKEN}":
            return self._send(401, {"code": 16, "message": "no"})
        if FakeZitadel.fail:
            return self._send(*FakeZitadel.fail)
        if self.path == "/v2/users":
            email = body["queries"][0]["emailQuery"]["emailAddress"].lower()
            assert body["queries"][1] == {"organizationIdQuery": {"organizationId": ORGANIZATION}}
            found = [{"userId": user, "human": {"email": {"email": email}}}
                     for mail, user in FakeZitadel.users.items() if mail == email]
            return self._send(200, {"details": {"totalResult": str(len(found))}, "result": found})
        if self.path == "/v2/users/human":
            assert body["organization"] == {"orgId": ORGANIZATION} and body["email"]["returnCode"] == {}
            user = str(uuid4().int)[:18]
            FakeZitadel.users[body["email"]["email"].lower()] = user
            return self._send(200, {"userId": user, "emailCode": "a-code-hive-drops"})
        if self.path.endswith("/invite_code"):
            FakeZitadel.invited.append((self.path.split("/")[3], body))
            return self._send(200, {"details": {}})
        return self._send(404, {"message": "no such route"})


@pytest.fixture(scope="module")
def zitadel_api():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeZitadel)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture(autouse=True)
def service_account(zitadel_api, tmp_path, monkeypatch):
    FakeZitadel.users, FakeZitadel.invited, FakeZitadel.seen, FakeZitadel.fail = {}, [], [], None
    token_file = tmp_path / "zitadel-token"
    token_file.write_text(SERVICE_TOKEN + "\n", encoding="utf-8")
    monkeypatch.setattr(people, "users", ZitadelUsers(zitadel_api, ORGANIZATION, str(token_file), timeout=2))
    monkeypatch.setattr(people.settings, "idp_issuer", ISSUER)
    return token_file


@pytest.fixture
def idp(monkeypatch):
    fake = IdP()
    monkeypatch.setattr(app_module, "IDP", IdentityProvider(ISSUER, AUDIENCE, read_keys=fake.jwks))
    return fake


@pytest.fixture
def http():
    return TestClient(app_module.app)


@pytest.fixture
def acme(register):
    return register("acme")


@pytest.fixture
def logged(caplog):
    caplog.set_level(logging.WARNING)
    return lambda: "\n".join(record.getMessage() for record in caplog.records)


def invite(http, caller, email=EMAIL, role="cliente-admin", **more):
    return http.post("/people", headers=caller.auth, json={"email": email, "role": role, **more})


def a_person(caller, role=PersonRole.ADMIN, status=PersonStatus.ACTIVE, subject=None, email=None):
    subject = subject or str(uuid4().int)[:18]
    return TenantRepository(caller.client_id).add_person(email or f"{subject}@acme.test", ISSUER, subject, role,
                                                          invited_by="test", status=status)


def as_person(http, idp, subject, **form):
    response = exchange(http, idp.id_token(sub=subject), **form)
    if response.status_code != 200:
        return response.json()
    KEY_OF[response.json()["access_token"]] = BFF
    return auth(response.json()["access_token"])


# --- Inviting -------------------------------------------------------------------------------------------------------

def test_a_new_person_is_created_in_zitadel_which_sends_the_invitation(http, acme, logged):
    response = invite(http, acme, givenName="Ana", familyName="Díaz")

    assert response.status_code == 201
    data = response.json()["data"]
    assert (data["email"], data["role"], data["status"], data["invitedBy"]) == (EMAIL, "cliente-admin", "pending",
                                                                                f"key {acme.kid}")
    paths = [path for path, _, _ in FakeZitadel.seen]
    assert paths[:2] == ["/v2/users", "/v2/users/human"] and paths[2].endswith("/invite_code")
    created = FakeZitadel.seen[1][2]
    assert (created["username"], created["profile"]) == (EMAIL, {"givenName": "Ana", "familyName": "Díaz"})
    user = FakeZitadel.users[EMAIL]
    assert FakeZitadel.invited == [(user, {"sendCode": {"applicationName": "Hive"}})]
    person = ClientUser.objects(uuid=data["uuid"]).first()
    assert (person.issuer, person.subject, person.status) == (ISSUER, user, "pending")
    assert f"invited person {data['uuid']} to client {acme.client_id}" in logged()
    assert EMAIL not in logged() and SERVICE_TOKEN not in logged()


def test_without_names_zitadel_gets_the_local_part_of_the_email(http, acme):
    assert invite(http, acme).status_code == 201

    assert FakeZitadel.seen[1][2]["profile"] == {"givenName": "ana", "familyName": "ana"}


def test_a_person_already_in_zitadel_is_linked_and_not_created_again(http, acme):
    FakeZitadel.users[EMAIL] = "300000000000000042"

    response = invite(http, acme)

    assert response.status_code == 201
    assert [path for path, _, _ in FakeZitadel.seen] == ["/v2/users"]
    assert FakeZitadel.invited == []
    assert ClientUser.objects(uuid=response.json()["data"]["uuid"]).first().subject == "300000000000000042"


def test_one_person_of_zitadel_may_be_of_two_clients(http, acme, register, idp):
    globex = register("globex")
    assert invite(http, acme).status_code == 201
    assert invite(http, globex, role="cliente-analista").status_code == 201

    user = FakeZitadel.users[EMAIL]
    assert ClientUser.objects(subject=user).count() == 2
    assert as_person(http, idp, user) == {"error": "invalid_target"}
    assert isinstance(as_person(http, idp, user, hive_client=str(globex.client_id)), dict)


def test_the_same_person_twice_in_a_client_is_a_conflict_and_zitadel_is_not_asked(http, acme):
    assert invite(http, acme).status_code == 201
    asked = len(FakeZitadel.seen)

    again = invite(http, acme, email="ANA@Acme.test")

    assert again.status_code == 409
    assert len(FakeZitadel.seen) == asked


@pytest.mark.parametrize("email", ["ana", "ana@acme", "a na@acme.test", "ana@@acme.test", "x" * 250 + "@a.test"])
def test_an_email_that_is_not_one_is_turned_away_before_zitadel(http, acme, email):
    assert invite(http, acme, email=email).status_code == 422
    assert FakeZitadel.seen == []


@pytest.mark.parametrize("role", ["operador", "admin", "cliente-ADMIN"])
def test_only_the_two_roles_of_the_catalogue(http, acme, role):
    assert invite(http, acme, role=role).status_code == 422


# --- When Zitadel does not --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("fail", [(500, {}), (503, {}), (429, {})])
def test_when_zitadel_cannot_answer_nobody_is_recorded(http, acme, fail):
    FakeZitadel.fail = fail

    assert invite(http, acme).status_code == 503
    assert ClientUser.objects(clientId=acme.client_id).count() == 0


def test_what_zitadel_says_no_to_gives_only_its_status(http, acme, logged):
    FakeZitadel.fail = (400, {"code": 3, "message": f"the email {EMAIL} is not valid"})

    response = invite(http, acme)

    assert response.status_code == 422
    assert response.json()["message"] == "IDP_REFUSED: Zitadel did not take it (400)"
    assert EMAIL not in response.text + logged()


def test_a_service_account_turned_away_is_for_the_operator_to_fix(http, acme, service_account, logged):
    service_account.write_text("a-token-zitadel-does-not-know", encoding="utf-8")

    assert invite(http, acme).status_code == 503
    assert "the service account was turned away: 401" in logged()
    assert "a-token-zitadel-does-not-know" not in logged()


def test_without_the_token_of_the_service_account_nobody_is_invited(http, acme, service_account):
    service_account.unlink()

    assert invite(http, acme).status_code == 503
    assert FakeZitadel.seen == []


def test_without_a_service_account_configured_nobody_is_invited(http, acme, monkeypatch, logged):
    monkeypatch.setattr(people, "users", None)

    assert invite(http, acme).status_code == 503
    assert "service account in Zitadel is not configured" in logged()


def test_two_users_of_zitadel_with_one_email_are_not_told_apart(http, acme, monkeypatch):
    monkeypatch.setattr(people.users, "_post", lambda path, body: {"result": [{"userId": "1"}, {"userId": "2"}]})

    assert invite(http, acme).status_code == 422


# --- Listing, and AB24 ---------------------------------------------------------------------------------------------

def test_the_list_has_the_people_of_the_client_and_only_them(http, acme, register):
    globex = register("globex")
    invite(http, acme)
    invite(http, globex, email="bob@globex.test", role="cliente-analista")

    mine = http.get("/people", headers=acme.auth).json()["data"]["people"]
    theirs = http.get("/people", headers=globex.auth).json()["data"]["people"]

    assert [person["email"] for person in mine] == [EMAIL]
    assert [person["email"] for person in theirs] == ["bob@globex.test"]


def test_an_invitation_to_one_client_gives_nothing_of_another(http, acme, register, idp):
    globex = register("globex")
    assert invite(http, acme, role="cliente-analista").status_code == 201
    user = FakeZitadel.users[EMAIL]

    assert as_person(http, idp, user, hive_client=str(globex.client_id)) == {"error": "invalid_grant"}
    headers = as_person(http, idp, user)
    assert http.get("/people", headers=globex.auth).json()["data"]["people"] == []
    assert http.get("/people", headers=headers).status_code == 403


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
def test_a_person_of_another_client_is_not_found(http, acme, register, method):
    theirs = a_person(register("globex"))

    response = http.request(method, f"/people/{theirs.uuid}", headers=acme.auth, json={"status": "blocked"})

    assert response.status_code == 404
    assert ClientUser.objects(uuid=theirs.uuid).first().status == "active"


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
def test_an_id_that_cannot_be_one_is_not_found(http, acme, method):
    assert http.request(method, "/people/not-a-uuid", headers=acme.auth, json={"status": "blocked"}).status_code == 404


def test_an_analyst_does_not_manage_people(http, acme, idp):
    analyst = a_person(acme, role=PersonRole.ANALYST)
    headers = as_person(http, idp, analyst.subject)

    assert http.get("/people", headers=headers).status_code == 403
    assert http.post("/people", headers=headers, json={"email": "x@acme.test", "role": "cliente-admin"}).status_code \
        == 403
    assert http.put(f"/people/{analyst.uuid}", headers=headers, json={"role": "cliente-admin"}).status_code == 403


def test_an_admin_person_manages_the_people_of_their_client(http, acme, idp):
    admin = a_person(acme)
    headers = as_person(http, idp, admin.subject)

    response = http.post("/people", headers=headers, json={"email": "bob@acme.test", "role": "cliente-analista"})

    assert response.status_code == 201
    assert response.json()["data"]["invitedBy"] == f"person {admin.uuid}"


# --- Role, blocking and removing --------------------------------------------------------------------------------------

def test_the_role_changes_at_the_next_exchange(http, acme, idp):
    a_person(acme)
    analyst = a_person(acme, role=PersonRole.ANALYST)

    changed = http.put(f"/people/{analyst.uuid}", headers=acme.auth, json={"role": "cliente-admin"})

    assert changed.status_code == 200 and changed.json()["data"]["role"] == "cliente-admin"
    assert http.get("/people", headers=as_person(http, idp, analyst.subject)).status_code == 200


def test_a_blocked_person_gets_no_token_and_one_let_in_again_does(http, acme, idp):
    a_person(acme)
    bob = a_person(acme, role=PersonRole.ANALYST)

    assert http.put(f"/people/{bob.uuid}", headers=acme.auth, json={"status": "blocked"}).status_code == 200
    assert as_person(http, idp, bob.subject) == {"error": "invalid_grant"}

    assert http.put(f"/people/{bob.uuid}", headers=acme.auth, json={"status": "active"}).status_code == 200
    assert "Authorization" in as_person(http, idp, bob.subject)


@pytest.mark.parametrize("change", [{"role": "cliente-analista"}, {"status": "blocked"}, None],
                         ids=["demoted", "blocked", "removed"])
def test_the_last_active_admin_stays_one(http, acme, change):
    only = a_person(acme)

    response = (http.delete(f"/people/{only.uuid}", headers=acme.auth) if change is None
                else http.put(f"/people/{only.uuid}", headers=acme.auth, json=change))

    assert response.status_code == 409
    assert response.json()["message"].startswith("LAST_ADMIN")
    stored = ClientUser.objects(uuid=only.uuid).first()
    assert (stored.role, stored.status) == ("cliente-admin", "active")


def test_with_another_active_admin_one_may_go(http, acme):
    first, _ = a_person(acme), a_person(acme)

    assert http.put(f"/people/{first.uuid}", headers=acme.auth, json={"role": "cliente-analista"}).status_code == 200


def test_an_admin_still_invited_does_not_count_as_the_other_one(http, acme):
    only = a_person(acme)
    a_person(acme, status=PersonStatus.PENDING)

    assert http.delete(f"/people/{only.uuid}", headers=acme.auth).status_code == 409


def test_a_removed_person_gets_no_token_and_their_email_is_erased(http, acme, idp, logged):
    a_person(acme)
    bob = a_person(acme, role=PersonRole.ANALYST, email="bob@acme.test")

    response = http.delete(f"/people/{bob.uuid}", headers=acme.auth)

    assert response.status_code == 200
    assert (response.json()["data"]["status"], response.json()["data"]["email"]) == ("removed", None)
    stored = ClientUser.objects(uuid=bob.uuid).first()
    assert (stored.email, stored.emailIndex) == (None, None)
    assert as_person(http, idp, bob.subject) == {"error": "invalid_grant"}
    assert f"removed person {bob.uuid}" in logged() and "bob@acme.test" not in logged()
    assert http.put(f"/people/{bob.uuid}", headers=acme.auth, json={"status": "active"}).status_code == 409


def test_a_removed_person_invited_again_comes_back_invited(http, acme):
    a_person(acme)
    FakeZitadel.users["bob@acme.test"] = "300000000000000077"
    bob = invite(http, acme, email="bob@acme.test", role="cliente-analista").json()["data"]
    http.delete(f"/people/{bob['uuid']}", headers=acme.auth)

    again = invite(http, acme, email="bob@acme.test", role="cliente-admin")

    assert again.status_code == 201
    data = again.json()["data"]
    assert (data["uuid"], data["email"], data["role"], data["status"]) == (bob["uuid"], "bob@acme.test",
                                                                           "cliente-admin", "pending")


def test_removing_twice_answers_the_same(http, acme):
    a_person(acme)
    bob = a_person(acme, role=PersonRole.ANALYST)

    first = http.delete(f"/people/{bob.uuid}", headers=acme.auth)
    second = http.delete(f"/people/{bob.uuid}", headers=acme.auth)

    assert first.status_code == second.status_code == 200


def test_a_change_without_anything_to_change_is_turned_away(http, acme):
    bob = a_person(acme)

    assert http.put(f"/people/{bob.uuid}", headers=acme.auth, json={}).status_code == 422
    assert http.put(f"/people/{bob.uuid}", headers=acme.auth, json={"status": "removed"}).status_code == 422
