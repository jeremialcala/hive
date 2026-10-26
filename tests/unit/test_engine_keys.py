# -*- coding: utf-8 -*-
"""
    The command that issues the keys of the response engine for a client (I6b, ADR-0022): one per worker, the
    private part kept in Andvari and the public part registered with the management API. Here Andvari is a store
    in memory that behaves like its KV engine with check-and-set; the real one is tested in
    tests/integration/test_engine_key_store.py.
"""
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app as app_module
import ops.engine_keys as engine_keys
from shared.auth import gateway_keys
from classes import Client
from conftest import GATEWAY_AUDIENCE, GATEWAY_KEYS_FILE, bearer, signed_assertion
from shared.engine_key_store import KeyStoreUnavailable
from enums import Worker

WORKERS = [worker.value for worker in Worker]


class Store:
    """Andvari's KV engine as the command sees it: a key is created only where there is none."""

    def __init__(self):
        self.keys = {}
        self.down = False
        self.created = []

    def read(self, client_id, worker):
        if self.down:
            raise KeyStoreUnavailable("ConnectionError")
        return self.keys.get((str(client_id), worker))

    def create(self, client_id, worker, key):
        if self.down:
            raise KeyStoreUnavailable("ConnectionError")
        if (str(client_id), worker) in self.keys:
            return False
        self.keys[(str(client_id), worker)] = key
        self.created.append(worker)
        return True


@pytest.fixture
def http():
    return TestClient(app_module.app)


@pytest.fixture
def store():
    return Store()


@pytest.fixture
def acme(register):
    return register("acme")


@pytest.fixture
def call(http, operator):
    """The management API as the operator calls it, with a fresh token each time."""
    def as_operator(method, path, body=None):
        response = http.request(method, path, headers=operator.auth, json=body)
        return response.status_code, response.json()

    return as_operator


def service_keys(acme):
    return Client.objects(uuid=acme.client_id).first().serviceKeys


def signs_as(key: dict, client_id) -> dict:
    return bearer(signed_assertion(key, client_id))


# --- Issuing --------------------------------------------------------------------------------------------------------

def test_every_worker_gets_a_key_kept_in_andvari_and_registered_with_its_worker(acme, call, store):
    done = engine_keys.issue(acme.client_id, call, store)

    assert [entry["worker"] for entry in done] == WORKERS
    assert all((entry["kept"], entry["registered"]) == ("new", "new") for entry in done)
    kept = {worker: store.keys[(str(acme.client_id), worker)] for worker in WORKERS}
    assert all("d" in key for key in kept.values())
    assert sorted((key["kid"], key["worker"]) for key in service_keys(acme)) == sorted(
        (kept[worker]["kid"], worker) for worker in WORKERS)
    assert [entry["kid"] for entry in done] == [kept[worker]["kid"] for worker in WORKERS]


def test_each_kept_key_signs_for_its_worker_and_reaches_its_routes(http, acme, call, store):
    engine_keys.issue(acme.client_id, call, store)
    bot = uuid4()

    def tokens_with(worker):
        key = store.keys[(str(acme.client_id), worker)]
        return http.get(f"/chat/{bot}/tokens", headers=signs_as(key, acme.client_id)).status_code

    # The notifier reaches the route: there is just no such chatbot. Any other worker is turned away.
    assert {worker: tokens_with(worker) for worker in WORKERS} == {
        worker: 404 if worker == "notifier" else 403 for worker in WORKERS}


def test_nothing_private_is_printed(acme, call, store):
    done = engine_keys.issue(acme.client_id, call, store)

    printed = json.dumps(done)
    assert all(key["d"] not in printed for key in store.keys.values())
    assert all(set(entry) == {"worker", "kept", "kid", "registered"} for entry in done)


# --- Running it again -----------------------------------------------------------------------------------------------

def test_running_it_again_changes_nothing(acme, call, store):
    first = engine_keys.issue(acme.client_id, call, store)
    kept = dict(store.keys)

    again = engine_keys.issue(acme.client_id, call, store)

    assert all((entry["kept"], entry["registered"]) == ("already", "already") for entry in again)
    assert [entry["kid"] for entry in again] == [entry["kid"] for entry in first]
    assert store.keys == kept and len(store.created) == 6
    assert len(service_keys(acme)) == 6


def test_a_key_kept_but_not_registered_is_registered_on_the_next_run(acme, call, store):
    def api_down_for_registering(method, path, body=None):
        return (503, {"detail": "HTTP_503_SERVICE_UNAVAILABLE"}) if method == "POST" else call(method, path, body)

    halfway = engine_keys.issue(acme.client_id, api_down_for_registering, store)
    assert all(entry["error"] == "API: 503 HTTP_503_SERVICE_UNAVAILABLE" for entry in halfway)
    assert service_keys(acme) == [] and len(store.keys) == 6

    finished = engine_keys.issue(acme.client_id, call, store)

    assert all((entry["kept"], entry["registered"]) == ("already", "new") for entry in finished)
    assert [entry["kid"] for entry in finished] == [entry["kid"] for entry in halfway]


def test_a_key_kept_by_another_run_in_between_is_the_one_registered(acme, call, store):
    theirs = {}
    original = store.create

    def another_run_first(client_id, worker, key):
        # Another run keeps its key for the coordinator just before this one does.
        if worker == "coordinator" and not theirs:
            from shared.auth.client import new_signing_key
            theirs.update(new_signing_key())
            original(client_id, worker, theirs)
        return original(client_id, worker, key)

    store.create = another_run_first

    done = {entry["worker"]: entry for entry in engine_keys.issue(acme.client_id, call, store)}

    assert done["coordinator"]["kid"] == theirs["kid"]
    assert (done["coordinator"]["kept"], done["coordinator"]["registered"]) == ("already", "new")
    assert store.keys[(str(acme.client_id), "coordinator")] is theirs


# --- What does not work ---------------------------------------------------------------------------------------------

def test_without_andvari_no_key_is_made_nor_registered(acme, call, store):
    store.down = True

    done = engine_keys.issue(acme.client_id, call, store)

    assert all(entry["error"] == "Andvari: ConnectionError" for entry in done)
    assert service_keys(acme) == []


def test_a_worker_that_already_has_two_keys_is_reported_and_the_others_are_issued(http, operator, acme, call, store):
    from shared.auth.client import new_signing_key, public_part

    for _ in range(2):
        added = http.post(f"/clients/{acme.client_id}/service-keys", headers=operator.auth,
                          json={"signingKey": public_part(new_signing_key()), "worker": "notifier"})
        assert added.status_code == 200

    done = {entry["worker"]: entry for entry in engine_keys.issue(acme.client_id, call, store)}

    assert done["notifier"]["error"] == "API: 409 TOO_MANY_KEYS"
    assert all(done[worker]["registered"] == "new" for worker in WORKERS if worker != "notifier")


def test_a_kept_key_without_a_kid_is_reported(acme, call, store):
    store.keys[(str(acme.client_id), "link")] = {"kty": "EC"}

    done = {entry["worker"]: entry for entry in engine_keys.issue(acme.client_id, call, store)}

    assert done["link"]["error"] == "Andvari: the kept key has no kid"
    assert store.keys[(str(acme.client_id), "link")] == {"kty": "EC"}


def test_a_client_that_does_not_exist_stops_it(call, store):
    with pytest.raises(SystemExit, match="There is no client"):
        engine_keys.issue(uuid4(), call, store)
    assert store.keys == {}


def test_without_the_list_of_clients_it_stops(acme, store):
    with pytest.raises(SystemExit, match="could not be read: 403"):
        engine_keys.issue(acme.client_id, lambda method, path, body=None: (403, {}), store)


# --- The command ----------------------------------------------------------------------------------------------------

@pytest.fixture
def environment(monkeypatch, tmp_path, http, operator, store):
    """The command as it runs: its settings in the environment, the API over HTTP and Andvari replaced."""
    (tmp_path / "operator.json").write_text(json.dumps(operator.key), encoding="utf-8")
    public = gateway_keys.GatewayKeys.load(GATEWAY_KEYS_FILE).public_set()
    (tmp_path / "gateway.json").write_text(json.dumps(public), encoding="utf-8")
    for name, value in {"GATEWAY_URL": "http://gestion:5002", "GATEWAY_AUDIENCE": GATEWAY_AUDIENCE,
                        "GATEWAY_PUBLIC_KEYS_FILE": str(tmp_path / "gateway.json"),
                        "OPERATOR_KEY_FILE": str(tmp_path / "operator.json"),
                        "OPERATOR_CLIENT_ID": str(operator.client_id), "ANDVARI_ADDR": "https://andvari:8200",
                        "ENGINE_KEYS_ROLE_ID": "motor-claves",
                        "ENGINE_KEYS_SECRET_ID_FILE": str(tmp_path / "secret-id")}.items():
        monkeypatch.setenv(name, value)
    sent = []

    def over_http(method, url, json=None, timeout=None, headers=None):
        sent.append((method, url))
        return http.request(method, url.removeprefix("http://gestion:5002"), json=json, headers=headers)

    def token_over_http(url, data=None, timeout=None):
        sent.append(("POST", url))
        return http.post(url.removeprefix("http://gestion:5002"), data=data)

    monkeypatch.setattr(engine_keys.requests, "request", over_http)
    monkeypatch.setattr(engine_keys.requests, "post", token_over_http)
    monkeypatch.setattr(engine_keys, "EngineKeyStore", lambda *args: store)
    return sent


def test_the_command_issues_the_keys_and_says_what_it_did(environment, acme, store, capsys):
    assert engine_keys.main([str(acme.client_id)]) == 0

    printed = json.loads(capsys.readouterr().out)
    assert [entry["worker"] for entry in printed] == WORKERS
    assert len(service_keys(acme)) == 6
    # One access token, one list of clients and six registrations, all with that token (ADR-0025).
    assert environment == ([("POST", "http://gestion:5002/oauth/token"), ("GET", "http://gestion:5002/clients")]
                           + [("POST", f"http://gestion:5002/clients/{acme.client_id}/service-keys")] * 6)


def test_the_command_ends_with_an_error_when_a_worker_was_not_done(environment, acme, store, capsys):
    store.down = True

    assert engine_keys.main([str(acme.client_id)]) == 1
    assert "Andvari: ConnectionError" in capsys.readouterr().out


@pytest.mark.parametrize("arguments", [[], ["a", "b"], ["not-a-uuid"]])
def test_the_command_takes_one_client_id(arguments):
    with pytest.raises(SystemExit):
        engine_keys.main(arguments)
