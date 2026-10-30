# -*- coding: utf-8 -*-
"""
    The stack of the end-to-end test (HIG-135), brought up as an operator would on midgard, and taken down after:

    1. the secrets, born in a temporary directory: a CA and the certificate of NATS and OpenBao, a password per
       Bifröst user, the gateway's keys, the operator's key; the portal's client secret in Zitadel and its sessions'
       key, Zitadel's signing key and the token of hive-api's service account there (HIG-141);
    2. OpenBao, initialized and unsealed, with what Andvari has: transit `clientes` and the AppRole `gateway`, the KV
       `motor` and the AppRoles `motor-claves`, `motor-coordinator` and `motor-notifier`;
    3. Bifröst with its own configuration and its own provisioning (higerotech/bifrost, in $BIFROST_DIR);
    4. the gateway and the portal; the operator's key; a client, its chatbot and its credentials; the engine's keys
       for the client;
    5. the engine.

        E2E=1 BIFROST_DIR=../bifrost pytest tests/e2e         # E2E_KEEP=1 leaves it up; E2E_NO_BUILD=1 skips building

    Without E2E=1 nothing of this runs: the unit suite collects tests/ and has no Docker. The fixtures are in
    conftest.py. tests/e2e is a package, so that its conftest is not the `conftest` that tests/unit imports.
"""
import base64
import datetime
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
COMPOSE = HERE / "compose.yaml"
NATS_BOX = "natsio/nats-box:0.20.0-nonroot@sha256:be25666441c3aee65193aba33d60ff2f06ede7ee9eba58864324b33d7eb94fea"
API, WEBHOOK, BAO, NATS_MONITOR, FAKES = ("http://127.0.0.1:15002", "http://127.0.0.1:15003", "https://127.0.0.1:18200",
                                          "http://127.0.0.1:18222", "http://127.0.0.1:18080")
AUDIENCE = "https://hive-api.e2e.test"
# The portal, where the browser reaches it: http://localhost is a secure context for its __Host- cookies. And Zitadel,
# by the name the containers know it by, which the browser maps to 127.0.0.1 (compose.yaml, x-zitadel).
PORTAL, ZITADEL = "http://localhost:15004", "http://zitadel.e2e.test:18090"
ZITADEL_HOST = "zitadel.e2e.test"
PHONE_ID = "100000000000001"
BIFROST_USERS = ["admin", "webhook", "ingesta", "portal", "gestion", "classifier", "coordinator", "responder",
                 "notifier"]
ENGINE_ROLES = {"motor-claves": 'path "motor/data/clientes/+/+" { capabilities = ["create", "read"] }',
                "motor-coordinator": 'path "motor/data/clientes/+/coordinator" { capabilities = ["read"] }',
                "motor-notifier": 'path "motor/data/clientes/+/notifier" { capabilities = ["read"] }'}
GATEWAY_POLICY = ('path "transit/datakey/plaintext/clientes" { capabilities = ["update"] }\n'
                  'path "transit/decrypt/clientes" { capabilities = ["update"] }')


def wanted() -> bool:
    return os.environ.get("E2E") == "1"


def until(what: str, check, timeout: float = 120, every: float = 1.0):
    """Waits for `check()` to give something truthy, and gives it back; or fails saying what it waited for."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            found = check()
            if found:
                return found
        except (requests.RequestException, OSError, ValueError, KeyError) as e:
            last = e
        time.sleep(every)
    raise AssertionError(f"Waited {timeout:.0f} s for {what}" + (f" (last: {type(last).__name__})" if last else ""))


def notification(number: str, name: str, message_id: str, text: str) -> bytes:
    """What Meta sends the webhook when a person writes to the chatbot's number."""
    return json.dumps({"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{
        "field": "messages", "value": {
            "messaging_product": "whatsapp",
            "metadata": {"display_phone_number": "12025550101", "phone_number_id": PHONE_ID},
            "contacts": [{"profile": {"name": name}, "wa_id": number}],
            "messages": [{"from": number, "id": message_id, "timestamp": str(int(time.time())), "type": "text",
                          "text": {"body": text}}]}}]}]}).encode()


def deliver(bot: str, app_secret: str, raw: bytes) -> requests.Response:
    """A notification to the webhook of the chatbot, signed as Meta signs it."""
    signature = "sha256=" + hmac.new(app_secret.encode(), raw, hashlib.sha256).hexdigest()
    return requests.post(f"{WEBHOOK}/chat/{bot}", data=raw, timeout=10,
                         headers={"Content-Type": "application/json", "X-Hub-Signature-256": signature})


def seen() -> dict:
    """What Meta, the LLM and Zitadel were asked (fakes.py)."""
    return requests.get(f"{FAKES}/_seen", timeout=5).json()


class Stack:
    def __init__(self, directory: Path):
        self.dir = directory
        self.env = {**os.environ, "E2E_DIR": str(directory), "BIFROST_DIR": str(Path(os.environ["BIFROST_DIR"]).resolve()),
                    "COMPOSE_PROJECT_NAME": "hive-e2e",
                    # tests/conftest.py sets the unit tests' own in this process: compose would put it in the engine.
                    "GATEWAY_AUDIENCE": AUDIENCE}

    # --- Docker -------------------------------------------------------------------------------------------------------

    def compose(self, *args, check=True, profile=None) -> subprocess.CompletedProcess:
        command = ["docker", "compose", "-f", str(COMPOSE)] + (["--profile", profile] if profile else []) + list(args)
        done = subprocess.run(command, env=self.env, cwd=HERE, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", check=False)
        if check and done.returncode != 0:
            raise AssertionError(f"docker compose {' '.join(args)} failed:\n{done.stderr[-3000:]}")
        return done

    def ops(self, *args, extra_env: dict | None = None) -> dict | list:
        """One operation of the gateway image, as the operator runs it, and the JSON it prints."""
        flags = [item for name, value in (extra_env or {}).items() for item in ("-e", f"{name}={value}")]
        done = self.compose("run", "--rm", "--no-deps", *flags, "ops", *args, profile="operations")
        # The JSON is the first object or list that starts a line; the log may come before and after it.
        output = "\n" + done.stdout
        for start in (index + 1 for index in range(len(output)) if output.startswith(("\n{", "\n["), index)):
            try:
                return json.JSONDecoder().raw_decode(output[start:])[0]
            except ValueError:
                continue
        raise AssertionError(f"{' '.join(args)} printed no JSON:\n{done.stdout[-2000:]}\n{done.stderr[-2000:]}")

    def logs(self) -> str:
        return self.compose("logs", "--no-color", "--tail", "80", check=False).stdout

    def running(self) -> dict:
        done = self.compose("ps", "--all", "--format", "json", check=False)
        rows = [json.loads(line) for line in done.stdout.splitlines() if line.strip().startswith("{")]
        return {row["Service"]: row["State"] for row in rows}

    # --- The secrets --------------------------------------------------------------------------------------------------

    def write(self, name: str, content: str) -> Path:
        path = self.dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        # Read by the containers' users (10001 in the gateway and the engine, others in the rest): test secrets only.
        path.chmod(0o644)
        return path

    def env_file(self, name: str, values: dict) -> None:
        self.write(name, "".join(f"{key}={value}\n" for key, value in values.items()))

    def certificates(self) -> None:
        from cryptography import x509  # pylint: disable=import-outside-toplevel
        from cryptography.hazmat.primitives import hashes, serialization  # pylint: disable=import-outside-toplevel
        from cryptography.hazmat.primitives.asymmetric import ec  # pylint: disable=import-outside-toplevel
        from cryptography.x509.oid import NameOID  # pylint: disable=import-outside-toplevel
        import ipaddress  # pylint: disable=import-outside-toplevel

        now = datetime.datetime.now(datetime.timezone.utc)
        ca_key, server_key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
        ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Hive e2e CA")])
        ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
              .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
              .not_valid_after(now + datetime.timedelta(days=1))
              .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
              .sign(ca_key, hashes.SHA256()))
        names = [x509.DNSName(name) for name in ("nats", "openbao", "localhost")]
        server = (x509.CertificateBuilder()
                  .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "hive-e2e")]))
                  .issuer_name(ca_name).public_key(server_key.public_key())
                  .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
                  .not_valid_after(now + datetime.timedelta(days=1))
                  .add_extension(x509.SubjectAlternativeName(names + [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                                 critical=False)
                  .sign(ca_key, hashes.SHA256()))
        pem = serialization.Encoding.PEM
        self.write("tls/ca.crt", ca.public_bytes(pem).decode())
        self.write("tls/servidor.crt", server.public_bytes(pem).decode())
        self.write("tls/servidor.key", server_key.private_bytes(
            pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode())

    def make_secrets(self) -> None:
        from shared.auth import gateway_keys  # pylint: disable=import-outside-toplevel
        from shared.auth.client import new_signing_key, public_part  # pylint: disable=import-outside-toplevel

        self.certificates()
        (self.dir / "openbao.hcl").write_bytes((HERE / "openbao.hcl").read_bytes())
        # Hex, as Bifröst's instalar.sh makes them: NATS parses the value of a variable, and a `-` breaks it.
        self.passwords = {user: secrets.token_hex(24) for user in BIFROST_USERS}
        self.env_file("bifrost.env", {**{f"BIFROST_CLAVE_{user.upper()}": password
                                         for user, password in self.passwords.items()},
                                      "BIFROST_CLAVE_ALMACEN": secrets.token_hex(32)})
        for user in BIFROST_USERS[1:]:
            self.env_file(f"nats-{user}.env", {"NATS_URL": "tls://nats:4222", "NATS_USER": user,
                                                "NATS_PASSWORD": self.passwords[user], "NATS_CA": "/run/e2e/tls/ca.crt",
                                                "NATS_INBOX_PREFIX": f"_INBOX_{user}"})
        self.mongo_password = secrets.token_urlsafe(24)
        self.write("mongo-password", self.mongo_password)
        self.rabbit_password = secrets.token_urlsafe(24)
        self.env_file("rabbitmq.env", {"RABBITMQ_DEFAULT_USER": "hive", "RABBITMQ_DEFAULT_PASS": self.rabbit_password})
        self.write("llm-api-key", secrets.token_urlsafe(24))

        keys = [gateway_keys.new_key(), gateway_keys.new_signing_key()]
        gateway_keys.write_file(str(self.dir / "gateway-keys.json"), keys)
        (self.dir / "gateway-keys.json").chmod(0o644)
        self.gateway_public = [gateway_keys.public_part(key) for key in keys if not gateway_keys.is_signing(key)]
        self.write("gateway-public.json", json.dumps({"keys": self.gateway_public}))

        self.operator_key = new_signing_key()
        self.write("operador.json", json.dumps(self.operator_key))
        self.write("operador-publica.json", json.dumps(public_part(self.operator_key)))
        self.zitadel_secrets()

    def zitadel_secrets(self) -> None:
        """The portal's client secret in Zitadel and its sessions' key; Zitadel's key; hive-api's service account."""
        from cryptography.hazmat.primitives.asymmetric import rsa  # pylint: disable=import-outside-toplevel

        self.write("oidc-client-secret", secrets.token_urlsafe(32))
        self.write("session-key", base64.b64encode(secrets.token_bytes(32)).decode())
        self.write("zitadel-token", secrets.token_urlsafe(32))
        numbers = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_numbers()
        self.write("zitadel-key.json", json.dumps({"kid": "zitadel-e2e-1", "n": f"{numbers.public_numbers.n:x}",
                                                   "e": f"{numbers.public_numbers.e:x}", "d": f"{numbers.d:x}"}))

    def gateway_env(self, gateway_role: str, engine_keys_role: str) -> None:
        self.env_file("gateway.env", {
            "ENVIRONMENT": "production", "VERSION": "e2e",
            "GATEWAY_AUDIENCE": AUDIENCE,
            "ANDVARI_ADDR": "https://openbao:8200", "ANDVARI_CA_FILE": "/run/e2e/tls/ca.crt",
            "ANDVARI_ROLE_ID": gateway_role, "ANDVARI_SECRET_ID_FILE": "/run/e2e/andvari-secret-id",
            "ENGINE_KEYS_ROLE_ID": engine_keys_role,
            "DB_NAME": "hive_e2e", "DB_HOST": "mongodb://mongo:27017/?authSource=admin", "DB_USERNAME": "hive",
            "DB_PASSWORD": self.mongo_password,
            "QMS_SERVER": "rabbitmq", "QMS_USER": "hive", "QMS_PASSWORD": self.rabbit_password,
            # What the old app still asks for and nothing in this journey uses.
            "CLIENT_ID": "e2e", "NATIONAL_ID_URL": "http://fakes:8080/none", "SENDER_EMAIL": "e2e@example.com",
            "SENDER_PASSWORD": "unused", "GMAIL_SERVER_URL": "fakes", "GMAIL_SERVER_PORT": "1",
            "EXPIRATION_TIME": "60", "ACTIVATION_CODE_LENGTH": "6", "PASSWORD_LENGTH": "12", "KEY_SIZE": "2048",
            "PRIVATE_KEY_FILENAME": "certs/{UUID}_private.pem", "PUBLIC_KEY_FILENAME": "certs/{UUID}_public.pem",
            "VERIFY_TOKEN": "unused",
            "FACEBOOK_GRAPH_URL": "http://fakes:8080/{fb_id}?access_token={ACCESSTOKEN}",
            "FACEBOOK_WHATSAPP_MESSAGE": "http://fakes:8080/{version}/{PhoneNumberId}/messages",
            "FACEBOOK_GRAPH_VERSION": "v26.0", "META_GRAPH_URL": "http://fakes:8080",
        })

    # --- OpenBao, as Andvari ------------------------------------------------------------------------------------------

    def bao(self, method: str, path: str, body: dict | None = None, token: str | None = None) -> dict:
        token = token or getattr(self, "root_token", None)
        answer = requests.request(method, f"{BAO}/v1/{path}", json=body, timeout=10,
                                  verify=str(self.dir / "tls" / "ca.crt"),
                                  headers={"X-Vault-Token": token} if token else {})
        if answer.status_code >= 400:
            raise AssertionError(f"OpenBao {method} {path}: {answer.status_code} {answer.text[:300]}")
        return answer.json() if answer.content else {}

    def approle(self, name: str, policy: str) -> str:
        """A policy and its AppRole; writes the secret id to <name>-secret-id and gives back the role id."""
        self.bao("PUT", f"sys/policies/acl/{name}", {"policy": policy})
        self.bao("POST", f"auth/approle/role/{name}", {"token_policies": name, "token_ttl": "20m"})
        secret_id = self.bao("POST", f"auth/approle/role/{name}/secret-id")["data"]["secret_id"]
        self.write(f"{name}-secret-id", secret_id)
        return self.bao("GET", f"auth/approle/role/{name}/role-id")["data"]["role_id"]

    def andvari(self) -> tuple[str, str]:
        until("OpenBao", lambda: requests.get(f"{BAO}/v1/sys/health", timeout=3,
                                              verify=str(self.dir / "tls" / "ca.crt")).status_code in (200, 429, 501, 503))
        init = self.bao("POST", "sys/init", {"secret_shares": 1, "secret_threshold": 1})
        self.root_token = init["root_token"]
        self.bao("PUT", "sys/unseal", {"key": init["keys_base64"][0]})
        self.bao("POST", "sys/mounts/transit", {"type": "transit"})
        self.bao("POST", "transit/keys/clientes", {"type": "aes256-gcm96", "derived": True})
        self.bao("POST", "sys/auth/approle", {"type": "approle"})
        gateway_role = self.approle("gateway", GATEWAY_POLICY)
        (self.dir / "gateway-secret-id").rename(self.dir / "andvari-secret-id")
        self.bao("POST", "sys/mounts/motor", {"type": "kv", "options": {"version": "2"}})
        self.bao("POST", "motor/config", {"cas_required": True})
        roles = {name: self.approle(name, policy) for name, policy in ENGINE_ROLES.items()}
        for worker in ("coordinator", "notifier"):
            (self.dir / f"motor-{worker}-secret-id").rename(self.dir / f"{worker}-secret-id")
            self.env_file(f"role-{worker}.env", {"ENGINE_ROLE_ID": roles[f"motor-{worker}"]})
        return gateway_role, roles["motor-claves"]

    # --- Bifröst ------------------------------------------------------------------------------------------------------

    def bifrost(self) -> None:
        until("Bifröst", lambda: requests.get(f"{NATS_MONITOR}/healthz?js-enabled-only=true", timeout=3).ok)
        done = subprocess.run(
            ["docker", "run", "--rm", "--network", "hive-e2e_e2e",
             "-e", "NATS_URL=tls://nats:4222", "-e", "NATS_USER=admin", "-e", f"NATS_PASSWORD={self.passwords['admin']}",
             "-e", "NATS_CA=/ca.crt",
             "-v", f"{self.dir / 'tls' / 'ca.crt'}:/ca.crt:ro",
             "-v", f"{Path(self.env['BIFROST_DIR']) / 'jetstream'}:/jetstream:ro",
             NATS_BOX, "sh", "/jetstream/aprovisionar.sh"],
            capture_output=True, text=True, check=False, env={**os.environ, "MSYS_NO_PATHCONV": "1"})
        if done.returncode != 0:
            raise AssertionError(f"Bifröst's provisioning failed:\n{done.stdout[-2000:]}\n{done.stderr[-2000:]}")

    def nats(self, *args: str) -> str:
        """A command of the nats CLI against Bifröst, as its admin, and what it printed."""
        done = subprocess.run(
            ["docker", "run", "--rm", "--network", "hive-e2e_e2e",
             "-e", "NATS_URL=tls://nats:4222", "-e", "NATS_USER=admin", "-e", f"NATS_PASSWORD={self.passwords['admin']}",
             "-e", "NATS_CA=/ca.crt", "-v", f"{self.dir / 'tls' / 'ca.crt'}:/ca.crt:ro", NATS_BOX, "nats", *args],
            capture_output=True, check=False, env={**os.environ, "MSYS_NO_PATHCONV": "1"})
        if done.returncode != 0:
            raise AssertionError(f"nats {' '.join(args)} failed: {done.stderr[-1000:]!r}")
        return done.stdout.decode("latin-1")


class Hive:
    """A client of the management API, as an integration would be: its key, its token with DPoP, and its calls."""

    def __init__(self, key: dict, client_id: str, gateway_key: dict):
        self.key, self.client_id, self.gateway_key = key, client_id, gateway_key
        self.token = None

    def _token(self) -> str:
        from shared.auth.client import build_assertion, dpop_proof, token_request  # pylint: disable=import-outside-toplevel
        url = f"{API}/oauth/token"
        answer = requests.post(url, data=token_request(build_assertion(self.key, self.client_id, AUDIENCE,
                                                                       self.gateway_key)),
                               headers={"DPoP": dpop_proof(self.key, "POST", url)}, timeout=10)
        assert answer.status_code == 200, (answer.status_code, answer.text[:300])
        return answer.json()["access_token"]

    def call(self, method: str, path: str, body=None, expect: int = 200) -> dict:
        from shared.auth.client import dpop_proof  # pylint: disable=import-outside-toplevel
        self.token = self.token or self._token()
        url = f"{API}{path}"
        answer = requests.request(method, url, json=body, timeout=15, headers={
            "Authorization": f"DPoP {self.token}", "DPoP": dpop_proof(self.key, method, url, self.token)})
        assert answer.status_code == expect, (method, path, answer.status_code, answer.text[:300])
        return answer.json().get("data") or {}
