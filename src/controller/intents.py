# -*- coding: utf8 -*-
import logging
from uuid import uuid4, UUID
from asyncio import ensure_future
from inspect import currentframe

from fastapi import Response, status
from mongoengine.errors import OperationError

from classes import Settings, ResponseData
from classes import Intent, Action, ApiCall, ActionParameter, IntentRequest
from constants import APPLICATION_JSON, CONTENT_TYPE, CONTENT_LENGTH
from enums import Status
from repository import TenantRepository, NotFound
from utils import describe
from .events import ctr_notify_action

__all__ = ["ctr_create_intent", "ctr_get_intents", "ctr_get_intent", "ctr_update_intent", "ctr_delete_intent",
           "ctr_get_bot_tools"]

settings = Settings()
log = logging.getLogger(settings.environment)


def _build_actions(request: IntentRequest) -> list:
    """Map the request DTO actions onto embedded mongoengine documents."""
    actions = []
    for action in request.actions:
        api_call = ApiCall(
            method=action.apiCall.method,
            url=action.apiCall.url,
            headers=action.apiCall.headers,
            query=action.apiCall.query,
            body=action.apiCall.body,
            authType=action.apiCall.authType,
            authRef=action.apiCall.authRef,
            timeout=action.apiCall.timeout
        ) if action.apiCall is not None else None

        actions.append(Action(
            name=action.name,
            description=action.description,
            type=action.type,
            parameters=[ActionParameter(
                name=p.name,
                type=p.type,
                description=p.description,
                required=p.required,
                enum=p.enum
            ) for p in action.parameters],
            apiCall=api_call
        ))
    return actions


def ctr_create_intent(repo: TenantRepository, request: IntentRequest, bot_id: str, eventId: str = None) -> Response:
    log.info(f"Starting: {currentframe().f_code.co_name}")
    body = ResponseData(code=status.HTTP_400_BAD_REQUEST, message="BAD REQUEST", data=None)
    _action = uuid4()

    if eventId is not None:
        ensure_future(ctr_notify_action(
            _uuid=_action,
            event_id=UUID(eventId),
            operation="",
            method=currentframe().f_code.co_name
        ))

    response = Response(
        content=body.json(),
        status_code=status.HTTP_400_BAD_REQUEST,
        headers={CONTENT_TYPE: APPLICATION_JSON}
    )

    try:
        _intent = Intent(
            uuid=uuid4(),
            name=request.name,
            description=request.description,
            samplePhrases=request.samplePhrases,
            actions=_build_actions(request)
        )
        repo.add_intent(bot_id, _intent)
        body = ResponseData(code=status.HTTP_200_OK, message="Process completed successfully",
                            data=_intent.intent_info())

    except NotFound as e:
        log.error(describe(e))
        body = ResponseData(code=status.HTTP_404_NOT_FOUND, message=f"This chatbot {bot_id} was not found",
                            data=None)

    except OperationError as e:
        log.error(describe(e))
        body = ResponseData(code=status.HTTP_400_BAD_REQUEST,
                            message=f"This intent already exists {request.name}", data=None)

    except Exception as e:
        log.error(describe(e))
        body = ResponseData(code=status.HTTP_500_INTERNAL_SERVER_ERROR, message=f"INTERNAL SERVER ERROR",
                            data=None)

    if eventId is not None:
        ensure_future(ctr_notify_action(
            _uuid=_action,
            event_id=UUID(eventId),
            description=body.json(),
            status=Status.COM.value
        ))

    response.status_code = body.code
    response.body = body.json(exclude_none=True).encode()
    response.headers[CONTENT_LENGTH] = str(len(response.body))
    response.headers[CONTENT_TYPE] = APPLICATION_JSON

    log.info(f"Ending: {currentframe().f_code.co_name} code:{response.status_code}")
    return response


def ctr_get_intents(repo: TenantRepository, bot_id: str, eventId: str = None) -> Response:
    log.info(f"Starting: {currentframe().f_code.co_name}")
    body = ResponseData(code=status.HTTP_400_BAD_REQUEST, message="BAD REQUEST", data=None)
    _action = uuid4()

    if eventId is not None:
        ensure_future(ctr_notify_action(
            _uuid=_action,
            event_id=UUID(eventId),
            operation="",
            method=currentframe().f_code.co_name
        ))

    response = Response(
        content=body.json(),
        status_code=status.HTTP_400_BAD_REQUEST,
        headers={CONTENT_TYPE: APPLICATION_JSON}
    )

    try:
        _intents = [intent.intent_info() for intent in repo.list_intents(bot_id)]
        body = ResponseData(code=status.HTTP_200_OK, message="Process completed successfully",
                            data=_intents)

    except NotFound as e:
        log.error(describe(e))
        body = ResponseData(code=status.HTTP_404_NOT_FOUND, message=f"This chatbot {bot_id} was not found",
                            data=None)

    except Exception as e:
        log.error(describe(e))
        body = ResponseData(code=status.HTTP_500_INTERNAL_SERVER_ERROR, message=f"INTERNAL SERVER ERROR",
                            data=None)

    if eventId is not None:
        ensure_future(ctr_notify_action(
            _uuid=_action,
            event_id=UUID(eventId),
            description=body.json(),
            status=Status.COM.value
        ))

    response.status_code = body.code
    response.body = body.json(exclude_none=True).encode()
    response.headers[CONTENT_LENGTH] = str(len(response.body))
    response.headers[CONTENT_TYPE] = APPLICATION_JSON

    log.info(f"Ending: {currentframe().f_code.co_name} code:{response.status_code}")
    return response


def ctr_get_intent(repo: TenantRepository, bot_id: str, intent_id: str, eventId: str = None) -> Response:
    log.info(f"Starting: {currentframe().f_code.co_name}")
    body = ResponseData(code=status.HTTP_400_BAD_REQUEST, message="BAD REQUEST", data=None)
    _action = uuid4()

    if eventId is not None:
        ensure_future(ctr_notify_action(
            _uuid=_action,
            event_id=UUID(eventId),
            operation="",
            method=currentframe().f_code.co_name
        ))

    response = Response(
        content=body.json(),
        status_code=status.HTTP_400_BAD_REQUEST,
        headers={CONTENT_TYPE: APPLICATION_JSON}
    )

    try:
        _intent = repo.get_intent(bot_id, intent_id)
        body = ResponseData(code=status.HTTP_200_OK, message="Process completed successfully",
                            data=_intent.intent_info())

    except (ValueError, IndexError, NotFound) as e:
        log.error(describe(e))
        body = ResponseData(code=status.HTTP_404_NOT_FOUND,
                            message=f"This intent {intent_id} was not found", data=None)

    except Exception as e:
        log.error(describe(e))
        body = ResponseData(code=status.HTTP_500_INTERNAL_SERVER_ERROR, message=f"INTERNAL SERVER ERROR",
                            data=None)

    if eventId is not None:
        ensure_future(ctr_notify_action(
            _uuid=_action,
            event_id=UUID(eventId),
            description=body.json(),
            status=Status.COM.value
        ))

    response.status_code = body.code
    response.body = body.json(exclude_none=True).encode()
    response.headers[CONTENT_LENGTH] = str(len(response.body))
    response.headers[CONTENT_TYPE] = APPLICATION_JSON

    log.info(f"Ending: {currentframe().f_code.co_name} code:{response.status_code}")
    return response


def ctr_update_intent(repo: TenantRepository, request: IntentRequest, bot_id: str, intent_id: str,
                            eventId: str = None) -> Response:
    log.info(f"Starting: {currentframe().f_code.co_name}")
    body = ResponseData(code=status.HTTP_400_BAD_REQUEST, message="BAD REQUEST", data=None)
    _action = uuid4()

    if eventId is not None:
        ensure_future(ctr_notify_action(
            _uuid=_action,
            event_id=UUID(eventId),
            operation="",
            method=currentframe().f_code.co_name
        ))

    response = Response(
        content=body.json(),
        status_code=status.HTTP_400_BAD_REQUEST,
        headers={CONTENT_TYPE: APPLICATION_JSON}
    )

    try:
        _intent = repo.get_intent(bot_id, intent_id)
        _intent.name = request.name
        _intent.description = request.description
        _intent.samplePhrases = request.samplePhrases
        _intent.actions = _build_actions(request)
        _intent.statusDate = _intent.statusDate
        repo.save_intent(_intent)
        body = ResponseData(code=status.HTTP_200_OK, message="Process completed successfully",
                            data=_intent.intent_info())

    except (ValueError, IndexError, NotFound) as e:
        log.error(describe(e))
        body = ResponseData(code=status.HTTP_404_NOT_FOUND,
                            message=f"This intent {intent_id} was not found", data=None)

    except Exception as e:
        log.error(describe(e))
        body = ResponseData(code=status.HTTP_500_INTERNAL_SERVER_ERROR, message=f"INTERNAL SERVER ERROR",
                            data=None)

    if eventId is not None:
        ensure_future(ctr_notify_action(
            _uuid=_action,
            event_id=UUID(eventId),
            description=body.json(),
            status=Status.COM.value
        ))

    response.status_code = body.code
    response.body = body.json(exclude_none=True).encode()
    response.headers[CONTENT_LENGTH] = str(len(response.body))
    response.headers[CONTENT_TYPE] = APPLICATION_JSON

    log.info(f"Ending: {currentframe().f_code.co_name} code:{response.status_code}")
    return response


def ctr_delete_intent(repo: TenantRepository, bot_id: str, intent_id: str, eventId: str = None) -> Response:
    log.info(f"Starting: {currentframe().f_code.co_name}")
    body = ResponseData(code=status.HTTP_400_BAD_REQUEST, message="BAD REQUEST", data=None)
    _action = uuid4()

    if eventId is not None:
        ensure_future(ctr_notify_action(
            _uuid=_action,
            event_id=UUID(eventId),
            operation="",
            method=currentframe().f_code.co_name
        ))

    response = Response(
        content=body.json(),
        status_code=status.HTTP_400_BAD_REQUEST,
        headers={CONTENT_TYPE: APPLICATION_JSON}
    )

    try:
        _intent = repo.get_intent(bot_id, intent_id)
        _intent.status = Status.DIS.value
        repo.save_intent(_intent)
        body = ResponseData(code=status.HTTP_200_OK, message="Process completed successfully",
                            data=None)

    except (ValueError, IndexError, NotFound) as e:
        log.error(describe(e))
        body = ResponseData(code=status.HTTP_404_NOT_FOUND,
                            message=f"This intent {intent_id} was not found", data=None)

    except Exception as e:
        log.error(describe(e))
        body = ResponseData(code=status.HTTP_500_INTERNAL_SERVER_ERROR, message=f"INTERNAL SERVER ERROR",
                            data=None)

    if eventId is not None:
        ensure_future(ctr_notify_action(
            _uuid=_action,
            event_id=UUID(eventId),
            description=body.json(),
            status=Status.COM.value
        ))

    response.status_code = body.code
    response.body = body.json(exclude_none=True).encode()
    response.headers[CONTENT_LENGTH] = str(len(response.body))
    response.headers[CONTENT_TYPE] = APPLICATION_JSON

    log.info(f"Ending: {currentframe().f_code.co_name} code:{response.status_code}")
    return response


def ctr_get_bot_tools(repo: TenantRepository, bot_id: str, eventId: str = None) -> Response:
    """
    Aggregate every active action of every active intent of the bot into a flat
    list of LLM tool definitions. This is the bridge consumed by the worker.
    """
    log.info(f"Starting: {currentframe().f_code.co_name}")
    body = ResponseData(code=status.HTTP_400_BAD_REQUEST, message="BAD REQUEST", data=None)
    _action = uuid4()

    if eventId is not None:
        ensure_future(ctr_notify_action(
            _uuid=_action,
            event_id=UUID(eventId),
            operation="",
            method=currentframe().f_code.co_name
        ))

    response = Response(
        content=body.json(),
        status_code=status.HTTP_400_BAD_REQUEST,
        headers={CONTENT_TYPE: APPLICATION_JSON}
    )

    try:
        _tools = []
        for intent in repo.list_intents(bot_id):
            _tools.extend(intent.tool_definitions())
        body = ResponseData(code=status.HTTP_200_OK, message="Process completed successfully",
                            data=_tools)

    except NotFound as e:
        log.error(describe(e))
        body = ResponseData(code=status.HTTP_404_NOT_FOUND, message=f"This chatbot {bot_id} was not found",
                            data=None)

    except Exception as e:
        log.error(describe(e))
        body = ResponseData(code=status.HTTP_500_INTERNAL_SERVER_ERROR, message=f"INTERNAL SERVER ERROR",
                            data=None)

    if eventId is not None:
        ensure_future(ctr_notify_action(
            _uuid=_action,
            event_id=UUID(eventId),
            description=body.json(),
            status=Status.COM.value
        ))

    response.status_code = body.code
    response.body = body.json(exclude_none=True).encode()
    response.headers[CONTENT_LENGTH] = str(len(response.body))
    response.headers[CONTENT_TYPE] = APPLICATION_JSON

    log.info(f"Ending: {currentframe().f_code.co_name} code:{response.status_code}")
    return response
