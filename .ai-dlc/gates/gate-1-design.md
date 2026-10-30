# Gate 1 — Diseño (cierre de Fase 02)

- [x] Arquitectura C4 validada (`docs/02-design/`)
- [x] Threat model STRIDE de sistema + servicios con superficie relevante
- [x] ADRs registrados para decisiones clave
- [x] Contratos de API definidos
- [x] Patrones de seguridad seleccionados por amenaza priorizada (DREAD)

**Aprobado el 2026-10-12 por Jeremi Alcala.** Cortada la versión `0.2.0`; arquitectura y threat model en `approved`,
y ADR-0002 a ADR-0012 en `accepted`.

| Criterio | Evidencia |
|---|---|
| Arquitectura C4 | [`architecture.md`](../../docs/02-design/architecture.md): Container, Component, dos secuencias, ciclo de vida del mensaje, ER y puertos |
| Threat model | [`threat-model.md`](../../docs/02-design/threat-model.md): DFD con seis fronteras, 21 amenazas con STRIDE y DREAD, y seis hallazgos |
| ADRs | ADR-0002 a ADR-0012, en `docs/00-project/adr/` |
| Contratos | [`openapi.yaml`](../../docs/02-design/contracts/openapi.yaml), generado de la aplicación, y [`asyncapi.yaml`](../../docs/02-design/contracts/asyncapi.yaml); ocho pruebas en `tests/contract` |
| Patrones por amenaza | Tabla de patrones en `architecture.md`, ordenada por DREAD |

## Refactor de la topología de workers: aprobado el 2026-10-18

El refactor reabre el diseño como cambios sobre esta línea base ([plan](../../docs/00-project/plan-topologia-de-workers.md),
paso 2).

| Criterio | Evidencia del refactor |
|---|---|
| Arquitectura C4 | `architecture.md` 0.3.0, "Objetivo del refactor": Container con los tres servicios del gateway, Bifröst y el bucket de contexto; secuencia de la notificación con sus fallos; ciclo de vida de un evento en `WEBHOOK`; qué tiene cada servicio; brecha por incrementos |
| Threat model | `threat-model.md` 0.3.0: DFD del objetivo con la frontera TB7 (Bifröst), y T22–T31 con STRIDE, DREAD y su verificación prevista |
| ADRs | ADR-0017 a ADR-0022 (la 0018, aceptada; las demás, propuestas) |
| Contratos | `asyncapi-v2.yaml`, con su prueba de coherencia; los dos OpenAPI, generados de los dos servicios |
| Patrones por amenaza | Siete patrones nuevos en `architecture.md`, para T22–T31 |

**Aprobado el 2026-10-18 por Jeremi Alcala**, con las cinco casillas cumplidas también para los cambios:
arquitectura, threat model y `asyncapi-v2.yaml` en `approved`. ADR-0021 y ADR-0022 quedan aceptadas. ADR-0017,
ADR-0019 y ADR-0020 se aceptan **con condición**: que I0 confirme en el NATS de midgard que el bucket de contexto
renueva su caducidad en cada escritura y que el `fsync` en cada escritura cabe en el plazo del webhook. Si no, vuelven
a `proposed` con la salida que dice cada una. **I0, el 2026-10-19:** la caducidad del bucket se cumple, y ADR-0017 queda aceptada sin condición. El `fsync` cabe para una notificación de un mensaje (44,5 ms de mediana), pero no para las de varios. El 2026-10-19 se decidió un evento por notificación: hasta la notificación más grande de Meta (3 MB) cabe en 196 ms de mediana, y ADR-0019 y ADR-0020 quedan aceptadas sin condición. ADR-0006 queda sustituida por ADR-0021, y ADR-0016 ampliada por
ADR-0022. No se corta versión: la `0.4.0` es del Gate 3 del refactor.

## Qué significa este cierre

El gate aprueba el **diseño**, no su construcción. La sección "Brecha entre lo construido y el diseño" de
`architecture.md` lista siete trabajos por hacer; ninguno está hecho. El gateway sigue siendo de un solo operador,
con la clave de gestión única y los datos en claro.

## HITL

Decisiones del 2026-10-12:

| ADR | Decisión | Resuelve del Gate 0 |
|---|---|---|
| [ADR-0009](../../docs/00-project/adr/0009-parametros-del-token-de-autenticacion.md) | Firma `ES256`, cifrado con los algoritmos que ya usa el código, 60 segundos de vida y un solo uso por token | Parámetros del token |
| [ADR-0010](../../docs/00-project/adr/0010-clave-de-cifrado-de-datos-por-cliente.md) | Una clave de datos por cliente, generada y custodiada por la plataforma, con índice ciego para los números | Custodia de la clave de cifrado de datos |
| [ADR-0011](../../docs/00-project/adr/0011-contrato-de-la-cola.md) | El sobre del gateway como contrato para los dos canales, versión 1, con deduplicación en el motor | Contrato de la cola |
| [ADR-0012](../../docs/00-project/adr/0012-aislamiento-por-cliente-en-la-capa-de-datos.md) | `clientId` en cada documento y capa de repositorio obligatoria | — |

## Queda abierto sin bloquear el gate

- **Clave maestra** (ADR-0010): dónde vive y cómo se rota. Resuelto después del cierre en
  [ADR-0013](../../docs/00-project/adr/0013-openbao-como-gestor-de-secretos.md): un OpenBao propio, Andvari.
- **PSID de Messenger** (ADR-0010): si se cifra también. El requisito habla de números.
- **Amenaza T10**: lo que el threat model dice de los guardrails del motor sale de su documentación; no se ha
  verificado desde este repositorio.
- **Contrato de la cola**: `asyncapi.yaml` describe el mensaje de hoy. La versión 1 de ADR-0011 es código por
  escribir en los dos repositorios.
- Siguen sin aportar los dos datos del Gate 0: delegado de protección de datos y región del clúster de Atlas.
