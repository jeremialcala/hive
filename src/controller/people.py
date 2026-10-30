# -*- coding: utf-8 -*-
"""
    The people of a client, managed by its cliente-admin (RF16, ADR-0029, HIG-94): invited by their email, listed, given
    another role, blocked and removed. What changes is felt at the person's next exchange of a token, within 10 minutes.

    Inviting goes to Zitadel first, with Hive's service account (zitadel.py): a person who is already a user there is
    linked, and one who is not is created, and Zitadel sends them the invitation to set up their account and the MFA.
    Then Hive records them as invited. A person may be of more than one client.

    The last active cliente-admin of a client cannot be demoted, blocked or removed: nobody would be left to manage
    its people. The machine credential keeps its permissions (D3), but it is not a person who can be let in again.

    Each invitation, change and removal leaves a warning, the level production keeps, with who did it, to whom and
    what: it is the audit of the people until the client's own (RF18, HIG-95). Never the email.
"""
import logging

from fastapi import status
from fastapi.responses import JSONResponse

from classes import ClientUser, PersonChangeRequest, PersonInviteRequest, Settings
from classes.tool_clock import as_utc
from crypto.key_service import KeyServiceUnavailable
from enums import PersonRole, PersonStatus
from repository import Conflict, NotFound, TenantRepository

from .answers import answer
from .zitadel import ZitadelRefused, ZitadelUnavailable, ZitadelUsers

__all__ = ["ctr_invite_person", "ctr_list_people", "ctr_change_person", "ctr_remove_person", "person_info"]

settings = Settings()
log = logging.getLogger(settings.environment)

PERSON_NOT_FOUND = "This person was not found"
UNAVAILABLE = "SERVICE UNAVAILABLE"
LAST_ADMIN = "LAST_ADMIN: the last active cliente-admin of the client stays one: make another one first"


def _users() -> ZitadelUsers | None:
    if not (settings.idp_issuer and settings.idp_organization_id and settings.idp_token_file):
        return None
    return ZitadelUsers(settings.idp_api_url or settings.idp_issuer, settings.idp_organization_id,
                        settings.idp_token_file)


# Hive's service account in Zitadel. Without it configured, nobody is invited.
users = _users()


def person_info(repo: TenantRepository, person: ClientUser) -> dict:
    return {"uuid": str(person.uuid), "email": repo.person_email(person), "role": person.role,
            "status": person.status, "invitedBy": person.invitedBy, "invitedAt": as_utc(person.invitedAt),
            "statusDate": as_utc(person.statusDate)}


def _unavailable(error: Exception) -> JSONResponse:
    log.error(f"Zitadel could not be reached to invite a person: {error}")
    return answer(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE)


def ctr_invite_person(repo: TenantRepository, request: PersonInviteRequest, actor: str) -> JSONResponse:
    try:
        if repo.find_person_by_email(request.email) is not None:
            return answer(status.HTTP_409_CONFLICT, "ALREADY_INVITED: this person is already of the client")
        if users is None:
            log.error("Nobody can be invited: Hive's service account in Zitadel is not configured "
                      "(IDP_ORGANIZATION_ID, IDP_TOKEN_FILE)")
            return answer(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE)
        user_id, sent = users.find_by_email(request.email), False
        if user_id is None:
            local = request.email.split("@", 1)[0]
            user_id = users.create(request.email, request.givenName or local, request.familyName or local)
            users.invite(user_id)
            sent = True
        person = repo.add_person(request.email, settings.idp_issuer, user_id, PersonRole(request.role),
                                 invited_by=actor)
    except ZitadelUnavailable as e:
        return _unavailable(e)
    except ZitadelRefused as e:
        log.warning(f"Zitadel did not take a person invited to client {repo.client_id}: {e.reason}")
        return answer(status.HTTP_422_UNPROCESSABLE_ENTITY, f"IDP_REFUSED: Zitadel did not take it ({e.reason})")
    except Conflict:
        return answer(status.HTTP_409_CONFLICT, "ALREADY_INVITED: this person is already of the client")
    except KeyServiceUnavailable as e:
        log.error(f"Key service unavailable: {e}")
        return answer(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE)
    log.warning(f"Audit: {actor} invited person {person.uuid} to client {repo.client_id} as {person.role}: "
                + ("Zitadel sent them the invitation" if sent else "they were already a user of Zitadel"))
    return answer(status.HTTP_201_CREATED, data=person_info(repo, person))


def ctr_list_people(repo: TenantRepository) -> JSONResponse:
    try:
        people = [person_info(repo, person) for person in repo.list_people()]
    except KeyServiceUnavailable as e:
        log.error(f"Key service unavailable: {e}")
        return answer(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE)
    return answer(status.HTTP_200_OK, data={"people": people})


def _the_last_admin(repo: TenantRepository, person: ClientUser) -> bool:
    return (person.role == PersonRole.ADMIN.value and person.status == PersonStatus.ACTIVE.value
            and repo.active_admins() <= 1)


def _person(repo: TenantRepository, person_id: str) -> ClientUser | None:
    try:
        return repo.get_person(person_id)
    except NotFound:
        return None


def ctr_change_person(repo: TenantRepository, person_id: str, request: PersonChangeRequest,
                      actor: str) -> JSONResponse:
    person = _person(repo, person_id)
    if person is None:
        return answer(status.HTTP_404_NOT_FOUND, PERSON_NOT_FOUND)
    if person.status == PersonStatus.REMOVED.value:
        return answer(status.HTTP_409_CONFLICT, "REMOVED: this person was removed; invite them again")
    stops_being_admin = request.role == PersonRole.ANALYST.value or request.status == PersonStatus.BLOCKED.value
    if stops_being_admin and _the_last_admin(repo, person):
        return answer(status.HTTP_409_CONFLICT, LAST_ADMIN)
    before = (person.role, person.status)
    try:
        if request.role is not None:
            person = repo.set_person_role(person, PersonRole(request.role))
        if request.status is not None:
            person = repo.set_person_status(person, PersonStatus(request.status))
        info = person_info(repo, person)
    except NotFound:
        return answer(status.HTTP_404_NOT_FOUND, PERSON_NOT_FOUND)
    except KeyServiceUnavailable as e:
        log.error(f"Key service unavailable: {e}")
        return answer(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE)
    log.warning(f"Audit: {actor} changed person {person.uuid} of client {repo.client_id}: role {before[0]} -> "
                f"{person.role}, status {before[1]} -> {person.status}")
    return answer(status.HTTP_200_OK, data=info)


def ctr_remove_person(repo: TenantRepository, person_id: str, actor: str) -> JSONResponse:
    person = _person(repo, person_id)
    if person is None:
        return answer(status.HTTP_404_NOT_FOUND, PERSON_NOT_FOUND)
    if person.status != PersonStatus.REMOVED.value:
        if _the_last_admin(repo, person):
            return answer(status.HTTP_409_CONFLICT, LAST_ADMIN)
        try:
            person = repo.remove_person(person)
        except NotFound:
            return answer(status.HTTP_404_NOT_FOUND, PERSON_NOT_FOUND)
        log.warning(f"Audit: {actor} removed person {person.uuid} of client {repo.client_id}: no more tokens, and "
                    f"their email erased")
    return answer(status.HTTP_200_OK, data=person_info(repo, person))
