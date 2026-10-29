# -*- coding: utf-8 -*-
"""
    The WhatsApp templates of a client's chatbot (ADR-0031, RF30, HIG-85): Hive creates, edits and deletes them in
    Meta, with the chatbot's WhatsApp token and the id of its WhatsApp Business Account, and keeps a copy of each with
    its state. The send route (outbound.py) only sends a template whose copy says APPROVED.

    Meta reviews what is created or edited: the copy says PENDING until Meta's answer comes back, with the webhook of
    the template states (HIG-86) or a sync (GET with ?sync=true), which brings Meta's list over the copy.

    The log says which chatbot and which of Meta's ids, never what the template says: its text is the client's. Of
    what Meta answers, only its code (meta_templates.py).
"""
import logging
import re

from fastapi import status
from fastapi.responses import JSONResponse

from classes import MessageTemplate, Settings, TemplateCreateRequest, TemplateEditRequest
from classes.dto_templates import values_in
from classes.entity_outbound import CATEGORIES
from classes.tool_clock import as_utc
from crypto.key_service import KeyServiceUnavailable
from repository import NotFound, TenantRepository

from .answers import answer
from .meta_templates import META_ID, MetaPermission, MetaRefused, MetaTemplates, MetaUnavailable

__all__ = ["ctr_create_template", "ctr_list_templates", "ctr_edit_template", "ctr_delete_template", "template_info"]

settings = Settings()
log = logging.getLogger(settings.environment)

meta = MetaTemplates(settings.meta_graph_url, settings.meta_graph_version)

PENDING = "PENDING"
# What Meta lets be edited: an approved template, one it turned away, and one it paused for its quality.
EDITABLE = ("APPROVED", "REJECTED", "PAUSED")
# What Meta does not let be deleted.
DISABLED = "DISABLED"
# What Meta answers for an object that does not exist, or that the token cannot see: the same, on purpose.
NOT_THERE = (100, 33)
# What a state or a category of Meta looks like: anything else is not stored as one.
WORD = re.compile(r"^[A-Z_]{1,30}$")
NAME = re.compile(r"^[a-z0-9_]{1,512}$")
LANGUAGE = re.compile(r"^[a-z]{2,3}(_[A-Za-z0-9]{2,8})?$")

BOT_NOT_FOUND = "This chatbot was not found"
TEMPLATE_NOT_FOUND = "This template was not found"
UNAVAILABLE = "SERVICE UNAVAILABLE"


def template_info(template: MessageTemplate) -> dict:
    return {"id": template.metaId, "name": template.name, "language": template.language,
            "category": template.category, "status": template.status, "parameters": template.parameters,
            "components": list(template.components or []), "updatedAt": as_utc(template.updatedAt)}


def _refused(code: str, why: str) -> JSONResponse:
    return answer(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{code}: {why}")


def _account(repo: TenantRepository, bot) -> tuple[str, str] | JSONResponse:
    """The WhatsApp Business Account of the chatbot and its token, or why there are not."""
    if not bot.wabaId:
        return _refused("NO_WABA", "load the id of the chatbot's WhatsApp Business Account, wabaId, with "
                                   "PUT /chat/{bot_id}/credentials")
    token = repo.bot_secrets(bot).whatsapp_token
    if not token:
        return _refused("NO_TOKEN", "load the chatbot's WhatsApp token with PUT /chat/{bot_id}/credentials")
    return bot.wabaId, token


def _meta_failed(error: Exception, bot, doing: str) -> JSONResponse:
    if isinstance(error, MetaPermission):
        log.warning(f"Meta turned the WhatsApp token of chatbot {bot.uuid} away to {doing} templates: "
                    f"{error.reason}")
        return answer(status.HTTP_502_BAD_GATEWAY,
                      "META_PERMISSION: Meta did not take the chatbot's WhatsApp token to manage its templates: it "
                      "needs the permission whatsapp_business_management, on the chatbot's WhatsApp Business Account")
    if isinstance(error, MetaRefused):
        log.info(f"Meta did not {doing} a template of chatbot {bot.uuid}: {error.reason}")
        if (error.code, error.subcode) == NOT_THERE:
            return _refused("META_REFUSED", f"Meta did not take it, with its code {error.reason}: the WhatsApp "
                                            "Business Account wabaId is not one this token reaches, or the token "
                                            "lacks the permission whatsapp_business_management")
        return _refused("META_REFUSED", f"Meta did not take it, with its code {error.reason}")
    log.error(f"Meta could not be reached to {doing} templates of chatbot {bot.uuid}: {error}")
    return answer(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE)


def _state(value, otherwise: str) -> str:
    return value if isinstance(value, str) and WORD.fullmatch(value) else otherwise


def _from_meta(item: dict) -> MessageTemplate | None:
    """
        Hive's copy of a template as Meta lists it, or None for one it does not keep: one of a category the v1 does
        not send (ADR-0031), or that does not look like what Meta gives.
    """
    name, language, meta_id = item.get("name"), item.get("language"), item.get("id")
    components = item.get("components")
    if not (isinstance(name, str) and NAME.fullmatch(name) and isinstance(language, str)
            and LANGUAGE.fullmatch(language) and isinstance(meta_id, str) and META_ID.fullmatch(meta_id)
            and item.get("category") in CATEGORIES):
        return None
    components = [component for component in components if isinstance(component, dict)] \
        if isinstance(components, list) else []
    body = next((component.get("text") for component in components if component.get("type") == "BODY"), "")
    return MessageTemplate(name=name, language=language, category=item["category"], metaId=meta_id,
                           status=_state(item.get("status"), PENDING), components=components,
                           parameters=len(set(values_in(body))) if isinstance(body, str) else 0)


def _bot(repo: TenantRepository, bot_id: str):
    try:
        return repo.get_bot(bot_id)
    except NotFound:
        return None


def _template(repo: TenantRepository, bot, template_id: str) -> MessageTemplate | None:
    # An id that cannot be Meta's is answered like one that is not there: it goes neither to Meta nor to the log.
    return repo.template_by_meta_id(bot.uuid, template_id) if META_ID.fullmatch(template_id or "") else None


def ctr_create_template(repo: TenantRepository, bot_id: str, request: TemplateCreateRequest) -> JSONResponse:
    """Creates the template in Meta, where it goes to review, and keeps its copy as Meta says it stays."""
    bot = _bot(repo, bot_id)
    if bot is None:
        return answer(status.HTTP_404_NOT_FOUND, BOT_NOT_FOUND)
    try:
        account = _account(repo, bot)
    except KeyServiceUnavailable as e:
        log.error(f"Key service unavailable: {e}")
        return answer(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE)
    if isinstance(account, JSONResponse):
        return account
    waba_id, token = account
    try:
        made = meta.create(waba_id, token, request.for_meta())
    except (MetaUnavailable, MetaRefused) as e:
        return _meta_failed(e, bot, "create")
    template = MessageTemplate(name=request.name, language=request.language, metaId=made["id"],
                               category=made["category"] if made.get("category") in CATEGORIES else request.category,
                               status=_state(made.get("status"), PENDING), parameters=request.values(),
                               components=request.components_for_meta())
    try:
        repo.save_template(bot, template)
    except NotFound:
        return answer(status.HTTP_404_NOT_FOUND, BOT_NOT_FOUND)
    log.info(f"Template {template.metaId} created in Meta for chatbot {bot.uuid}: {template.status}")
    return answer(status.HTTP_201_CREATED, data=template_info(template))


def ctr_list_templates(repo: TenantRepository, bot_id: str, sync: bool = False) -> JSONResponse:
    """The copies of the chatbot's templates; with sync, brought from Meta first."""
    bot = _bot(repo, bot_id)
    if bot is None:
        return answer(status.HTTP_404_NOT_FOUND, BOT_NOT_FOUND)
    if sync:
        try:
            account = _account(repo, bot)
        except KeyServiceUnavailable as e:
            log.error(f"Key service unavailable: {e}")
            return answer(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE)
        if isinstance(account, JSONResponse):
            return account
        try:
            found, complete = meta.every(*account)
        except (MetaUnavailable, MetaRefused) as e:
            return _meta_failed(e, bot, "list")
        kept = set()
        for template in filter(None, map(_from_meta, found)):
            try:
                kept.add(repo.save_template(bot, template).key)
            except NotFound:
                return answer(status.HTTP_404_NOT_FOUND, BOT_NOT_FOUND)
        # Only with every page of Meta: a copy that is not in a list cut short may still be in Meta.
        gone = repo.forget_templates(bot.uuid, kept) if complete else 0
        log.info(f"Templates of chatbot {bot.uuid} brought from Meta: {len(kept)} kept, {gone} no longer there"
                 + ("" if complete else ", the list cut short"))
    return answer(status.HTTP_200_OK, data={"templates": [template_info(t) for t in repo.list_templates(bot.uuid)]})


def ctr_edit_template(repo: TenantRepository, bot_id: str, template_id: str,
                      request: TemplateEditRequest) -> JSONResponse:
    """Replaces every component of the template in Meta, which reviews it again: till then it is not sent."""
    bot = _bot(repo, bot_id)
    if bot is None:
        return answer(status.HTTP_404_NOT_FOUND, BOT_NOT_FOUND)
    template = _template(repo, bot, template_id)
    if template is None:
        return answer(status.HTTP_404_NOT_FOUND, TEMPLATE_NOT_FOUND)
    if template.status not in EDITABLE:
        return answer(status.HTTP_409_CONFLICT, f"A template {template.status} is not edited: only one "
                                                f"{', '.join(EDITABLE[:-1])} or {EDITABLE[-1]}")
    try:
        account = _account(repo, bot)
    except KeyServiceUnavailable as e:
        log.error(f"Key service unavailable: {e}")
        return answer(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE)
    if isinstance(account, JSONResponse):
        return account
    try:
        meta.edit(template.metaId, account[1], request.components_for_meta())
    except (MetaUnavailable, MetaRefused) as e:
        return _meta_failed(e, bot, "edit")
    template.components, template.parameters, template.status = request.components_for_meta(), request.values(), PENDING
    try:
        repo.save_template(bot, template)
    except NotFound:
        return answer(status.HTTP_404_NOT_FOUND, BOT_NOT_FOUND)
    log.info(f"Template {template.metaId} of chatbot {bot.uuid} edited in Meta: in review again")
    return answer(status.HTTP_200_OK, data=template_info(template))


def ctr_delete_template(repo: TenantRepository, bot_id: str, template_id: str) -> JSONResponse:
    """
        Deletes the template in Meta, in this language only, and its copy. Meta does not let its name be used
        again for 30 days, nor a disabled one be deleted.
    """
    bot = _bot(repo, bot_id)
    if bot is None:
        return answer(status.HTTP_404_NOT_FOUND, BOT_NOT_FOUND)
    template = _template(repo, bot, template_id)
    if template is None:
        return answer(status.HTTP_404_NOT_FOUND, TEMPLATE_NOT_FOUND)
    if template.status == DISABLED:
        return answer(status.HTTP_409_CONFLICT, "A template DISABLED is not deleted: Meta does not let it")
    try:
        account = _account(repo, bot)
    except KeyServiceUnavailable as e:
        log.error(f"Key service unavailable: {e}")
        return answer(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE)
    if isinstance(account, JSONResponse):
        return account
    try:
        meta.remove(account[0], account[1], template.metaId, template.name)
    except (MetaUnavailable, MetaRefused) as e:
        return _meta_failed(e, bot, "delete")
    repo.delete_template(template)
    log.info(f"Template {template.metaId} of chatbot {bot.uuid} deleted in Meta and in Hive")
    return answer(status.HTTP_200_OK, data={"id": template.metaId, "deleted": True})
