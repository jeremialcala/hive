# 04 — Pruebas (Gate 3)

Fase sin documentar todavía: esta página solo recoge el punto de partida.

Los tests siguen la pirámide en `tests/` (ver [`tests/README.md`](../../tests/README.md)). Hoy solo
tiene contenido `tests/unit/`.

Lo que esta fase tiene que producir:

- Estrategia de pruebas y matriz OWASP Top 10 ejecutada (`.ai-dlc/owasp-mapping.md`).
- `requirementDiagram` con `verifies`, que cierra la trazabilidad abierta en el PRD.
- Evaluación del rail de entrada contra el modelo real con un corpus de jailbreaks y de
  mensajes legítimos (T1).
- Tests de integración contra RabbitMQ real: DLQ, reentrega, publicación confirmada (T6, T9).

Cierre: `.ai-dlc/gates/gate-3-testing.md`.
