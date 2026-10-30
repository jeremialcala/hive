# ADR-0001: Adopción de la estructura AI-DLC en variante polyrepo

* **Estado:** accepted
* **Fecha:** 2026-10-12
* **Decisores:** Jeremi Alcalá
* **Fase AI-DLC:** 00-project
* **Versión:** 1.0.0
* **ID:** ADR-0001
* **Supersede / Superseded-by:** —
* **Controles OWASP afectados:** A06 (diseño seguro como proceso), A03 (trazabilidad de dependencias)

## Contexto

El repositorio nació como un servicio único con código, tests unitarios, `Dockerfile` y un
manifest de Kubernetes, pero sin requisitos escritos, threat model, ADRs ni CI. Las decisiones
de seguridad (sanitización, guardrails fail-closed, PII enmascarada) vivían solo en el código
y en el README. El proyecto se incorpora a la metodología AI-DLC cuando ya tiene fases 1 a 3
del pipeline implementadas, así que la documentación se reconstruye sobre un sistema existente.

## Decisión

Adoptar el estándar AI-DLC (`AI-DLC/repository-structure.md`) en su **adaptación a polyrepo**:

- `src/` se queda en la raíz; `apps/<servicio>/` no existe porque el repo aloja un solo servicio.
- `test/` pasa a `tests/unit/` y se crean los niveles `integration`, `contract`, `e2e` y `security`.
- `.ai-dlc/` contiene los gates, las plantillas y el mapeo OWASP de este repo.
- `docs/00-project` a `docs/06-monitoring` describen el servicio; diseño y threat model de
  sistema y de servicio son el mismo documento.
- ADRs de sistema en `docs/00-project/adr/`; ADRs de placement en `docs/02-design/adr/`, como
  pide la guía de deployment placement. La numeración es única entre las dos carpetas.

## Alternativas consideradas

| Opción | Pros | Contras | Riesgo de seguridad |
|---|---|---|---|
| Polyrepo (elegida) | Cambio mínimo de rutas; coherente con un repo de un servicio | Los artefactos transversales a varios servicios no tienen sitio aquí | Bajo |
| Monorepo `apps/metamsghandler/` | Idéntico al template | Obliga a mover código, `Dockerfile`, `tox.ini` y manifest sin que haya un segundo servicio | Bajo; riesgo de regresión por el movimiento |
| Solo documentación, sin tocar tests | Cero riesgo de regresión | La pirámide de tests queda sin estructura y Gate 3 no tiene dónde apoyarse | Medio: los tests de seguridad no tienen sitio |

## Consecuencias

- Positivas: cada requisito de seguridad queda trazado a un control OWASP y a una amenaza;
  las brechas ya existentes pasan a estar escritas y priorizadas.
- Negativas / deuda asumida: los gates 2 a 5 arrancan abiertos (sin CI, SAST, SCA, DAST ni SLOs);
  el threat model transversal con el receptor y el servicio de respuesta queda fuera de este repo.
- Impacto en threat model: se crea `docs/02-design/threat-model.md` (T1 a T12).
