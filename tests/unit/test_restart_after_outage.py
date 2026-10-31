# -*- coding: utf-8 -*-
"""
    Hive comes back by itself after a power cut of midgard (RNF06, HIG-116; docs/05-deployment/arranque-tras-apagon.md):
    Docker starts with the system and brings back every container with `restart: unless-stopped`. This fails if a
    service of the production composes loses it, and if the list of containers Thor watches goes out of date.
"""
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSES = ["deploy/docker-compose.yml", "engine/deploy/docker-compose.yml", "portal/deploy/docker-compose.yml"]
RUNBOOK = ROOT / "docs" / "05-deployment" / "arranque-tras-apagon.md"


def services():
    for compose in COMPOSES:
        for name, service in yaml.safe_load((ROOT / compose).read_text(encoding="utf-8"))["services"].items():
            # A one-off of the operator (engine-keys) runs and ends: it is not brought back.
            if "operations" not in (service.get("profiles") or []):
                yield compose, name, service


@pytest.mark.parametrize("compose,name,service", list(services()), ids=lambda part: part if isinstance(part, str) else "")
def test_every_service_comes_back_by_itself(compose, name, service):
    assert service.get("restart") == "unless-stopped", f"{compose}: {name}"


def test_the_runbook_names_every_container_thor_has_to_watch():
    """The runbook says what goes in THOR_CONTENEDORES on midgard: every container of these composes."""
    runbook = RUNBOOK.read_text(encoding="utf-8")
    containers = {service["container_name"] for _, _, service in services()}

    assert len(containers) == 11
    for container in containers:
        assert f"`{container}`" in runbook and container in runbook.split("THOR_CONTENEDORES=")[1].split("\n")[0], container
