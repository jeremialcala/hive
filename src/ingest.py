# -*- coding: utf-8 -*-
"""
    The ingestion worker. It serves no route, and reads two streams of Bifröst, each with its own durable consumer,
    one event after another (controller/ingestion.py):

        * WEBHOOK, with `ingesta`: the notifications the webhook published (ADR-0021). Each message is identified,
          published for the engine and stored in the history.
        * RESPUESTAS, with `historial`: what the engine's notifier sent and Meta accepted (ADR-0017). Each answer is
          stored in the history.

        python ingest.py

    Each event is acknowledged once it is done. One that has to wait (the database, Andvari, Bifröst or Hermod are
    not there) is not acknowledged: Bifröst delivers it again after the consumer's backoff, 30 s, 1 min, 5 min... for
    as long as the stream keeps it. One that can never be ingested is dropped.

    It is alive while it touches ALIVE_FILE: the container's health check looks at how old that file is.
"""
import asyncio
import json
import logging
import pathlib
import signal
import ssl
from dataclasses import dataclass
from typing import Awaitable, Callable

import nats
import nats.errors

from shared import bus
import service  # Settings, logging, the key service and the publisher of this process.
from shared import tracing
from controller.ingestion import RETRY_LATER, Unprocessable, ingest_notification, ingest_reply
from utils import configure_logging, describe

settings = service.settings
log = logging.getLogger(settings.environment)

BATCH = 10           # events asked for at a time: they are still ingested one after the other
WAIT = 5             # seconds a request for events waits when there are none
ALIVE_FILE = pathlib.Path("/tmp/ingest-alive")


@dataclass(frozen=True)
class Feed:
    """One stream the worker reads, with Bifröst's consumer for it and what is done with each event."""
    stream: str
    consumer: str
    ingest: Callable[[dict, str | None], Awaitable[dict]]


async def _notification(event: dict, trace: str | None) -> dict:
    # Looked up here when it is called, not when the Feed is made, so that a test can replace it in this module.
    return await ingest_notification(event, trace)


async def _answer(event: dict, trace: str | None) -> dict:
    return await ingest_reply(event, trace)


NOTIFICATIONS = Feed("WEBHOOK", "ingesta", _notification)
ANSWERS = Feed("RESPUESTAS", "historial", _answer)
FEEDS = (NOTIFICATIONS, ANSWERS)


async def handle(message, feed: Feed = NOTIFICATIONS) -> str:
    """What is done with one event: acknowledged, dropped, or left for Bifröst to deliver again."""
    try:
        event = json.loads(message.data)
    except (ValueError, UnicodeDecodeError):
        log.error(f"An event of {feed.stream} is not JSON: dropped")
        await message.term()
        return "dropped"
    trace = (message.headers or {}).get("traceparent")
    try:
        outcome = await feed.ingest(event, trace)
    except Unprocessable as e:
        log.warning(f"An event of {feed.stream} cannot be ingested, dropped: {e}")
        await message.term()
        return "dropped"
    except RETRY_LATER as e:
        log.error(f"An event of {feed.stream} has to wait: {type(e).__name__}")
        return "waiting"
    except Exception as e:
        # Not known to be of the event: it is tried again rather than lost.
        log.error(f"An event of {feed.stream} could not be ingested: {describe(e)}")
        return "waiting"
    await message.ack()
    log.info(f"Event of {feed.stream} ingested: {outcome}")
    return "acknowledged"


async def consume(subscription, stop: asyncio.Event, feed: Feed = NOTIFICATIONS) -> None:
    while not stop.is_set():
        ALIVE_FILE.touch()
        try:
            messages = await subscription.fetch(BATCH, timeout=WAIT)
        except asyncio.TimeoutError:
            # Nothing to fetch in WAIT seconds. nats-py says so with its own TimeoutError, a subclass of this one,
            # and sometimes with this one itself: when the time runs out between its two requests to Bifröst.
            # Catching only its own let that one through, and the worker died every few minutes.
            continue
        except nats.errors.Error as e:
            # The connection is reconnecting, or Bifröst is restarting: the client recovers on its own.
            log.warning(f"Waiting for {feed.stream}: {type(e).__name__}")
            await asyncio.sleep(1)
            continue
        for message in messages:
            await handle(message, feed)


async def connect(url: str, user: str | None, password: str | None, ca: str | None, inbox_prefix: str | None):
    tls = None
    if url.startswith("tls://"):
        tls = ssl.create_default_context(cafile=ca or None)
    return await nats.connect(url, user=user, password=password, tls=tls, name="ingest",
                              inbox_prefix=inbox_prefix or "_INBOX", max_reconnect_attempts=-1)


async def bind(connection, feed: Feed):
    """
        The consumers are Bifröst's, made when it was provisioned: they are bound, never created here, and the
        worker's user may not create one.
    """
    return await connection.jetstream().pull_subscribe_bind(durable=feed.consumer, stream=feed.stream)


async def main() -> None:
    configure_logging()
    if not settings.nats_url:
        raise SystemExit("The ingestion worker needs NATS_URL")
    stop = asyncio.Event()
    for name in ("SIGTERM", "SIGINT"):
        if hasattr(signal, name):
            try:
                asyncio.get_running_loop().add_signal_handler(getattr(signal, name), stop.set)
            except NotImplementedError:
                # Windows has no signal handlers in the event loop: there, Ctrl+C stops the worker as it is.
                pass
    connection = await connect(settings.nats_url, settings.nats_user, settings.nats_password, settings.nats_ca,
                               settings.nats_inbox_prefix)
    try:
        subscriptions = [await bind(connection, feed) for feed in FEEDS]
        log.info("Ingesting " + ", ".join(f"{feed.stream} with the consumer {feed.consumer}" for feed in FEEDS))
        # One loop per stream: an answer does not wait behind a notification. Each one keeps its own order.
        await asyncio.gather(*(consume(subscription, stop, feed) for subscription, feed in zip(subscriptions, FEEDS)))
    finally:
        await connection.drain()
        await bus.close()
        tracing.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
