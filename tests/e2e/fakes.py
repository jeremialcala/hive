# -*- coding: utf-8 -*-
"""
    Meta, the LLM and Zitadel of the end-to-end test, with nothing but the standard library: they run in a bare python
    image, and the test reads back what each one was asked.

    On :8080, Meta and the LLM:

        POST /v26.0/{phone_number_id}/messages        Meta's Cloud API: a text, a template, a «read» or a «typing»
        POST /v26.0/{waba_id}/message_templates       a new template, which stays in review (PENDING)
        GET  /v26.0/{waba_id}/message_templates       the templates of the account, in one page
        POST /v1/chat/completions                     the LLM: the classifier (a verdict, as JSON) or the responder
        POST /_approve                                Meta approves the template of that name, for the test
        GET  /_seen                                   what each one was asked, for the test

    On :18090, Zitadel (HIG-141), as the portal and hive-api reach it, at http://zitadel.e2e.test:18090 for the browser
    and the containers alike:

        GET  /.well-known/openid-configuration        its discovery
        GET  /oauth/v2/keys                           its JWKS: one RSA key, made by the test (zitadel-key.json)
        GET  /oauth/v2/authorize                      its login page: the person's login name, and nothing more
        POST /oauth/v2/authorize                      the login: a code for the portal, back to its redirect URI
        POST /oauth/v2/token                          the ID token for the code, with the client secret and PKCE
        GET  /oidc/v1/end_session                     the logout, back to the portal
        POST /v2/users, /v2/users/human,              the User Service v2 that hive-api invites the people with, with
             /v2/users/{id}/invite_code               the token of its service account

    The ID token is RS256, signed here by hand (RFC 8017, RSASSA-PKCS1-v1_5): the image has no library for it, and
    jwcrypto checks it in the portal and in hive-api as it checks Zitadel's.
"""
import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

REPLY = "Hola Ana, sí: abrimos mañana a las 9."
VERDICT = {"permitido": True, "motivo": "ninguno", "etiqueta": "pregunta", "confianza": 0.9, "entidades": []}
# What the evaluator says of the answer (HIG-146): it complies.
JUDGEMENT = {"cumple": True, "criterio": "ninguno", "regla": 0, "motivo": "Cumple."}
MESSAGES = re.compile(r"^/v\d{2}\.0/(\d+)/messages$")
TEMPLATES = re.compile(r"^/v\d{2}\.0/(\d+)/message_templates$")

seen = {"meta": [], "llm": [], "templates": [], "zitadel": []}
templates: dict = {}  # name -> the template, as Meta lists it
lock = threading.Lock()


def _read(handler) -> bytes:
    return handler.rfile.read(int(handler.headers.get("Content-Length", 0)))


def reply(code: int, body: dict | None = None, headers: dict | None = None, page: str | None = None) -> tuple:
    """What to answer: JSON, or a page of HTML."""
    return code, body, headers, page


class Answering(BaseHTTPRequestHandler):
    """Each request, answered with what its _get or _post gives back."""
    def log_message(self, *_args):
        pass

    def _answer(self, answer: tuple) -> None:
        code, body, headers, page = answer
        data = page.encode() if page is not None else json.dumps(body if body is not None else {}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8" if page is not None else "application/json")
        self.send_header("Content-Length", str(len(data)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        self._answer(self._get())

    def do_POST(self):  # noqa: N802
        self._answer(self._post())

    def _get(self) -> tuple:
        return reply(404, {})

    def _post(self) -> tuple:
        return reply(404, {})


class Fakes(Answering):
    """Meta and the LLM."""

    def _get(self) -> tuple:
        path = urlsplit(self.path).path
        if path == "/_seen":
            with lock:
                return reply(200, json.loads(json.dumps(seen)))
        if TEMPLATES.match(path):
            with lock:
                return reply(200, {"data": list(templates.values()), "paging": {}})
        return reply(404, {"error": {"code": 100}})

    def _post(self) -> tuple:
        body = json.loads(_read(self) or b"{}")
        authorization = self.headers.get("Authorization", "")
        found = MESSAGES.match(self.path)
        if found:
            with lock:
                seen["meta"].append({"phoneId": found.group(1), "authorization": authorization, "body": body})
            if body.get("status") == "read":
                return reply(200, {"success": True})
            return reply(200, {"messaging_product": "whatsapp", "messages": [
                {"id": f"wamid.E2E{len(seen['meta'])}"}]})
        found = TEMPLATES.match(self.path)
        if found:
            with lock:
                made = {"id": str(1_000_000_000_000_000 + len(templates) + 1), "name": body.get("name"),
                        "language": body.get("language"), "category": body.get("category"),
                        "components": body.get("components") or [], "status": "PENDING"}
                templates[made["name"]] = made
                seen["templates"].append({"waba": found.group(1), "authorization": authorization, "body": body})
            return reply(200, {"id": made["id"], "status": "PENDING", "category": made["category"]})
        if self.path == "/_approve":
            with lock:
                templates[body["name"]]["status"] = "APPROVED"
            return reply(200, {})
        if self.path == "/v1/chat/completions":
            # The classifier and the evaluator ask for a JSON schema, each its own; the responder, for text.
            schema = ((body.get("response_format") or {}).get("json_schema") or {}).get("schema") or {}
            kind = ("evaluator" if "cumple" in schema.get("properties", {}) else
                    "classifier" if schema else "responder")
            with lock:
                seen["llm"].append({"kind": kind, "authorization": authorization})
            content = {"classifier": json.dumps(VERDICT), "evaluator": json.dumps(JUDGEMENT), "responder": REPLY}[kind]
            return reply(200, {"choices": [{"message": {"role": "assistant", "content": content},
                                            "finish_reason": "stop"}],
                               "usage": {"prompt_tokens": 10, "completion_tokens": 10}})
        return reply(404, {"error": {"code": 100}})


# --- Zitadel ----------------------------------------------------------------------------------------------------------

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _int_bytes(number: int) -> bytes:
    return number.to_bytes((number.bit_length() + 7) // 8, "big")


# DigestInfo of SHA-256 (RFC 8017, section 9.2, note 1).
SHA256_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


def _read_file(path: str) -> str:
    with open(path, encoding="utf-8") as file:
        return file.read().strip()


# What the test made for Zitadel (e2e_stack.py, Stack.zitadel_secrets).
CLIENT_SECRET_FILE, SERVICE_TOKEN_FILE, KEY_FILE = ("/run/e2e/oidc-client-secret", "/run/e2e/zitadel-token",
                                                    "/run/e2e/zitadel-key.json")


class Zitadel(Answering):
    """Zitadel: the IdP the portal signs people in with, and the users hive-api invites."""
    issuer = os.environ.get("IDP_ISSUER", "")
    client_id = os.environ.get("OIDC_CLIENT_ID", "")
    redirect_uri = os.environ.get("PORTAL_URL", "").rstrip("/") + "/auth/callback"
    portal = os.environ.get("PORTAL_URL", "").rstrip("/")
    organization = os.environ.get("IDP_ORGANIZATION_ID", "")
    users: dict = {}      # user id -> {"email", "invited"}
    requests: dict = {}   # id of an authorization request -> its parameters
    codes: dict = {}      # code -> the login it answers

    @classmethod
    def key(cls) -> dict:
        return json.loads(_read_file(KEY_FILE))

    @classmethod
    def sign(cls, claims: dict) -> str:
        key = cls.key()
        n, d = int(key["n"], 16), int(key["d"], 16)
        header = {"alg": "RS256", "kid": key["kid"], "typ": "JWT"}
        signing_input = f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(claims).encode())}".encode()
        size = (n.bit_length() + 7) // 8
        digest = SHA256_INFO + hashlib.sha256(signing_input).digest()
        encoded = b"\x00\x01" + b"\xff" * (size - 3 - len(digest)) + b"\x00" + digest
        signature = pow(int.from_bytes(encoded, "big"), d, n).to_bytes(size, "big")
        return f"{signing_input.decode()}.{_b64(signature)}"

    def _form(self) -> dict:
        return {name: values[0] for name, values in parse_qs(_read(self).decode()).items()}

    def _service_account(self) -> bool:
        return hmac.compare_digest(self.headers.get("Authorization", ""), f"Bearer {_read_file(SERVICE_TOKEN_FILE)}")

    def _get(self) -> tuple:
        parts = urlsplit(self.path)
        query = {name: values[0] for name, values in parse_qs(parts.query).items()}
        if parts.path == "/.well-known/openid-configuration":
            return reply(200, {"issuer": self.issuer,
                               "authorization_endpoint": f"{self.issuer}/oauth/v2/authorize",
                               "token_endpoint": f"{self.issuer}/oauth/v2/token",
                               "end_session_endpoint": f"{self.issuer}/oidc/v1/end_session",
                               "jwks_uri": f"{self.issuer}/oauth/v2/keys"})
        if parts.path == "/oauth/v2/keys":
            key = self.key()
            return reply(200, {"keys": [{"kty": "RSA", "use": "sig", "alg": "RS256", "kid": key["kid"],
                                         "n": _b64(_int_bytes(int(key["n"], 16))),
                                         "e": _b64(_int_bytes(int(key["e"], 16)))}]})
        if parts.path == "/oauth/v2/authorize":
            if (query.get("client_id") != self.client_id or query.get("redirect_uri") != self.redirect_uri
                    or query.get("response_type") != "code" or query.get("code_challenge_method") != "S256"
                    or "openid" not in query.get("scope", "").split() or not query.get("code_challenge")):
                return reply(400, page="<!doctype html><title>Zitadel</title><p>invalid_request</p>")
            request_id = secrets.token_urlsafe(16)
            with lock:
                Zitadel.requests[request_id] = query
            return reply(200, page=(
                "<!doctype html><html><head><meta charset=\"utf-8\"><title>Zitadel (e2e)</title></head><body>"
                "<form method=\"post\" action=\"/oauth/v2/authorize\">"
                f"<input type=\"hidden\" name=\"authRequest\" value=\"{html.escape(request_id)}\">"
                "<label for=\"loginName\">Login name</label> <input id=\"loginName\" name=\"loginName\">"
                "<button type=\"submit\">Sign in</button></form></body></html>"))
        if parts.path == "/oidc/v1/end_session":
            after = query.get("post_logout_redirect_uri", "")
            if query.get("client_id") == self.client_id and after.startswith(self.portal + "/"):
                return reply(302, {}, headers={"Location": after})
            return reply(400, {"error": "invalid_request"})
        return reply(404, {})

    def _post(self) -> tuple:
        path = urlsplit(self.path).path
        if path == "/oauth/v2/authorize":
            return self._sign_in(self._form())
        if path == "/oauth/v2/token":
            return self._token(self._form())
        if path.startswith("/v2/users"):
            return self._users(path, json.loads(_read(self) or b"{}"))
        return reply(404, {})

    def _sign_in(self, form: dict) -> tuple:
        """The person said who they are; with their password and their second factor, as this fake takes it."""
        with lock:
            request = Zitadel.requests.pop(form.get("authRequest", ""), None)
            login_name = form.get("loginName", "").strip().lower()
            user_id = next((uid for uid, user in Zitadel.users.items() if user["email"] == login_name), None)
            if request is None or user_id is None:
                return reply(400, page="<!doctype html><title>Zitadel</title><p>User not found</p>")
            code = secrets.token_urlsafe(24)
            Zitadel.codes[code] = {"sub": user_id, "nonce": request.get("nonce"),
                                   "challenge": request["code_challenge"], "redirect_uri": request["redirect_uri"]}
            seen["zitadel"].append({"login": user_id})
        back = f"{request['redirect_uri']}?" + urlencode({"code": code, "state": request.get("state", "")})
        return reply(302, {}, headers={"Location": back})

    def _token(self, form: dict) -> tuple:
        expected = "Basic " + base64.b64encode(f"{self.client_id}:{_read_file(CLIENT_SECRET_FILE)}".encode()).decode()
        if not hmac.compare_digest(self.headers.get("Authorization", ""), expected):
            return reply(401, {"error": "invalid_client"})
        with lock:
            login = Zitadel.codes.pop(form.get("code", ""), None)
        verifier = form.get("code_verifier", "")
        if (login is None or form.get("grant_type") != "authorization_code"
                or _b64(hashlib.sha256(verifier.encode()).digest()) != login["challenge"]
                or form.get("redirect_uri") != login["redirect_uri"]):
            return reply(400, {"error": "invalid_grant"})
        now = int(time.time())
        claims = {"iss": self.issuer, "sub": login["sub"], "aud": [self.client_id], "azp": self.client_id,
                  "iat": now, "exp": now + 3600, "auth_time": now, "amr": ["pwd", "mfa", "otp"]}
        if login["nonce"]:
            claims["nonce"] = login["nonce"]
        return reply(200, {"access_token": secrets.token_urlsafe(24), "token_type": "Bearer",
                           "expires_in": 3600, "id_token": self.sign(claims)})

    def _users(self, path: str, body: dict) -> tuple:
        if not self._service_account():
            return reply(401, {"code": 16, "message": "Errors.Token.Invalid (AUTH-7fs1e)"})
        with lock:
            if path == "/v2/users":
                queries = body.get("queries") or []
                email = next((q["emailQuery"]["emailAddress"].lower() for q in queries if "emailQuery" in q), None)
                organization = next((q["organizationIdQuery"]["organizationId"] for q in queries
                                     if "organizationIdQuery" in q), None)
                found = [{"userId": uid} for uid, user in Zitadel.users.items()
                         if user["email"] == email and organization == self.organization]
                return reply(200, {"details": {"totalResult": str(len(found))}, "result": found})
            if path == "/v2/users/human":
                if (body.get("organization") or {}).get("orgId") != self.organization:
                    return reply(400, {"code": 3, "message": "Errors.Org.NotFound (COMMAND-3n8fs)"})
                user_id = str(300_000_000_000_000_000 + len(Zitadel.users) + 1)
                Zitadel.users[user_id] = {"email": body["email"]["email"].lower(), "invited": False}
                return reply(200, {"userId": user_id, "emailCode": secrets.token_urlsafe(6)})
            found = re.fullmatch(r"/v2/users/(\d+)/invite_code", path)
            if found and found.group(1) in Zitadel.users:
                Zitadel.users[found.group(1)]["invited"] = True
                seen["zitadel"].append({"invited": found.group(1)})
                return reply(200, {"details": {}})
        return reply(404, {"code": 5, "message": "Errors.User.NotFound (COMMAND-uyhj3)"})


if __name__ == "__main__":
    threading.Thread(target=ThreadingHTTPServer(("0.0.0.0", 18090), Zitadel).serve_forever, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 8080), Fakes).serve_forever()
