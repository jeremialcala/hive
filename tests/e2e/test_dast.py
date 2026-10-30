# -*- coding: utf-8 -*-
"""
    The DAST (HIG-90, Gate 3): OWASP ZAP against the stack of the end-to-end test, the images of production.

        ZAP ──hive-e2e_dast (internal)──▶ hive-api   zap-api-scan.py, with the OpenAPI contract
                                       ──▶ webhook    zap-api-scan.py, with its own contract
                                       ──▶ portal     zap-full-scan.py: spider, passive and active scan

    ZAP cannot sign a DPoP proof per request, so what it scans is what answers without a token: the authenticated
    routes of hive-api are scanned too, and must answer 401 (test_every_route_that_asks_for_a_token_answers_401).
    The network is internal: nothing ZAP sends leaves the machine, and the contracts' servers, production's, are
    replaced by the services of the stack before ZAP reads them.

    A High finding fails, unless tests/e2e/zap/rules.tsv accepts it as a false positive, with its reason. Medium, Low
    and Informational stay in the report: $DAST_REPORTS (.e2e/dast by default) gets the HTML, JSON and Markdown of
    each scan, what ZAP printed, and summary.md, which the CI also writes to the job's summary.

        DAST=1 E2E=1 BIFROST_DIR=../bifrost pytest tests/e2e/test_dast.py

    The rules file is checked without Docker, in the unit suite.
"""
import json
import os
import re
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import requests
import yaml

from .e2e_stack import API, HERE, ROOT, wanted

ZAP = "ghcr.io/zaproxy/zaproxy:stable@sha256:030d3df57735dacbb83464854758e674dd9ad01d79d12a129a8e58b1fd384238"
NETWORK = "hive-e2e_dast"
RULES = HERE / "zap" / "rules.tsv"
HOOKS = HERE / "zap" / "hooks.py"
CONTRACTS = ROOT / "docs" / "02-design" / "contracts"
RISKS = {"3": "High", "2": "Medium", "1": "Low", "0": "Informational"}
HIGH = "3"
# What ZAP may take, each scan, with the three at once: the CI job has 30 minutes for this and the stack.
MINUTES = 15
# -silent: no add-on from the internet, there is no way out; what runs is what the image pinned by digest has.
# -I: a Medium or a Low does not make ZAP fail; this test decides, from the JSON report.
COMMON = ["-I", "-z", "-silent"]
SCANS = {
    "hive-api": ["zap-api-scan.py", "-t", "openapi.yaml", "-f", "openapi"],
    "webhook": ["zap-api-scan.py", "-t", "openapi.yaml", "-f", "openapi"],
    # A minute of spider from the seeds of hooks.py, and the active scan of what it found.
    "portal": ["zap-full-scan.py", "-t", "http://portal:8080/", "-m", "1", "--hook", "/zap/wrk/hooks.py"],
}
# The contract of each API, and where its service is on ZAP's network: its `servers` are production's.
CONTRACT = {"hive-api": ("openapi.yaml", "http://hive-api:5002"),
            "webhook": ("openapi-webhook.yaml", "http://webhook:5002")}
ENTRY = re.compile(r"^(?P<ids>\*|\d+(,\d+)*)\t(?P<action>IGNORE|OUTOFSCOPE)\t(?P<what>\S.*)$")


def reports() -> Path:
    return Path(os.environ.get("DAST_REPORTS") or ROOT / ".e2e" / "dast").resolve()


def dast_wanted() -> bool:
    return wanted() and os.environ.get("DAST") == "1"


# --- The accepted false positives -------------------------------------------------------------------------------------

def accepted_rules(text: str) -> list[tuple[set[str], str, re.Pattern | None]]:
    """The entries of the rules file: (rule ids, action, URL regex or None); a ValueError if one has no reason."""
    entries, reason = [], False
    for number, line in enumerate(text.splitlines(), 1):
        line = line.rstrip("\r")
        if not line.strip():
            reason = False
        elif line.startswith("#"):
            reason = reason or bool(line.lstrip("#").strip())
        else:
            found = ENTRY.match(line)
            if not found:
                raise ValueError(f"rules.tsv:{number}: neither '<id>\\tIGNORE\\t(<name>)' "
                                 f"nor '<id>\\tOUTOFSCOPE\\t<regex>'")
            if not reason:
                raise ValueError(f"rules.tsv:{number}: an accepted false positive needs its reason in a comment "
                                 f"above it")
            pattern = re.compile(found["what"]) if found["action"] == "OUTOFSCOPE" else None
            entries.append((set(found["ids"].split(",")), found["action"], pattern))
    return entries


def is_accepted(rule_id: str, uri: str, entries) -> bool:
    return any((rule_id in ids or "*" in ids) and (pattern is None or pattern.match(uri))
               for ids, _, pattern in entries)


def findings(report: dict, entries) -> list[dict]:
    """Every instance of every alert of a ZAP JSON report, and whether rules.tsv accepts it."""
    return [{"risk": alert["riskcode"], "rule": alert["pluginid"], "name": alert["alert"],
             "method": instance.get("method", ""), "uri": instance.get("uri", ""),
             "accepted": is_accepted(alert["pluginid"], instance.get("uri", ""), entries)}
            for site in report.get("site", []) for alert in site.get("alerts", [])
            for instance in alert.get("instances", []) or [{}]]


def test_each_accepted_false_positive_says_why():
    accepted_rules(RULES.read_text(encoding="utf-8"))


def test_a_false_positive_without_its_reason_is_refused():
    with pytest.raises(ValueError, match="reason"):
        accepted_rules("10021\tIGNORE\t(X-Content-Type-Options Header Missing)\n")
    with pytest.raises(ValueError, match="IGNORE"):
        accepted_rules("# because\n10021 IGNORE (spaces, not tabs)\n")
    entries = accepted_rules("# Only the health: accepted by Jeremi, 2026-10-30\n"
                             "40012\tOUTOFSCOPE\thttp://portal:8080/health\n")
    assert is_accepted("40012", "http://portal:8080/health", entries)
    assert not is_accepted("40012", "http://portal:8080/auth/login", entries)
    assert not is_accepted("40014", "http://portal:8080/health", entries)


def test_a_high_finding_is_counted_unless_it_is_accepted():
    report = {"site": [{"@name": "http://portal:8080", "alerts": [
        {"riskcode": "3", "pluginid": "40012", "alert": "Cross Site Scripting (Reflected)",
         "instances": [{"method": "GET", "uri": "http://portal:8080/health"},
                       {"method": "GET", "uri": "http://portal:8080/auth/login?next=x"}]},
        {"riskcode": "2", "pluginid": "10038", "alert": "CSP", "instances": [{"uri": "http://portal:8080/"}]}]}]}
    entries = accepted_rules("# The health has no input: accepted by Jeremi, 2026-10-30\n"
                             "40012\tOUTOFSCOPE\thttp://portal:8080/health$\n")
    failing = [each for each in findings(report, entries) if each["risk"] == HIGH and not each["accepted"]]
    assert [each["uri"] for each in failing] == ["http://portal:8080/auth/login?next=x"]


# --- What answers without a token -------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def dast(request):
    """The stack, only with DAST=1: the end-to-end job runs without it, and this does not bring the stack up."""
    if not dast_wanted():
        pytest.skip("The DAST runs with DAST=1 and the end-to-end stack: DAST=1 E2E=1 BIFROST_DIR=<higerotech/bifrost>")
    return request.getfixturevalue("stack")


def test_every_route_that_asks_for_a_token_answers_401_without_one(dast):  # pylint: disable=redefined-outer-name
    contract = yaml.safe_load((CONTRACTS / "openapi.yaml").read_text(encoding="utf-8"))
    asked, wrong = 0, []
    for path, operations in contract["paths"].items():
        for method, operation in operations.items():
            if not operation.get("security"):
                continue
            url = API + re.sub(r"\{[^}]+\}", lambda _: str(uuid.uuid4()), path)
            for authorization in (None, "DPoP not.a.token", "Bearer not.a.token"):
                answer = requests.request(method.upper(), url, json={} if method in ("post", "put") else None,
                                          headers={"Authorization": authorization} if authorization else {}, timeout=10)
                asked += 1
                if answer.status_code != 401:
                    wrong.append((method.upper(), path, authorization, answer.status_code))
    assert asked >= 100, f"Only {asked} requests: the contract lost its authenticated routes?"
    assert not wrong, wrong


# --- ZAP ------------------------------------------------------------------------------------------------------------

def _workdir(name: str) -> Path:
    """The directory ZAP reads and writes in, /zap/wrk: its contract or its hooks in, and the reports out."""
    work = reports() / name
    work.mkdir(parents=True, exist_ok=True)
    # ZAP runs as its own user (zap, 1000); in the CI the directory is the runner's.
    work.chmod(0o777)
    if name in CONTRACT:
        file, server = CONTRACT[name]
        contract = yaml.safe_load((CONTRACTS / file).read_text(encoding="utf-8"))
        contract["servers"] = [{"url": server}]
        (work / "openapi.yaml").write_text(yaml.safe_dump(contract, sort_keys=False, allow_unicode=True),
                                           encoding="utf-8")
    else:
        (work / "hooks.py").write_bytes(HOOKS.read_bytes())
    return work


def _scan(name: str) -> dict:
    work = _workdir(name)
    container = f"hive-e2e-zap-{name}"
    command = ["docker", "run", "--rm", "--name", container, "--network", NETWORK, "-v", f"{work}:/zap/wrk:rw",
               "-e", "PYTHONDONTWRITEBYTECODE=1", ZAP,
               *SCANS[name], *COMMON, "-T", str(MINUTES),
               "-J", f"{name}.json", "-r", f"{name}.html", "-w", f"{name}.md"]
    try:
        done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=(MINUTES + 5) * 60, check=False, env={**os.environ, "MSYS_NO_PATHCONV": "1"})
        code, output = done.returncode, done.stdout + done.stderr
    except subprocess.TimeoutExpired as e:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True, check=False)
        code, output = -1, f"ZAP did not finish in {e.timeout:.0f} s"
    (work / "zap.out").write_text(output, encoding="utf-8")
    report = work / f"{name}.json"
    return {"code": code, "output": output,
            "report": json.loads(report.read_text(encoding="utf-8")) if report.exists() else None}


def _summary(results: dict, entries) -> str:
    lines = ["## DAST: OWASP ZAP (HIG-90)", "",
             "| Target | High | Medium | Low | Informational | Accepted (rules.tsv) |", "|---|---|---|---|---|---|"]
    for name, result in results.items():
        if result["code"] != 0 or not result["report"]:
            lines.append(f"| {name} | ZAP ended with {result['code']}: see zap.out | | | | |")
            continue
        found = findings(result["report"], entries)
        alerts = {risk: {(each["rule"], each["name"]) for each in found
                         if each["risk"] == risk and not each["accepted"]} for risk in RISKS}
        accepted = {(each["rule"], each["name"]) for each in found if each["accepted"]}
        lines.append(f"| {name} | " + " | ".join(str(len(alerts[risk])) for risk in RISKS) + f" | {len(accepted)} |")
    lines += ["", "Alerts, not instances: each one, with where it was found, is in the HTML report of its target.", ""]
    for name, result in results.items():
        for each in sorted({(e["risk"], e["rule"], e["name"], e["accepted"])
                            for e in findings(result["report"] or {}, entries)}, reverse=True):
            lines.append(f"- {name}: {RISKS[each[0]]}, {each[2]} [{each[1]}]" + (" (accepted)" if each[3] else ""))
    return "\n".join(lines) + "\n"


@pytest.fixture(scope="module")
def scans(dast):  # pylint: disable=redefined-outer-name,unused-argument
    """The three scans, at once, against the stack that is up."""
    internal = subprocess.run(["docker", "network", "inspect", "-f", "{{.Internal}}", NETWORK], capture_output=True,
                              text=True, check=False).stdout.strip()
    assert internal == "true", f"{NETWORK} must be internal (compose.yaml): ZAP must not reach anything outside"
    with ThreadPoolExecutor(max_workers=len(SCANS)) as pool:
        results = dict(zip(SCANS, pool.map(_scan, SCANS)))
    summary = _summary(results, accepted_rules(RULES.read_text(encoding="utf-8")))
    (reports() / "summary.md").write_text(summary, encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as step:
            step.write(summary)
    print(summary)
    return results


@pytest.mark.parametrize("name", list(SCANS))
def test_zap_finds_nothing_high(scans, name):  # pylint: disable=redefined-outer-name
    result = scans[name]
    # 0: done, with at most warnings (-I). 1 would be a rule set to FAIL, 3 an error of ZAP or nothing to scan.
    assert result["code"] == 0 and result["report"], f"ZAP ended with {result['code']}:\n{result['output'][-4000:]}"
    high = [f"{each['name']} [{each['rule']}] {each['method']} {each['uri']}"
            for each in findings(result["report"], accepted_rules(RULES.read_text(encoding="utf-8")))
            if each["risk"] == HIGH and not each["accepted"]]
    assert not high, f"High findings in {name} (the report is in {reports() / name}):\n" + "\n".join(high)
