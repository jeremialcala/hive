# Bus de eventos: cómo publica el gateway en Bifröst

* **Estado:** review
* **Fecha:** 2026-10-19
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 03-implementation
* **Versión:** 0.1.0
* **ADR:** [ADR-0019](../00-project/adr/0019-nats-jetstream-como-bus.md), [ADR-0021](../00-project/adr/0021-el-webhook-solo-publica.md)
* **Incremento:** I2 del [plan de la topología de workers](../00-project/plan-topologia-de-workers.md) (R2)

El gateway publica eventos con un puerto, `EventPublisher` (`shared/bus`), que no dice qué bus hay detrás. Su adaptador
es `NatsPublisher`, sobre NATS JetStream, es decir Bifröst.

**Publican el webhook, desde I3, y el worker de ingesta, desde I4.**

- El webhook publica cada notificación en `WEBHOOK` y no hace nada más
  ([El evento del webhook](#el-evento-del-webhook-i3)).
- El worker la consume, identifica a quien escribe y publica cada mensaje en `MENSAJES`
  ([El worker de ingesta](#el-worker-de-ingesta-i4)).
- Mientras el motor siga leyendo la cola `llm` de Hermod, el worker le manda allí también el sobre v1. Hermod se
  retira cuando el motor lea de Bifröst.

## Qué garantiza

- **Una conexión por proceso.** Se abre la primera vez que hace falta y la mantiene el cliente: si se cae, reconecta
  solo, sin límite de intentos. Con RabbitMQ había una conexión por mensaje.
- **El evento lleva `Nats-Msg-Id`.** Si el mismo identificador se publica otra vez dentro de la ventana del stream (2
  minutos en `WEBHOOK`), el stream no lo guarda dos veces y la respuesta dice que era un duplicado.
- **Se espera el acuse del stream** (`PubAck`), con 3 segundos de plazo. Bifröst hace `fsync` antes de darlo
  (`sync_interval: always`).
- **Un fallo es siempre `BusUnavailable`.** Lo provocan: no llegar al bus, una contraseña que no vale, un sujeto que
  el usuario no puede publicar, un sujeto sin stream, el búfer lleno mientras reconecta o un acuse que no llega a
  tiempo. Quien llama responde `503`, y el mensaje de la excepción es solo el tipo de fallo: el texto de NATS puede
  llevar la dirección.
- **Un bus caído no hace esperar.** Tras un intento fallido de conectar, durante 1 segundo no se vuelve a intentar.
  Cada petición recibe `BusUnavailable` al momento.

## Configuración

Las variables se llaman igual que en el fichero que Bifröst escribe para cada servicio,
`/srv/infra/bifrost/credenciales/<servicio>.env`. Ese fichero se le puede dar al contenedor tal cual:

| Variable | Qué es |
|---|---|
| `NATS_URL` | `tls://bifrost:4222`. Sin ella no hay publicador. Fuera de desarrollo tiene que ser `tls://`: con otra cosa el servicio no arranca |
| `NATS_USER`, `NATS_PASSWORD` | El usuario del servicio. El del webhook solo puede publicar en `webhook.meta.>` |
| `NATS_CA` | La CA de Bifröst, montada en `/run/bifrost/ca.crt` (en el servidor, `/srv/infra/bifrost/tls/ca.crt`) |
| `NATS_INBOX_PREFIX` | `_INBOX_<servicio>`. Bifröst solo deja a cada servicio leer sus propias respuestas: con el prefijo por defecto, el acuse no llega nunca |

El contenedor tiene que estar en la red Docker externa `bifrost`. En
[`deploy/docker-compose.yml`](../../deploy/docker-compose.yml), el webhook entra en ella y recibe como `env_file`
el fichero `webhook.env` de Bifröst, con su CA montada. La API de gestión no publica todavía y no la tiene.

## El evento del webhook (I3)

Por cada notificación firmada que trae algún mensaje de una persona, y antes de nada más, el webhook publica **un
solo evento con todos sus mensajes** ([ADR-0021](../00-project/adr/0021-el-webhook-solo-publica.md)). El evento es
`EventoWebhook`, del [contrato v2](../02-design/contracts/asyncapi-v2.yaml), y va al sujeto
`webhook.meta.whatsapp` o `webhook.meta.messenger`, según el canal. Lo construye `controller/webhook_event.py`.

| Parte | Qué lleva |
|---|---|
| Cuerpo | `schemaVersion: 2`, `botId`, `clientId`, `channel`, `receivedAt` (UTC, con zona) y `messages`. Cada mensaje lleva `messageId` y `message`, que es lo mismo que recibe hoy el motor en el sobre v1 |
| `Nats-Msg-Id` | `<botId>:` seguido del SHA-256 de los identificadores de Meta de los mensajes, ordenados y unidos por comas. La misma notificación reenviada da el mismo identificador, y no se ven los `wamid` en claro |
| `traceparent` | Un contexto W3C propio de cada evento. Con OpenTelemetry (I5) pasa a ser el de la traza de la petición |

Qué responde el webhook:

- Los acuses de entrega y de lectura y los ecos de la página no se publican.
- Si Bifröst no acusa el evento, el webhook responde `503` y no manda nada a Hermod: Meta reenvía la notificación.
- Si Bifröst acusa el evento pero después falla Hermod, el webhook también responde `503`. El reenvío de Meta lo
  descarta Bifröst por `Nats-Msg-Id`, si llega dentro de los 2 minutos de la ventana.
- Desde I4, eso es todo lo que hace. Fuera de desarrollo, el webhook no arranca sin `NATS_URL`. En desarrollo y en
  las pruebas, sin bus, ejecuta él mismo la ingesta del evento antes de responder.

### Medido en midgard con I3 (2026-10-19)

Se usó la instancia de medición de [I1](../04-testing/medicion-webhook-i1.md), con la imagen `i3-70d07d1` y el
compose de esta rama. El script es el mismo de I1. Las imágenes se alternaron sobre el mismo despliegue: I3, I1, I3.

| Escenario | I1 (Hermod) | I3 (Bifröst + Hermod) |
|---|---|---|
| Contacto conocido, uno tras otro | 177 ms | 181 ms |
| Contacto nuevo, uno tras otro | 394 ms | 399–401 ms |
| Contacto conocido, 4 a la vez | 492 ms (357–472 ms en las pasadas de I1) | 347–357 ms; en una primera pasada, 645 ms |

- **Publicar en Bifröst le añade al webhook unos 4 ms.** Sin concurrencia, el plazo lo sigue poniendo Atlas.
- **Con 4 a la vez, la mediana varía de una pasada a otra, entre 347 y 645 ms**, con la misma imagen y con las dos.
  Es la cola que forman los procesos bloqueados por pymongo (H14). No se ve que I3 lo empeore.
- **Las 610 notificaciones de cada pasada quedaron como 610 eventos en `WEBHOOK`**, uno por notificación. Al terminar
  se purgaron el stream y la cola `llm`.

## El worker de ingesta (I4)

`src/ingest.py`, el servicio `ingest` del compose, contenedor `hive-ingest`. Lee `WEBHOOK` con el consumidor durable
`ingesta` de Bifröst, que solo enlaza: su usuario no puede crear consumidores. Trata las notificaciones una detrás
de otra, en orden, con `controller/ingestion.py`. Con cada mensaje de la notificación hace esto:

1. **Si ya está en el historial, lo salta.** Es Meta reenviándolo.
2. **Identifica al contacto** o lo guarda, como lo hacía el webhook. En Messenger, con la Graph API.
3. **Lo publica**, en dos sitios:
   - como `MensajeIdentificado` en la partición de su conversación,
     `gateway.message.<partición>.<botId>.<contactId>`, con `Nats-Msg-Id` igual a `<botId>:<messageId>` y el
     `traceparent` del evento;
   - como sobre v1 en la cola `llm` de Hermod, para el motor que todavía la lee.
4. **Lo guarda en el historial.**

**Publicar va antes que guardar.** Así un corte entre los dos pasos repite una publicación, que el bus descarta por
`Nats-Msg-Id` dentro de su ventana, y nunca pierde ninguna. Al revés, un mensaje guardado y sin publicar se saltaría
en la siguiente entrega y se perdería. Es un cambio de orden respecto de ADR-0021, que guardaba antes de publicar;
la ADR lo recoge. El motor tiene que tolerar un duplicado de todas formas.

| Qué pasa | Qué hace con el evento |
|---|---|
| Todos sus mensajes tratados | Lo confirma, y sale de la cola de trabajo |
| No responden la base, Andvari, Bifröst o Hermod | No lo confirma. Bifröst lo reentrega con su espera creciente (30 s, 1 min, 5 min, 15 min, 30 min, 1 h…) mientras lo guarde, 7 días |
| Un mensaje falla por sí mismo | Lo deja en el log y sigue con los demás |
| No es JSON, es de otra versión o su bot ya no existe | Lo descarta (`term`) y lo deja en el log |
| Falla de un modo que no se conoce | No lo confirma: mejor reintentar que perderlo |

Está sano mientras siga pidiendo eventos. Toca `/tmp/ingest-alive` al menos cada 5 s, y el healthcheck del compose
mira la antigüedad del fichero. Usa `ingesta.env` de Bifröst: su usuario solo puede leer su consumidor y publicar
en `gateway.message.>`.

Ritmo medido en midgard: 2,1 notificaciones de un mensaje por segundo, porque cada una hace varios viajes seguidos a
Atlas ([medición](../04-testing/medicion-webhook-i1.md#lo-que-cuesta-ahora-el-worker-de-ingesta)).

### Las respuestas del motor, al historial

El worker lee también `RESPUESTAS`, con el consumidor `historial`, en un bucle propio para que una respuesta no
espere detrás de una notificación. Cada `RespuestaEnviada` del contrato v2 la publica el notificador del motor cuando
Meta acepta un envío. El worker la guarda en la conversación del contacto como mensaje de salida (`out`):

- con `inReplyTo`, el identificador de Meta del mensaje al que responde;
- con el texto cifrado;
- de cada adjunto, solo el tipo.

| Respuesta | Qué hace |
|---|---|
| Guardada, o ya estaba | La confirma |
| Su contacto, su bot o su cliente ya no existen | La descarta: la conversación se fue con ellos |
| No es de la versión 2 o le falta un campo | La descarta |
| No responden la base o Andvari | No la confirma: Bifröst la reentrega mientras la guarde, 24 horas |

Probado en midgard contra el Bifröst real (2026-10-19): una respuesta publicada en `engine.reply.sent.<botId>` quedó
en el historial del contacto con su `inReplyTo` y el texto cifrado, y el consumidor `historial` sin pendientes.

## Medido contra Bifröst (2026-10-19)

En midgard, con la imagen de esta rama (`i2-fcc3764`), el usuario `webhook` y TLS. Fueron 300 eventos de 1 KB
seguidos por la misma conexión, al stream `WEBHOOK`, en el SSD:

| Medida | Valor |
|---|---|
| Primera publicación, abriendo la conexión con TLS | 139 ms |
| Cada evento después | 2,1 ms de mediana; p95 2,9 ms; p99 3,8 ms; máximo 13,8 ms |
| El mismo identificador otra vez | No se guarda; la respuesta lo marca como duplicado |
| Un sujeto que el webhook no puede publicar (`gateway.message.…`) | `BusUnavailable` al vencer el plazo |

Con Hermod y una conexión por mensaje, publicar costaba 8,3 ms de mediana ([I1](../04-testing/medicion-webhook-i1.md)).
La primera publicación de cada proceso paga la conexión. Los 301 eventos de la prueba se purgaron del stream al
terminar.

## Trampas encontradas

- **Con reconexión infinita, `nats-py` también reintenta para siempre la primera conexión**, y eso incluye una
  contraseña equivocada: la petición se queda colgada. Por eso la primera conexión tiene un plazo propio
  (`CONNECT_TIMEOUT` + 1 s), y el intento fallido se cierra para que no siga reintentando por detrás.
- **Vaciar un stream no borra su ventana de duplicados.** Un identificador publicado antes de la purga se sigue
  descartando hasta que pasa la ventana. Hay una prueba que lo fija. Si se purga `WEBHOOK` para reprocesar algo,
  ese algo no puede volver a entrar con el mismo `Nats-Msg-Id` hasta pasados 2 minutos.
- **Una conexión y su cerrojo pertenecen al bucle de eventos que los creó.** Si cambia el bucle (en las pruebas,
  cada `asyncio.run`), el publicador abre otra conexión en vez de usar la anterior.

## Pruebas

- `tests/unit/test_event_bus.py`: cuándo conecta, cuándo no reintenta y en qué acaba cada fallo, con un cliente falso.
- `tests/integration/test_nats_publisher.py`: contra un NATS 2.14.8 real configurado como Bifröst
  (`tests/integration/nats-test.conf`), con el usuario del webhook. Comprueba qué guarda el stream, los duplicados,
  el mensaje de 3 MB, los permisos, el prefijo de buzón y que hay una sola conexión, contada en `/connz`. En el CI
  corren siempre (`NATS_TEST_REQUIRED`).

  Para correrlas en local:

  ```bash
  docker run --rm -p 127.0.0.1:24222:4222 -p 127.0.0.1:28222:8222 \
      -v "$PWD/tests/integration/nats-test.conf:/etc/nats/test.conf:ro" nats:2.14.8-alpine -c /etc/nats/test.conf
  NATS_TEST_ADDR=127.0.0.1:24222 NATS_TEST_MONITOR=127.0.0.1:28222 pytest tests/integration/test_nats_publisher.py
  ```
