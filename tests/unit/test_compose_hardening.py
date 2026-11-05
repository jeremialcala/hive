# -*- coding: utf-8 -*-
"""
    The IaC of the composes that run Hive (HIG-157, Gate 4): Trivy reads the Dockerfiles, not the composes, so what
    each container may do is checked here, in every compose of production, whatever is changed in it later.

    * every image pinned: by digest, or ours by the tag of the release;
    * no container gains privileges or keeps capabilities, nor shares the network, the PIDs or the devices of the host;
    * what is always up has a read-only filesystem and a memory limit;
    * nothing is published on every address of the machine: a port is always bound to one.

    What may not, says why in EXCEPTIONS.
"""
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSES = ["deploy/docker-compose.yml", "deploy/idp/docker-compose.yml", "engine/deploy/docker-compose.yml",
            "engine/deploy/llm/docker-compose.yml", "portal/deploy/docker-compose.yml"]
SERVICES = {(compose, name): service for compose in COMPOSES
            for name, service in yaml.safe_load((ROOT / compose).read_text(encoding="utf-8"))["services"].items()}

# Ours, built from this repository, go by the tag of the release; every other by its digest.
OURS = re.compile(r"ghcr\.io/jeremialcala/hive-[a-z]+:\$\{[A-Z_]+(:\?[^}]*)?\}")
PINNED = re.compile(r"[\w./-]+:[\w.-]+@sha256:[0-9a-f]{64}")

LLM = ("engine/deploy/llm/docker-compose.yml", "llm")
ENGINE_KEYS = ("deploy/docker-compose.yml", "engine-keys")
EXCEPTIONS = {
    # The LLM runs on Jeremi's PC, with the GPU, its memory locked (mlock) and no limit: the model is 15 GiB and the
    # VM of WSL is the limit. Hardening it waits for it to be tried there (HIG-156).
    "capabilities": {LLM},
    "memory": {LLM, ENGINE_KEYS},
    # Zitadel and its database write their data and their keys where they run; the tunnel of Zitadel keeps its own
    # state. engine-keys runs on demand, and the operator mounts what it reads.
    "read_only": {LLM, ENGINE_KEYS, ("deploy/idp/docker-compose.yml", "db"), ("deploy/idp/docker-compose.yml", "api"),
                  ("deploy/idp/docker-compose.yml", "login"), ("deploy/idp/docker-compose.yml", "tunel")},
}
IDS = [f"{compose.split('/docker-compose')[0]}:{name}" for compose, name in SERVICES]


@pytest.mark.parametrize("key", SERVICES, ids=IDS)
def test_every_image_is_pinned_by_digest_or_is_ours_by_its_tag(key):
    image = SERVICES[key]["image"]

    assert OURS.fullmatch(image) or PINNED.fullmatch(image), image


@pytest.mark.parametrize("key", SERVICES, ids=IDS)
def test_no_container_gains_privileges_or_keeps_capabilities(key):
    service = SERVICES[key]
    if key in EXCEPTIONS["capabilities"]:
        pytest.skip("EXCEPTIONS says why")

    assert "no-new-privileges:true" in service.get("security_opt", []), key
    assert service.get("cap_drop") == ["ALL"], key


@pytest.mark.parametrize("key", SERVICES, ids=IDS)
def test_no_container_shares_the_host(key):
    service = SERVICES[key]

    assert not service.get("privileged"), key
    assert service.get("network_mode") not in ("host",), key
    assert service.get("pid") != "host" and service.get("ipc") != "host", key
    assert not service.get("devices"), key


@pytest.mark.parametrize("key", SERVICES, ids=IDS)
def test_what_is_always_up_has_a_read_only_filesystem_and_a_memory_limit(key):
    service = SERVICES[key]

    if key not in EXCEPTIONS["read_only"]:
        assert service.get("read_only") is True, key
    if key not in EXCEPTIONS["memory"]:
        assert service.get("mem_limit"), key


@pytest.mark.parametrize("key", SERVICES, ids=IDS)
def test_a_published_port_is_bound_to_one_address(key):
    for port in SERVICES[key].get("ports", []):
        address = str(port).split(":")[0]

        assert str(port).count(":") == 2 and address not in ("0.0.0.0", "::", ""), (key, port)


def test_the_exceptions_are_of_services_that_exist():
    """An exception of a service that went away is a hole left open for the next one with its name."""
    excepted = set().union(*EXCEPTIONS.values())

    assert excepted <= set(SERVICES), excepted - set(SERVICES)
