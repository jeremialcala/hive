# -*- coding: utf-8 -*-
"""
    Traces (OpenTelemetry, R4, RNF09): each message can be followed from the webhook to the ingestion and on to the
    engine, through the W3C context (`traceparent`) every event carries. Spans follow the rule of the logs (RS19):
    no number, no text of a message, nothing the caller sent. That is why nothing here instruments a library on its
    own, and why a span only takes the attributes of ALLOWED, each with a value of the shape it expects. Anything
    else is dropped, and the log says which key, never its value.

    Exceptions are never recorded on a span: their text can carry anything. A span that fails says the kind of
    failure in `error.type`, as the logs do.

    Without OTEL_EXPORTER_OTLP_ENDPOINT spans are made but never leave the process: the context still goes on with
    each event, for whoever traces downstream.

        tracing.configure("gateway-webhook")
        with tracing.span("webhook.notification", **{"gateway.bot_id": bot}) as current:
            ...
            headers = {"traceparent": tracing.traceparent()}
"""
import logging
import os
import re
from contextlib import contextmanager
from uuid import UUID

from opentelemetry import context as otel_context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

__all__ = ["ALLOWED", "SpanKind", "configure", "provider", "span", "traceparent", "safe", "shutdown"]

log = logging.getLogger(__name__)

# The only attributes a span takes, and what their values may be.
UUIDS = {"gateway.bot_id", "gateway.client_id", "gateway.contact_id"}
NUMBERS = {"gateway.messages", "http.response.status_code"}
WORDS = {
    "gateway.channel": {"whatsapp", "messenger", "instagram"},
    "gateway.entity": {"contact", "bot", "client"},
    "gateway.outcome": {"acknowledged", "dropped", "waiting", "ingested", "seen", "failed", "stored",
                        "sent"},
    "messaging.system": {"nats"},
    "http.request.method": {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "OTHER"},
}
# Made by the service, never by the caller: the route as it is declared, and the subjects of Bifröst.
SHAPES = {
    "http.route": re.compile(r"^(/[a-z_\-]*(\{[a-z_]+\})?)+$|^no route$"),
    "messaging.destination.name": re.compile(r"^[A-Z]+$|^[a-z]+(\.[a-z]+)*(\.[0-9]{1,2})?(\.[0-9a-f\-]{36})*$"),
    "error.type": re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,80}$"),
}
ALLOWED = UUIDS | NUMBERS | set(WORDS) | set(SHAPES)

_PROPAGATOR = TraceContextTextMapPropagator()
_provider: TracerProvider | None = None


def safe(key: str, value) -> bool:
    """Whether an attribute may go on a span: a key of ALLOWED, and a value of the shape that key expects."""
    if key in UUIDS:
        try:
            return str(UUID(str(value))) == str(value).lower()
        except ValueError:
            return False
    if key in NUMBERS:
        return isinstance(value, int) and not isinstance(value, bool)
    if key in WORDS:
        return value in WORDS[key]
    if key in SHAPES:
        return isinstance(value, str) and bool(SHAPES[key].match(value))
    return False


def _filtered(attributes: dict) -> dict:
    kept = {}
    for key, value in attributes.items():
        if value is None:
            continue
        if safe(key, value):
            kept[key] = value
        else:
            log.warning(f"An attribute was kept out of a span: {key if key in ALLOWED else 'not allowed'}")
    return kept


def configure(service_name: str | None = None) -> TracerProvider:
    """
        The provider of this process, made once. It exports over OTLP/HTTP only when OTEL_EXPORTER_OTLP_ENDPOINT
        says where; the exporter reads it and the rest of the OTEL_EXPORTER_OTLP_* settings by itself.
    """
    global _provider
    if _provider is not None:
        return _provider
    name = os.environ.get("OTEL_SERVICE_NAME") or service_name or "gateway"
    _provider = TracerProvider(resource=Resource.create({"service.name": name}))
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        _provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    return _provider


def provider() -> TracerProvider:
    return _provider or configure()


def shutdown() -> None:
    """Sends what is left and stops. Registered for when the service stops."""
    if _provider is not None:
        _provider.shutdown()


def _parent(traceparent_header: str | None):
    if not traceparent_header:
        return None
    return _PROPAGATOR.extract({"traceparent": traceparent_header})


@contextmanager
def span(name: str, parent: str | None = None, kind: SpanKind = SpanKind.INTERNAL, traced: bool = True,
         **attributes):
    """
        A span, current while the block runs. `parent` is a traceparent that came with an event: the span goes on
        with that trace. Without it, it is a child of the current span, or the start of a trace. With `traced`
        false there is no span at all, and the block runs the same.
    """
    if not traced:
        yield _NoSpan()
        return
    tracer = provider().get_tracer("gateway")
    context = _parent(parent)
    with tracer.start_as_current_span(name, context=context, kind=kind, attributes=_filtered(attributes),
                                      record_exception=False, set_status_on_exception=False) as current:
        try:
            yield _Span(current)
        except BaseException as e:
            current.set_attribute("error.type", type(e).__name__)
            current.set_status(Status(StatusCode.ERROR))
            raise


class _Span:
    """The span of a block, that only takes allowed attributes."""

    def __init__(self, current):
        self._current = current

    def set(self, **attributes) -> None:
        for key, value in _filtered(attributes).items():
            self._current.set_attribute(key, value)

    def failed(self, kind: str) -> None:
        """A failure the block handled itself: said by its kind, as the logs do."""
        self.set(**{"error.type": kind})
        self._current.set_status(Status(StatusCode.ERROR))


class _NoSpan:
    """What a block that is not traced gets: it takes the same calls and keeps nothing."""

    def set(self, **attributes) -> None:
        pass

    def failed(self, kind: str) -> None:
        pass


def traceparent() -> str | None:
    """The W3C context of the current span, for the event about to be published. None outside a span."""
    carrier = {}
    _PROPAGATOR.inject(carrier, context=otel_context.get_current())
    return carrier.get("traceparent")
