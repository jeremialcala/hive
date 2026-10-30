# Gate 1 — Diseño (cierre de Fase 02)

**Estado a 2026-10-12: abierto.** El diseño está escrito; faltan la validación humana y las dos
decisiones de placement. No se puede cerrar antes que Gate 0.

- [ ] Arquitectura C4 validada (`docs/02-design/`)
  — Context en el PRD, Container y Component en `architecture.md`; elaborada, sin validar
- [x] Threat model STRIDE de sistema + servicios con superficie relevante
  — `docs/02-design/threat-model.md`, T1 a T12; en un repo de un solo servicio, sistema y servicio coinciden
- [x] ADRs registrados para decisiones clave
  — ADR-0001 a ADR-0005 `accepted`; ADR-0006 y ADR-0007 (placement) `proposed`
- [x] Contratos de API definidos
  — `docs/02-design/contracts/asyncapi.yaml`, validado con la CLI de AsyncAPI (0 errores, 0 avisos)
- [x] Patrones de seguridad seleccionados por amenaza priorizada (DREAD)
  — tabla final de `architecture.md`

Pendiente para cerrar:

1. Validar los diagramas C4 y revisar el threat model con quien ejerza de seguridad.
2. Aceptar ADR-0007: necesita el volumen esperado y la decisión del DPO sobre la transferencia de datos.
3. Aceptar ADR-0006, que depende de la anterior.
4. Resolver las cinco observaciones del threat model que requieren revisión humana.

Al cerrar: cortar a `0.2.0` en `CHANGELOG.md`.
