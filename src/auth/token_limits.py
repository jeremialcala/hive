# -*- coding: utf-8 -*-
"""
    How often POST /oauth/token is answered (ADR-0028). Every request decrypts a JWE with RSA and a good one writes to
    MongoDB, so a burst costs the service even when every assertion in it is rejected. Three limits, each a sliding
    window kept in this process:

        * an origin asks for at most ORIGIN_REQUESTS in ORIGIN_WINDOW;
        * an origin whose assertions were rejected FAILURES times in FAILURES_WINDOW is turned away until the oldest of
          them leaves the window;
        * a client's key gets at most KEY_TOKENS access tokens in KEY_WINDOW.

    Failures are counted by origin and never by client: whoever knows a client's identifier could otherwise lock it
    out by sending bad assertions in its name. Cloudflare limits the same path by IP in front of this (the rule is in
    docs/03-implementation/autenticacion.md); this is what the edge of the free plan cannot do.

    The counters live in memory: the management API runs one worker (WEB_CONCURRENCY), and a restart clears them.
"""
import ipaddress
import math
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

ORIGIN_REQUESTS, ORIGIN_WINDOW = 20, 60.0          # per origin, a minute
FAILURES, FAILURES_WINDOW = 10, 600.0              # rejected assertions per origin, ten minutes
KEY_TOKENS, KEY_WINDOW = 30, 3600.0                # access tokens per client key, an hour: a client asks for ~6
# Above this many keys, the expired ones are dropped, and if that is not enough, the oldest: a burst from many
# origins costs memory up to here and no further.
MAX_KEYS = 10_000

# Set by Cloudflare on every request that comes through the tunnel. The management API is only reached through the
# tunnel or from the private networks of midgard (its port is published on loopback), so nobody else can set it.
CLIENT_IP_HEADER = "cf-connecting-ip"


@dataclass(frozen=True)
class Refusal:
    """Why a request was turned away, and in how many seconds the same request would be let in."""
    reason: str
    retry_after: int


class _Windows:
    """Sliding windows by key: the times of what was counted in the last `window` seconds."""

    def __init__(self, limit: int, window: float, clock: Callable[[], float]):
        self.limit, self.window, self._clock = limit, window, clock
        self._seen: dict[str, deque] = {}

    def _live(self, key: str) -> deque:
        times = self._seen.get(key)
        if times is None:
            return deque()
        cutoff = self._clock() - self.window
        while times and times[0] <= cutoff:
            times.popleft()
        if not times:
            del self._seen[key]
        return times

    def full(self, key: str) -> int | None:
        """The seconds until `key` has room again, or None if it has room now."""
        times = self._live(key)
        if len(times) < self.limit:
            return None
        return max(1, math.ceil(times[0] + self.window - self._clock()))

    def count(self, key: str) -> None:
        if key not in self._seen and len(self._seen) >= MAX_KEYS:
            self._shrink()
        self._seen.setdefault(key, deque()).append(self._clock())

    def _shrink(self) -> None:
        for key in list(self._seen):
            self._live(key)
        while len(self._seen) >= MAX_KEYS:
            del self._seen[next(iter(self._seen))]

    def __len__(self) -> int:
        return len(self._seen)


class TokenLimits:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.requests = _Windows(ORIGIN_REQUESTS, ORIGIN_WINDOW, clock)
        self.failures = _Windows(FAILURES, FAILURES_WINDOW, clock)
        self.tokens = _Windows(KEY_TOKENS, KEY_WINDOW, clock)

    def admit(self, origin: str) -> Refusal | None:
        """Before anything of the request is read: an origin that failed too often, or asks too often, waits."""
        wait = self.failures.full(origin)
        if wait is not None:
            return Refusal("failures", wait)
        wait = self.requests.full(origin)
        if wait is not None:
            return Refusal("requests", wait)
        self.requests.count(origin)
        return None

    def failed(self, origin: str) -> None:
        self.failures.count(origin)

    def issue(self, client_id, kid: str) -> Refusal | None:
        """Once the assertion is good: a key that already got its share of tokens this hour gets no more."""
        key = f"{client_id}/{kid}"
        wait = self.tokens.full(key)
        if wait is not None:
            return Refusal("tokens", wait)
        self.tokens.count(key)
        return None


def origin_of(headers, peer: str | None) -> str:
    """
        Who is asking: the address Cloudflare saw, or the peer's on the private networks. An IPv6 address counts by
        its /64, which is what one subscriber gets: anything finer would let one machine look like many.
    """
    for candidate in (headers.get(CLIENT_IP_HEADER), peer):
        try:
            address = ipaddress.ip_address((candidate or "").strip())
        except ValueError:
            continue
        if address.version == 6:
            if address.ipv4_mapped is not None:
                return str(address.ipv4_mapped)
            return str(ipaddress.ip_network(f"{address}/64", strict=False))
        return str(address)
    return "unknown"


LIMITS = TokenLimits()
