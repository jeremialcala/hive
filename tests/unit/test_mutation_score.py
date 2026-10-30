# -*- coding: utf-8 -*-
"""
    The score of mutation testing per module (HIG-137, Gate 3) is read from the .meta files mutmut 3 writes next to
    each mutated file. These tests use that shape: {"exit_code_by_key": {mutant: exit code or null}}.
"""
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / ".ai-dlc" / "scripts" / "mutation_score.py"


@pytest.fixture(scope="module")
def score():
    spec = importlib.util.spec_from_file_location("mutation_score", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_meta(mutants: Path, module: str, codes: list) -> None:
    meta = mutants / f"{module}.meta"
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text(json.dumps({"exit_code_by_key": {f"m{index}": code for index, code in enumerate(codes)},
                                "durations_by_key": {}}), encoding="utf-8")


def test_killed_and_timed_out_count_as_detected_and_a_mutant_without_tests_does_not(score):
    # 2 killed, 1 timeout, 1 survived, 1 without tests, 1 skipped.
    counted = score.contar([1, 3, 36, 0, 33, 34])

    assert (counted["muertos"], counted["timeout"], counted["sobreviven"], counted["sin_pruebas"],
            counted["otros"]) == (2, 1, 1, 1, 1)
    assert score.puntuacion(counted) == pytest.approx(60.0)


def test_a_worker_that_never_ran_its_tests_is_not_a_detection(score, tmp_path, capsys):
    """mutmut files the -1 of a worker that pytest did not even start (255) under timeout: it is no verdict."""
    write_meta(tmp_path, "src/utils/security.py", [255, 255, 1, 0])

    assert score.main([str(tmp_path)]) == 0
    out = capsys.readouterr().out

    assert "| `src/utils/security.py` | 4 | 1 | 0 | 1 | 0 | 2 | 50.0 % |" in out
    assert "::warning::src/utils/security.py: 2 mutantes sin veredicto" in out


def test_a_module_with_nothing_that_counts_has_no_score(score):
    assert score.puntuacion(score.contar([None, 34])) is None


def test_each_module_is_scored_by_its_path_and_there_is_a_total(score, tmp_path, capsys):
    write_meta(tmp_path, "src/auth/dpop.py", [1, 1, 1, 0])
    write_meta(tmp_path, "src/utils/security.py", [1, 0])
    # A file mutmut found nothing to mutate in.
    write_meta(tmp_path, "src/auth/__init__.py", [])

    assert score.main([str(tmp_path)]) == 0
    out = capsys.readouterr().out

    assert "| `src/auth/dpop.py` | 4 | 3 | 0 | 1 | 0 | 0 | 75.0 % |" in out
    assert "| `src/utils/security.py` | 2 | 1 | 0 | 1 | 0 | 0 | 50.0 % |" in out
    assert "| **Total** | 6 | 4 | 0 | 2 | 0 | 0 | 66.7 % |" in out
    assert "__init__" not in out


def test_below_the_objective_it_warns_and_does_not_fail(score, tmp_path, capsys):
    write_meta(tmp_path, "src/crypto/fields.py", [1, 0, 0, None])

    assert score.main([str(tmp_path), "--objetivo", "60"]) == 0
    out = capsys.readouterr().out

    assert "::warning::src/crypto/fields.py: 33.3 %, por debajo del objetivo del 60 %" in out
    assert "::warning::src/crypto/fields.py: 1 mutantes sin correr" in out


def test_without_results_there_is_nothing_to_pass(score, tmp_path, capsys):
    assert score.main([str(tmp_path)]) == 2
    assert "::error::" in capsys.readouterr().out
