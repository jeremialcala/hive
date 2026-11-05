# Gate 4 — Despliegue (cierre de Fase 05)

**Estado a 2026-11-05: abierto.** Lo que falta está en HIG-20 y sus sub-issues (HIG-157 a HIG-160).

- [ ] Pipeline con 7 gates limpio (SAST, SCA, secrets, license, container, IaC, DAST)
  - **Están** (`seguridad.yml`, `e2e.yml`):
    - SAST, con CodeQL y su umbral;
    - SCA, con `pip-audit` sobre los seis locks;
    - secretos, con gitleaks en toda la historia;
    - DAST, con ZAP en los PR a `main` (HIG-90);
    - **container**, con Trivy sobre las cuatro imágenes (HIG-157);
    - **IaC**, con hadolint y `trivy config` (HIG-157);
    - **license**, con Trivy sobre los seis locks y la política de `.trivy-licencias.yaml` (HIG-158).
  - **Las Actions del pipeline,** fijadas por SHA, y Dependabot para moverlas, igual que las imágenes base (HIG-159).
  - **Falta:** que los siete pasen juntos en el CI de la release que cierre el gate.
- [x] IaC escaneada sin findings críticos
  - **Está** (2026-11-05, HIG-157): hadolint y `trivy config`, sobre los cuatro Dockerfile, sin avisos ni errores. Los
    compose, que Trivy no lee, los prueba `tests/unit/test_compose_hardening.py`. Encontró los servicios del gateway
    sin `no-new-privileges`, `cap_drop` ni `read_only`, ya corregidos.
- [ ] Runbook de rollback documentado con disparadores
  - **Está la base** (HIG-160, ADR-0036): las imágenes de cada release, firmadas y con procedencia, en ghcr.
  - **Está el despliegue** (HIG-20): `deploy/desplegar.sh` las descarga por digest, verifica su firma con la
    identidad del workflow en esa etiqueta y, si los diez contenedores no quedan sanos, vuelve solo a la versión
    anterior (RF11). Lo prueba `tests/unit/test_desplegar.py`, y `--solo-verificar` verificó las cuatro de la v0.20.0
    desde nidavellir el 2026-11-05.
  - **Está el runbook,** con sus disparadores y lo que no se deshace:
    [runbook de despliegue y vuelta atrás](../../docs/05-deployment/runbook-despliegue-y-rollback.md).
  - **Falta:** la prueba de la vuelta atrás en producción, de la v0.20.0 a la v0.19.0 y de vuelta a la v0.20.0
    firmada.
- [ ] SBOM generado
  - **Está** (2026-11-05, HIG-158): un CycloneDX por imagen en cada build, como artefacto del job «Contenedores»
    (el del gateway: 129 componentes, 87 de Debian y 41 de PyPI).
  - **Y con cada release** (HIG-160): el workflow `release` atesta el SBOM de cada imagen con cosign y lo adjunta a
    la release de GitHub. Queda por verse con la primera etiqueta.
