# Documentación AI-DLC — ms-python-metamsghandler

Los diagramas van dentro del documento de su fase, como bloques Mermaid. Para validarlos:
`python ~/.claude/skills/ai-dlc/scripts/validate_mermaid.py docs`.

| Fase | Gate | Documentos | Estado del gate |
|---|---|---|---|
| 00 Proyecto | — | [Charter](00-project/charter.md) · [Glosario](00-project/glossary.md) · [Clasificación de datos](00-project/data-classification.md) · [ADRs](00-project/adr/) | — |
| 01 Requisitos | 0 | [PRD MMH-F01](01-requirements/clasificacion-mensajes-meta.md) | Abierto: falta aprobación |
| 02 Diseño | 1 | [Arquitectura](02-design/architecture.md) · [Threat model](02-design/threat-model.md) · [AsyncAPI del sobre](02-design/contracts/asyncapi-motor.yaml) · [ADRs de placement](02-design/adr/) | Abierto: falta validación y placement |
| 03 Implementación | 2 | [Historial del repo](03-implementation/repo-history.md) | Abierto |
| 04 Pruebas | 3 | [Estado](04-testing/README.md) | Abierto |
| 05 Despliegue | 4 | [Estado](05-deployment/README.md) | Abierto |
| 06 Monitoreo | 5 | [Estado](06-monitoring/README.md) | Abierto |

Los checklists de cada gate, con su evidencia, están en [`.ai-dlc/gates/`](../.ai-dlc/gates/).

## Índice de diagramas

| Diagrama | Eje | Documento |
|---|---|---|
| Mapa mental del alcance | Trazabilidad | `00-project/charter.md` |
| C4 Context | Estructura | `01-requirements/clasificacion-mensajes-meta.md` |
| Journey | Trazabilidad | `01-requirements/clasificacion-mensajes-meta.md` |
| Trazabilidad de requisitos | Trazabilidad | `01-requirements/clasificacion-mensajes-meta.md` |
| DFD de nivel 0 y DREAD inicial | Comportamiento · Trazabilidad | `01-requirements/clasificacion-mensajes-meta.md` |
| C4 Container y Component | Estructura | `02-design/architecture.md` |
| Secuencia del flujo crítico | Comportamiento | `02-design/architecture.md` |
| Ciclo de vida del mensaje | Comportamiento | `02-design/architecture.md` |
| Modelo lógico (ER) y clases | Estructura | `02-design/architecture.md` |
| DFD con límites de confianza y DREAD | Comportamiento · Trazabilidad | `02-design/threat-model.md` |
| Performance vs costo | Trazabilidad | `02-design/adr/ADR-0006-*.md`, `ADR-0007-*.md` |
| Grafo de commits | Trazabilidad | `03-implementation/repo-history.md` |
