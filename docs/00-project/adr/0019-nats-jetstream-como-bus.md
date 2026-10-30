# ADR-0019: NATS JetStream es el bus de eventos entre el gateway y los workers del motor

* **Estado:** accepted
* **Fecha:** 2026-10-18
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 02-design
* **Versión:** 0.1.0
* **ID:** ADR-0019
* **Supersede:** — (sustituye a RabbitMQ, que decidió [ADR-0006](0006-publicacion-confirmada-y-503.md), cuando el motor
  lea del bus nuevo; [ADR-0011](0011-contrato-de-la-cola.md) pasa a su versión 2 en el AsyncAPI 2.0.0)
* **Controles OWASP afectados:** A01 (cada servicio solo publica y consume lo suyo), A04 (datos personales en el bus y
  en disco), A08 (integridad entre servicios), A10 (duplicados, desorden y pérdida)

Aceptada el 2026-10-18 al aprobar el Gate 1 del refactor, con una condición que se cumplió el 2026-10-19: con un evento por notificación, el acuse cabe en el plazo del webhook también con la notificación más grande que envía Meta (ver «Medido en I0»).

Propuesta como entregable del Gate 1 del refactor ([plan](../plan-topologia-de-workers.md), paso 2). Decisión D1 del 2026-10-18.

## Contexto

Hoy el gateway publica cada mensaje en la cola `llm` de Hermod, un RabbitMQ 4.3 en midgard, y responde `200` a Meta
cuando el broker confirma ([ADR-0006](0006-publicacion-confirmada-y-503.md)). La topología de ocho workers del plan
necesita un bus con enrutamiento por sujetos entre muchos servicios. El 2026-10-18 Jeremi eligió NATS JetStream (D1),
en contra de mi recomendación, que era seguir con RabbitMQ. El análisis de origen no compara los dos, y casi todo lo
que pide (enrutamiento jerárquico, entrega al menos una vez, mensajes en vuelo acotados, plazo de confirmación) lo
cubre también RabbitMQ. Lo que distingue a JetStream aquí es la deduplicación al publicar, el almacén clave-valor que
usará el contexto ([ADR-0017](0017-contexto-de-conversacion-e-historial.md)) y un servidor más ligero.

Hechos comprobados en la documentación de NATS el 2026-10-18:

- **La última versión menor es la 2.14** (abril de 2026). La 2.14.3 corrige CVE-2026-58208, que puede tumbar un
  servidor JetStream que solo escucha por WebSocket.
- **La deduplicación al publicar** rechaza un segundo mensaje con el mismo `Nats-Msg-Id` durante una ventana de 2
  minutos por defecto.
- **Por defecto, el almacén en fichero se sincroniza con el disco cada 2 minutos** (`sync_interval: 2m`). La propia
  documentación advierte que, sin réplicas, un fallo del sistema operativo puede perder mensajes ya confirmados. El
  análisis de Jepsen sobre la 2.12.1 lo reprodujo con cortes de luz. Con `sync_interval: always` el servidor hace
  `fsync` en cada escritura, a costa del rendimiento.
- **El orden por clave entre varios consumidores** se consigue repartiendo los sujetos en particiones fijas con la
  función `partition` del mapeo de sujetos, y consumiendo cada partición con un solo mensaje en vuelo
  (`max_ack_pending: 1`). Desde la 2.11 hay además grupos de prioridad con cliente fijado.
- **Los permisos son por usuario y por sujeto**, para publicar y para suscribirse. Con una lista `allow`, todo lo
  demás queda denegado.
- **JetStream puede cifrar el almacén en disco** con una clave del servidor (`encryption_key`).

## Decisión

**NATS JetStream 2.14, con la 2.14.3 como mínimo, en un solo servidor: Bifröst**
([ADR-0020](0020-placement-de-bifrost.md)).

### Durabilidad

- **`sync_interval: always`.** Es un solo nodo, sin réplicas, en un servidor que pierde la luz a menudo. Con el
  valor por defecto, un mensaje que el webhook ya confirmó a Meta podría perderse sin que Meta lo reenvíe.
  Cuánto cuesta el `fsync` en el disco de midgard se mide en I0, antes de que el webhook dependa de ello.
- **El webhook responde `200` solo con el acuse de JetStream** (`PubAck`), igual que hoy con la confirmación de
  RabbitMQ. Si no llega, responde `503` y Meta reenvía.

### Streams

| Stream | Sujetos | Quién publica | Quién consume | Retención |
|---|---|---|---|---|
| `WEBHOOK` | `webhook.meta.whatsapp`, `.messenger` e `.instagram` | El webhook: cada notificación verificada que trae algún mensaje de una persona, como un solo evento ([ADR-0021](0021-el-webhook-solo-publica.md)) | El worker de ingesta | Cola de trabajo (se borra al confirmarse), y **7 días** como máximo: lo que reintenta Meta en WhatsApp. Decidido el 2026-10-18 ([ADR-0021](0021-el-webhook-solo-publica.md)) |
| `MENSAJES` | `gateway.message.<partición>.<botId>.<contactId>` | El worker de ingesta: el mensaje identificado | Los workers del motor, una partición por consumidor | 24 horas como máximo |
| `RESPUESTAS` | `engine.reply.sent.<botId>` | El notificador del motor, cuando Meta acepta un envío | El worker de ingesta, para el historial | 24 horas como máximo |
| `SUPRESIONES` | `gateway.contact.erased`, `gateway.bot.erased`, `gateway.client.erased` | La API de gestión | El coordinador, para borrar el contexto | 7 días |

Los nombres exactos, los campos y los esquemas se fijan en el AsyncAPI 2.0.0, que es el siguiente entregable de este
gate.

### Duplicados y orden

- **`Nats-Msg-Id`:** en `WEBHOOK`, el bot y un resumen de los identificadores de Meta de los mensajes de la
  notificación; en `MENSAJES`, el bot y el identificador de Meta del mensaje. Así una publicación repetida del propio
  gateway se descarta en JetStream.
- **Los reintentos de Meta duran días, no minutos:** 7 días en WhatsApp, 36 horas en Instagram. Esos duplicados los
  detiene el índice único del historial ([ADR-0017](0017-contexto-de-conversacion-e-historial.md)), no el bus.
- **El orden por conversación** sale de la clave en el sujeto: la ingesta publica en la partición que le toca a cada
  bot y contacto. Cada partición la atiende un solo consumidor con un mensaje en vuelo. Las particiones se fijan en
  16, y cambiarlas obliga a vaciar el stream.

### Seguridad

- **Un usuario por servicio** (RS20), con una lista `allow` de sus sujetos para publicar y para suscribirse, más los
  sujetos de la API de JetStream que necesita, y nada más. Sus credenciales viven en Andvari
  ([ADR-0013](0013-openbao-como-gestor-de-secretos.md)).
- **TLS en todas las conexiones**, con una CA propia de Bifröst.
- **Almacén cifrado en disco** con `encryption_key`, también guardada en Andvari. Por el bus pasa el contenido de
  los mensajes, que es dato restringido.

## Medido en I0 (2026-10-19)

Bifröst desplegado en midgard ([ADR-0020](0020-placement-de-bifrost.md)), con `sync_interval: always`. Disco `sda`:
Seagate ST500LT012, un disco de portátil de 5400 rpm. Cada medición, 500 publicaciones de 1 KB una tras otra, desde un
contenedor en la red `bifrost`:

| Servidor | Mediana | p90 | p99 | Máximo |
|---|---|---|---|---|
| Bifröst, `fsync` en cada escritura | 44,5 ms | 45,0 ms | 88,9 ms | 288,7 ms |
| Temporal en el mismo disco, `fsync` cada 2 minutos | 0,42 ms | 0,54 ms | 0,76 ms | 0,99 ms |

Una notificación con varios mensajes paga el `fsync` de cada uno, y publicarlos a la vez no lo agrupa:

| Mensajes de la notificación | En serie | A la vez |
|---|---|---|
| 1 | 44,5 ms | 44,5 ms |
| 5 | 184 ms | 179 ms |
| 10 | 356 ms (máx. 634) | 390 ms (máx. 945) |

**Para una notificación de un mensaje, que es lo habitual, cabe en RNF01 con holgura.** Con cinco mensajes ya ronda
los 250 ms, y Meta agrupa hasta 1000 cuando hay mucho tráfico.

**Decidido el 2026-10-19: un evento por notificación** ([ADR-0021](0021-el-webhook-solo-publica.md)). El acuse depende
entonces del tamaño de la notificación, no del número de mensajes. Medido ese día en Bifröst, con `max_payload` en
4 MB:

| Evento | Mediana | Máximo |
|---|---|---|
| 1 KB, una notificación típica | 44,5 ms | 57 ms |
| 64 KB | 44,6 ms | 56 ms |
| 1 MB | 134 ms | 467 ms |
| 3 MB, lo más que envía Meta | 196 ms | 385 ms |

Hasta la notificación más grande cabe en los 250 ms de mediana y por debajo de 1 s.

**Con el almacén en el SSD (2026-10-19, [ADR-0020](0020-placement-de-bifrost.md)):** el acuse de 1 KB baja a 2,2 ms de
mediana (p99 9,7 ms) y el de 3 MB a 69 ms. El techo del bus deja de ser el disco.

## Alternativas consideradas

| Opción | Por qué no |
|---|---|
| Seguir con RabbitMQ (Hermod) | Era mi recomendación: ya está desplegado y cubre el enrutamiento por *topic*, las confirmaciones y el `prefetch`. Jeremi eligió NATS por la deduplicación al publicar, el almacén clave-valor y la ligereza |
| Redis Streams | Persistencia más débil y sin permisos por sujeto. Habría que montarlo y operarlo igual |
| Kafka | Demasiado para dos núcleos y 7,6 GB: necesita más memoria y más operación que el resto de la plataforma junta |

## Consecuencias

- **La deduplicación de NATS no basta sola.** Hace falta el índice único del historial y que el motor no responda dos
  veces al mismo identificador.
- **`sync_interval: always` puede encarecer el acuse** en un disco mecánico de 2013. Si en I0 no cabe en el plazo
  del webhook (RNF01), la alternativa es un SSD en midgard, no volver al valor por defecto.
- **Un solo nodo:** si Bifröst cae, el webhook responde `503` y Meta reenvía, como hoy con Hermod. No hay réplicas.
- **El motor tiene que cambiar:** leer de NATS el contrato v2, consumir por particiones y deduplicar. Hasta que lo
  haga, el gateway sigue publicando en Hermod, y el paso de uno a otro necesita su propio plan.
- **Hermod se retira** cuando el motor deje de leer la cola `llm`.
- **La clasificación de datos** suma el bus y sus credenciales, y el threat model, sus fronteras: cada servicio con
  su usuario y el almacén en disco.

## Condiciones de revisión

- El `fsync` en cada escritura no cabe en el plazo del webhook.
- Se necesita alta disponibilidad: entonces, un clúster de tres nodos, que no cabe en midgard.
- El motor no puede leer NATS en el plazo del refactor.
