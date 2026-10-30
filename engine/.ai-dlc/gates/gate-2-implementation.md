# Gate 2 — Implementación (cierre de Fase 03)

**Estado a 2026-10-12: abierto.** El código existe, pero ningún criterio tiene evidencia.

- [ ] SAST sin findings críticos/altos
  — nunca se ha ejecutado; no hay CI
- [ ] Dependencias verificadas (SCA) — sin deps alucinadas ni vulnerables (A03)
  — versiones fijadas en `requirements.txt`, sin SCA ni SBOM; dependencias de desarrollo y de ejecución en el mismo fichero
- [ ] Cobertura ≥ 80% branch
  — medido: 70 % (`pytest --cov=src --cov-branch`, 29 tests). El umbral de `tox.ini` es 60 %
- [ ] Dual review completado (humano + IA)
  — los pull requests #1 y #2 tienen revisión de IA (CodeRabbit, solo comentarios) y ninguna aprobación humana: autor y quien fusiona son la misma cuenta
- [ ] Sin secretos en el código
  — `config.env` nunca se ha commiteado (`git log --all -- config.env`: 0 commits), pero no se ha pasado ningún escáner de secretos por el historial

Correcciones de código que el threat model deja para este gate: T2, T3, T6, T7, T9, T10 y T11
(ver `docs/02-design/threat-model.md`).
