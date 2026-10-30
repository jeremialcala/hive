# Gate 4 — Despliegue (cierre de Fase 05)

**Estado a 2026-10-12: abierto.** El servicio no está desplegado en ningún entorno.

- [ ] Pipeline con 7 gates limpio (SAST, SCA, secrets, license, container, IaC, DAST)
  — `.github/workflows/security-gates.yml` es un esqueleto de ejecución manual: ningún paso analiza nada todavía
- [ ] IaC escaneada sin findings críticos
  — `deployment.yaml` es un borrador heredado de un template; se sustituye según ADR-0006
- [ ] Runbook de rollback documentado con disparadores
- [ ] SBOM generado

Específico del servicio: imagen base por digest y build multi-etapa (T8), `amqps` y secretos
desde un gestor (T5, T12), cola de entrada con DLQ (T6).
