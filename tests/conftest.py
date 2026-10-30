# -*- coding: utf-8 -*-
"""
    Settings are read at import time all over the project, so they are pinned here before anything else is imported.
    Environment variables win over config.env: the tests never see real credentials nor reach a real service.
"""
import os
import tempfile

from shared.auth import gateway_keys  # Needs neither settings nor database.

# A key pair for the gateway, made for this run and thrown away with it.
GATEWAY_KEYS_FILE = os.path.join(tempfile.mkdtemp(prefix="gateway-keys-"), "gateway-keys.json")
gateway_keys.write_file(GATEWAY_KEYS_FILE, [gateway_keys.new_key(), gateway_keys.new_signing_key()])
GATEWAY_AUDIENCE = "https://gateway.test"
# Where the test client sends its requests: a DPoP proof says it (ADR-0033).
TEST_SERVER = "http://testserver"

# The key service is replaced by a double further down. These only have to be there for the settings to load.
ANDVARI_SECRET_ID_FILE = os.path.join(os.path.dirname(GATEWAY_KEYS_FILE), "secret-id")
with open(ANDVARI_SECRET_ID_FILE, "w", encoding="utf-8") as _file:
    _file.write("test-secret-id")

os.environ.update({
    "GATEWAY_KEYS_FILE": GATEWAY_KEYS_FILE,
    "GATEWAY_AUDIENCE": GATEWAY_AUDIENCE,
    "GATEWAY_URLS": TEST_SERVER,
    # Nothing listens there: a test that reached the real key service would fail, not hang.
    "ANDVARI_ADDR": "http://127.0.0.1:1",
    "ANDVARI_ROLE_ID": "test-role-id",
    "ANDVARI_SECRET_ID_FILE": ANDVARI_SECRET_ID_FILE,
    # Each test gets its own register of erasures further down. This one only has to be there for the settings.
    "ERASURES_DIR": os.path.join(os.path.dirname(GATEWAY_KEYS_FILE), "erasures"),
    "FACEBOOK_APP_SECRET": "test-app-secret",

    "CLIENT_ID": "test",
    "NATIONAL_ID_URL": "http://127.0.0.1:1",

    "DB_NAME": "test",
    # Nothing listens on port 1, and the connection is replaced by an in-memory database further down: no test
    # can write anywhere real.
    "DB_HOST": "mongodb://127.0.0.1:1/?serverSelectionTimeoutMS=500",
    "DB_USERNAME": "test",
    "DB_PASSWORD": "test",

    "SENDER_EMAIL": "test@example.com",
    "SENDER_PASSWORD": "test",
    "GMAIL_SERVER_URL": "127.0.0.1",
    "GMAIL_SERVER_PORT": "1",

    "EXPIRATION_TIME": "60",
    "ACTIVATION_CODE_LENGTH": "6",
    "PASSWORD_LENGTH": "12",

    "QMS_SERVER": "127.0.0.1",
    "QMS_USER": "test",
    "QMS_PASSWORD": "test",

    "KEY_SIZE": "2048",
    "PRIVATE_KEY_FILENAME": "certs/{UUID}_private.pem",
    "PUBLIC_KEY_FILENAME": "certs/{UUID}_public.pem",

    "ENVIRONMENT": "development",
    "VERSION": "0.0.0",

    "VERIFY_TOKEN": "test",

    "FACEBOOK_GRAPH_URL": "http://127.0.0.1:1/{fb_id}?access_token={ACCESSTOKEN}",
    "FACEBOOK_WHATSAPP_MESSAGE": "http://127.0.0.1:1/{version}/{PhoneNumberId}/messages",
    "FACEBOOK_GRAPH_VERSION": "v19.0",
})

# Every entity module connects to MongoDB when it is imported. The connection is swapped here, before any of them
# is, for an in-memory database: the tests run real queries against real documents without a server.
import bson  # noqa: E402
import mongoengine  # noqa: E402
import mongomock  # noqa: E402
import mongomock.collection  # noqa: E402
import pytest  # noqa: E402
from bson.binary import UuidRepresentation  # noqa: E402

# mongoengine stores UUIDs the legacy way unless told otherwise, and that is what the service does today.
LEGACY_UUID = bson.codec_options.CodecOptions(uuid_representation=UuidRepresentation.PYTHON_LEGACY)


class _SizeCheck(bson.BSON):
    """mongomock encodes each document only to measure it, and does it without the codec options of the client."""

    @classmethod
    def encode(cls, document, check_keys=False, codec_options=LEGACY_UUID):
        return super().encode(document, check_keys, LEGACY_UUID)


mongomock.collection.BSON = _SizeCheck

_connect = mongoengine.connect


def _connect_in_memory(*args, **kwargs):
    return _connect(*args, **{**kwargs, "mongo_client_class": mongomock.MongoClient,
                              "uuidRepresentation": "pythonLegacy"})


mongoengine.connect = _connect_in_memory


@pytest.fixture(autouse=True)
def empty_database():
    """
        Each test starts with an empty database. The documents are deleted, not the collections: dropping them
        would take their unique indexes away, and mongoengine only creates those once per process.
    """
    import classes  # noqa: F401  The entities register the connection.

    from repository import kept_bots

    database = mongoengine.get_db()
    for name in database.list_collection_names():
        database[name].delete_many({})
    # Neither are the chatbots the webhook kept in memory left over from another test.
    kept_bots.forget()
    yield


class FakeKeyService:
    """
        Stands in for the key service, and behaves like its Transit engine with a derived key: a data key
        wrapped for a client only opens with that client's id. The real client is tested against a real
        OpenBao in tests/integration.
    """

    def __init__(self):
        self.master = os.urandom(32)
        self.down = False
        self.calls = []

    def _wrapping(self, client_id):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF

        return AESGCM(HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                           info=str(client_id).encode()).derive(self.master))

    def _check(self, operation):
        from crypto.key_service import KeyServiceUnavailable

        self.calls.append(operation)
        if self.down:
            raise KeyServiceUnavailable("ConnectionError")

    def new_data_key(self, client_id):
        import base64

        self._check("new_data_key")
        key, nonce = os.urandom(32), os.urandom(12)
        sealed = self._wrapping(client_id).encrypt(nonce, key, None)
        return key, "fake:v1:" + base64.b64encode(nonce + sealed).decode()

    def unwrap(self, client_id, wrapped):
        import base64
        from cryptography.exceptions import InvalidTag
        from crypto.key_service import KeyServiceUnavailable

        self._check("unwrap")
        raw = base64.b64decode(wrapped[len("fake:v1:"):])
        try:
            return self._wrapping(client_id).decrypt(raw[:12], raw[12:], None)
        except InvalidTag:
            raise KeyServiceUnavailable("decrypt refused: 400") from None


@pytest.fixture(autouse=True)
def key_service():
    """Each test gets its own key service, with its own master key and no data key left open in memory."""
    from repository import data_keys

    service = FakeKeyService()
    data_keys.configure(service)
    return service


@pytest.fixture(autouse=True)
def register_of_erasures(tmp_path):
    """Each test gets its own, empty register of erasures. Returns the directory it is kept in."""
    from repository import erasures

    directory = tmp_path / "erasures"
    erasures.configure(directory)
    return directory


@pytest.fixture(autouse=True)
def token_limits(monkeypatch):
    """
        Each test gets its own counters of POST /oauth/token (ADR-0028): the whole suite asks for tokens from the same
        test client, and would otherwise run into the limit. Returns them.
    """
    from auth import token_limits

    limits = token_limits.TokenLimits()
    monkeypatch.setattr(token_limits, "LIMITS", limits)
    return limits


@pytest.fixture(autouse=True)
def request_limits(monkeypatch):
    """
        The same for the limits of the other routes and of the webhook (HIG-139): every test gets its own counters of
        credentials and of failures. Returns them.
    """
    from auth import request_limits

    limits = request_limits.RequestLimits()
    monkeypatch.setattr(request_limits, "LIMITS", limits)
    return limits


# The private key each assertion and each access token of the tests was made with. For every request that carries
# one of those tokens, the test client signs the DPoP proof a client would (ADR-0033); a token that is not here goes
# without one, and a request that already brings its own DPoP header is left as it is.
KEY_OF: dict[str, dict] = {}


# A request with this header goes without a proof: it is how a test sends none. It never reaches the service.
WITHOUT_PROOF = "X-Test-Without-DPoP"


@pytest.fixture(autouse=True)
def dpop_proofs(monkeypatch):
    """
        Each request of the test client gets the proof a client would sign: with `Authorization: DPoP <a token of
        KEY_OF>`, for that token; at POST /oauth/token, with the key of the assertion, if it was built here. The token
        that request gets goes to KEY_OF, so the requests that carry it are signed too.
    """
    from urllib.parse import parse_qs

    import httpx

    from shared.auth.client import dpop_proof

    def prove(request) -> str | None:
        """Adds the proof to the request; the assertion of a token request, to remember the key of its token."""
        if WITHOUT_PROOF.lower() in request.headers:
            del request.headers[WITHOUT_PROOF]
            return None
        if "dpop" in request.headers:
            return None
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme == "DPoP" and token in KEY_OF:
            request.headers["DPoP"] = dpop_proof(KEY_OF[token], request.method, str(request.url), token)
        elif request.method == "POST" and request.url.path == "/oauth/token":
            assertion = (parse_qs(request.content.decode("ascii", "replace")).get("client_assertion") or [None])[0]
            if assertion in KEY_OF:
                request.headers["DPoP"] = dpop_proof(KEY_OF[assertion], "POST", str(request.url))
                return assertion
        return None

    def remember(assertion, response):
        if assertion is not None and response.status_code == 200:
            KEY_OF[response.json()["access_token"]] = KEY_OF[assertion]

    send, send_async = httpx.Client.send, httpx.AsyncClient.send

    def signing(self, request, *args, **kwargs):
        assertion = prove(request)
        response = send(self, request, *args, **kwargs)
        remember(assertion, response)
        return response

    async def signing_async(self, request, *args, **kwargs):
        assertion = prove(request)
        response = await send_async(self, request, *args, **kwargs)
        remember(assertion, response)
        return response

    monkeypatch.setattr(httpx.Client, "send", signing)
    monkeypatch.setattr(httpx.AsyncClient, "send", signing_async)


class Caller:
    """A registered client and its private key: everything needed to call the service as that client."""

    def __init__(self, client, key: dict):
        self.client_id = client.uuid
        self.key = key
        self.kid = client.signingKeys[0]["kid"]

    def assertion(self, **changes) -> str:
        """An assertion for POST /oauth/token, built the documented way; `changes` go to build_assertion."""
        return signed_assertion(self.key, self.client_id, **changes)

    def access_token(self, **changes) -> str:
        """An access token from POST /oauth/token, as a client gets it (ADR-0025)."""
        return access_token_for(self.assertion(**changes))

    @property
    def auth(self) -> dict:
        """The headers of a request with a new access token, or with none the API takes if the gateway gave none."""
        return bearer(self.assertion())

    def adding(self, new_key: dict, **proof_changes) -> dict:
        """What POST /keys takes to add `new_key` (private): the headers and the body, with the proof it signs."""
        from shared.auth.client import access_token_jti, key_proof, public_part

        token = self.access_token()
        proof = key_proof(new_key, self.client_id, GATEWAY_AUDIENCE, access_token_jti(token), **proof_changes)
        return {"headers": {"Authorization": f"DPoP {token}"},
                "json": {"signingKey": public_part(new_key), "proof": proof}}


def signed_assertion(key: dict, client_id, **changes) -> str:
    """An assertion signed with `key` for `client_id`, encrypted to the gateway's key."""
    from shared.auth.client import build_assertion

    gateway_key = gateway_keys.GatewayKeys.load(GATEWAY_KEYS_FILE).public_set()["keys"][0]
    assertion = build_assertion(key, client_id, GATEWAY_AUDIENCE, gateway_key, **changes)
    KEY_OF[assertion] = key
    return assertion


def request_token(assertion: str, headers: dict | None = None):
    """
        What POST /oauth/token answers to that assertion: with the DPoP proof of the key that signed it, if it was
        built here, unless `headers` brings its own DPoP header or WITHOUT_PROOF.
    """
    from fastapi.testclient import TestClient

    import app as app_module
    from shared.auth.client import token_request

    return TestClient(app_module.app).post("/oauth/token", data=token_request(assertion), headers=headers or {})


def access_token_for(assertion: str) -> str:
    response = request_token(assertion)
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def bearer(assertion: str) -> dict:
    """
        The headers of a request with the access token that assertion gets; the test client adds its DPoP proof. If
        it gets none, a token the API turns away: a test of a key that is not good expects the 401 of the route, as a
        client would get it.
    """
    response = request_token(assertion)
    token = response.json()["access_token"] if response.status_code == 200 else "no.access.token"
    return {"Authorization": f"DPoP {token}"}


@pytest.fixture
def register():
    """Returns a function that registers a client with a new key pair and gives back its Caller."""
    from shared.auth.client import new_signing_key, public_part
    from auth.client_keys import public_signing_key
    from repository import register_client

    def new_caller(name: str) -> Caller:
        key = new_signing_key()
        return Caller(register_client(name, public_signing_key(public_part(key))), key)

    return new_caller


@pytest.fixture
def operator():
    """The operator, with a signing key registered the way the server command does it."""
    from shared.auth.client import new_signing_key, public_part
    from auth.client_keys import public_signing_key
    from repository import add_signing_key, operator_client

    key = new_signing_key()
    return Caller(add_signing_key(operator_client().uuid, public_signing_key(public_part(key))), key)


@pytest.fixture
def another():
    """
        Returns a function that gives a copy of a notification with new identifiers of Meta: the next message of
        the same person. The same notification sent twice is Meta sending it again, and the history skips it.
    """
    import copy
    from uuid import uuid4

    def renamed(payload: dict) -> dict:
        fresh = copy.deepcopy(payload)
        suffix = uuid4().hex[:8]
        for entry in fresh.get("entry", []):
            for change in entry.get("changes", []):
                for message in change.get("value", {}).get("messages", []):
                    message["id"] = f"{message.get('id')}.{suffix}"
            for event in entry.get("messaging", []):
                if isinstance(event.get("message"), dict):
                    event["message"]["mid"] = f"{event['message'].get('mid')}.{suffix}"
        return fresh

    return renamed


# The security suite (Gate 3, HIG-134): tests/security/, and the unit tests that check a control of the OWASP matrix
# (.ai-dlc/owasp-mapping.md). `pytest -m security` runs them alone, and the CI counts them apart.
SECURITY_MODULES = {
    "test_authentication", "test_bot_config", "test_bot_state", "test_bot_versions", "test_client_encryption",
    "test_client_isolation",
    "test_data_access_boundary", "test_dpop", "test_intent_destinations", "test_intent_request", "test_key_proof",
    "test_logs_and_errors",
    "test_owasp_mapping", "test_people", "test_request_limits", "test_role_matrix", "test_service_credential",
    "test_token_limits", "test_webhook_security",
}


def pytest_collection_modifyitems(items):
    for item in items:
        if item.path.parent.name == "security" or item.path.stem in SECURITY_MODULES:
            item.add_marker(pytest.mark.security)
