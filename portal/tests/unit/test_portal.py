# -*- coding: utf-8 -*-
"""
    hive-portal against its Zitadel and its hive-api (conftest.py): the login with PKCE, state and nonce, the session
    server side, and the PWA's requests on to hive-api as the person (HIG-93). What the issue asks:

    - a stolen cookie without the PWA's header changes nothing;
    - a session that ended leads back to the login;
    - the browser never gets a token.
"""
import os
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from conftest import CLIENT_ID, CLIENT_SECRET, PORTAL, FakeHive, FakeZitadel, Servers
from shared.auth.idp import IdentityProvider
from src.app import CSRF_HEADER, LOGIN_COOKIE, SESSION_COOKIE, build
from src.hive import Hive
from src.oidc import Zitadel
from src.sessions import IDLE, LIFETIME, LOGIN_FOR, MemoryStore, Sealer, Sessions
from src.settings import Settings

SUBJECT = "312345678901234567"


class Clock:
    def __init__(self):
        import time  # pylint: disable=import-outside-toplevel
        self.now = time.time()

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store():
    return MemoryStore()


@pytest.fixture
def app(tmp_path, clock, store):
    secret = tmp_path / "client-secret"
    secret.write_text(CLIENT_SECRET + "\n", encoding="utf-8")
    key = tmp_path / "session-key"
    key.write_bytes(__import__("base64").b64encode(os.urandom(32)))
    settings = Settings(portal_url=PORTAL, idp_issuer=Servers.zitadel, oidc_client_id=CLIENT_ID,
                        oidc_client_secret_file=str(secret), session_key_file=str(key), hive_api_url=Servers.hive,
                        environment="development")
    return build(settings, Sessions(store, Sealer.from_file(str(key)), clock=clock),
                 Zitadel(Servers.zitadel, CLIENT_ID, str(secret), f"{PORTAL}/auth/callback"),
                 IdentityProvider(Servers.zitadel, CLIENT_ID), Hive(Servers.hive))


@pytest.fixture
def http(app):
    return TestClient(app, base_url=PORTAL, follow_redirects=False)


def begin(http, **params):
    response = http.get("/auth/login", params=params)
    assert response.status_code == 302, response.text
    query = parse_qs(urlsplit(response.headers["location"]).query)
    return response, {name: values[0] for name, values in query.items()}


def sign_in(http, subject=SUBJECT, amr=("pwd", "mfa", "otp"), **params):
    """The whole login, as Zitadel would answer it: the portal's answer to its callback."""
    _, query = begin(http, **params)
    code = os.urandom(8).hex()
    FakeZitadel.codes[code] = {"challenge": query["code_challenge"], "nonce": query["nonce"], "subject": subject,
                               "amr": amr}
    return http.get("/auth/callback", params={"code": code, "state": query["state"]})


def portal_headers(**more):
    return {CSRF_HEADER: "1", "Origin": PORTAL, **more}


def every_answer_text(*responses) -> str:
    return "\n".join(f"{r.headers}\n{r.text}" for r in responses)


# --- The login --------------------------------------------------------------------------------------------------------

def test_the_login_goes_to_zitadel_with_pkce_state_and_nonce(http):
    response, query = begin(http)

    assert response.headers["location"].startswith(f"{Servers.zitadel}/oauth/v2/authorize?")
    assert (query["response_type"], query["client_id"], query["scope"], query["code_challenge_method"]) == (
        "code", CLIENT_ID, "openid", "S256")
    assert query["redirect_uri"] == f"{PORTAL}/auth/callback"
    assert len(query["state"]) >= 40 and len(query["nonce"]) >= 40 and len(query["code_challenge"]) == 43
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{LOGIN_COOKIE}=") and all(part in cookie for part in
                                                          ("HttpOnly", "Secure", "SameSite=lax", "Path=/"))


def test_a_full_login_makes_a_session_whose_cookie_is_the_only_thing_the_browser_gets(http, store):
    answer = sign_in(http)

    assert answer.status_code == 302 and answer.headers["location"] == "/"
    cookie = [value for name, value in answer.headers.multi_items() if name == "set-cookie"]
    session_cookie = next(value for value in cookie if value.startswith(f"{SESSION_COOKIE}="))
    assert all(part in session_cookie for part in ("HttpOnly", "Secure", "SameSite=lax", "Path=/"))
    assert "Domain" not in session_cookie
    assert any(value.startswith(f'{LOGIN_COOKIE}=""') for value in cookie)
    # The client secret went to Zitadel with Basic, and the PKCE verifier with it.
    authorization, form = FakeZitadel.token_calls[-1]
    assert authorization.startswith("Basic ") and form["grant_type"] == "authorization_code"
    assert len(form["code_verifier"]) >= 43
    # The exchange went with a proof, and for the ID token Zitadel gave.
    proof, exchange = FakeHive.exchanges[-1]
    assert proof and exchange["subject_token_type"] == "urn:ietf:params:oauth:token-type:id_token"
    session = http.get("/auth/session")
    assert session.status_code == 200
    assert session.json()["role"] == "cliente-admin" and session.json()["person"] == f"person-of-{SUBJECT}"
    for token in (exchange["subject_token"], "zitadel-access-token-never-used", "signature-of-hive"):
        assert token not in every_answer_text(answer, session)
    assert len(store.values) == 1


def test_where_to_go_after_the_login_is_only_a_path_of_the_portal(http):
    assert sign_in(http, next="/chat/abc?x=1").headers["location"] == "/chat/abc?x=1"
    for elsewhere in ("//evil.test/x", "https://evil.test", "/\\evil.test", "chat"):
        assert sign_in(http, next=elsewhere).headers["location"] == "/"


def test_a_login_is_finished_by_the_browser_that_began_it(http, app):
    _, query = begin(http)
    code = "a-code"
    FakeZitadel.codes[code] = {"challenge": query["code_challenge"], "nonce": query["nonce"], "subject": SUBJECT,
                               "amr": ("mfa",)}
    other_browser = TestClient(app, base_url=PORTAL, follow_redirects=False)

    answer = other_browser.get("/auth/callback", params={"code": code, "state": query["state"]})

    assert answer.status_code == 400
    assert FakeZitadel.token_calls == []


def test_a_state_is_good_once(http):
    _, query = begin(http)
    FakeZitadel.codes["c1"] = {"challenge": query["code_challenge"], "nonce": query["nonce"], "subject": SUBJECT,
                               "amr": ("mfa",)}
    first = http.get("/auth/callback", params={"code": "c1", "state": query["state"]})
    http.cookies.clear()

    again = http.get("/auth/callback", params={"code": "c1", "state": query["state"]})

    assert first.status_code == 302 and again.status_code == 400


def test_a_login_that_waited_too_long_is_begun_again(http, clock):
    _, query = begin(http)
    clock.now += LOGIN_FOR + 1

    assert http.get("/auth/callback", params={"code": "x", "state": query["state"]}).status_code == 400


def test_an_error_from_zitadel_or_no_code_ends_the_login(http):
    _, query = begin(http)

    assert http.get("/auth/callback", params={"error": "access_denied", "state": query["state"]}).status_code == 400


def test_an_id_token_without_the_nonce_of_the_login_is_turned_away(http, monkeypatch):
    import conftest  # pylint: disable=import-outside-toplevel
    real = conftest.id_token
    monkeypatch.setattr(conftest, "id_token", lambda nonce, subject, amr: real("another-nonce", subject, amr))

    answer = sign_in(http)

    assert answer.status_code == 403 and FakeHive.exchanges == []


def test_without_a_second_factor_there_is_no_session(http):
    answer = sign_in(http, amr=("pwd",))

    assert answer.status_code == 403
    assert SESSION_COOKIE not in answer.headers.get("set-cookie", "") or 'hive-session=""' in answer.headers.get(
        "set-cookie", "")
    assert FakeHive.exchanges == []


@pytest.mark.parametrize("error,page", [("invalid_grant", "Sin acceso a Hive"), ("invalid_target", "Elige el cliente")])
def test_a_person_hive_gives_no_token_gets_no_session(http, error, page):
    FakeHive.refuse = error

    answer = sign_in(http)

    assert answer.status_code == 403 and page in answer.text
    assert http.get("/auth/session").status_code == 401


def test_the_client_asked_for_at_the_login_goes_to_the_exchange(http):
    sign_in(http, client="ac3cd51f-b641-4bec-b234-770922174cf4")

    assert FakeHive.exchanges[-1][1]["hive_client"] == "ac3cd51f-b641-4bec-b234-770922174cf4"


def test_a_client_that_is_not_a_uuid_is_not_taken(http):
    assert http.get("/auth/login", params={"client": "../x"}).status_code == 400


def test_what_zitadel_says_no_with_stays_out_of_the_page(http):
    _, query = begin(http)

    answer = http.get("/auth/callback", params={"code": "unknown", "state": query["state"]})

    assert answer.status_code == 403 and "somebody@else.test" not in answer.text


def test_when_zitadel_cannot_be_reached_it_is_for_later(http):
    FakeZitadel.fail = (503, {})

    assert sign_in(http).status_code == 503


# --- The PWA's requests ------------------------------------------------------------------------------------------

def test_a_request_goes_on_to_hive_api_as_the_person_and_without_the_cookie(http):
    sign_in(http)

    answer = http.get("/api/chat/abc/templates", params={"sync": "true"})

    assert answer.status_code == 200 and answer.json() == {"ok": True}
    seen = FakeHive.seen[-1]
    assert (seen["method"], seen["path"], seen["query"]) == ("GET", "/chat/abc/templates", "sync=true")
    assert seen["headers"]["Authorization"].startswith("DPoP ") and seen["headers"].get("DPoP")
    assert "Cookie" not in seen["headers"] and "cookie" not in {name.lower() for name in seen["headers"]}
    assert answer.headers["cache-control"] == "no-store"
    assert "set-cookie" not in answer.headers
    assert seen["token"] not in every_answer_text(answer)


def test_a_change_with_the_pwa_s_header_goes_on(http):
    sign_in(http)

    answer = http.post("/api/people", json={"email": "bob@acme.test", "role": "cliente-analista"},
                       headers=portal_headers(**{"Idempotency-Key": "k-1"}))

    assert answer.status_code == 200
    seen = FakeHive.seen[-1]
    assert (seen["method"], seen["path"]) == ("POST", "/people")
    sent = {name.lower(): value for name, value in seen["headers"].items()}
    assert '"bob@acme.test"' in seen["body"] and sent["idempotency-key"] == "k-1"
    assert sent["content-type"] == "application/json" and CSRF_HEADER.lower() not in sent and "origin" not in sent


@pytest.mark.parametrize("headers", [{}, {CSRF_HEADER: "0"}, {CSRF_HEADER: "1", "Origin": "https://evil.test"}],
                         ids=["no header", "another value", "another origin"])
@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
def test_a_stolen_cookie_without_the_pwa_s_header_changes_nothing(http, method, headers):
    sign_in(http)

    answer = http.request(method, "/api/people/x", headers=headers, json={"status": "blocked"})

    assert answer.status_code == 403
    assert FakeHive.seen == []


def test_a_request_without_a_session_is_told_to_sign_in(http):
    answer = http.get("/api/people")

    assert (answer.status_code, answer.json()) == (401, {"authenticated": False, "login": "/auth/login"})
    assert FakeHive.seen == []


@pytest.mark.parametrize("route", ["oauth/token", "oauth", "chat/../oauth/token", "chat//x", ""])
def test_the_token_endpoint_and_odd_routes_are_not_reached(http, route):
    sign_in(http)

    assert http.get(f"/api/{route}").status_code == 404
    assert FakeHive.seen == []


def test_a_body_too_big_is_not_sent(http):
    sign_in(http)

    answer = http.post("/api/people", content=b"x" * (1024 * 1024 + 1), headers=portal_headers())

    assert answer.status_code == 413 and FakeHive.seen == []


def test_what_hive_api_answers_comes_back_as_it_is(http):
    sign_in(http)
    FakeHive.answer = (429, {"code": 429, "message": "slow down"})

    answer = http.get("/api/people")

    assert answer.status_code == 429 and answer.headers["retry-after"] == "7"


# --- When it ends -------------------------------------------------------------------------------------------------

def test_a_session_unused_for_half_an_hour_leads_back_to_the_login(http, clock):
    sign_in(http)
    clock.now += IDLE + 1

    assert http.get("/api/people").status_code == 401
    assert http.get("/auth/session").status_code == 401


def test_a_session_in_use_still_ends_after_twelve_hours(http, clock):
    sign_in(http)
    for _ in range(int(LIFETIME / (IDLE - 60)) + 1):
        clock.now += IDLE - 60
        http.get("/auth/session")

    assert http.get("/auth/session").status_code == 401


def test_a_token_about_to_expire_is_renewed_by_the_exchange(http):
    FakeHive.lifetime = 30
    sign_in(http)
    exchanges = len(FakeHive.exchanges)

    assert http.get("/api/people").status_code == 200

    assert len(FakeHive.exchanges) == exchanges + 1


def test_a_person_blocked_meanwhile_loses_the_session_at_the_renewal(http):
    FakeHive.lifetime = 30
    sign_in(http)
    FakeHive.refuse = "invalid_grant"

    answer = http.get("/api/people")

    assert answer.status_code == 401
    assert http.get("/auth/session").status_code == 401
    assert FakeHive.seen == []


def test_a_token_from_before_a_restart_is_renewed_once_and_the_request_goes_again(http):
    sign_in(http)
    http.get("/api/people")
    FakeHive.tokens_refused.add(FakeHive.seen[-1]["token"])

    answer = http.get("/api/people")

    assert answer.status_code == 200
    assert FakeHive.seen[-2]["token"] != FakeHive.seen[-1]["token"]


def test_the_logout_ends_the_session_and_says_where_to_end_zitadel_s(http):
    sign_in(http)

    answer = http.post("/auth/logout", headers=portal_headers())

    assert answer.status_code == 200
    where = answer.json()["logout"]
    assert where.startswith(f"{Servers.zitadel}/oidc/v1/end_session?")
    query = parse_qs(urlsplit(where).query)
    assert query["client_id"] == [CLIENT_ID] and query["post_logout_redirect_uri"] == [f"{PORTAL}/"]
    assert "id_token_hint" not in query
    assert http.get("/auth/session").status_code == 401


def test_a_logout_without_the_pwa_s_header_does_nothing(http):
    sign_in(http)

    assert http.post("/auth/logout").status_code == 403
    assert http.get("/auth/session").status_code == 200


# --- The pages -------------------------------------------------------------------------------------------------------

def test_every_page_says_what_the_browser_may_load(http):
    page = http.get("/")

    assert page.status_code == 200 and "Hive" in page.text
    csp = page.headers["content-security-policy"]
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp and "http" not in csp
    assert page.headers["x-content-type-options"] == "nosniff"
    assert page.headers["strict-transport-security"].startswith("max-age=")
    assert http.get("/auth/session").headers["content-security-policy"] == csp


def test_the_health_check(http):
    assert http.get("/health").json() == {"status": "ok"}
