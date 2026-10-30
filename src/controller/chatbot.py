# -*- coding: utf8 -*-
import logging
from uuid import uuid4, UUID
from asyncio import ensure_future

from fastapi import Response, status
from mongoengine.errors import OperationError
from inspect import currentframe

from classes import ChatbotRequest, ChatBot, ChatbotPhone, RulesRequest
from classes import Settings, ResponseData, ChatbotPhoneRequest
from constants import APPLICATION_JSON, CONTENT_TYPE, CONTENT_LENGTH
from crypto.key_service import KeyServiceUnavailable
from repository import TenantRepository, NotFound
from utils import describe
from .events import ctr_notify_action
from enums import Status

__all__ = ["ctr_create_chatbot", "ctr_get_chatbot_from_uuid", "ctr_add_phone_chatbot", "ctr_get_chatbot_phones",
           "ctr_get_chatbot_rules_from_uuid", "ctr_create_chatbot_rules"]

settings = Settings()
log = logging.getLogger(settings.environment)


def ctr_create_chatbot(repo: TenantRepository, request: ChatbotRequest, eventId: str = None) -> Response:
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
        _chatbot = ChatBot.from_json(request.json())
        _chatbot.uuid = uuid4()
        _chatbot.generate_verification_token()
        # Kept before it is stored: once in the repository the document only holds it encrypted.
        _verify_token = _chatbot.verifyToken
        repo.add_bot(_chatbot)
        body = ResponseData(code=status.HTTP_200_OK, message="Process completed successfully",
                            data={**_chatbot.chatbot_info(), "verifyToken": _verify_token})

    except KeyServiceUnavailable as e:
        log.error(f"Key service unavailable: {e}")
        body = ResponseData(code=status.HTTP_503_SERVICE_UNAVAILABLE, message="SERVICE UNAVAILABLE",
                            data=None)

    except OperationError as e:
        log.error(describe(e))
        body = ResponseData(code=status.HTTP_400_BAD_REQUEST, message=f"This chatbot already exists {request.name}",
                            data=None)

    except Exception as e:
        log.error(describe(e))
        body = ResponseData(code=status.HTTP_500_INTERNAL_SERVER_ERROR, message=f"INTERNAL SERVER ERROR",
                            data=None)

    finally:

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


def ctr_get_chatbot_from_uuid(repo: TenantRepository, _uuid: str, eventId: str = None,
                              with_verify_token: bool = True) -> Response:
    """The card of a chatbot. A cliente-analista gets it without the verifyToken (D6 of the role × action matrix)."""
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
        _chatbot = repo.get_bot(_uuid)
        # The verification token is the client's to paste into Meta. The Meta tokens are not returned.
        info = _chatbot.chatbot_info()
        if with_verify_token:
            info["verifyToken"] = repo.bot_secrets(_chatbot).verify_token
        body = ResponseData(code=status.HTTP_200_OK, message="Process completed successfully", data=info)
    except KeyServiceUnavailable as e:
        log.error(f"Key service unavailable: {e}")
        body = ResponseData(code=status.HTTP_503_SERVICE_UNAVAILABLE, message="SERVICE UNAVAILABLE",
                            data=None)
    except (ValueError, NotFound) as e:
        log.error(describe(e))
        body = ResponseData(code=status.HTTP_404_NOT_FOUND, message=f"This chatbot {_uuid} was not found",
                            data=None)
    except Exception as e:
        log.error(describe(e))
        body = ResponseData(code=status.HTTP_500_INTERNAL_SERVER_ERROR, message=f"INTERNAL SERVER ERROR",
                            data=None)
    finally:

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


def ctr_add_phone_chatbot(repo: TenantRepository, bot_uuid, req: ChatbotPhoneRequest, eventId: str = None):
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
        _chatbot_phone = ChatbotPhone().generate_chatbot_phone_from_request(req=req)
        _chatbot_phone.uuid = uuid4()
        _chatbot_phone.status = Status.ACT.value
        repo.add_phone(bot_uuid, _chatbot_phone)
        body = ResponseData(code=status.HTTP_200_OK, message="Process completed successfully",
                            data=None)
    except KeyServiceUnavailable as e:
        log.error(f"Key service unavailable: {e}")
        body = ResponseData(code=status.HTTP_503_SERVICE_UNAVAILABLE, message="SERVICE UNAVAILABLE",
                            data=None)
    except NotFound as e:
        log.error(describe(e))
        body = ResponseData(code=status.HTTP_404_NOT_FOUND, message=f"This chatbot {bot_uuid} was not found",
                            data=None)
    except Exception as e:
        log.error(describe(e))
        body = ResponseData(code=status.HTTP_500_INTERNAL_SERVER_ERROR, message=f"INTERNAL SERVER ERROR",
                            data=None)
    finally:

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


def ctr_get_chatbot_phones(repo: TenantRepository, bot_uuid: str, environment: str | None, eventId: str = None):
    log.info(f"Starting: {currentframe().f_code.co_name}")
    log.info("Listing the phones of a chatbot")
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
        _phones = [{**phone.get_phones_info(), **repo.phone_numbers(phone)}
                   for phone in repo.list_phones(bot_uuid, environment)]
        if environment is not None:
            # Asking for one environment answers with a single phone, the last one registered for it.
            if not _phones:
                raise NotFound(environment)
            _phones = _phones[-1]

        body = ResponseData(code=status.HTTP_200_OK, message="Process completed successfully",
                            data=_phones)
    except KeyServiceUnavailable as e:
        log.error(f"Key service unavailable: {e}")
        body = ResponseData(code=status.HTTP_503_SERVICE_UNAVAILABLE, message="SERVICE UNAVAILABLE",
                            data=None)
    except (ValueError, NotFound) as e:
        log.error(describe(e))
        body = ResponseData(code=status.HTTP_404_NOT_FOUND, message=f"RECORD NOT FOUND")

    except Exception as e:
        log.error(describe(e))
        body = ResponseData(code=status.HTTP_500_INTERNAL_SERVER_ERROR, message=f"INTERNAL SERVER ERROR",
                            data=None)
    finally:
        if eventId is not None:
            ensure_future(ctr_notify_action(
                _uuid=_action,
                event_id=UUID(eventId),
                operation="",
                method=currentframe().f_code.co_name,
                description=body.json(),
                status=Status.COM.value
            ))

        response.status_code = body.code
        response.body = body.json(exclude_none=True).encode()
        response.headers[CONTENT_LENGTH] = str(len(response.body))
        response.headers[CONTENT_TYPE] = APPLICATION_JSON

        log.info(f"Ending: {currentframe().f_code.co_name} code:{response.status_code}")
    return response


def ctr_get_chatbot_rules_from_uuid(repo: TenantRepository, _uuid: str, eventId: str = None) -> Response:
    """
        The persona of a chatbot and its rules, as data: the response engine writes Claude's `system` with them
        (ADR-0009 of the engine). Until E4 this was a prompt already formatted for Llama 2, for the engine of
        before.
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
        _chatbot = repo.get_bot(_uuid)
        body = ResponseData(code=status.HTTP_200_OK, message="Process completed successfully",
                            data={"name": _chatbot.name, "role": _chatbot.role, "tone": _chatbot.tone,
                                  "description": _chatbot.description,
                                  "rules": [rule.rule for rule in repo.list_rules(_uuid)]})

    except (ValueError, NotFound) as e:
        log.error(describe(e))
        body = ResponseData(code=status.HTTP_404_NOT_FOUND, message=f"This chatbot {_uuid} was not found",
                            data=None)
    except Exception as e:
        log.error(describe(e))
        body = ResponseData(code=status.HTTP_500_INTERNAL_SERVER_ERROR, message=f"INTERNAL SERVER ERROR",
                            data=None)
    finally:
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


def ctr_create_chatbot_rules(repo: TenantRepository, request: RulesRequest, bot_id: str, eventId: str = None) -> Response:
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
        repo.add_rules(bot_id, request.rules)
        body = ResponseData(code=status.HTTP_200_OK, message="Process completed successfully",
                            data=None)

    except NotFound as e:
        log.error(describe(e))
        body = ResponseData(code=status.HTTP_404_NOT_FOUND, message=f"This chatbot {bot_id} was not found",
                            data=None)

    except OperationError as e:
        log.error(describe(e))
        body = ResponseData(code=status.HTTP_400_BAD_REQUEST, message=f"This chatbot already exists {request.name}",
                            data=None)

    except Exception as e:
        log.error(describe(e))
        body = ResponseData(code=status.HTTP_500_INTERNAL_SERVER_ERROR, message=f"INTERNAL SERVER ERROR",
                            data=None)

    finally:

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
