# Gate 0 — Requisitos (cierre de Fase 01)

**Estado a 2026-10-12: abierto.** Los artefactos están en `review`; falta la aprobación humana y
tres datos de la clasificación.

No avanzar a diseño hasta cumplir TODO:

- [x] Requisitos funcionales documentados en `docs/01-requirements/`
  — RF-01 a RF-08 en `clasificacion-mensajes-meta.md`
- [x] Requisitos de seguridad mapeados a OWASP ASVS
  — RS-01 a RS-14, a nivel de sección de ASVS 4.0.3; los identificadores concretos se fijan en la revisión
- [x] Escenarios **negativos / de abuso** definidos
  — AB-01 a AB-12 y el `journey` del PRD
- [x] Threat assessment inicial realizado
  — DFD de nivel 0 y cuadrante DREAD en el PRD
- [ ] Datos clasificados (`docs/00-project/data-classification.md`)
  — inventario hecho; faltan los países LatAm de operación, el DPO y los plazos de retención
- [ ] Charter y glosario aprobados
  — ambos en `review`; faltan el sponsor, los owners de los sistemas vecinos y la aprobación

Al cerrar: cortar `[Unreleased]` a `0.1.0` en `CHANGELOG.md` y pasar los artefactos a `approved`.
