# -*- coding: utf-8 -*-
"""
    The event bus, as the rest of the gateway sees it: a port to publish an event and wait until the bus has kept
    it (ADR-0019). Nothing outside this package knows which bus it is; today it is NATS JetStream, Bifröst.

    One publisher per process, set once when the service starts and closed when it stops:

        bus.configure(NatsPublisher(...))
        await bus.publisher().publish("webhook.meta.whatsapp", payload, message_id)
"""
from dataclasses import dataclass
from typing import Protocol

__all__ = ["BusUnavailable", "EventPublisher", "Published", "configure", "configured", "publisher", "close"]


class BusUnavailable(Exception):
    """
        The bus did not keep the event: it could not be reached, refused it, or did not acknowledge it in time.
        The caller answers 503, so that whoever sent it sends it again.
    """


@dataclass(frozen=True)
class Published:
    """What the bus says once it has kept an event."""
    stream: str
    sequence: int
    duplicate: bool  # It already had an event with that message_id: this one was not kept again.


class EventPublisher(Protocol):

    async def publish(self, subject: str, payload: bytes, message_id: str,
                      headers: dict[str, str] | None = None) -> Published:
        """
            Publishes the event, with its headers, and returns once the bus has written it. The same message_id
            published again within the bus's window is not kept twice: the answer says it was a duplicate. Raises
            BusUnavailable.
        """

    async def close(self) -> None:
        """Lets go of the connection, waiting for what is in flight."""


_publisher: EventPublisher | None = None


def configure(publisher: EventPublisher | None) -> None:
    """Sets the publisher of this process. Done once, when the service starts."""
    global _publisher
    _publisher = publisher


def configured() -> bool:
    """Whether this process has a publisher: without NATS_URL it has none."""
    return _publisher is not None


def publisher() -> EventPublisher:
    if _publisher is None:
        raise RuntimeError("No event bus configured: set NATS_URL")
    return _publisher


async def close() -> None:
    """Closes the publisher, if there is one. Registered for when the service stops."""
    if _publisher is not None:
        await _publisher.close()
