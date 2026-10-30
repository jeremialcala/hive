# -*- coding: utf-8 -*-
"""
    The role × action matrix, against the code (HIG-92). Its section «Rutas y roles» says, for every route of hive-api
    that asks for a token, which roles reach it; here the same is worked out from the code: the dependencies of each
    route, ANALYST_ROUTES and WORKER_ROUTES. A route added, or a role that changes, fails this until the matrix says it.
"""
import pathlib
import re

from fastapi.routing import APIRoute

import app as app_module

MATRIX = pathlib.Path(__file__).resolve().parents[2] / "docs" / "02-design" / "matriz-rol-accion.md"
PUBLIC = {("GET", "/health"), ("POST", "/oauth/token")}
ROW = re.compile(r"^\| `(GET|POST|PUT|DELETE) (/[^`]*)` \| (✅|⛔) \| (✅|⛔) \| (✅|⛔) \| (✅|⛔) \|$")


def the_matrix() -> dict:
    text = MATRIX.read_text(encoding="utf-8")
    section = text.split("## Rutas y roles", 1)[1].split("\n## ", 1)[0]
    rows = {}
    for line in section.splitlines():
        found = ROW.match(line.strip())
        if found:
            method, path, *marks = found.groups()
            assert (method, path) not in rows, f"{method} {path} twice in the matrix"
            rows[(method, path)] = tuple(mark == "✅" for mark in marks)
    return rows


def _calls(dependant):
    for dependency in dependant.dependencies:
        yield dependency.call
        yield from _calls(dependency)


def the_code() -> dict:
    rows = {}
    for route in app_module.app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods:
            if (method, route.path) in PUBLIC:
                continue
            calls = set(_calls(route.dependant))
            operator, engine_only = app_module.require_operator in calls, app_module.require_service in calls
            rows[(method, route.path)] = (not operator and not engine_only,
                                          (method, route.path) in app_module.ANALYST_ROUTES,
                                          (method, route.path) in app_module.SERVICE_ROUTES, operator)
    return rows


def test_every_route_that_asks_for_a_token_is_in_the_matrix_and_nothing_else():
    assert sorted(the_matrix()) == sorted(the_code())


def test_each_role_reaches_in_the_code_what_the_matrix_says():
    matrix, code = the_matrix(), the_code()
    differ = {f"{method} {path}": {"matrix": matrix[(method, path)], "code": code[(method, path)]}
              for method, path in code if matrix.get((method, path)) != code[(method, path)]}

    assert differ == {}


def test_what_the_analyst_reaches_is_a_route_of_the_application():
    assert app_module.ANALYST_ROUTES <= set(the_code())


def test_the_analyst_reaches_nothing_of_the_operator_nor_only_of_the_engine():
    code = the_code()
    assert not [route for route in app_module.ANALYST_ROUTES if code[route][3] or not code[route][0]]
