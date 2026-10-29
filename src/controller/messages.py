# -*- coding: utf8 -*-
"""
    What the webhook does with a signed notification it understood (ADR-0021). With the bus, it only publishes the
    notification as one event and Meta is answered once Bifröst keeps it: who wrote, the history and the queue of
    the response engine are the ingestion worker's (ingestion.py). Without a bus, which only development and the
    tests have, it ingests the same event itself before answering.
"""
import logging
from asyncio import ensure_future
from collections import Counter
from datetime import datetime, timezone
from inspect import currentframe
from uuid import uuid4, UUID

from shared import bus
from classes import ChatBot
from classes import Settings, FacebookRequest
from enums import Status
from .events import ctr_notify_action
from .incoming import READERS, incoming_messages, template_changes
from .ingestion import DatabaseUnavailable, RETRY_LATER, ingest_notification
from .webhook_event import CHANNELS, publish_notification, publish_template_changes, template_event, webhook_event

__all__ = ["ctr_process_messages", "DatabaseUnavailable", "RETRY_LATER"]

settings = Settings()
log = logging.getLogger(settings.environment)


async def ctr_process_messages(req: FacebookRequest, _bot: ChatBot, eventId: str = None):
    """
        Raises one of RETRY_LATER when Meta has to send the notification again: Bifröst did not keep the event or,
        without a bus, the ingestion has to wait.
    """
    log.info(f"Starting: {currentframe().f_code.co_name}")
    _action = uuid4()
    if eventId is not None:
        ensure_future(ctr_notify_action(
            _uuid=_action,
            event_id=UUID(eventId),
            operation="",
            status=Status.ACT.value,
            method=currentframe().f_code.co_name
        ))

    if req.object not in READERS:
        log.warning(f"A notification for chatbot {_bot.uuid} is about a kind of object the service does not handle")
        return

    left_out = []
    incoming = incoming_messages(req.object, req.entry, left_out)
    log.info(f"Messages from a person in this notification: {len(incoming)}")
    if left_out:
        log.warning(f"Messages of a notification for chatbot {_bot.uuid} were left out: {dict(Counter(left_out))}")

    if incoming and bus.configured():
        await publish_notification(req.object, _bot, incoming)
    elif incoming:
        event = webhook_event(CHANNELS[req.object], _bot, incoming, datetime.now(timezone.utc))
        await ingest_notification(event, bot=_bot)

    # The notices of the templates' states (HIG-86): an event of their own, which the ingestion applies to the copies.
    changes = template_changes(req.object, req.entry)
    if changes:
        log.info(f"Notices of templates for chatbot {_bot.uuid} in this notification: {len(changes)}")
    if changes and bus.configured():
        await publish_template_changes(_bot, changes)
    elif changes:
        await ingest_notification(template_event(_bot, changes, datetime.now(timezone.utc)), bot=_bot)

    if eventId is not None:
        ensure_future(ctr_notify_action(
            _uuid=_action,
            event_id=UUID(eventId),
            operation="",
            method=currentframe().f_code.co_name,
            description="This process completed the way we needed",
            status=Status.COM.value
        ))

    log.info(f"Ending: {currentframe().f_code.co_name}")
