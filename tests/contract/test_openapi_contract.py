# -*- coding: utf-8 -*-
"""
    The committed OpenAPI contracts must be what the two services actually serve: the management API and the
    webhook.
    If this fails, regenerate it:  python .ai-dlc/scripts/gen_openapi.py
"""
import importlib.util
import pathlib
import re

import pytest
import yaml

import app as app_module
import webhook as webhook_module
from constants import DESCRIPTION, WEBHOOK_DESCRIPTION

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "docs" / "02-design" / "contracts"
SERVICES = {"management": (app_module, CONTRACTS / "openapi.yaml", DESCRIPTION, "app"),
            "webhook": (webhook_module, CONTRACTS / "openapi-webhook.yaml", WEBHOOK_DESCRIPTION, "webhook")}
GENERATOR = ROOT / ".ai-dlc" / "scripts" / "gen_openapi.py"


def load_generator():
    spec = importlib.util.spec_from_file_location("gen_openapi", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("service", sorted(SERVICES))
def test_committed_openapi_matches_the_application(service):
    generator = load_generator()
    module, contract, _, name = SERVICES[service]
    committed = contract.read_text(encoding="utf-8").replace("\r\n", "\n")

    assert committed == generator.volcar(module.app.openapi(), generator.PUBLICO[name])


@pytest.mark.parametrize("service,at_least", [("management", 20), ("webhook", 2)])
def test_the_description_names_no_route_the_application_does_not_serve(service, at_least):
    """It described an /answers endpoint that was never there."""
    def shape(path: str) -> str:
        return re.sub(r"\{[^}]*\}", "{}", path)

    module, _, description, _ = SERVICES[service]
    served = {shape(route.path) for route in module.app.routes}
    named = re.findall(r"(?<![\w}])(/[a-z][\w{}/-]*)", description)

    assert len(named) >= at_least
    for path in named:
        assert shape(path) in served, path
    assert "answers" not in module.app.openapi()["info"]["description"]


def test_volatile_keys_are_dropped_but_property_names_are_kept():
    generator = load_generator()
    schema = {"default": 1, "properties": {"default": {"type": "string", "examples": ["a"]}}}

    assert generator.normalizar(schema) == {"properties": {"default": {"type": "string"}}}


@pytest.mark.parametrize("service,public", [("management", "https://hive-api.higerotech.com"),
                                            ("webhook", "https://hive-webhook.higerotech.com")])
def test_a_published_contract_says_where_the_api_is_and_names_nobody(service, public):
    """
        The management contract is published at hive.higerotech.com/docs (ADR-0026). It carries no contact and no
        terms (Jeremi's decision of 2026-10-26), and no address but the API's own and the standards it cites.
    """
    _, contract, _, _ = SERVICES[service]
    text = contract.read_text(encoding="utf-8")
    spec = yaml.safe_load(text)

    assert spec["servers"] == [{"url": public}]
    assert not {"contact", "termsOfService"} & set(spec["info"])
    assert not re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", text)
    assert "web-ones" not in text
