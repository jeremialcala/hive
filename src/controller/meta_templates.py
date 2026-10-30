# -*- coding: utf-8 -*-
"""
    The WhatsApp templates of a chatbot in Meta (ADR-0031, HIG-85), with the chatbot's WhatsApp token, which needs the
    permission `whatsapp_business_management` besides the one to send (decision of Jeremi of 2026-10-29: one token).

        POST   {graph}/{version}/{waba_id}/message_templates             create: it stays in review
        GET    {graph}/{version}/{waba_id}/message_templates?fields=...   list, a page at a time
        POST   {graph}/{version}/{template_id}                            edit: every component is replaced
        DELETE {graph}/{version}/{waba_id}/message_templates?hsm_id=...&name=...

    Of an answer of Meta that is not good, only its code and its subcode are kept: its message can carry what the template said
    (developers.facebook.com, «Template management» and «Error codes», read on 2026-10-29). Neither the token
    nor the body of a template goes to the log.
"""
import re
from urllib.parse import quote

import requests

__all__ = ["MetaTemplates", "MetaUnavailable", "MetaRefused", "MetaPermission", "META_ID", "TIMEOUT", "MAX_PAGES"]

TIMEOUT = 10        # seconds
MAX_PAGES = 30      # of 100 templates: Meta allows 250 a WhatsApp account, or 6 000 for a verified business
FIELDS = "id,name,language,status,category,components"
# The token is not good, or it lacks the permission to manage templates: the client has to load another.
PERMISSION = {10, 190, 200}
# What Meta's ids look like: anything else is not put into an address, nor stored.
META_ID = re.compile(r"^[0-9]{1,30}$")


def _number(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


class MetaUnavailable(Exception):
    """Meta could not be reached, or is too busy: it may work later. It carries the kind of failure."""


class MetaRefused(Exception):
    """Meta said no to what was asked: it carries Meta's code and subcode, never its message."""

    def __init__(self, code, subcode: int | None = None):
        super().__init__(f"Meta {code}")
        self.code, self.subcode = code, subcode

    @property
    def reason(self) -> str:
        return f"{self.code}/{self.subcode}" if self.subcode is not None else str(self.code)


class MetaPermission(MetaRefused):
    """The chatbot's token is not good, or cannot manage templates (`whatsapp_business_management`)."""


class MetaTemplates:
    def __init__(self, url: str, version: str, timeout: float = TIMEOUT):
        self._base = f"{url.rstrip('/')}/{version}"
        self._timeout = timeout

    def _call(self, method: str, path: str, token: str, **options) -> dict:
        try:
            response = requests.request(method, f"{self._base}/{path}", headers={"Authorization": f"Bearer {token}"},
                                        timeout=self._timeout, allow_redirects=False, **options)
        except (requests.RequestException, OSError) as e:
            # Only the kind: the text of these exceptions carries the address.
            raise MetaUnavailable(type(e).__name__) from None
        if response.status_code == 200:
            try:
                return response.json()
            except ValueError:
                raise MetaUnavailable("an answer that is not JSON") from None
        code, subcode = self._code(response)
        if response.status_code >= 500 or response.status_code == 429 or code in (1, 2, 4, 80008):
            raise MetaUnavailable(f"Meta {code if code is not None else response.status_code}")
        if code in PERMISSION or response.status_code in (401, 403):
            raise MetaPermission(code if code is not None else response.status_code, subcode)
        raise MetaRefused(code if code is not None else response.status_code, subcode)

    @staticmethod
    def _code(response) -> tuple[int | None, int | None]:
        """Meta's code and subcode, when they are numbers: nothing else of the error is read."""
        try:
            error = response.json()["error"]
        except (ValueError, KeyError, TypeError):
            return None, None
        if not isinstance(error, dict):
            return None, None
        return _number(error.get("code")), _number(error.get("error_subcode"))

    def create(self, waba_id: str, token: str, template: dict) -> dict:
        """Meta's id of the new template, its state (in review, most of the time) and its category."""
        made = self._call("POST", f"{quote(waba_id)}/message_templates", token, json=template)
        if not isinstance(made.get("id"), str) or not META_ID.fullmatch(made["id"]):
            raise MetaUnavailable("a template made without its id")
        return made

    def every(self, waba_id: str, token: str) -> tuple[list[dict], bool]:
        """
            The templates of the WhatsApp account, following Meta's pages up to MAX_PAGES, and whether those are all
            of them: only then can a copy that is not among them be taken for one deleted in Meta.
        """
        found, after = [], None
        for _ in range(MAX_PAGES):
            params = {"fields": FIELDS, "limit": 100, **({"after": after} if after else {})}
            page = self._call("GET", f"{quote(waba_id)}/message_templates", token, params=params)
            found.extend(item for item in page.get("data") or [] if isinstance(item, dict))
            after = ((page.get("paging") or {}).get("cursors") or {}).get("after")
            if not after or not (page.get("paging") or {}).get("next"):
                return found, True
        return found, False

    def edit(self, template_id: str, token: str, components: list[dict]) -> None:
        self._call("POST", quote(template_id), token, json={"components": components})

    def remove(self, waba_id: str, token: str, template_id: str, name: str) -> None:
        self._call("DELETE", f"{quote(waba_id)}/message_templates", token,
                   params={"hsm_id": template_id, "name": name})
