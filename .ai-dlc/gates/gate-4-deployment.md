# Gate 4 — Despliegue (cierre de Fase 05)

**Estado a 2026-11-05: abierto.** Lo que falta está en HIG-20 y sus sub-issues (HIG-157 a HIG-160).

- [ ] Pipeline con 7 gates limpio (SAST, SCA, secrets, license, container, IaC, DAST)
  - **Están** (`seguridad.yml`, `e2e.yml`):
    - SAST, con CodeQL y su umbral;
    - SCA, con `pip-audit` sobre los seis locks;
    - secretos, con gitleaks en toda la historia;
    - DAST, con ZAP en los PR a `main` (HIG-90);
    - **container**, con Trivy sobre las cuatro imágenes (HIG-157);
    - **IaC**, con hadolint y `trivy config` (HIG-157).
  - **Las Actions del pipeline,** fijadas por SHA, y Dependabot para moverlas, igual que las imágenes base (HIG-159).
  - **Falta:** el de licencias (HIG-158).
- [x] IaC escaneada sin findings críticos
  - **Está** (2026-11-05, HIG-157): hadolint y `trivy config`, sobre los cuatro Dockerfile, sin avisos ni errores. Los
    compose, que Trivy no lee, los prueba `tests/unit/test_compose_hardening.py`. Encontró los servicios del gateway
    sin `no-new-privileges`, `cap_drop` ni `read_only`, ya corregidos.
- [ ] Runbook de rollback documentado con disparadores
  - **Falta:** HIG-20, con el despliegue desde ghcr verificado (HIG-160).
- [ ] SBOM generado
  - **Falta:** HIG-158.
