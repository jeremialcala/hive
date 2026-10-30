# -*- coding: utf-8 -*-
"""
    The script that cuts a release in the CHANGELOG (.ai-dlc/scripts/cortar_release.py, docs/05-deployment/releases.md):
    what is unreleased becomes the version, the links follow, and nothing is written when something does not add up.
"""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / ".ai-dlc" / "scripts" / "cortar_release.py"
spec = importlib.util.spec_from_file_location("cortar_release", SCRIPT)
cortar_release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cortar_release)

CHANGELOG = """# Changelog

## [Unreleased]

### Añadido
- **Algo nuevo.**

## [0.3.0] - 2026-10-18

### Añadido
- **Lo de entonces.**

[Unreleased]: https://github.com/jeremialcala/wh-python-fastapi-messenger/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/jeremialcala/wh-python-fastapi-messenger/compare/v0.2.0...v0.3.0
"""


@pytest.mark.parametrize("nl", ["\n", "\r\n"], ids=["LF", "CRLF"])
def test_what_was_unreleased_becomes_the_version_and_the_links_follow(nl):
    cut = cortar_release.cut(CHANGELOG.replace("\n", nl), "0.4.0", "2026-10-30")

    assert cut.count("\r\n") == (cut.count("\n") if nl == "\r\n" else 0)
    lines = cut.replace("\r\n", "\n").splitlines()
    assert lines[2:5] == ["## [Unreleased]", "", "## [0.4.0] - 2026-10-30"]
    assert lines[6:8] == ["### Añadido", "- **Algo nuevo.**"]
    assert "[Unreleased]: https://github.com/jeremialcala/hive/compare/v0.4.0...HEAD" in lines
    assert "[0.4.0]: https://github.com/jeremialcala/hive/compare/v0.3.0...v0.4.0" in lines
    assert cortar_release.notes(cut, "0.4.0") == "### Añadido\n- **Algo nuevo.**\n"
    assert cortar_release.notes(cut, "0.3.0") == "### Añadido\n- **Lo de entonces.**\n"


@pytest.mark.parametrize("version,changelog,why", [
    ("0.4", CHANGELOG, "SemVer"),
    ("0.3.0", CHANGELOG, "ya está"),
    ("0.2.9", CHANGELOG, "no es mayor"),
    ("0.4.0", CHANGELOG.replace("### Añadido\n- **Algo nuevo.**\n\n", ""), "vacío"),
    ("0.4.0", CHANGELOG.replace("## [Unreleased]\n", ""), "exactamente un"),
])
def test_nothing_is_cut_when_something_does_not_add_up(version, changelog, why):
    with pytest.raises(ValueError, match=why):
        cortar_release.cut(changelog, version, "2026-10-30")


def test_there_are_no_notes_for_a_version_not_cut():
    with pytest.raises(ValueError, match="no está cortada"):
        cortar_release.notes(CHANGELOG, "0.4.0")


def test_the_changelog_of_the_repository_can_be_cut():
    """The real one: one [Unreleased], one link of it, and versions in order."""
    text = (Path(__file__).resolve().parents[2] / "CHANGELOG.md").read_text(encoding="utf-8")
    versions = cortar_release.VERSION_HEADING.findall(text)
    assert versions == sorted(versions, key=cortar_release.as_tuple, reverse=True)
    major, minor, _ = cortar_release.as_tuple(versions[0])
    if text.split("## [Unreleased]", 1)[1].split(f"## [{versions[0]}]", 1)[0].strip():
        assert cortar_release.cut(text, f"{major}.{minor + 1}.0", "2099-01-01")
