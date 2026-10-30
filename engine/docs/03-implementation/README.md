# 03 — Implementación (Gate 2)

El código vive en `src/` y los tests en `tests/`. Aquí va lo transversal de implementación.

| Documento | Contenido |
|---|---|
| [repo-history.md](repo-history.md) | Grafo y bitácora derivados de `git log`; decisiones ya implementadas y su commit |

## Estado medido el 2026-10-12

| Criterio de Gate 2 | Medición |
|---|---|
| Tests unitarios | 29, todos pasan (`pytest`) |
| Cobertura con ramas | 70 % (`pytest --cov=src --cov-branch`); objetivo 80 % |
| Módulos sin cobertura | `presentation/bootstrap.py`, `infrastructure/security/`, `infrastructure/persistence/` (0 %); `amqp/consumer.py` (25 %) |
| SAST, SCA, escáner de secretos | Sin ejecutar: no hay CI |

## Trabajo de código que deja abierto el threat model

En orden de puntuación DREAD. Ninguno está hecho.

1. T7: timeout en la llamada del rail.
2. T3: quitar el cuerpo del mensaje del `log.info` de `amqp/publisher.py`.
3. T6: DLQ, confirmaciones del publicador, mensajes persistentes, enlace de la cola de salida.
4. T2: lista permitida de etiquetas y esquema para `entidades`.
5. T10: validar las URL de adjuntos.
6. T9: idempotencia por `mensaje_id`.
7. T11: decidir qué se hace con el código heredado de JWE y MongoDB.

Flujo de trabajo: `CONTRIBUTING.md`. Cierre: `.ai-dlc/gates/gate-2-implementation.md`.
