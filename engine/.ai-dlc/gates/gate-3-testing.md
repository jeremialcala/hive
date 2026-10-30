# Gate 3 — Pruebas (cierre de Fase 04)

**Estado a 2026-10-12: abierto.**

- [ ] Pirámide completa pasando (unit → integration → contract → e2e → security)
  — solo existe `tests/unit/` (29 tests, todos pasan). Los otros cuatro niveles están vacíos
- [ ] Matriz OWASP Top 10 ejecutada
- [ ] DAST limpio
  — el servicio no expone HTTP: el equivalente es inyectar payloads hostiles en la cola de entrada
- [ ] Rendimiento dentro de SLOs
  — no hay SLOs ni mediciones de latencia
- [ ] Mutation testing ≥ 60% (objetivo)

Específico del servicio: los tests de guardrails mockean el motor de NeMo, así que la eficacia
del rail no está probada. Hace falta un corpus de jailbreaks y de mensajes legítimos ejecutado
contra el modelo real (T1).
