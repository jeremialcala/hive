# -*- coding: utf-8 -*-
"""
    The audit of a client (RF18, HIG-95; decisions of Jeremi of 2026-11-01): what its people and its credentials did
    through the management API, read by its cliente-admin, by pages and as CSV. The cliente-analista has no route to it
    (D5), and the response engine neither.

    What it keeps, one entry per request, once the answer went out (`entry_for`):

    * every request that changes something, and how it came out: done, denied (403) or failed;
    * the reads that open personal data: a contact's conversation, its export, and a search of contacts;
    * what the response engine read with the client's keys (decision of Jeremi): which worker, and what.

    Only identifiers, the route by its template and the parameters of its address that are identifiers (RS07). It is
    written after the answer is sent, so that nobody waits for it, the engine least of all. An entry that cannot be
    written is left in the log, without its resources: the request it is about already happened.
"""
import csv
import io
import logging
import re
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import status
from fastapi.responses import JSONResponse, Response

from auth.principal import Principal
from classes import AuditEntry, Settings
from classes.entity_audit import CATEGORIES, RESULTS
from classes.tool_clock import as_utc
from enums import Role
from repository import TenantRepository, database_failed

from .answers import answer

__all__ = ["entry_for", "write_entry", "ctr_audit", "KEPT_FOR"]

settings = Settings()
log = logging.getLogger(settings.environment)

KEPT_FOR = timedelta(days=365)
CHANGES = {"POST", "PUT", "PATCH", "DELETE"}
# The reads that open personal data.
SENSITIVE_READS = {("GET", "/chat/{bot_id}/contacts/{contact_id}/conversation"),
                   ("GET", "/chat/{bot_id}/contacts/{contact_id}")}
# Searching contacts is a POST that reads: it is kept as what it is.
READS_BY_POST = {("POST", "/contacts/search")}
# What a parameter of an address may be to be kept: an id, a number, a key's name. Anything else is "?".
SAFE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
ROUTES_OF = {
    "people": ("/people",),
    "keys": ("/keys", "/clients/{client_id}/keys", "/clients/{client_id}/service-keys",
             "/chat/{bot_id}/credentials", "/chat/{bot_id}/verify-token"),
}
DATA = {("DELETE", "/bot/{bot_id}"), ("DELETE", "/chat/{bot_id}/contacts/{contact_id}"),
        ("DELETE", "/clients/{client_id}"), ("PUT", "/chat/{bot_id}/contacts/{contact_id}/consent"),
        ("DELETE", "/chat/{bot_id}/contacts/{contact_id}/consent"),
        ("PUT", "/chat/{bot_id}/contacts/{contact_id}/ratings")}
SENDS = {("POST", "/chat/{bot_id}/contacts/{contact_id}/messages")}
MAX_PAGE = 200
# The rows a CSV carries at most: with what the engine reads, a year of a busy client is hundreds of thousands, and
# the whole of it would not fit in the memory of the gateway. The answer says when it was cut (X-Audit-Truncated).
MAX_CSV = 50_000


def _category(method: str, route: str, principal: Principal) -> str:
    if principal.role is Role.SERVICE:
        return "engine"
    if (method, route) in SENSITIVE_READS | READS_BY_POST | DATA:
        return "data"
    if (method, route) in SENDS:
        return "sends"
    for category, prefixes in ROUTES_OF.items():
        if any(route == prefix or route.startswith(prefix + "/") for prefix in prefixes):
            return category
    return "config"


def _kept(method: str, route: str, principal: Principal) -> bool:
    if principal.role is Role.SERVICE:
        return True
    return method in CHANGES or (method, route) in SENSITIVE_READS


def _result(code: int) -> str:
    return "ok" if code < 400 else "denied" if code == status.HTTP_403_FORBIDDEN else "failed"


def _kind(principal: Principal) -> str:
    if principal.role is Role.SERVICE:
        return "engine"
    if principal.role is Role.OPERATOR:
        return "operator"
    return "person" if principal.is_person else "machine"


def entry_for(principal: Principal | None, method: str, route, path_params: dict, code: int) -> AuditEntry | None:
    """
        The entry a request leaves, or None if it leaves none: one without a principal (not authenticated, or the
        health check), or one the audit does not keep.
    """
    template = getattr(route, "path", None)
    if principal is None or template is None or not _kept(method, template, principal):
        return None
    now = datetime.now(timezone.utc)
    resource = {name: str(value) if SAFE.match(str(value)) else "?" for name, value in (path_params or {}).items()}
    return AuditEntry(
        id=str(uuid4()), clientId=principal.client_id, at=now, actor=principal.actor, actorKind=_kind(principal),
        worker=principal.worker.value if principal.worker else None, action=getattr(route, "name", "") or template,
        method=method, route=template, category=_category(method, template, principal),
        resource=resource, ids=sorted({value for value in resource.values() if value != "?"}),
        result=_result(code), status=code, expiresAt=now + KEPT_FOR)


def write_entry(entry: AuditEntry) -> None:
    """Runs once the answer went out (a background task of the response). A failure stays in the log, and only that."""
    try:
        TenantRepository(entry.clientId).record_audit(entry)
    except Exception as e:
        if not database_failed(e):
            log.error(f"An entry of the audit of client {entry.clientId} could not be written: {type(e).__name__}")
            return
        log.error(f"The audit of client {entry.clientId} lost an entry ({entry.action}, {entry.result}): the "
                  f"database is unavailable")


def _moment(value: str | None) -> datetime | None:
    if not value:
        return None
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _csv(entries: list[dict], total: int) -> Response:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["at", "actor", "actorKind", "worker", "action", "method", "route", "category", "resource",
                     "result", "status"])
    for each in entries:
        resource = " ".join(f"{name}={value}" for name, value in sorted(each["resource"].items()))
        writer.writerow([each["at"].isoformat(), each["actor"], each["actorKind"], each["worker"] or "",
                         each["action"], each["method"], each["route"], each["category"], resource, each["result"],
                         each["status"]])
    headers = {"Content-Disposition": 'attachment; filename="auditoria.csv"', "Cache-Control": "no-store",
               "X-Audit-Total": str(total)}
    if total > len(entries):
        headers["X-Audit-Truncated"] = "true"
    return Response(content=out.getvalue(), media_type="text/csv; charset=utf-8", headers=headers)


def ctr_audit(repo: TenantRepository, since: str | None, until: str | None, category: str | None, actor: str | None,
              action: str | None, resource: str | None, result: str | None, offset: int, limit: int,
              as_csv: bool) -> Response | JSONResponse:
    """The client's audit, newest first, by pages, with its filters; or all that matches, as CSV."""
    try:
        start, end = _moment(since), _moment(until)
    except ValueError:
        return answer(status.HTTP_422_UNPROCESSABLE_ENTITY, "since and until are dates in ISO 8601")
    if category is not None and category not in CATEGORIES:
        return answer(status.HTTP_422_UNPROCESSABLE_ENTITY, f"category is one of {', '.join(CATEGORIES)}")
    if result is not None and result not in RESULTS:
        return answer(status.HTTP_422_UNPROCESSABLE_ENTITY, f"result is one of {', '.join(RESULTS)}")
    if resource is not None:
        try:
            resource = str(UUID(resource))
        except ValueError:
            return answer(status.HTTP_422_UNPROCESSABLE_ENTITY, "resource is the id of a resource")
    total, found = repo.audit(start, end, category, actor, action, resource, result,
                              0 if as_csv else max(offset, 0), MAX_CSV if as_csv else max(1, min(limit, MAX_PAGE)))
    entries = [{**entry.info(), "at": as_utc(entry.at)} for entry in found]
    if as_csv:
        return _csv(entries, total)
    return answer(status.HTTP_200_OK, data={"total": total, "offset": offset, "limit": limit, "entries": entries,
                                            "keptFor": KEPT_FOR.days})
