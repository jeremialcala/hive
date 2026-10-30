"""Antepone la cabecera AI-DLC al historial generado y corrige ramas repetidas en el gitGraph."""
import sys

raw_path, out_path = sys.argv[1], sys.argv[2]
with open(raw_path, encoding="utf-8") as fh:
    lines = fh.read().splitlines()

seen, fixed, dropped = set(), [], 0
for line in lines:
    stripped = line.strip()
    if stripped.startswith("branch "):
        name = stripped.split(" ", 1)[1]
        if name in seen:
            dropped += 1
            continue
        seen.add(name)
    fixed.append(line)

HEADER = """# Historial de implementación — ms-python-metamsghandler

* **Estado:** review
* **Fecha:** 2026-10-12
* **Decisores:** Jeremi Alcalá
* **Fase AI-DLC:** 03-implementation
* **Versión:** 0.3.0
* **Gate:** 2
* **Rama principal:** main
* **Estrategia de branching:** GitFlow reducido: `develop` se integra en `main` por pull request

Documento generado, no escrito a mano. Para regenerarlo tras cada merge o tag:

```bash
python ~/.claude/skills/ai-dlc/scripts/gitgraph_from_log.py . --branch origin/main --out repo-history.raw.md
python .ai-dlc/scripts/assemble_repo_history.py repo-history.raw.md docs/03-implementation/repo-history.md
```

El grafo sigue la primera línea de `origin/main`; la bitácora incluye todas las ramas. Por eso
`4c7bb9e` aparece en la bitácora y no en el grafo: está en `develop` y aún no se ha fusionado.
El script de la skill emite `branch develop` una vez por merge y Mermaid rechaza crear dos
veces la misma rama; el ensamblado elimina la repetición.

"""

FOOTER = """
## Trazabilidad tag ↔ versión ↔ decisión

El repositorio no tiene tags. La tabla se rellena al cortar la primera versión.

| Tag | Versión CHANGELOG | ADR / feature | Nota |
|---|---|---|---|
| — | Unreleased | ADR-0001 a ADR-0007 · MMH-F01 | Adopción de AI-DLC en la rama `chore/ai-dlc-adoption`, sin commitear a 2026-10-12 |

## Decisiones ya implementadas y su commit

| Commit | Fecha | Decisión documentada |
|---|---|---|
| `a66e3e9` | 2026-06-17 | ADR-0002 (capas y puertos), ADR-0004 (motor de intención), ADR-0005 (guardrails) |
| `6b2aceb` | 2026-06-17 | ADR-0002: puerto `MessageNormalizer` |
| `824fdcd` | 2026-06-17 | ADR-0003: manejo de conexiones AMQP; imagen con usuario no-root |
| `c8a2de3` | 2026-06-18 | ADR-0003: límite de espera al cerrar los hilos |
| `4c7bb9e` | 2026-06-18 | ADR-0003: colas durables; ADR-0005: Ollama por la API `/v1`; sobre custom `jwe_body` |
"""

with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
    fh.write(HEADER + "\n".join(fixed) + "\n" + FOOTER)
print("ramas repetidas eliminadas:", dropped)
