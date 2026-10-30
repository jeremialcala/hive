# Plan: el gateway dentro de la topología de ocho workers

* **Estado:** approved
* **Fecha:** 2026-10-18
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 00-project
* **Versión:** 0.3.0
* **Origen:** «Análisis comparativo exhaustivo: Rust vs. Python para la arquitectura de motores de chatbots asistidos por IA» (documento externo, revisado el 2026-10-18)

Este documento revisa el análisis y planifica qué cambia en este repositorio para encajar en la arquitectura que
propone. Las seis decisiones se tomaron el 2026-10-18 y están en la sección [Decisiones](#decisiones); cada una
se escribe como ADR en el diseño (paso 2).

## Qué propone el análisis

Un motor de chatbots partido en ocho workers que se hablan por un bus de eventos (NATS JetStream con enrutamiento
por sujetos), cada uno en el lenguaje que mejor le sirve:

| # | Worker | Lenguaje propuesto | Quién lo tiene hoy |
|---|---|---|---|
| 1 | Webhook de recepción | Python | **Este repositorio** (el gateway) |
| 2 | Clasificador (guardrails) | Python | El motor, `ms-python-metamsghandler`, en un solo servicio |
| 3 | Coordinador (enrutamiento y contexto) | Python con Pydantic v2 | El motor |
| 4 | Enlace (MCP y APIs externas) | Rust (`rmcp`) | El motor |
| 5 | Respuesta (LLM) | Python | El motor |
| 6 | Evaluador (tono, certeza) | Python | El motor |
| 7 | Notificador (envío y control de tasas) | Rust, o Python con Redis | El motor (ADR-0002) |
| 8 | Auditor (OpenTelemetry) | Rust o Go | Nadie |

Al gateway le toca el worker 1, y sigue siendo además el registro de clientes, bots, reglas, intents y contactos
cifrados (ADR-0004, ADR-0010, ADR-0012): el análisis no dice dónde vive esa configuración, y los workers 2 a 7 la
leen de aquí.

## Revisión del análisis

### Lo que se sostiene

- **El plazo de Meta es real y es más exigente que lo que tenemos escrito.** La página de
  [throughput de WhatsApp Cloud API](https://developers.facebook.com/documentation/business-messaging/whatsapp/throughput),
  consultada el 2026-10-18, pide al webhook una latencia mediana de 250 ms como máximo y menos de un 1 % por
  encima de 1 s, aguantar el tráfico entrante esperado y el triple del saliente, y fija 80 mensajes por segundo
  por número, ampliables a 1000. RNF01 del [PRD](../01-requirements/mvp-baas-gateway.md) no compromete ninguna
  cifra; esta es la que hay que adoptar para WhatsApp.
- **El webhook se queda en Python.** Coincide con lo construido y con lo que hace falta: el trabajo es de E/S.
- **La validación HMAC** que describe es la que ya hace el gateway (ADR-0004, ADR-0007), sobre los bytes crudos y
  en tiempo constante.
- **Pydantic v2 sobre los bytes.** El gateway ya lee la notificación con `model_validate_json` sobre el cuerpo
  crudo (`controller/incoming.py`), como recomienda para el coordinador.
- **La propagación del contexto de traza (W3C `traceparent`) dentro del mensaje** es lo que falta hoy para seguir
  un mensaje de Meta a la respuesta.

### Lo que no se sostiene o falta

- **No evalúa RabbitMQ, que es el broker que ya está desplegado** (Hermod, ADR-0006, ADR-0011). Compara Redis
  Streams, Kafka y NATS. Los argumentos que da a favor de NATS —enrutamiento jerárquico, entrega al menos una vez,
  límite de mensajes en vuelo, plazo de confirmación— los cubre también RabbitMQ con intercambios `topic`,
  confirmaciones del consumidor y `prefetch`. Lo que sí distingue a JetStream para este caso es la
  deduplicación al publicar (`Nats-Msg-Id`, con una ventana de 2 minutos por defecto según la
  [documentación de NATS](https://docs.nats.io/jetstream/model_deep_dive)) y un servidor más ligero. Se decidió
  pasar a NATS (D1), sabiendo que la comparación con RabbitMQ no la hace el análisis.
- **La ventana de deduplicación no cubre los reintentos de Meta.** WhatsApp reintenta durante 7 días (ADR-0015);
  la ventana de JetStream es de minutos y no conviene agrandarla. El consumidor tiene que deduplicar por `botId` y
  `messageId` igual, con NATS o sin él.
- **El orden de la conversación no aparece.** Con ocho workers asíncronos y varios consumidores por etapa, dos
  mensajes seguidos de la misma persona pueden contestarse al revés. Hace falta una clave de orden por
  conversación (bot y remitente) que el bus respete, y el contrato tiene que llevarla.
- **El benchmark de MCP mide servidores, y el worker de enlace es sobre todo cliente.** Los 259 frente a 4845
  peticiones por segundo comparan servidores MCP bajo estrés; un cliente que espera a APIs remotas está limitado
  por esas APIs, no por el lenguaje.
- **El *confused deputy* no se neutraliza en compilación.** Validar el esquema de la entrada de una herramienta
  (con `schemars` o con Pydantic) rechaza entradas mal formadas, no acciones bien formadas que el LLM no debía
  pedir. La defensa es de autorización: a qué destinos puede llamar cada bot y con qué credencial. Es la amenaza
  T11 del [threat model](../02-design/threat-model.md) (intents que apuntan a la red interna), y le toca al
  gateway validar esos destinos al registrarlos.
- **El argumento de memoria supone cientos de réplicas en Kubernetes.** El despliegue es un servidor propio con
  topes de 512 MB y 1 CPU (ADR-0008, RNF03). Un segundo lenguaje duplica la cadena de herramientas y los gates del
  CI (`pip-audit` y `cargo-audit`, dos configuraciones de CodeQL) para una sola persona.
- **El plazo de 250 ms es de WhatsApp.** El análisis lo extiende a Messenger e Instagram; para Messenger lo
  documentado es la baja de la suscripción tras una hora de fallos (ADR-0015). Lo que Meta exige al webhook de
  Instagram, que entra en el alcance (D4), hay que consultarlo en su documentación en el paso 1.
- **No trata el carácter multi-cliente ni el GDPR.** El bus llevará datos personales (números, texto de los
  mensajes) entre más servicios; cada worker nuevo es un sitio más que entra en la clasificación de datos y en el
  derecho de supresión.
- **Fuentes.** Varias citas son foros, blogs o un documento de Scribd; las cifras de rendimiento de FastAPI y Axum
  son genéricas, no de este servicio.

## Por qué el webhook actual no cumple el plazo

Por cada mensaje de una notificación, y uno detrás de otro, el webhook hace hoy (`controller/messages.py`):

1. En Messenger, una llamada a la Graph API para el perfil del contacto, con 5 s de plazo (`controller/facebook.py`).
2. Lecturas y escrituras en MongoDB Atlas, y el descifrado de la clave del cliente en Andvari si no está en caché.
3. Una conexión nueva a RabbitMQ, con su negociación, `queue_declare` y confirmación (`controller/amqp_broker.py`).

Y solo entonces responde. Una notificación puede traer hasta 1000 mensajes. Sin medir todavía, una sola llamada
lenta a la Graph API basta para salir del 1 % por encima de 1 s, y la conexión por mensaje multiplica el coste de
publicar por el número de mensajes.

## Lo que cambia en este repositorio

| # | Cambio | Por qué | Toca |
|---|---|---|---|
| R1 | **El webhook responde en cuanto el bus confirma el evento verificado.** Comprobar la firma, leer la notificación, publicar un evento por mensaje y contestar. La identificación del contacto (Graph API, MongoDB, cifrado) pasa a un worker de ingesta de este mismo repositorio, que consume ese evento y publica el sobre que hoy va a la cola `llm` | Plazo de 250 ms; los fallos de la base o de Andvari los absorbe el bus en vez de los reintentos de Meta, que en Messenger acaban dando de baja la página | ADR-0006 y ADR-0015 (se reescriben: `503` solo si el bus no confirma), RNF01, RNF02 |
| R2 | **Una conexión a NATS JetStream por proceso, detrás de un puerto de publicación** (`EventPublisher`), que sustituye a RabbitMQ (D1). Se publica con `Nats-Msg-Id` igual al identificador de Meta y se espera el acuse del stream | Quitar la conexión por mensaje; que el dominio no dependa del broker; deduplicar los reintentos cercanos de Meta | `controller/amqp_broker.py` (desaparece), ADR-0006 |
| R3 | **Contrato de eventos v2:** sujetos jerárquicos (`webhook.meta.whatsapp`, `webhook.meta.messenger`, `webhook.meta.instagram`, `gateway.message.identified`), identificador de Meta como identificador del mensaje, clave de orden por conversación y `traceparent` en las cabeceras | Lo piden el bus por sujetos, la deduplicación, el orden y la traza | AsyncAPI 2.0.0 (incompatible con la 1.1.0), ADR-0011 |
| R4 | **OpenTelemetry en el gateway**, con la misma regla que los logs: ni números, ni texto de mensajes, ni nada que mande el llamante en los atributos de un span | Seguir un mensaje entre workers (worker 8) sin abrir una vía nueva de fuga | `logging_config`, `utils`, threat model |
| R5 | **Una credencial por worker, no una por motor.** Cada clave de servicio dice qué rutas alcanza: el notificador, los tokens de Meta; el clasificador y el coordinador, reglas y herramientas | Mínimo privilegio: hoy la clave del motor alcanza las cuatro rutas, y con ocho workers solo uno necesita los tokens | ADR-0016, `SERVICE_ROUTES` |
| R6 | **Validar los destinos de los intents al registrarlos** (sin red interna, sin esquemas distintos de `https`) | Es la defensa real contra el *confused deputy* del worker de enlace | T11, DTO de intents (H6) |
| R7 | **Instagram como tercer canal** (D4): leer sus notificaciones, verificarlas con la firma de Meta, identificar al contacto por su identificador de Instagram y guardarlo cifrado como los demás | Decisión de alcance | `controller/incoming.py`, `controller/facebook.py`, entidad de contacto, clasificación de datos, RF del PRD |
| R8 | **NATS JetStream en midgard**, con TLS, un usuario por servicio con permisos por sujeto y un stream con retención y tamaño acotados. Hermod se retira cuando el motor deje de leer la cola `llm` | Lo exige D1. Los permisos por sujeto son el equivalente en el bus de la credencial por worker (D3) | ADR de placement nueva, ADR-0013 (sus credenciales en Andvari), despliegue |

Lo que **no** cambia: el gateway sigue en Python y FastAPI, sin Rust; sigue siendo el registro y el custodio de
los datos cifrados por cliente; la autenticación (ADR-0004, ADR-0005) y el borrado (ADR-0014) no se tocan.

### Vista C4 — Container (objetivo)

Eje estructura, fase 00-project: dónde queda el gateway cuando el motor se parta en workers.

```mermaid
C4Container
    title Gateway en la topologia de ocho workers (objetivo)

    System_Ext(meta, "Meta", "WhatsApp Business, Messenger e Instagram")
    ContainerDb_Ext(atlas, "MongoDB Atlas", "MongoDB", "Registro y contactos cifrados por cliente")
    System_Ext(andvari, "Andvari", "OpenBao: claves de cliente")

    Container_Boundary(repo, "Este repositorio") {
        Container(webhook, "Webhook", "Python, FastAPI", "Firma, lectura y publicacion; responde en cuanto el bus confirma")
        Container(ingesta, "Worker de ingesta", "Python", "Identifica y guarda el contacto, publica el sobre")
        Container(api, "API de gestion", "Python, FastAPI", "Registro de clientes, bots, reglas e intents")
    }

    ContainerQueue(bus, "Bus de eventos", "NATS JetStream en midgard", "Eventos con clave de orden y traceparent")

    System_Boundary(motor, "Motor (ms-python-metamsghandler)") {
        Container_Ext(workers, "Workers 2 a 6", "Python", "Clasificador, coordinador, enlace, respuesta, evaluador")
        Container_Ext(notificador, "Notificador", "Python o Rust", "Envia la respuesta a Meta con control de tasas")
    }

    Rel(meta, webhook, "Notificacion firmada", "HTTPS")
    Rel(webhook, bus, "webhook.meta.*", "confirmado")
    Rel(bus, ingesta, "webhook.meta.*")
    Rel(ingesta, atlas, "Contacto cifrado")
    Rel(ingesta, andvari, "Clave del cliente")
    Rel(ingesta, bus, "gateway.message.identified")
    Rel(bus, workers, "Sobre del mensaje")
    Rel(workers, api, "Reglas y herramientas", "JWE")
    Rel(notificador, api, "Tokens de Meta", "JWE")
    Rel(notificador, meta, "Respuesta", "HTTPS")
    Rel(api, atlas, "Lee y escribe")
```

### El camino nuevo de una notificación

Eje comportamiento: qué espera Meta y qué ya no.

```mermaid
sequenceDiagram
    autonumber
    participant M as Meta
    participant W as Webhook
    participant B as Bus
    participant I as Worker de ingesta
    participant D as Atlas y Andvari
    participant E as Motor

    M->>W: POST /chat/{bot_id} firmado
    W->>W: Comprueba la firma y lee la notificacion
    W->>B: Un evento por mensaje (id de Meta, clave de orden, traceparent)
    B-->>W: Confirmado
    W-->>M: 200
    Note over M,W: Meta solo espera hasta aqui
    B->>I: Evento
    I->>D: Busca o guarda el contacto cifrado
    alt La base o Andvari no responden
        I-->>B: Sin confirmar: el bus lo entrega otra vez
    else Todo responde
        I->>B: gateway.message.identified
        I-->>B: Confirma el evento
        B->>E: Sobre del mensaje
    end
```

## Decisiones

Tomadas el 2026-10-18 por Jeremi Alcala.

| # | Decisión | Qué se decidió | Lo que arrastra |
|---|---|---|---|
| D1 | El bus | **NATS JetStream**, en lugar de RabbitMQ | Desplegarlo en midgard (R8); el motor pasa a leer de NATS; Hermod se retira al final. La deduplicación en el consumidor sigue haciendo falta |
| D2 | Sacar la identificación del contacto del webhook | **Sí** (R1) | ADR-0006 y ADR-0015 se reescriben: `503` solo si NATS no acusa el evento |
| D3 | Una credencial por worker | **Sí** (R5) | ADR-0016 se amplía: cada clave de servicio dice qué rutas alcanza |
| D4 | Instagram | **Entra en el alcance** (R7) | Requisito nuevo en el PRD, contrato y clasificación de datos; consultar lo que Meta exige a ese webhook |
| D5 | Cerrar el Gate 2 antes de empezar | **Sí** | Revisión dual y corte de la 0.3.0 antes del paso 1 |
| D6 | Contexto de conversación | **Historial exportable; contexto de 10 vueltas más resumen que caduca a las 24 horas** | [ADR-0017](adr/0017-contexto-de-conversacion-e-historial.md), propuesta: historial en Atlas, escrito por el worker de ingesta; contexto en un bucket KV de NATS, del coordinador. Incrementos nuevos detrás de I4 |

La recomendación de D1 era seguir con RabbitMQ y D4 dejaba Instagram fuera; las dos se cambiaron.

## Cómo se recorre con AI-DLC

El refactor cambia requisitos, diseño y contratos, así que no entra como un incremento más del Gate 2: reabre los
artefactos de los gates 0 y 1 como cambios sobre la línea base 0.3.0, y luego se implementa por incrementos.

| Paso | Gate | Qué se produce |
|---|---|---|
| 0 | Cierre del Gate 2 actual | **Hecho el 2026-10-18**: revisión dual, corte de la 0.3.0 en el CHANGELOG y tag |
| 1 | Requisitos (Gate 0) | **Aprobado el 2026-10-18** (PRD 0.3.0). PRD: RNF01 con 250 ms de mediana y menos de un 1 % sobre 1 s para WhatsApp; requisitos de orden por conversación, de traza y del canal de Instagram; escenarios de abuso nuevos (un evento repetido, un evento fuera de orden, datos personales en el bus o en una traza, un servicio que publica en un sujeto ajeno); clasificación de datos con el bus, los spans y el identificador de Instagram |
| 2 | Diseño (Gate 1) | **Aprobado el 2026-10-18**, con ADR-0017, ADR-0019 y ADR-0020 aceptadas a condición de I0. ADR-0017 (D6); [ADR-0019](adr/0019-nats-jetstream-como-bus.md) (D1, NATS JetStream) y [ADR-0020](adr/0020-placement-de-bifrost.md) (su placement en midgard, como Bifröst), [ADR-0021](adr/0021-el-webhook-solo-publica.md) (D2) y [ADR-0022](adr/0022-una-credencial-por-worker.md) (D3), propuestas, y el [AsyncAPI 2.0.0](../02-design/contracts/asyncapi-v2.yaml); arquitectura y threat model del objetivo (en revisión); de D2 (reescribe ADR-0006 y ADR-0015) y de D3 (amplía ADR-0016); ADR-0011 y AsyncAPI 2.0.0; arquitectura con el worker de ingesta y NATS; threat model con los límites de confianza nuevos y T11 |
| 3 | Implementación (Gate 2) | Los incrementos de abajo, uno por PR a `develop` |
| 4 | Pruebas (Gate 3) | Medición de latencia contra Atlas y NATS reales en midgard, que es la evidencia de RNF01; corte de la 0.4.0 |

Incrementos del paso 3, en este orden (eje trazabilidad: qué depende de qué):

```mermaid
flowchart LR
    IS["IS Separar el webhook de la API de gestion (RD-05), hecho"] --> I1
    I1["I1 Medir el webhook actual en midgard, hecho"] --> I4
    I0["I0 NATS JetStream en midgard (R8), hecho"] --> I2
    I2["I2 Puerto de publicacion sobre NATS (R2), hecho"] --> I3
    I3["I3 Contrato v2 y un evento por notificacion (R3), hecho"] --> I4
    I4["I4 Worker de ingesta y webhook que solo publica (R1), hecho"] --> I5
    I4 --> I8
    I5["I5 OpenTelemetry con la regla de los logs (R4), hecho"]
    I6["I6 Credencial por worker (R5)"]
    I7["I7 Destinos de los intents (R6)"]
    I8["I8 Canal de Instagram (R7)"]
```

IS va antes que nada: es la condición con la que se aceptó RD-05 al cerrar el Gate 2, y tiene que estar antes del primer despliegue. Hecho con [ADR-0018](adr/0018-webhook-y-gestion-por-separado.md). Deja el webhook en su propio contenedor, sin las rutas ni las claves de gestión. I1 va después porque da la cifra contra la que se mide todo lo demás, y se mide con el camino actual, todavía
sobre Hermod. I0 es un despliegue en midgard, como el de Hermod y Andvari. I6 e I7 no dependen del resto y pueden
ir en paralelo; Instagram va detrás del worker de ingesta para nacer ya en el camino nuevo. Del lado del motor,
que es otro repositorio, quedan partirlo en workers, leer de NATS el contrato v2, deduplicar y respetar la clave
de orden; hasta que lo haga, el gateway no puede dejar de publicar en Hermod, y el paso de uno a otro necesita su
propio plan de corte en el Gate 4.
