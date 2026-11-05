# -*- coding: utf-8 -*-
"""
    The Actions of the workflows, pinned by the SHA of their commit (HIG-159, Gate 4; A03 of the OWASP matrix). A tag
    such as `@v7` is a pointer its owner can move: the code that runs with this repository's token would change
    without a commit here. The version goes in a comment, for whoever reads it and for Dependabot.
"""
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)(.*)$")
PINNED = re.compile(r"[\w.-]+/[\w.-]+(?:/[\w./-]+)?@[0-9a-f]{40}")
VERSION = re.compile(r"\s+#\s+v\d+\.\d+\.\d+\s*$")


def uses_of(workflow: Path) -> list[tuple[int, str, str]]:
    found = []
    for number, line in enumerate(workflow.read_text(encoding="utf-8").splitlines(), 1):
        match = USES.match(line)
        if match:
            found.append((number, match.group(1), match.group(2)))
    return found


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=[each.name for each in WORKFLOWS])
def test_every_action_is_pinned_by_the_sha_of_its_commit_with_its_version_beside_it(workflow):
    # A workflow without Actions (gitflow-guard.yml runs only shell) has nothing to pin.
    for number, ref, rest in uses_of(workflow):
        assert PINNED.fullmatch(ref) or ref.startswith("./"), f"{workflow.name}:{number} {ref}"
        assert ref.startswith("./") or VERSION.match(rest), f"{workflow.name}:{number} lacks its version"


def test_one_action_is_pinned_to_one_commit_everywhere():
    """Two pins of the same Action at the same version must be the same commit, or one of them is not that version."""
    seen: dict[tuple[str, str], set[str]] = {}
    for workflow in WORKFLOWS:
        for _, ref, rest in uses_of(workflow):
            if "@" in ref:
                action, sha = ref.split("@")
                seen.setdefault((action.split("/")[0] + "/" + action.split("/")[1], rest.strip()), set()).add(sha)

    assert {key: shas for key, shas in seen.items() if len(shas) > 1} == {}


def test_dependabot_moves_the_pins_of_the_actions_and_of_every_dockerfile():
    config = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    by_ecosystem = {update["package-ecosystem"]: update for update in config["updates"]}
    dockerfiles = {"/" + str(path.parent.relative_to(ROOT)).replace("\\", "/").replace(".", "").strip("/")
                   for path in ROOT.glob("**/Dockerfile")
                   if not any(part in (".claude", "node_modules", "tests") for part in path.parts)}

    assert by_ecosystem["github-actions"]["directory"] == "/"
    assert {each.rstrip("/") or "/" for each in by_ecosystem["docker"]["directories"]} == \
        {each.rstrip("/") or "/" for each in dockerfiles}
    assert all(update["target-branch"] == "develop" for update in config["updates"])
