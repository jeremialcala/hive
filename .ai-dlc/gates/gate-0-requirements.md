# Gate 0 — Requisitos (cierre de Fase 01)

No avanzar a diseño hasta cumplir TODO:

- [x] Requisitos funcionales documentados en `docs/01-requirements/`
- [x] Requisitos de seguridad mapeados a OWASP ASVS
- [x] Escenarios **negativos / de abuso** definidos
- [x] Threat assessment inicial realizado
- [x] Datos clasificados (`docs/00-project/data-classification.md`)
- [x] Charter y glosario aprobados

**Aprobado el 2026-10-12 por Jeremi Alcala.** Cortada la versión `0.1.0`; charter, glosario, clasificación de
datos y PRD en `approved`.

| Criterio | Evidencia |
|---|---|
| Requisitos funcionales | [PRD BAAS-001](../../docs/01-requirements/mvp-baas-gateway.md): RF01–RF12 y RNF01–RNF07, cada uno con su estado |
| Seguridad mapeada a ASVS | PRD: RS01–RS18 con sección de ASVS 4.0.3, nivel L2, y OWASP Top 10:2025; `requirementDiagram` |
| Escenarios de abuso | PRD: AB01–AB16; `journey` |
| Threat assessment inicial | PRD: DFD con cinco fronteras y `quadrantChart` DREAD |
| Datos clasificados | [Clasificación de datos](../../docs/00-project/data-classification.md), con roles GDPR, inventario y retención |
| Charter y glosario | [Charter](../../docs/00-project/charter.md) con `mindmap`; [glosario](../../docs/00-project/glossary.md) |

## Refactor de la topología de workers: aprobado el 2026-10-18

El refactor reabre los requisitos como cambios sobre esta línea base
([plan](../../docs/00-project/plan-topologia-de-workers.md), paso 1). **Aprobados el 2026-10-18 por Jeremi
Alcala**, con las seis casillas cumplidas también para los cambios: PRD 0.3.0, charter, glosario y clasificación de
datos en `approved`. No se corta versión: el plan reserva la siguiente, la `0.4.0`, para el Gate 3 del refactor.

| Criterio | Evidencia del refactor |
|---|---|
| Requisitos funcionales | PRD 0.3.0: RF13 (Instagram), RF14 (historial exportable) y RF15 (contexto del coordinador); RNF01 con cifra, RNF08 (orden por conversación) y RNF09 (traza) |
| Seguridad mapeada a ASVS | RS19 (trazas y bus sin datos personales de más), RS20 (un usuario por servicio en el bus) y RS21 (una credencial por worker) |
| Escenarios de abuso | AB17–AB22: duplicados, desorden, datos en trazas o bus, sujetos ajenos, credencial de worker y fuga de historial o contexto; escenarios positivos ES07–ES09 |
| Threat assessment | DFD del objetivo con las fronteras nuevas, y cuatro amenazas más en el DREAD |
| Datos clasificados | IGSID, contenido en historial y contexto, trazas y credenciales del bus |
| Charter y glosario | Instagram y el historial en el alcance; términos nuevos (bus, sujeto, clave de orden, worker de ingesta, IGSID, historial, contexto, traza) |

Decidido al aprobarlo: el historial se guarda 12 meses por mensaje como máximo; de un adjunto, solo su tipo; e
Instagram se conecta con *Facebook Login* a través de la página del cliente.

## Qué significa este cierre

El gate aprueba los **requisitos**, no su cumplimiento. El gateway de hoy sigue siendo de un solo operador: de los
37 requisitos, 9 están construidos, 12 parciales, 14 pendientes y 2 sin medir. Lo que falta por construir está
identificado y priorizado; no está hecho.

## HITL

Decisiones del 2026-10-12, recogidas en la sección "Decisiones" del PRD:

- El motor de respuestas entrega la respuesta al usuario (ADR-0002) y pide la configuración del bot al gateway con
  el `bot_id` (ADR-0003).
- Solo el webhook se autentica con la firma de Meta; el resto, con un JWT firmado por el cliente dentro de un JWE
  (ADR-0004, ADR-0005).
- La gestión se queda en red privada hasta que existan el registro de clientes y el JWE.
- El cifrado con la clave del cliente cubre el número del contacto y el número del bot.
- Nivel ASVS L2.
- Retención: 24 horas en la cola y 12 meses sin actividad para un contacto, como máximos que el cliente puede
  acortar.
- Sin SLO comprometido hasta medir en producción.

## Queda abierto sin bloquear el gate

- Dos datos por aportar: si se designa delegado de protección de datos, y la región del clúster de Atlas.
- Tres decisiones de diseño que pasan al Gate 1: parámetros del token, contrato de la cola y custodia de la clave
  de cifrado de datos.
- Dos entregables anteriores al primer cliente externo: contrato de encargo y procedimiento de aviso de brechas.
