# -*- coding: utf8 -*-
import logging
import asyncio

from uuid import UUID
from inspect import currentframe

from classes import EventTransport, Event, EventAction, Jwk
from classes import Settings
from classes.tool_clock import as_utc, utc_now
from enums import Status
from utils import describe

__all__ = ["ctr_create_event", "ctr_notify_action"]

settings = Settings()
log = logging.getLogger(settings.environment)


async def ctr_create_event(event: EventTransport):
    """
    This method only creates events

    :param event: Event DTO that handles the validation for all events.
    :return: Void
    """
    try:
        log.info(f"Starting: {currentframe().f_code.co_name}")
        log.info(f"Creating the Event {event.uuid}")
        _event = Event(**event.model_dump())
        _event.save()
        asyncio.ensure_future(Jwk().create_key_for_event(event.uuid))
        log.info(f"Ending: {currentframe().f_code.co_name}")
    except Exception as e:
        log.error(describe(e))


async def ctr_notify_action(_uuid: UUID, event_id: UUID, operation: str = "", method: str = "", step: int = 1,
                            description: str = "", status: int = Status.ACT.value):
    """
    On this method we will register the current moment of the process to real time tracking.

    :param _uuid:
    :param event_id: Unique identifier of the current Event
    :param operation:
    :param method: this is the method's name
    :param step: this is the position of this action on the event.
    :param description:
    :param status:
    :return:
    """
    log.info(f"Starting: {currentframe().f_code.co_name}")
    try:
        match status:
            case Status.ACT.value:
                _action = EventAction(uuid=_uuid, eventId=event_id, operation=operation, method=method, step=step,
                                      description=description, status=status)
                _action.save(force_insert=True)

            case _:
                _action = EventAction.get_event_action_by_uuid(_uuid)
                _action.description = description
                _action.status = status
                _action.totalTime = (utc_now() - as_utc(_action.createdAt)).total_seconds()
                _action.save()

        log.info(f"Ending: {currentframe().f_code.co_name}")
    except Exception as e:
        log.error(describe(e))
