# -*- coding: utf-8 -*-
"""
    hive-portal: the BFF of Hive's PWA (ADR-0029, point 5; HIG-93), at https://hive-app.higerotech.com.

        GET  /auth/login      begins the login with Zitadel: Authorization Code, PKCE, state and nonce
        GET  /auth/callback   Zitadel's answer: the ID token, its exchange for a token of Hive, and the session
        GET  /auth/session    whether there is a session, and whose; never a token
        POST /auth/logout     ends the session, and says where to end Zitadel's
        *    /api/<route>     the PWA's request to hive-api, as the person
        GET  /                the PWA

    The browser holds one cookie, `__Host-hive-session`: HttpOnly, Secure, SameSite=Lax, a random id. The tokens stay
    in the session, server side (sessions.py). A request that changes something carries the header `X-Hive-Portal`,
    which another site cannot make a browser send without asking, and, if the browser says where it comes from, the
    portal's own origin: a stolen cookie, or another site that makes the browser send it, changes nothing.

        uvicorn --factory src.app:create_app
"""
import logging
import mimetypes
import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from shared.auth.idp import IdentityProvider
from shared.auth.idp import IdpUnavailable as KeysUnavailable
from shared.auth.tokens import InvalidToken

from .hive import ANSWER_HEADERS, ClientNeeded, Hive, HiveUnavailable, NoAccess, claims_of
from .oidc import IdpUnavailable, LoginFailed, Zitadel, pkce
from .sessions import LIFETIME, LOGIN_FOR, KvStore, MemoryStore, Sealer, Sessions
from .settings import Settings

__all__ = ["create_app", "build", "SESSION_COOKIE", "LOGIN_COOKIE", "CSRF_HEADER"]

log = logging.getLogger("hive-portal")

SESSION_COOKIE = "__Host-hive-session"
LOGIN_COOKIE = "__Host-hive-login"
CSRF_HEADER = "X-Hive-Portal"
SAFE = ("GET", "HEAD")
MAX_BODY = 1024 * 1024
RENEW_BEFORE = 60  # seconds before a token of Hive expires that a new one is asked for
STATIC = Path(__file__).resolve().parent / "static"
# What StaticFiles says each file is: Python 3.12 does not know .woff2, and on Windows the registry can make a .js
# text/plain, which the browser, with nosniff, would not run as a module.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("image/png", ".png")
mimetypes.add_type("application/manifest+json", ".webmanifest")
NO_STORE = {"Cache-Control": "no-store"}
# The PWA's files, kept but asked again each time by their etag: a 304 if nothing changed. Without it, Cloudflare and
# the browser kept them 4 hours (max-age=14400), and after a deploy a new app.js could run with an old api.js.
REVALIDATE = "no-cache"
# Nothing from outside (ADR-0026, ADR-0027): the PWA's own scripts, styles and fonts, and its own API.
SECURITY = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                               "font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
                               "form-action 'self'; object-src 'none'",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _page(code: int, title: str, text: str) -> HTMLResponse:
    """A page of the portal's own, in words of the portal: never anything of the request or of Zitadel."""
    return HTMLResponse(status_code=code, headers=NO_STORE, content=(
        "<!doctype html><html lang=\"es\"><head><meta charset=\"utf-8\"><title>Hive</title>"
        "<link rel=\"stylesheet\" href=\"/app.css\"></head><body><main>"
        f"<h1>{title}</h1><p>{text}</p><p><a href=\"/auth/login\">Volver a entrar</a></p></main></body></html>"))


def _next_of(value: str | None) -> str:
    """Where to go after the login: only a path of the portal itself, never another site."""
    if (not value or not value.startswith("/") or value.startswith("//") or "\\" in value or len(value) > 512
            or any(ord(character) < 32 for character in value)):
        return "/"
    return value


def _cookie(response: Response, name: str, value: str, max_age: int) -> None:
    response.set_cookie(name, value, max_age=max_age, path="/", secure=True, httponly=True, samesite="lax")


def build(settings: Settings, sessions: Sessions, zitadel: Zitadel, idp: IdentityProvider, hive: Hive) -> FastAPI:
    """The portal, on these pieces: what each test or the real start passes."""
    app = FastAPI(title="hive-portal", docs_url=None, redoc_url=None, openapi_url=None)
    origin = _origin(settings.portal_url)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for name, value in SECURITY.items():
            response.headers.setdefault(name, value)
        response.headers.setdefault("Cache-Control", REVALIDATE)
        return response

    def csrf_ok(request: Request) -> bool:
        """The PWA's own header, and the portal's own origin when the browser says one."""
        if request.headers.get(CSRF_HEADER) != "1":
            return False
        sent_from = request.headers.get("origin")
        return sent_from is None or sent_from == origin

    async def renew(session_id: str, session: dict) -> dict | None:
        """A new token of Hive for the session's ID token, or None if Hive does not give one any more."""
        try:
            token, expires = await run_in_threadpool(hive.exchange, session["id_token"], session.get("client"))
        except NoAccess as e:
            log.warning("The session of person %s ended: Hive gave no token (%s)", session.get("person"), e)
            await sessions.end(session_id)
            return None
        session.update(token=token, expires=expires)
        await sessions.save(session_id, session)
        return session

    def signed_out(code: int = 401) -> JSONResponse:
        response = JSONResponse(status_code=code, headers=NO_STORE,
                                content={"authenticated": False, "login": "/auth/login"})
        response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
        return response

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/auth/login")
    async def login(request: Request):
        client = request.query_params.get("client")
        if client is not None:
            try:
                client = str(UUID(client))
            except ValueError:
                return _page(400, "No se pudo iniciar sesión", "El cliente que se pidió no es válido.")
        verifier, challenge = pkce()
        nonce = secrets.token_urlsafe(32)
        state, browser = await sessions.start_login({"nonce": nonce, "verifier": verifier, "client": client,
                                                     "next": _next_of(request.query_params.get("next"))})
        try:
            where = await run_in_threadpool(zitadel.authorize_url, state, nonce, challenge)
        except IdpUnavailable as e:
            log.error("A login could not begin: %s", e)
            return _page(503, "El inicio de sesión no está disponible", "Inténtalo de nuevo en unos minutos.")
        response = RedirectResponse(where, status_code=302, headers=NO_STORE)
        _cookie(response, LOGIN_COOKIE, browser, LOGIN_FOR)
        return response

    @app.get("/auth/callback")
    async def callback(request: Request):
        login_ = await sessions.finish_login(request.query_params.get("state", ""), request.cookies.get(LOGIN_COOKIE))
        code = request.query_params.get("code")
        if login_ is None or not code or request.query_params.get("error"):
            log.warning("A login came back without its state, its browser, its code, or within its time")
            return _without_login_cookie(_page(400, "No se pudo completar el inicio de sesión",
                                               "Vuelve a empezar desde el principio."))
        try:
            id_token = await run_in_threadpool(zitadel.id_token_for, code, login_["verifier"])
            identity = await run_in_threadpool(idp.verify, id_token, login_["nonce"])
            token, expires = await run_in_threadpool(hive.exchange, id_token, login_.get("client"))
        except (IdpUnavailable, KeysUnavailable, HiveUnavailable) as e:
            log.error("A login could not be completed: %s", e)
            return _without_login_cookie(_page(503, "El inicio de sesión no está disponible",
                                               "Inténtalo de nuevo en unos minutos."))
        except ClientNeeded:
            return _without_login_cookie(_page(
                403, "Elige el cliente", "Tu cuenta es de más de un cliente de Hive: entra desde el enlace de ese "
                                         "cliente."))
        except (LoginFailed, InvalidToken, NoAccess) as e:
            log.warning("A login was turned away: %s", e)
            return _without_login_cookie(_page(403, "Sin acceso a Hive",
                                               "Tu cuenta no tiene acceso a Hive, o no entró con un segundo factor."))
        claims = claims_of(token)
        session_id = await sessions.create({"id_token": id_token, "token": token, "expires": expires,
                                            "client": login_.get("client"), "person": claims.get("hive_user"),
                                            "subject": identity.subject})
        log.warning("A session began: client %s, person %s, %s", claims.get("client_id"), claims.get("hive_user"),
                    claims.get("hive_user_role"))
        response = _without_login_cookie(RedirectResponse(login_.get("next") or "/", status_code=302,
                                                          headers=NO_STORE))
        _cookie(response, SESSION_COOKIE, session_id, LIFETIME)
        return response

    @app.get("/auth/session")
    async def session_of(request: Request):
        session = await sessions.load(request.cookies.get(SESSION_COOKIE))
        if session is None:
            return signed_out()
        claims = claims_of(session["token"])
        return JSONResponse(headers=NO_STORE, content={
            "authenticated": True, "client": claims.get("client_id"), "person": claims.get("hive_user"),
            "role": claims.get("hive_user_role"), "endsAt": int(session["created"] + LIFETIME)})

    @app.post("/auth/logout")
    async def logout(request: Request):
        if not csrf_ok(request):
            return JSONResponse(status_code=403, headers=NO_STORE, content={"error": "csrf"})
        await sessions.end(request.cookies.get(SESSION_COOKIE))
        try:
            where = await run_in_threadpool(zitadel.logout_url, f"{origin}/")
        except IdpUnavailable:
            where = "/"
        response = JSONResponse(headers=NO_STORE, content={"authenticated": False, "logout": where})
        response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
        return response

    @app.api_route("/api/{route:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"])
    async def forward(route: str, request: Request):
        if request.method not in SAFE and not csrf_ok(request):
            return JSONResponse(status_code=403, headers=NO_STORE, content={"error": "csrf"})
        # The token endpoint is the portal's own business, and nothing climbs out of the API.
        if not route or route.startswith("oauth") or ".." in route.split("/") or "//" in route or "\\" in route:
            return JSONResponse(status_code=404, headers=NO_STORE, content={"error": "not_found"})
        session_id = request.cookies.get(SESSION_COOKIE)
        session = await sessions.load(session_id)
        if session is None:
            return signed_out()
        body = await request.body()
        if len(body) > MAX_BODY:
            return JSONResponse(status_code=413, headers=NO_STORE, content={"error": "too_large"})
        try:
            if session["expires"] - time.time() < RENEW_BEFORE:
                session = await renew(session_id, session)
                if session is None:
                    return signed_out()
            answer = await run_in_threadpool(hive.forward, session["token"], request.method, route,
                                             request.url.query, body, dict(request.headers))
            if answer.status_code == 401:
                # A token from before a restart is bound to the key the portal had then: a new one, and once more.
                session = await renew(session_id, session)
                if session is None:
                    return signed_out()
                answer = await run_in_threadpool(hive.forward, session["token"], request.method, route,
                                                 request.url.query, body, dict(request.headers))
        except HiveUnavailable as e:
            log.error("hive-api could not be reached for the PWA: %s", e)
            return JSONResponse(status_code=503, headers=NO_STORE, content={"error": "unavailable"})
        headers = {name: value for name, value in answer.headers.items() if name.lower() in ANSWER_HEADERS}
        return Response(status_code=answer.status_code, content=answer.content, headers={**headers, **NO_STORE})

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="pwa")
    return app


def _without_login_cookie(response: Response) -> Response:
    response.delete_cookie(LOGIN_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
    return response


def create_app() -> FastAPI:
    """The portal of midgard, from its environment (uvicorn --factory)."""
    settings = Settings()
    logging.basicConfig(level=logging.WARNING if settings.environment == "production" else logging.INFO,
                        format="[%(asctime)s] %(levelname)s %(name)s %(message)s")
    if settings.nats_url:
        store = KvStore(settings.nats_url, settings.nats_user, settings.nats_password, settings.nats_ca,
                        settings.nats_inbox_prefix)
    elif settings.environment == "development":
        store = MemoryStore()
    else:
        raise ValueError("The portal needs NATS_URL: its sessions live in Bifröst")
    portal = settings.portal_url.rstrip("/")
    return build(settings, Sessions(store, Sealer.from_file(settings.session_key_file)),
                 Zitadel(settings.idp_issuer, settings.oidc_client_id, settings.oidc_client_secret_file,
                         f"{portal}/auth/callback"),
                 IdentityProvider(settings.idp_issuer, settings.oidc_client_id), Hive(settings.hive_api_url))
