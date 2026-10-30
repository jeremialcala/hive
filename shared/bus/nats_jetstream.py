# -*- coding: utf-8 -*-
"""
    The event publisher on NATS JetStream (ADR-0019): one connection per process, kept open and reconnected by the
    client, instead of one connection per message as with RabbitMQ. Each event goes with Nats-Msg-Id, so that the
    stream drops a second copy published within its window, and the publisher waits for the stream's acknowledgement
    (PubAck): Bifröst writes to disk before it gives one.
"""
import asyncio
import logging
import secrets
import ssl
import time

import nats.errors
import nats.js.errors
from nats.aio.client import Client

from . import BusUnavailable, Published

log = logging.getLogger(__name__)

ACK_TIMEOUT = 3      # seconds for the acknowledgement: inside what Meta waits for, and well above the fsync of I0
CONNECT_TIMEOUT = 2  # seconds for each attempt to reach the bus
RETRY_AFTER = 1.0    # seconds without trying again after a failed attempt: a bus that is down costs no wait
COUNT_ATTEMPTS = 20  # writes of a counter that lose against another process before it gives up
COUNT_BACKOFF = 0.02 # seconds at most of wait, at random, before writing again after losing
JITTER = secrets.SystemRandom()


class NatsPublisher:

    def __init__(self, url: str, user: str | None = None, password: str | None = None,
                 ca_file: str | None = None, inbox_prefix: str = "_INBOX", name: str = "gateway"):
        self._url = url
        self._user = user
        self._password = password
        self._ca_file = ca_file
        # Bifröst gives each service its own prefix of reply subjects, so that nobody reads another's answers.
        self._inbox_prefix = inbox_prefix
        self._name = name
        self._connection = None
        self._jetstream = None
        self._loop = None
        self._failed_at = None
        # A lock, like a connection, belongs to one event loop: both are made again if the loop changes.
        self._lock = None
        self._lock_loop = None
        # The counters of one process are written one at a time: only another process can then win a write.
        self._count_lock = None
        self._count_lock_loop = None

    def _tls(self) -> ssl.SSLContext | None:
        if not self._url.startswith("tls://"):
            return None
        return ssl.create_default_context(cafile=self._ca_file)

    async def _errored(self, error: Exception) -> None:
        # Only the kind of failure: the text can carry the address.
        log.warning(f"Event bus: {type(error).__name__}")

    async def _connect(self):
        """
            The connection of this process, opened the first time it is needed. Once open, the client reconnects
            on its own and for as long as it takes; a publication made meanwhile waits in its buffer until the
            acknowledgement times out.
        """
        loop = asyncio.get_running_loop()
        if self._open(loop):
            return self._jetstream
        if self._lock_loop is not loop:
            self._lock, self._lock_loop = asyncio.Lock(), loop
        async with self._lock:
            if self._open(loop):
                return self._jetstream
            if self._failed_at is not None and time.monotonic() - self._failed_at < RETRY_AFTER:
                raise BusUnavailable("not reachable")
            connection = Client()
            try:
                # Bounded by hand: with reconnection forever, the client also retries the first connection forever,
                # a wrong password included.
                await asyncio.wait_for(connection.connect(
                    self._url, user=self._user, password=self._password, tls=self._tls(), name=self._name,
                    inbox_prefix=self._inbox_prefix, connect_timeout=CONNECT_TIMEOUT, allow_reconnect=True,
                    max_reconnect_attempts=-1, error_cb=self._errored), timeout=CONNECT_TIMEOUT + 1)
            except (nats.errors.Error, OSError, asyncio.TimeoutError) as e:
                self._failed_at = time.monotonic()
                await self._abandon(connection)
                raise BusUnavailable(type(e).__name__) from None
            self._connection, self._jetstream, self._loop = connection, connection.jetstream(), loop
            self._failed_at = None
            return self._jetstream

    @staticmethod
    async def _abandon(connection: Client) -> None:
        """A connection that never opened still has its tasks running: they are stopped, or they keep retrying."""
        try:
            await connection.close()
        except (nats.errors.Error, OSError, asyncio.TimeoutError) as e:
            # Best-effort cleanup: the original connect failure is handled by the caller.
            log.debug("Event bus: ignoring close failure during abandon (%s)", type(e).__name__)

    def _open(self, loop) -> bool:
        return self._connection is not None and self._loop is loop and not self._connection.is_closed

    async def publish(self, subject: str, payload: bytes, message_id: str,
                      headers: dict[str, str] | None = None) -> Published:
        if not message_id:
            raise ValueError("An event needs a message_id: it is what the bus drops a second copy by")
        jetstream = await self._connect()
        try:
            ack = await jetstream.publish(subject, payload, timeout=ACK_TIMEOUT,
                                          headers={**(headers or {}), "Nats-Msg-Id": message_id})
        except (nats.errors.Error, OSError, asyncio.TimeoutError) as e:
            # No stream for the subject, a subject the user may not publish to, a full buffer while reconnecting,
            # or no acknowledgement in time: in every case the event may not be kept, and it is sent again.
            raise BusUnavailable(type(e).__name__) from None
        return Published(stream=ack.stream, sequence=ack.seq, duplicate=bool(ack.duplicate))

    async def count(self, bucket: str, key: str) -> int:
        """
            Adds one to the counter `key` of the bucket and says how many it holds now. Two callers at the same time
            do not lose a count: each write says which revision it read, and the one that loses tries again.
        """
        jetstream = await self._connect()
        loop = asyncio.get_running_loop()
        if self._count_lock_loop is not loop:
            self._count_lock, self._count_lock_loop = asyncio.Lock(), loop
        async with self._count_lock:
            return await self._count(jetstream, bucket, key)

    @staticmethod
    async def _count(jetstream, bucket: str, key: str) -> int:
        try:
            store = await jetstream.key_value(bucket)
            for attempt in range(COUNT_ATTEMPTS):
                if attempt:
                    await asyncio.sleep(JITTER.uniform(0, COUNT_BACKOFF))
                try:
                    entry = await store.get(key)
                except nats.js.errors.KeyNotFoundError:
                    try:
                        await store.create(key, b"1")
                        return 1
                    except nats.js.errors.KeyWrongLastSequenceError:
                        continue
                value = int(entry.value or b"0") + 1
                try:
                    await store.update(key, str(value).encode(), last=entry.revision)
                    return value
                except nats.js.errors.KeyWrongLastSequenceError:
                    continue
        except (nats.errors.Error, OSError, asyncio.TimeoutError, ValueError) as e:
            raise BusUnavailable(type(e).__name__) from None
        raise BusUnavailable("too many writers at once")

    async def close(self) -> None:
        connection, self._connection, self._jetstream = self._connection, None, None
        if connection is not None and not connection.is_closed and self._loop is asyncio.get_running_loop():
            await connection.drain()
