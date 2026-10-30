# -*- coding: utf-8 -*-
"""
    What the two services of the gateway share: the webhook Meta calls (webhook.py) and the management API (app.py).
    They run as separate processes, so that a slow management request never holds a notification up, and each one
    is built here with the same limits, the same log line per request and the same health check.
"""
import logging.config
import time
from uuid import UUID

from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from shared import bus
from shared import tracing
from shared.bus.nats_jetstream import NatsPublisher
from auth.request_limits import OriginFailures
from classes import ResponseData, Settings
from constants import APPLICATION_JSON, CONTENT_TYPE, PROCESSING_TIME
from crypto.key_service import OpenBaoKeyService
from repository import data_keys
from utils import BodySizeLimit, configure_logging, describe

settings = Settings()
# Before anything opens a span. The name comes from OTEL_SERVICE_NAME, set per service in the compose.
tracing.configure()
log = logging.getLogger(settings.environment)

# The key service is only reached when a data key is needed: a service starts without it, and until it answers
# whatever needs a key gets a 503. Outside development it must be reached over TLS.
if not settings.andvari_addr.startswith("https://") and settings.environment != "development":
    raise ValueError("ANDVARI_ADDR must be an https address")
data_keys.configure(OpenBaoKeyService(settings.andvari_addr, settings.andvari_role_id,
                                      settings.andvari_secret_id_file, settings.andvari_ca_file))

# The event bus, the same way: one connection per process, opened the first time an event is published. Until it
# answers, publishing gets a 503.
if settings.nats_url:
    if not settings.nats_url.startswith("tls://") and settings.environment != "development":
        raise ValueError("NATS_URL must be a tls:// address")
    bus.configure(NatsPublisher(settings.nats_url, settings.nats_user, settings.nats_password, settings.nats_ca or None,
                                settings.nats_inbox_prefix or "_INBOX"))

# No request may be larger than this: it is the largest notification Meta sends, and nothing a client sends comes
# close.
MAX_BODY_BYTES = 3 * 1024 * 1024

# The method of a request as the log names it: one of these, never the text that was sent.
METHODS = {method: method for method in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")}

# What every answer of both services says to whoever reads it (HIG-138, A02 of .ai-dlc/owasp-mapping.md), as the
# OWASP REST Security Cheat Sheet asks of a JSON API: HTTPS only from now on, no guessing its type, no address given to
# whoever it links to, no page of ours inside another, and nothing kept by a cache along the way, as what it carries
# is a client's. A route that says otherwise keeps its own (setdefault).
def security_headers(environment: str) -> dict:
    headers = {
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "X-Frame-Options": "DENY",
        "Cache-Control": "no-store",
    }
    # A JSON API loads nothing and is shown in no frame. Not in development: the interactive documentation is a page.
    if environment != "development":
        headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    return headers


SECURITY_HEADERS = security_headers(settings.environment)


def documentation(environment: str) -> dict:
    """
        The interactive documentation, and the schema it reads, only in development: anywhere else they show every
        route and model to whoever finds the address. The contracts are in docs/02-design/contracts/.
    """
    if environment == "development":
        return {}
    return {"docs_url": None, "redoc_url": None, "openapi_url": None}


def request_line(request: Request) -> str:
    """
        A request as it goes to the log: the route it matched, as it is declared, and the identifiers in its
        address that are well formed. Never the address as it was sent, nor its query string: they carry
        verification tokens, and whatever a caller wants to plant in the log.
    """
    identifiers = []
    for value in request.scope.get("path_params", {}).values():
        try:
            identifiers.append(str(UUID(value)))
        except (ValueError, TypeError, AttributeError):
            continue
    route = getattr(request.scope.get("route"), "path", "no route")
    return " ".join([METHODS.get(request.method, "OTHER"), route, *identifiers])


# The identifiers in an address that a span may carry, each under its name in the trace.
SPAN_IDENTIFIERS = {"bot_id": "gateway.bot_id", "contact_id": "gateway.contact_id", "client_id": "gateway.client_id"}


def request_attributes(request: Request) -> dict:
    """What the span of a request says of it: the same as its log line, and nothing the caller wrote."""
    attributes = {"http.request.method": METHODS.get(request.method, "OTHER"),
                  "http.route": getattr(request.scope.get("route"), "path", "no route")}
    for name, value in request.scope.get("path_params", {}).items():
        if name in SPAN_IDENTIFIERS:
            try:
                attributes[SPAN_IDENTIFIERS[name]] = str(UUID(value))
            except (ValueError, TypeError, AttributeError):
                continue
    return attributes


async def invalid_request(_request: Request, error: RequestValidationError):
    """
        Says where a request is wrong and why, never with what: by default the answer repeats what was sent,
        and what is sent here are credentials, phone numbers and messages.
    """
    detail = [{"type": item["type"], "loc": list(item["loc"]), "msg": item["msg"]} for item in error.errors()]
    return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content={"detail": detail})


async def interceptor(request: Request, call_next):
    s_time = time.time()
    body = ResponseData(code=status.HTTP_500_INTERNAL_SERVER_ERROR, message="INTERNAL SERVER ERROR", data=None)
    response = Response(
        content=body.json(exclude_none=True),
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        headers={CONTENT_TYPE: APPLICATION_JSON}
    )
    # A span per request, started here and not taken from the caller: the trace of a notification starts at the
    # webhook. What it says of the request is what the log line says, set once the route is known. Not the health
    # check: Docker calls it every 30 s on each service, and a trace of it says nothing.
    with tracing.span("http.request", kind=tracing.SpanKind.SERVER, traced=request.url.path != "/health") as current:
        try:
            request.state.event_id = None
            response = await call_next(request)
        except Exception as e:
            log.error(describe(e))
            current.failed(type(e).__name__)
        finally:
            process_time = "{:f}".format(time.time() - s_time)
            response.headers[PROCESSING_TIME] = str(process_time)
            for name, value in SECURITY_HEADERS.items():
                response.headers.setdefault(name, value)
            # The one line a request leaves, written once it is known which route it was.
            log.info(f"{request_line(request)} code:{response.status_code} time: {process_time}")
            current.set(**request_attributes(request), **{"http.response.status_code": response.status_code})
    return response


async def health():
    """
        Liveness only: it answers without touching MongoDB or the broker, so a slow dependency does not get a
        healthy container restarted or rolled back.
    """
    return {"status": "ok"}


def build(limit=None, **metadata) -> FastAPI:
    """
        One of the two services, with what both have: limits, error answers, the request log line and /health.
        `limit` is the rule of request_limits that says which origins of this service fail too often (HIG-139).
    """
    service = FastAPI(
        **documentation(settings.environment),
        on_startup=[configure_logging],
        on_shutdown=[bus.close, tracing.shutdown],
        version=settings.version,
        license_info={"name": "Apache 2.0", "url": "https://www.apache.org/licenses/LICENSE-2.0.html"},
        **metadata,
    )
    # Added before the interceptor, so that the interceptor is outside it and logs what is turned away.
    service.add_middleware(BodySizeLimit, limit=MAX_BODY_BYTES)
    # Outside BodySizeLimit, so that an origin turned away is answered before its body is read; inside the
    # interceptor, so that the 429 is logged and carries the headers of every answer.
    if limit is not None:
        service.add_middleware(OriginFailures, rule=limit, log=log)
    service.add_exception_handler(RequestValidationError, invalid_request)
    service.middleware("http")(interceptor)
    service.get(path="/health", tags=["Health"])(health)
    return service
