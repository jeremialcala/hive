# -*- coding: utf-8 -*-
"""
    The OWASP matrix (.ai-dlc/owasp-mapping.md, HIG-133) points at the tests, files and ADRs that hold each control up.
    If one is renamed or goes, the matrix says something that is no longer true: this fails instead.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MATRIX = (ROOT / ".ai-dlc" / "owasp-mapping.md").read_text(encoding="utf-8")
ALIASES = {"ptu/": "portal/tests/unit/", "tu/": "tests/unit/"}
TEST_FILE = r"(?:ptu/|tu/|tests/|engine/tests/)[\w/]+\.py"


def real(path: str) -> Path:
    for alias, prefix in ALIASES.items():
        if path.startswith(alias):
            return ROOT / (prefix + path[len(alias):])
    return ROOT / path


def cited_tests() -> list[tuple[str, str]]:
    cited = re.findall(rf"`({TEST_FILE})::(test_\w+)`", MATRIX)
    for file, inside in re.findall(rf"`({TEST_FILE})` \(([^)]*)\)", MATRIX):
        cited += [(file, name) for name in re.findall(r"`(test_\w+)`", inside)]
    return sorted(set(cited))


@pytest.mark.parametrize("file,test", cited_tests())
def test_every_test_the_matrix_cites_exists(file, test):
    path = real(file)
    assert path.is_file(), file
    assert re.search(rf"^def {test}\(", path.read_text(encoding="utf-8"), re.MULTILINE), f"{file}::{test}"


@pytest.mark.parametrize("path", sorted(set(
    re.findall(rf"`({TEST_FILE})", MATRIX)
    + re.findall(r"`((?:src|shared|portal|engine|deploy|docs|tests|\.ai-dlc)/[\w./-]+)`", MATRIX))))
def test_every_file_the_matrix_cites_exists(path):
    assert real(path.split("::")[0].rstrip("/")).exists(), path


def test_every_adr_the_matrix_cites_exists():
    adrs = {path.name[:4] for path in (ROOT / "docs" / "00-project" / "adr").glob("[0-9][0-9][0-9][0-9]-*.md")}
    assert set(re.findall(r"ADR-(\d{4})", MATRIX)) <= adrs


def test_the_matrix_covers_the_ten_risks_and_is_not_the_template():
    assert re.findall(r"^## (A\d\d) ", MATRIX, re.MULTILINE) == [f"A{n:02d}" for n in range(1, 11)]
    # The template's warning, and its default controls, are gone.
    assert "> **Sin adaptar todavía" not in MATRIX and "Controles por defecto" not in MATRIX
    assert len(cited_tests()) >= 40
