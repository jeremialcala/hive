# -*- coding: utf-8 -*-
"""
    The compose of Zitadel on Nidavellir (deploy/idp/docker-compose.yml, ADR-0029): what it must keep whatever is
    changed in it later. Every image pinned by digest; the database only on an internal network; nothing published on
    a port of the machine; the tunnel only in front of the proxy; no secret written in it.
"""
import re
from pathlib import Path

import pytest
import yaml

COMPOSE = Path(__file__).resolve().parents[2] / "deploy" / "idp" / "docker-compose.yml"
SERVICES = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
NETWORKS = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["networks"]


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_every_image_is_pinned_by_digest(name):
    assert re.fullmatch(r"[\w./-]+:[\w.-]+@sha256:[0-9a-f]{64}", SERVICES[name]["image"]), name


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_every_container_drops_its_capabilities_and_cannot_gain_privileges(name):
    service = SERVICES[name]

    assert service["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in service["security_opt"]
    assert "mem_limit" in service and "cpus" in service


def test_only_the_database_entrypoint_and_the_caddy_binary_get_capabilities_back():
    """The database entrypoint drops to its user; the caddy binary carries cap_net_bind_service, without which it
    cannot even be executed. Nothing else gets any back."""
    added = {name: service.get("cap_add") for name, service in SERVICES.items() if service.get("cap_add")}

    assert added == {"db": ["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"], "proxy": ["NET_BIND_SERVICE"]}


def test_nothing_is_published_on_a_port_of_the_machine():
    assert not [name for name, service in SERVICES.items() if service.get("ports")]


def test_the_database_lives_only_on_an_internal_network_with_the_api():
    assert NETWORKS["datos"]["internal"] is True
    assert SERVICES["db"]["networks"] == ["datos"]
    assert [name for name, service in SERVICES.items() if "datos" in service["networks"]] == ["db", "api"]


def test_the_tunnel_reaches_only_the_proxy():
    caddyfile = (COMPOSE.parent / "Caddyfile").read_text(encoding="utf-8")

    assert SERVICES["tunel"]["depends_on"] == {"proxy": {"condition": "service_healthy"}}
    assert "admin off" in caddyfile and "reverse_proxy h2c://hive-idp:8080" in caddyfile


def test_the_root_of_the_domain_goes_to_the_console():
    """The login page opened on its own ends in the user's account, not in the console (2026-10-28)."""
    caddyfile = (COMPOSE.parent / "Caddyfile").read_text(encoding="utf-8")

    assert "redir @root /ui/console 302" in caddyfile
    assert "rewrite @root" not in caddyfile


def test_no_secret_is_written_in_the_compose():
    """Every secret comes from the .env that generar-secretos.sh writes on the machine, and is required."""
    text = COMPOSE.read_text(encoding="utf-8")
    for variable in ("ZITADEL_MASTERKEY", "POSTGRES_ADMIN_PASSWORD", "POSTGRES_ZITADEL_PASSWORD",
                     "LOGIN_SESSION_COOKIE_SECRET", "ZITADEL_ADMIN_PASSWORD", "TUNNEL_TOKEN"):
        assert re.search(rf"\${{{variable}:\?", text), variable
    assert not re.search(r"(?i)(password|secret|token|masterkey)\s*:\s*['\"]?[A-Za-z0-9+/=]{16,}", text)


def test_the_access_log_of_zitadel_stays_off():
    """It writes addresses and paths: personal data (RS07)."""
    assert SERVICES["api"]["environment"]["ZITADEL_LOGSTORE_ACCESS_STDOUT_ENABLED"] == "false"


def test_the_first_administrator_changes_the_generated_password():
    assert SERVICES["api"]["environment"]["ZITADEL_FIRSTINSTANCE_ORG_HUMAN_PASSWORDCHANGEREQUIRED"] == "true"
