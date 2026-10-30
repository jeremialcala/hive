# -*- coding: utf-8 -*-
"""
    The registry of clients and of their signing keys: who may register whom, which keys are accepted, and how a
    client rotates its own without a gap. Everything goes through the application with real tokens.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jwcrypto import jwk

import app as app_module
from shared.auth import gateway_keys
from shared.auth.client import build_assertion, dpop_proof, new_signing_key, public_part, token_request
from classes import Client
from conftest import Caller, GATEWAY_AUDIENCE, GATEWAY_KEYS_FILE, KEY_OF, access_token_for
from enums import Role
from ops import client as ops_client, gateway_key as ops_gateway_key, operator_key as ops_operator_key
from repository import get_client, operator_client

SRC = Path(__file__).resolve().parents[2] / "src"
# The service imports `shared` from the root of the repository (ADR-0023).
PATH = os.pathsep.join([str(SRC), str(SRC.parent)])


@pytest.fixture
def http():
    return TestClient(app_module.app)


def registration(name: str = "acme", key: dict | None = None) -> dict:
    return {"name": name, "signingKey": public_part(key or new_signing_key())}


# --- The operator registers clients -----------------------------------------------------------------------------

def test_the_operator_registers_a_client_that_can_then_call_the_service(http, operator):
    key = new_signing_key()

    response = http.post("/clients", headers=operator.auth, json=registration("acme", key))
    data = response.json()["data"]

    assert response.status_code == 200
    assert (data["name"], data["role"], data["status"], data["keys"]) == ("acme", "client", "ACT", [key["kid"]])
    # The answer is all the new client needs: who it is, the audience and the key to encrypt to.
    assertion = build_assertion(key, data["uuid"], data["gateway"]["audience"], data["gateway"]["keys"][-1])
    KEY_OF[assertion] = key
    token = access_token_for(assertion)
    mine = http.get("/keys", headers={"Authorization": f"DPoP {token}"})
    assert (mine.status_code, mine.json()["data"]["uuid"]) == (200, data["uuid"])


def test_what_the_gateway_hands_out_is_only_public(http, operator):
    data = http.post("/clients", headers=operator.auth, json=registration()).json()["data"]
    keys = http.get("/gateway/keys", headers=operator.auth).json()["data"]

    assert keys == data["gateway"]
    assert keys["audience"] == GATEWAY_AUDIENCE
    for key in keys["keys"]:
        assert set(key) == {"kty", "n", "e", "kid", "use", "alg"}
    assert "signingKeys" not in json.dumps(data) and '"d"' not in json.dumps(data)


def test_only_the_public_key_of_a_client_is_stored(http, operator):
    key = new_signing_key()
    http.post("/clients", headers=operator.auth, json=registration("acme", key))

    stored = Client.objects(name="acme").get().signingKeys

    assert [set(entry) for entry in stored] == [{"kty", "crv", "x", "y", "kid", "use", "alg"}]
    assert stored[0]["kid"] == jwk.JWK(**public_part(key)).thumbprint()


@pytest.mark.parametrize("path,method", [("/clients", "POST"), ("/clients", "GET"),
                                         ("/clients/{client_id}/keys", "POST"),
                                         ("/clients/{client_id}/keys/{kid}", "DELETE")])
def test_a_client_cannot_use_the_operator_endpoints(http, register, path, method):
    caller, victim = register("acme"), register("globex")

    response = http.request(method, path.format(client_id=victim.client_id, kid=victim.kid), headers=caller.auth,
                            json=registration("initech"))

    assert response.status_code == 403
    assert Client.objects.count() == 2
    assert [key["kid"] for key in get_client(victim.client_id).signingKeys] == [victim.kid]


def test_a_client_does_not_become_operator_by_saying_so(http, register):
    """What a caller may do comes from its registry entry: nothing in a token can change it."""
    caller = register("acme")
    token = caller.access_token()

    assert http.get("/clients", headers={"Authorization": f"DPoP {token}"}).status_code == 403
    assert get_client(caller.client_id).role == Role.CLIENT.value


def test_the_operator_lists_the_clients(http, operator, register):
    register("acme")

    listed = http.get("/clients", headers=operator.auth).json()["data"]

    assert [(client["name"], client["role"]) for client in listed] == [("operator", "operator"), ("acme", "client")]
    assert all(set(client) == {"uuid", "name", "role", "status", "keys", "serviceKeys"} for client in listed)


def test_a_name_is_registered_once(http, operator):
    first = http.post("/clients", headers=operator.auth, json=registration("acme"))
    second = http.post("/clients", headers=operator.auth, json=registration("acme"))

    assert (first.status_code, second.status_code) == (200, 409)
    assert second.json() == {"detail": "CLIENT_NAME_TAKEN"}
    assert Client.objects(name="acme").count() == 1


@pytest.mark.parametrize("body", [
    {"name": "acme"},
    {"name": "", "signingKey": public_part(new_signing_key())},
    {"name": "a\nb", "signingKey": public_part(new_signing_key())},
    {"name": "x" * 65, "signingKey": public_part(new_signing_key())},
    {"name": "acme", "signingKey": "not a key"},
    {"name": "acme", "signingKey": public_part(new_signing_key()), "role": "operator"},
], ids=["no key", "no name", "a name with a line break", "a name too long", "a key that is text", "a role"])
def test_a_registration_that_is_not_well_formed_is_rejected(http, operator, body):
    response = http.post("/clients", headers=operator.auth, json=body)

    assert response.status_code == 422
    assert Client.objects.count() == 1


BAD_KEYS = {
    "a private key": new_signing_key(),
    "an RSA key": json.loads(jwk.JWK.generate(kty="RSA", size=2048).export_public()),
    "another curve": json.loads(jwk.JWK.generate(kty="EC", crv="P-384").export_public()),
    "a symmetric key": {"kty": "oct", "k": "c2VjcmV0"},
    "no coordinates": {"kty": "EC", "crv": "P-256"},
    "a point off the curve": {"kty": "EC", "crv": "P-256", "x": "AAAA", "y": "AAAA"},
    "coordinates that are not text": {"kty": "EC", "crv": "P-256", "x": 1, "y": 2},
    "nothing": {},
}


@pytest.mark.parametrize("kind", sorted(BAD_KEYS))
def test_only_a_public_p256_key_is_accepted(http, operator, kind):
    response = http.post("/clients", headers=operator.auth, json={"name": "acme", "signingKey": BAD_KEYS[kind]})

    assert response.status_code == 422
    assert Client.objects.count() == 1
    # The answer says what is wrong without repeating what was sent: a private key must not come back.
    assert BAD_KEYS[kind].get("d", "no-private-part") not in response.text


def test_the_answer_says_what_is_wrong_with_the_key(http, operator):
    """Each kind of bad key gets its own text, the one the 422 carries (HIG-137: surviving mutants)."""
    from auth.client_keys import public_signing_key

    not_ec = "The signing key must be an EC key on the P-256 curve"
    no_coordinates = "The signing key has no coordinates"
    expected = {"a private key": "That is a private key: only the public part is registered",
                "an RSA key": not_ec, "another curve": not_ec, "a symmetric key": not_ec, "nothing": not_ec,
                "no coordinates": no_coordinates, "coordinates that are not text": no_coordinates,
                "a point off the curve": "The signing key is not a valid point of the P-256 curve"}
    only_x = {"kty": "EC", "crv": "P-256", "x": public_part(new_signing_key())["x"]}

    for kind, key in [*BAD_KEYS.items(), ("only x", only_x), ("not a key", "a-key")]:
        with pytest.raises(ValueError) as refused:
            public_signing_key(key)
        assert str(refused.value) == {**expected, "only x": no_coordinates,
                                      "not a key": "The signing key must be a JWK"}[kind], kind
    response = http.post("/clients", headers=operator.auth, json={"name": "acme", "signingKey": only_x})
    assert response.json() == {"detail": no_coordinates}


# --- A client rotates its own key --------------------------------------------------------------------------------

def test_a_client_rotates_its_key_without_a_gap(http, register):
    caller = register("acme")
    new = new_signing_key()

    added = http.post("/keys", **caller.adding(new))
    with_new = Caller(get_client(caller.client_id), new)
    with_new.kid = new["kid"]

    assert added.json()["data"]["keys"] == [caller.kid, new["kid"]]
    # Both work while the client moves from one to the other.
    assert http.get("/keys", headers=caller.auth).status_code == 200
    assert http.get("/keys", headers=with_new.auth).status_code == 200

    removed = http.delete(f"/keys/{caller.kid}", headers=with_new.auth)

    assert removed.json()["data"]["keys"] == [new["kid"]]
    assert http.get("/keys", headers=caller.auth).status_code == 401
    assert http.get("/keys", headers=with_new.auth).status_code == 200


def test_a_client_has_two_keys_at_most(http, register):
    caller = register("acme")

    second = http.post("/keys", **caller.adding(new_signing_key()))
    third = http.post("/keys", **caller.adding(new_signing_key()))

    assert (second.status_code, third.status_code, third.json()) == (200, 409, {"detail": "TOO_MANY_KEYS"})
    assert len(get_client(caller.client_id).signingKeys) == 2


def test_a_key_is_registered_once(http, register):
    caller = register("acme")

    again = http.post("/keys", **caller.adding(caller.key))

    assert (again.status_code, again.json()) == (409, {"detail": "KEY_ALREADY_REGISTERED"})


def test_a_client_cannot_remove_its_last_key(http, register):
    caller = register("acme")

    response = http.delete(f"/keys/{caller.kid}", headers=caller.auth)

    assert (response.status_code, response.json()) == (409, {"detail": "LAST_KEY"})
    assert http.get("/keys", headers=caller.auth).status_code == 200


def test_removing_a_key_that_is_not_there_is_not_found(http, register):
    caller = register("acme")

    response = http.delete("/keys/unknown", headers=caller.auth)

    assert response.status_code == 404


def test_a_client_only_touches_its_own_keys(http, register):
    caller, victim = register("acme"), register("globex")

    http.post("/keys", **caller.adding(new_signing_key()))
    response = http.delete(f"/keys/{victim.kid}", headers=caller.auth)

    assert response.status_code == 404
    assert [key["kid"] for key in get_client(victim.client_id).signingKeys] == [victim.kid]


def test_a_key_added_meanwhile_does_not_bring_back_a_revoked_one(http, operator, register, monkeypatch):
    """Found in the dual review of Gate 2: saving the list as it was read undid a revocation made in between."""
    import repository.clients as clients
    from auth.client_keys import public_signing_key
    from repository import add_signing_key, find_signer

    caller = register("acme")
    read_before = get_client(caller.client_id)
    revoked = http.delete(f"/clients/{caller.client_id}/keys/{caller.kid}", headers=operator.auth)
    # Whatever the change in flight read of the client, it is from before the revocation.
    monkeypatch.setattr(clients, "get_client", lambda client_id: read_before)

    new = public_signing_key(public_part(new_signing_key()))
    add_signing_key(caller.client_id, new)
    monkeypatch.undo()

    assert revoked.status_code == 200
    assert [key["kid"] for key in get_client(caller.client_id).signingKeys] == [new["kid"]]
    assert find_signer(caller.client_id, caller.kid) is None


def test_a_key_change_does_not_bring_back_a_deleted_client(register, monkeypatch):
    """The other half of the same finding: saving a client that was deleted in between created it again."""
    import repository.clients as clients
    from auth.client_keys import public_signing_key
    from repository import Conflict, NotFound, add_signing_key

    caller = register("acme")
    read_before = get_client(caller.client_id)
    Client.objects(uuid=caller.client_id).delete()
    monkeypatch.setattr(clients, "get_client", lambda client_id: read_before)

    with pytest.raises((NotFound, Conflict)):
        add_signing_key(caller.client_id, public_signing_key(public_part(new_signing_key())))
    assert Client.objects.count() == 0


# --- The operator and the keys of a client -----------------------------------------------------------------------

def test_the_operator_lets_a_client_back_in(http, operator, register):
    caller = register("acme")
    new = new_signing_key()

    revoked = http.delete(f"/clients/{caller.client_id}/keys/{caller.kid}", headers=operator.auth)
    added = http.post(f"/clients/{caller.client_id}/keys", headers=operator.auth,
                      json={"signingKey": public_part(new)})
    with_new = Caller(get_client(caller.client_id), new)

    assert (revoked.status_code, revoked.json()["data"]["keys"]) == (200, [])
    assert added.json()["data"]["keys"] == [new["kid"]]
    assert http.get("/keys", headers=caller.auth).status_code == 401
    assert http.get("/keys", headers=with_new.auth).status_code == 200


@pytest.mark.parametrize("client_id", ["11111111-1111-4111-8111-111111111111", "not-a-uuid"])
def test_the_keys_of_a_client_that_does_not_exist_are_not_found(http, operator, client_id):
    added = http.post(f"/clients/{client_id}/keys", headers=operator.auth,
                      json={"signingKey": public_part(new_signing_key())})
    removed = http.delete(f"/clients/{client_id}/keys/a-kid", headers=operator.auth)

    assert (added.status_code, removed.status_code) == (404, 404)


# --- The server commands -----------------------------------------------------------------------------------------

def test_the_first_operator_key_is_registered_from_the_server(http, tmp_path):
    key = new_signing_key()
    path = tmp_path / "operator-public.json"
    path.write_text(json.dumps(public_part(key)), encoding="utf-8")

    result = ops_operator_key.register(str(path))
    caller = Caller(operator_client(), key)

    assert result == {"client": str(operator_client().uuid), "kid": key["kid"]}
    assert http.get("/clients", headers=caller.auth).status_code == 200


def test_the_operator_command_refuses_a_private_key(tmp_path):
    path = tmp_path / "operator-private.json"
    path.write_text(json.dumps(new_signing_key()), encoding="utf-8")

    with pytest.raises(ValueError):
        ops_operator_key.register(str(path))

    assert operator_client().signingKeys == []


def test_the_gateway_key_file_is_made_rotated_and_trimmed(tmp_path):
    path = str(tmp_path / "gateway-keys.json")

    first = ops_gateway_key.generate(path)
    second = ops_gateway_key.generate(path)
    signing = ops_gateway_key.generate_signing(path)

    # The signing key is not handed out: only the keys to encrypt to.
    assert [key["kid"] for key in ops_gateway_key.show(path)["keys"]] == [first["kid"], second["kid"]]
    assert "d" not in first and "d" not in signing
    assert all("d" not in key for key in ops_gateway_key.show(path)["keys"])
    left = ops_gateway_key.drop(path, first["kid"])

    assert [key["kid"] for key in left["keys"]] == [second["kid"], signing["kid"]]
    with pytest.raises(SystemExit):
        ops_gateway_key.drop(path, second["kid"])
    with pytest.raises(SystemExit):
        ops_gateway_key.drop(path, signing["kid"])
    with pytest.raises(SystemExit):
        ops_gateway_key.drop(path, "unknown")
    # What is left is a file the service starts with.
    loaded = gateway_keys.GatewayKeys.load(path)
    assert loaded.private(second["kid"]) is not None and loaded.signer()[0] == signing["kid"]


def test_the_service_does_not_start_without_a_key_of_each_kind(tmp_path):
    for keys in ([gateway_keys.new_key()], [gateway_keys.new_signing_key()]):
        with pytest.raises(ValueError):
            gateway_keys.GatewayKeys(keys)


@pytest.mark.skipif(os.name == "nt", reason="Windows has no owner-only file mode")
def test_the_key_files_are_owner_only(tmp_path):
    gateway, client = str(tmp_path / "gateway-keys.json"), str(tmp_path / "client-key.json")

    ops_gateway_key.generate(gateway)
    ops_client.new_key(client)

    assert (os.stat(gateway).st_mode & 0o777, os.stat(client).st_mode & 0o777) == (0o600, 0o600)


def test_a_client_key_is_never_overwritten(tmp_path):
    path = str(tmp_path / "client-key.json")
    public = ops_client.new_key(path)

    with pytest.raises(FileExistsError):
        ops_client.new_key(path)

    assert set(public) == {"kty", "crv", "x", "y"}
    assert public_part(json.loads(Path(path).read_text(encoding="utf-8"))) == public


@pytest.mark.parametrize("keys", [
    [],
    [{"kty": "RSA", "kid": "public-only", "n": "AQAB" * 86, "e": "AQAB"}],
    [{**gateway_keys.new_key(), "kid": ""}],
    [json.loads(jwk.JWK.generate(kty="RSA", size=1024, kid="small").export_private())],
    [json.loads(jwk.JWK.generate(kty="EC", crv="P-256", kid="curve").export_private())],
], ids=["no keys", "a public key", "no kid", "a short key", "not RSA"])
def test_the_service_does_not_start_with_a_key_file_it_cannot_use(tmp_path, keys):
    path = str(tmp_path / "gateway-keys.json")
    gateway_keys.write_file(path, keys)

    with pytest.raises(ValueError):
        gateway_keys.GatewayKeys.load(path)


def test_the_reference_client_runs_without_the_service_settings(tmp_path):
    """
        The client commands are what a client copies: they must work with nothing of the service around. They run
        here in another process, with no settings and no database, and the token they print gets in.
    """
    key_file, gateway_file = tmp_path / "client-key.json", tmp_path / "gateway-public.json"
    gateway_file.write_text(json.dumps(gateway_keys.GatewayKeys.load(GATEWAY_KEYS_FILE).public_set()),
                            encoding="utf-8")
    bare = {name: value for name, value in os.environ.items() if name in ("PATH", "SYSTEMROOT", "TEMP", "TMP")}

    def run(*arguments) -> str:
        # cwd is a directory with no config.env, so nothing of the service can be picked up by accident.
        done = subprocess.run([sys.executable, "-m", "ops.client", *arguments], capture_output=True, text=True,
                              cwd=str(tmp_path), env={**bare, "PYTHONPATH": PATH}, timeout=60)
        assert done.returncode == 0, done.stderr
        return done.stdout.strip()

    public = json.loads(run("new-key", str(key_file)))
    client = app_module  # The service side, in this process.
    registered = TestClient(client.app)
    from auth.client_keys import public_signing_key
    from repository import register_client
    client_id = register_client("acme", public_signing_key(public)).uuid

    # The assertion, from the reference client; the access token, from the service in this process.
    assertion = run("assertion", str(key_file), str(client_id), GATEWAY_AUDIENCE, str(gateway_file))
    token_proof = dpop_proof(json.loads(key_file.read_text(encoding="utf-8")), "POST",
                             f"{GATEWAY_AUDIENCE}/oauth/token")
    token = registered.post("/oauth/token", data=token_request(assertion),
                            headers={"DPoP": token_proof}).json()["access_token"]

    # Each request with it, with the proof the reference client signs (ADR-0033).
    def bound(method: str, path: str) -> dict:
        return {"Authorization": f"DPoP {token}",
                "DPoP": run("proof", str(key_file), method, f"{GATEWAY_AUDIENCE}{path}", token)}

    assert registered.get("/keys", headers=bound("GET", "/keys")).status_code == 200

    # And adding a second key, with the proof the new one signs for that access token.
    new_key_file = tmp_path / "client-key-2.json"
    second = json.loads(run("new-key", str(new_key_file)))
    body = json.loads(run("add-key", str(new_key_file), str(client_id), GATEWAY_AUDIENCE, token))
    added = registered.post("/keys", headers=bound("POST", "/keys"), json=body)

    assert (added.status_code, added.json()["data"]["keys"][-1]) == (200, jwk.JWK(**second).thumbprint())


def test_the_reference_client_gets_an_access_token_and_says_why_not(tmp_path, monkeypatch):
    http = TestClient(app_module.app)
    monkeypatch.setattr(ops_client.requests, "post",
                        lambda url, data=None, timeout=None, headers=None: http.post(
                            url.removeprefix("https://gateway.test"), data=data, headers=headers))
    key_file, gateway_file = tmp_path / "key.json", tmp_path / "gateway.json"
    public = ops_client.new_key(str(key_file))
    gateway_file.write_text(json.dumps(gateway_keys.GatewayKeys.load(GATEWAY_KEYS_FILE).public_set()),
                            encoding="utf-8")
    from auth.client_keys import public_signing_key
    from repository import register_client
    client_id = register_client("acme", public_signing_key(public)).uuid

    token = ops_client.access_token(str(key_file), str(client_id), GATEWAY_AUDIENCE, str(gateway_file),
                                    "https://gateway.test/")

    proof = ops_client.proof(str(key_file), "GET", "https://gateway.test/keys", token)
    assert http.get("/keys", headers={"Authorization": f"DPoP {token}", "DPoP": proof}).status_code == 200
    with pytest.raises(SystemExit, match="401"):
        ops_client.access_token(str(key_file), "00000000-0000-0000-0000-000000000000", GATEWAY_AUDIENCE,
                                str(gateway_file), "https://gateway.test")
