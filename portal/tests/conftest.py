# -*- coding: utf-8 -*-
"""
    What the tests of hive-portal stand on: a Zitadel and a hive-api of their own, local HTTP servers that answer what
    the real ones would, and keep what they were sent.

    - Zitadel: its discovery, its JWKS, and a token endpoint that checks the client secret, the code and its PKCE
      verifier, and gives an ID token signed with its RSA key, with the nonce of the login.
    - hive-api: the exchange of an ID token at /oauth/token, which checks that it comes with a DPoP proof, and any
      other route, which says what it was sent.
"""
import base64
import hashlib
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest
from jwcrypto import jwk, jws

CLIENT_ID, CLIENT_SECRET = "397204681903833093", "the-client-secret-of-the-test"
PORTAL = "https://hive-app.test"


class Servers:
    zitadel: str = ""
    hive: str = ""


def _serve(handler) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


class FakeZitadel(BaseHTTPRequestHandler):
    key = jwk.JWK.generate(kty="RSA", size=2048, kid="zitadel-1", use="sig")
    codes: dict = {}          # code -> the login it answers: challenge, nonce, subject, amr
    token_calls: list = []
    fail: tuple | None = None

    def log_message(self, *_args):
        pass

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # pylint: disable=invalid-name
        issuer = Servers.zitadel
        if self.path == "/.well-known/openid-configuration":
            return self._send(200, {"issuer": issuer, "authorization_endpoint": f"{issuer}/oauth/v2/authorize",
                                    "token_endpoint": f"{issuer}/oauth/v2/token",
                                    "end_session_endpoint": f"{issuer}/oidc/v1/end_session",
                                    "jwks_uri": f"{issuer}/oauth/v2/keys"})
        if self.path == "/oauth/v2/keys":
            return self._send(200, {"keys": [json.loads(FakeZitadel.key.export_public())]})
        return self._send(404, {})

    def do_POST(self):  # pylint: disable=invalid-name
        form = {name: values[0] for name, values in
                parse_qs(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()).items()}
        FakeZitadel.token_calls.append((self.headers.get("Authorization"), form))
        if FakeZitadel.fail:
            return self._send(*FakeZitadel.fail)
        expected = "Basic " + base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
        if self.headers.get("Authorization") != expected:
            return self._send(401, {"error": "invalid_client"})
        login = FakeZitadel.codes.pop(form.get("code"), None)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(form.get("code_verifier", "").encode()).digest()
                                             ).rstrip(b"=").decode()
        if login is None or challenge != login["challenge"] or form.get("redirect_uri") != f"{PORTAL}/auth/callback":
            return self._send(400, {"error": "invalid_grant", "error_description": "a code of somebody@else.test"})
        return self._send(200, {"access_token": "zitadel-access-token-never-used", "token_type": "Bearer",
                                "id_token": id_token(login["nonce"], login["subject"], login["amr"])})


def id_token(nonce, subject="312345678901234567", amr=("pwd", "mfa", "otp"), **changes) -> str:
    now = int(time.time())
    claims = {"iss": Servers.zitadel, "sub": subject, "aud": [CLIENT_ID], "azp": CLIENT_ID, "iat": now,
              "exp": now + 43200, "nonce": nonce, "amr": list(amr), **changes}
    signed = jws.JWS(json.dumps(claims).encode())
    signed.add_signature(FakeZitadel.key, alg="RS256", protected={"alg": "RS256", "kid": "zitadel-1", "typ": "JWT"})
    return signed.serialize(compact=True)


def hive_token(subject: str, lifetime: int = 600, role: str = "cliente-admin") -> str:
    """A token of Hive as the portal reads it: only its payload matters here, hive-api is the one that checks it."""
    payload = {"client_id": "ac3cd51f-b641-4bec-b234-770922174cf4", "hive_user": f"person-of-{subject}",
               "hive_user_role": role, "exp": int(time.time()) + lifetime, "jti": secrets.token_hex(8)}
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"eyJhbGciOiJFUzI1NiJ9.{encoded}.signature-of-hive"


class FakeHive(BaseHTTPRequestHandler):
    exchanges: list = []
    seen: list = []
    refuse: str | None = None      # an error of OAuth for the next exchanges
    lifetime: int = 600
    answer: tuple = (200, {"ok": True})
    tokens_refused: set = set()     # tokens the API answers 401 to, as after a restart of the portal

    def log_message(self, *_args):
        pass

    def _send(self, code, body, headers=None):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Set-Cookie", "hive-api-cookie=never-to-the-browser")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        parts = urlsplit(self.path)
        if parts.path == "/oauth/token":
            form = {name: values[0] for name, values in parse_qs(body.decode()).items()}
            FakeHive.exchanges.append((self.headers.get("DPoP"), form))
            if not self.headers.get("DPoP"):
                return self._send(400, {"error": "invalid_dpop_proof"})
            if FakeHive.refuse:
                return self._send(400, {"error": FakeHive.refuse})
            subject = json.loads(base64.urlsafe_b64decode(form["subject_token"].split(".")[1] + "==")).get("sub")
            return self._send(200, {"access_token": hive_token(subject, FakeHive.lifetime), "token_type": "DPoP",
                                    "expires_in": FakeHive.lifetime})
        token = (self.headers.get("Authorization") or "").removeprefix("DPoP ")
        FakeHive.seen.append({"method": self.command, "path": parts.path, "query": parts.query,
                              "headers": dict(self.headers.items()), "body": body.decode(errors="replace"),
                              "token": token})
        if token in FakeHive.tokens_refused:
            return self._send(401, {"code": 401})
        code, content = FakeHive.answer
        return self._send(code, content, {"Retry-After": "7"} if code == 429 else None)

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _handle  # pylint: disable=invalid-name


@pytest.fixture(scope="session", autouse=True)
def servers():
    zitadel, Servers.zitadel = _serve(FakeZitadel)
    hive, Servers.hive = _serve(FakeHive)
    yield Servers
    zitadel.shutdown()
    hive.shutdown()


@pytest.fixture(autouse=True)
def clean():
    FakeZitadel.codes, FakeZitadel.token_calls, FakeZitadel.fail = {}, [], None
    FakeHive.exchanges, FakeHive.seen, FakeHive.refuse, FakeHive.lifetime = [], [], None, 600
    FakeHive.answer, FakeHive.tokens_refused = (200, {"ok": True}), set()
