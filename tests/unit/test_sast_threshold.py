"""
    The SAST gate is a script that reads CodeQL's SARIF. Its first version passed a run with 29 error-level
    findings, because it looked for the rules and the severity where CodeQL does not put them. These tests
    use the shape CodeQL actually writes: rules under tool.extensions and results without a level.
"""
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / ".ai-dlc" / "scripts" / "sast_threshold.py"


@pytest.fixture(scope="module")
def threshold():
    spec = importlib.util.spec_from_file_location("sast_threshold", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rule(rule_id: str, severity: str | None) -> dict:
    properties = {"precision": "high"}
    if severity is not None:
        properties["security-severity"] = severity
    return {"id": rule_id, "defaultConfiguration": {"level": "error"}, "properties": properties}


def result(rule_id: str, path: str = "src/app.py") -> dict:
    return {
        "ruleId": rule_id,
        "rule": {"id": rule_id, "index": 0, "toolComponent": {"index": 0}},
        "message": {"text": "finding"},
        "locations": [{"physicalLocation": {"artifactLocation": {"uri": path}, "region": {"startLine": 35}}}],
    }


def write_sarif(folder: Path, rules: list, results: list) -> str:
    sarif = {"runs": [{
        "tool": {"driver": {"name": "CodeQL"}, "extensions": [{"name": "codeql/python-queries", "rules": rules}]},
        "results": results,
    }]}
    (folder / "python.sarif").write_text(json.dumps(sarif), encoding="utf-8")
    return str(folder)


def test_a_high_security_finding_breaks_the_build(threshold, tmp_path, capsys):
    folder = write_sarif(tmp_path, [rule("py/clear-text-logging-sensitive-data", "7.5")],
                         [result("py/clear-text-logging-sensitive-data")])

    assert threshold.evaluate(folder) == 1
    assert "src/app.py:35 [py/clear-text-logging-sensitive-data, 7.5]" in capsys.readouterr().out


def test_a_critical_security_finding_breaks_the_build(threshold, tmp_path):
    folder = write_sarif(tmp_path, [rule("py/code-injection", "9.3")], [result("py/code-injection")])

    assert threshold.evaluate(folder) == 1


def test_a_finding_below_high_is_reported_but_does_not_break_the_build(threshold, tmp_path, capsys):
    folder = write_sarif(tmp_path, [rule("py/log-injection", "6.1")], [result("py/log-injection")] * 3)

    assert threshold.evaluate(folder) == 0
    assert "::warning::3 hallazgos de seguridad por debajo del umbral" in capsys.readouterr().out


def test_a_quality_finding_does_not_break_the_build(threshold, tmp_path):
    folder = write_sarif(tmp_path, [rule("py/unused-import", None)], [result("py/unused-import")])

    assert threshold.evaluate(folder) == 0


def test_the_rule_is_found_when_the_result_only_carries_the_rule_object(threshold, tmp_path):
    finding = result("py/full-ssrf")
    del finding["ruleId"]
    folder = write_sarif(tmp_path, [rule("py/full-ssrf", "9.1")], [finding])

    assert threshold.evaluate(folder) == 1


@pytest.mark.parametrize("path", ["tests/integration/test_openbao_key_service.py", "tests/conftest.py",
                                  "engine/tests/unit/test_adapters.py"])
def test_a_high_finding_in_test_code_is_reported_but_does_not_break_the_build(threshold, tmp_path, capsys, path):
    folder = write_sarif(tmp_path, [rule("py/clear-text-storage-sensitive-data", "7.5")],
                         [result("py/clear-text-storage-sensitive-data", path)])

    assert threshold.evaluate(folder) == 0
    output = capsys.readouterr().out
    assert f"::warning::en código de pruebas, no bloquea: {path}:35" in output
    assert "0 de seguridad altos o críticos y 1 más en código de pruebas" in output


@pytest.mark.parametrize("path", ["src/app.py", ".ai-dlc/scripts/gen_openapi.py", "src/tests/helper.py",
                                  "deploy/tests/x.py", "testsuite.py", "engine/src/application/procesar_mensaje.py",
                                  "engine/src/tests/helper.py"])
def test_a_high_finding_anywhere_else_breaks_the_build(threshold, tmp_path, path):
    """Only the tests folder is test code: not what merely has the word in its path, nor the other scripts."""
    folder = write_sarif(tmp_path, [rule("py/code-injection", "9.3")], [result("py/code-injection", path)])

    assert threshold.evaluate(folder) == 1


def test_a_finding_in_shipped_code_still_blocks_next_to_one_in_test_code(threshold, tmp_path):
    folder = write_sarif(tmp_path, [rule("py/code-injection", "9.3")],
                         [result("py/code-injection", "tests/unit/test_x.py"), result("py/code-injection")])

    assert threshold.evaluate(folder) == 1


def test_a_clean_analysis_passes(threshold, tmp_path):
    folder = write_sarif(tmp_path, [rule("py/code-injection", "9.3")], [])

    assert threshold.evaluate(folder) == 0


def test_a_missing_sarif_is_a_failure_not_a_pass(threshold, tmp_path):
    assert threshold.evaluate(str(tmp_path)) == 2


def test_an_analysis_without_rules_is_a_failure_not_a_pass(threshold, tmp_path):
    folder = write_sarif(tmp_path, [], [])

    assert threshold.evaluate(folder) == 2
