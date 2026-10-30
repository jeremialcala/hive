# -*- coding: utf-8 -*-
"""
    How many messages a client may send to its contacts (ADR-0031, RS27): 60 a minute for each chatbot and 1 000 a
    day for each client, texts and templates together. Meta has limits of its own; these stop a stolen session, or a
    mistake of the client, before Meta lowers the quality of its number or blocks it.

    The counters live in Bifröst, in the bucket `limites`, so that a restart of the API does not set them to zero.
    Without a bus, in development and in the tests, they live in memory. A bus that does not answer turns the send
    away (it fails closed): a message that is not sent can be sent again, one that floods cannot be taken back.
"""
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from shared import bus

__all__ = ["PER_BOT_A_MINUTE", "PER_CLIENT_A_DAY", "BUCKET", "Refusal", "SendLimits", "LIMITS"]

PER_BOT_A_MINUTE = 60
PER_CLIENT_A_DAY = 1000
BUCKET = "limites"


@dataclass(frozen=True)
class Refusal:
    reason: str
    retry_after: int


class SendLimits:
    def __init__(self, per_bot_a_minute: int = PER_BOT_A_MINUTE, per_client_a_day: int = PER_CLIENT_A_DAY):
        self.per_bot_a_minute = per_bot_a_minute
        self.per_client_a_day = per_client_a_day
        self._counts: dict[str, int] = {}
        self._lock = threading.Lock()

    async def take(self, client_id: UUID, bot_id: UUID, now: datetime | None = None,
                   per_bot_a_minute: int | None = None, per_client_a_day: int | None = None) -> Refusal | None:
        """
            Counts one send of that chatbot and client, and says why not if it is one too many. The limits of a
            client the operator raised come as arguments.
        """
        moment = now or datetime.now(timezone.utc)
        minute = f"bot.{bot_id}.{moment:%Y%m%d%H%M}"
        day = f"client.{client_id}.{moment:%Y%m%d}"
        if await self._count(minute) > (per_bot_a_minute or self.per_bot_a_minute):
            return Refusal("sends of this chatbot this minute", 60 - moment.second)
        if await self._count(day) > (per_client_a_day or self.per_client_a_day):
            midnight = 86400 - (moment.hour * 3600 + moment.minute * 60 + moment.second)
            return Refusal("sends of this client today", midnight)
        return None

    async def _count(self, key: str) -> int:
        if bus.configured() and hasattr(bus.publisher(), "count"):
            return await bus.publisher().count(BUCKET, key)
        with self._lock:
            self._counts[key] = self._counts.get(key, 0) + 1
            return self._counts[key]


LIMITS = SendLimits()
