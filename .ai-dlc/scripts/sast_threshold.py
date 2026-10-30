"""Umbral de severidad sobre el SARIF de CodeQL: rompe el build con hallazgos de seguridad altos o críticos.

Uso:  python .ai-dlc/scripts/sast_threshold.py <carpeta con los .sarif>

CodeQL por sí solo no rompe el build: deja una alerta y sigue. El criterio del Gate 2 es "SAST sin hallazgos
críticos o altos", y GitHub llama alto a un `security-severity` de 7,0 en adelante y crítico de 9,0 en adelante.

Dos detalles del SARIF que escribe CodeQL en disco, y que la primera versión de este umbral no tuvo en cuenta:
las reglas vienen en `tool.extensions`, no en `tool.driver`, y el resultado no trae la severidad; hay que
buscarla en su regla.
"""
import json
import pathlib
import sys

HIGH = 7.0
# Lo que no bloquea aunque sea alto: el código de pruebas. Una prueba maneja secretos de mentira y servicios
# desechables por necesidad, y GitHub clasifica esas alertas aparte ("test"). Se informan, no rompen el build.
TEST_PATHS = ("tests/", "engine/tests/")


def rules_by_id(run: dict) -> dict:
    tool = run.get("tool", {})
    components = [tool.get("driver", {}), *tool.get("extensions", [])]
    return {rule.get("id"): rule for component in components for rule in component.get("rules", [])}


def security_severity(rule: dict) -> float | None:
    value = rule.get("properties", {}).get("security-severity")
    return float(value) if value is not None else None


def location(result: dict) -> dict:
    return (result.get("locations") or [{}])[0].get("physicalLocation", {})


def in_test_code(result: dict) -> bool:
    return location(result).get("artifactLocation", {}).get("uri", "").startswith(TEST_PATHS)


def describe(result: dict, rule_id: str, severity: float) -> str:
    place = location(result)
    uri = place.get("artifactLocation", {}).get("uri", "?")
    line = place.get("region", {}).get("startLine", "?")
    # En una sola línea: una anotación de GitHub Actions termina en el primer salto.
    message = " ".join(result.get("message", {}).get("text", "").split())
    return f"{uri}:{line} [{rule_id}, {severity}] {message}"


def evaluate(folder: str) -> int:
    files = sorted(pathlib.Path(folder).glob("*.sarif"))
    if not files:
        # Sin SARIF no hay nada que juzgar, y eso no puede contar como aprobado.
        print(f"::error::no hay ningún .sarif en {folder}: el análisis no dejó resultados que evaluar")
        return 2

    rules = results = 0
    blocking, in_tests, lower = [], [], 0
    for file in files:
        for run in json.loads(file.read_text(encoding="utf-8")).get("runs", []):
            known = rules_by_id(run)
            rules += len(known)
            for result in run.get("results", []):
                results += 1
                rule_id = result.get("ruleId") or result.get("rule", {}).get("id")
                severity = security_severity(known.get(rule_id, {}))
                if severity is None:
                    continue
                if severity >= HIGH and in_test_code(result):
                    in_tests.append(describe(result, rule_id, severity))
                elif severity >= HIGH:
                    blocking.append(describe(result, rule_id, severity))
                else:
                    lower += 1

    if not rules:
        print("::error::el SARIF no trae ninguna regla: el análisis no cargó las consultas")
        return 2

    for line in blocking:
        print(f"::error::{line}")
    for line in in_tests:
        print(f"::warning::en código de pruebas, no bloquea: {line}")
    if lower:
        print(f"::warning::{lower} hallazgos de seguridad por debajo del umbral; están en la pestaña Security")
    print(f"{rules} reglas, {results} resultados, {len(blocking)} de seguridad altos o críticos"
          f" y {len(in_tests)} más en código de pruebas")
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(evaluate(sys.argv[1]))
