# -*- coding: utf-8 -*-
"""
    How often the rest of the gateway is answered (ADR-0028, as amended on 2026-10-30 for HIG-139). POST /oauth/token
    keeps its own limits (token_limits.py) and is not counted here again; the health check has none. Sliding windows,
    the same as the token's:

        * in hive-api, a credential (a key of a client or of a worker of the engine, or a person) makes at most
          CREDENTIAL_REQUESTS in CREDENTIAL_WINDOW;
        * in hive-api, an origin whose requests were answered 401 or 403 API_FAILURES times in API_FAILURES_WINDOW is
          turned away until the oldest of them leaves the window;
        * in the webhook, an origin that sent WEBHOOK_FAILURES requests without a good signature, or a verification
          with the wrong token, in WEBHOOK_FAILURES_WINDOW is turned away the same way.

    An origin is turned away before its request reaches the application: OriginFailures sits outside BodySizeLimit,
    so not even the body of what it sends is read. Whatever carries a signature of the shape Meta sends is always let
    in: Meta sends again what is not answered 200, and turning its notifications away would lose messages. Whether
    that signature is good can only be known reading the body, and what it turns out to be counts like any failure.

    In hive-api, the failures of the callers inside midgard (the portal, the engine) are not counted by origin: one
    address carries the requests of many credentials there, and a person who asks the portal for routes their role
    does not reach would otherwise lock every other person out. Each of those credentials is limited on its own.

    The counters live in memory, in each process. hive-api runs one worker, so its numbers are exact. The webhook runs
    two (WEB_CONCURRENCY=2), each with its own counters, and the connections of an origin are shared between them by
    the kernel: an origin is turned away somewhere between WEBHOOK_FAILURES and twice as many failures. A restart
    clears them. Every window keeps token_limits.MAX_KEYS keys at most.
"""
import threading
import time
from typing import Callable

from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from constants import HUB_SIGNATURE
from utils.security import looks_like_signature

from .token_limits import CLIENT_IP_HEADER, Refusal, _Windows, origin_of

__all__ = ["CREDENTIAL_REQUESTS", "CREDENTIAL_WINDOW", "API_FAILURES", "API_FAILURES_WINDOW", "WEBHOOK_FAILURES",
           "WEBHOOK_FAILURES_WINDOW", "TooManyRequests", "RequestLimits", "OriginFailures", "API", "WEBHOOK",
           "LIMITS"]

# Five a second, kept for a whole minute: a page of the PWA asks for a handful, the engine a few for each message it
# answers (and it keeps the rules and the Meta tokens for a while), and a client that reads every contact of a chatbot
# does it 500 at a time. Only a loop that went wrong, or a stolen token, keeps above it.
CREDENTIAL_REQUESTS, CREDENTIAL_WINDOW = 300, 60.0
# A client that is still learning the API gets a 403 for a route its role does not reach, or a 401 for the token that
# just expired, a few at a time. Thirty in ten minutes is somebody trying tokens. Three times the failures of
# /oauth/token, which only an assertion that is not good gets.
API_FAILURES, API_FAILURES_WINDOW = 30, 600.0
# Meta sends nothing without its signature, and verifies a webhook once per subscription. A person who tries the
# verification by hand gets it wrong a few times; twenty in a minute is a scan or a flood.
WEBHOOK_FAILURES, WEBHOOK_FAILURES_WINDOW = 20, 60.0

# What hive-api does not count by origin: the health check, and /oauth/token, which counts its own failures.
NOT_COUNTED = frozenset({"/health", "/oauth/token"})
# RFC 6749, section 5.1, as POST /oauth/token answers: a 429 is kept by no cache either.
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


class TooManyRequests(Exception):
    """A credential asked too often: what hive-api answers with a 429 and its Retry-After."""

    def __init__(self, retry_after: int):
        super().__init__(f"retry after {retry_after} s")
        self.retry_after = retry_after


def too_many(retry_after: int) -> JSONResponse:
    """RFC 6585: 429, and when to ask again. The same answer as the limits of POST /oauth/token give."""
    return JSONResponse(status_code=429, content={"error": "temporarily_unavailable"},
                        headers={**NO_STORE, "Retry-After": str(retry_after)})


class RequestLimits:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.credentials = _Windows(CREDENTIAL_REQUESTS, CREDENTIAL_WINDOW, clock)
        self.api_failures = _Windows(API_FAILURES, API_FAILURES_WINDOW, clock)
        self.webhook_failures = _Windows(WEBHOOK_FAILURES, WEBHOOK_FAILURES_WINDOW, clock)
        # The credentials are counted in the pool of threads that runs get_principal; the failures, in the event loop.
        self._lock = threading.Lock()

    def admit(self, client_id, limited_as: str) -> Refusal | None:
        """Once the token is good: a credential that already made its requests this minute waits."""
        key = f"{client_id}/{limited_as}"
        with self._lock:
            wait = self.credentials.full(key)
            if wait is not None:
                return Refusal("requests", wait)
            self.credentials.count(key)
        return None


class _Rule:
    """What one service counts as a failure, and of whom."""

    def windows(self) -> _Windows:
        raise NotImplementedError

    def origin(self, scope, headers: Headers) -> str | None:
        """Who to count the request against; None if it is not counted at all."""
        raise NotImplementedError

    def let_in(self, scope, headers: Headers) -> bool:
        """Whether the request goes in even from an origin that failed too often."""
        return False

    def failed(self, scope, headers: Headers, code: int | None) -> bool:
        raise NotImplementedError


class _Api(_Rule):
    def windows(self) -> _Windows:
        return LIMITS.api_failures

    def origin(self, scope, headers: Headers) -> str | None:
        # Without the header of Cloudflare, the request came from the private networks of midgard.
        if scope["path"] in NOT_COUNTED or CLIENT_IP_HEADER not in headers:
            return None
        return origin_of(headers, None)

    def failed(self, scope, headers: Headers, code: int | None) -> bool:
        return code in (401, 403)


class _Webhook(_Rule):
    def windows(self) -> _Windows:
        return LIMITS.webhook_failures

    def origin(self, scope, headers: Headers) -> str | None:
        if scope["path"] == "/health":
            return None
        peer = scope.get("client")
        return origin_of(headers, peer[0] if peer else None)

    def let_in(self, scope, headers: Headers) -> bool:
        return scope["method"] == "POST" and looks_like_signature(headers.get(HUB_SIGNATURE))

    def failed(self, scope, headers: Headers, code: int | None) -> bool:
        # 403: a signature that is not good, or a verification with the wrong token. Anything else posted without a
        # signature is nothing Meta sent, and a 404 to what is not signed is somebody looking for chatbots.
        if code == 403:
            return True
        return not self.let_in(scope, headers) and (scope["method"] == "POST" or code == 404)


API, WEBHOOK = _Api(), _Webhook()


class OriginFailures:
    """
        Turns away a request whose origin failed too often, before the application sees any of it. It goes outside
        BodySizeLimit, which reads the body, and inside the interceptor, which logs the 429 and adds the headers of
        every answer. What the application answers says whether the request failed.
    """

    def __init__(self, app, rule: _Rule, log):
        self.app, self.rule, self.log = app, rule, log

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        origin = self.rule.origin(scope, headers)
        if origin is None:
            await self.app(scope, receive, send)
            return
        windows = self.rule.windows()
        if not self.rule.let_in(scope, headers):
            wait = windows.full(origin)
            if wait is not None:
                # Not the address: it is a personal datum (RS07).
                self.log.warning("Turned away a request: too many failures from its origin")
                await too_many(wait)(scope, receive, send)
                return

        answered = {}

        async def sending(message):
            if message["type"] == "http.response.start":
                answered["code"] = message["status"]
            await send(message)

        await self.app(scope, receive, sending)
        if self.rule.failed(scope, headers, answered.get("code")):
            windows.count(origin)


LIMITS = RequestLimits()
