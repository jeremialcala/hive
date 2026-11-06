# 05 — Despliegue (Gate 4)

El pipeline vive en `.github/workflows/` y el compose del servidor en `deploy/`. Aquí van:

- Arquitectura del pipeline y sus siete gates de seguridad: SAST, SCA, secretos, licencias, contenedor, IaC y DAST.
- Runbook de despliegue y de rollback, con sus disparadores.
- Generación y archivo del SBOM.

Cierra con `.ai-dlc/gates/gate-4-deployment.md`.

- [Arranque tras un apagón](arranque-tras-apagon.md): cómo vuelve Hive solo tras un corte de luz de midgard, qué
  vigila Thor y la prueba con un reinicio real (RNF06).
- [Releases](releases.md): cómo se corta una versión, cómo se despliega su etiqueta en midgard, cómo se vuelve atrás
  y los hotfix. Desde la 0.4.0, producción es la última release.
- [Runbook de despliegue y vuelta atrás](runbook-despliegue-y-rollback.md): `deploy/desplegar.sh`, que solo levanta
  imágenes firmadas por el CI y vuelve solo si no quedan sanas; los disparadores de la vuelta atrás y lo que no se
  deshace (HIG-20, RF11).

Lo demás está todavía vacío. El despliegue continuo desde `main` está resumido en la sección Despliegue del README.
