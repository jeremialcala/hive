# ADR-0008: El motor, seis workers sobre Bifröst

* **Estado:** accepted
* **Fecha:** 2026-10-20
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 02-design
* **Versión:** 0.1.0
* **ID:** ADR-0008
* **Supersede:** ADR-0003 (consumo AMQP con un hilo por mensaje y confirmación manual)
* **OWASP:** A01 (cada worker solo alcanza lo suyo), A04 (TLS hasta Bifröst), A09 (trazas sin datos personales)

Aceptada el 2026-10-20 al fusionar la PR #65.

## Contexto

El plan de la topología de workers del gateway (`docs/00-project/plan-topologia-de-workers.md`, en la raíz del
monorepo) parte el motor en seis workers. Hoy el motor es uno solo, y solo clasifica:

- lee de RabbitMQ con `pika` síncrono, con un hilo por mensaje y `prefetch=1`;
- pasa el mensaje por NeMo y Ollama;
- publica un `ResultadoClasificado` en una cola que nadie lee.

Además descarta `botId`, `clientId`, `messageId` y el contacto, y un `nack` pierde el mensaje.

Lo que ya está construido del otro lado:

- **El gateway publica en Bifröst** (NATS JetStream 2.14) cada mensaje identificado, en
  `gateway.message.{partición}.{botId}.{contactId}`, stream `MENSAJES`. La partición son 16 particiones, por un
  hash del bot y el contacto. El evento lleva `Nats-Msg-Id = {botId}:{messageId}` y el `traceparent`
  (`docs/02-design/contracts/asyncapi-v2.yaml`).
- **El historial de las respuestas** espera lo que el notificador envió en `engine.reply.sent.{botId}`, stream
  `RESPUESTAS`.
- **Cada worker tiene su clave de servicio para la API de gestión**, con sus rutas (ADR-0022 del gateway). La parte
  privada está en Andvari, en `motor/data/clientes/<cliente>/<worker>`, y cada worker la lee con su rol
  `motor-<worker>`.
- **Los destinos de los intents se validan al guardarlos** (I7). El worker de enlace tiene que volver a comprobarlos
  al llamar.

## Decisión

**Seis workers en la imagen del motor, uno por contenedor, que se pasan el mensaje por Bifröst.** Cada worker es un
punto de entrada de la misma imagen (`python -m src.workers.<worker>`), con su configuración, su clave, su rol de
Andvari y su usuario de Bifröst.

### El recorrido de un mensaje

```mermaid
flowchart LR
    GW["gateway<br/>(ingesta)"] -- "gateway.message.{p}.{bot}.{contacto}<br/>MENSAJES" --> CL["clasificador"]
    CL -- "motor.clasificado.{p}.{bot}.{contacto}" --> CO["coordinador"]
    CO -- "motor.accion.{p}.{bot}.{contacto}" --> EN["enlace"]
    EN -- "motor.resultado.{p}.{bot}.{contacto}" --> CO
    CO -- "motor.pedido.{p}.{bot}.{contacto}" --> RE["respuesta"]
    RE -- "motor.respuesta.{p}.{bot}.{contacto}" --> EV["evaluador"]
    EV -- "motor.aprobada.{p}.{bot}.{contacto}" --> NO["notificador"]
    NO -- "engine.reply.sent.{bot}<br/>RESPUESTAS" --> GW
    NO -- "Graph API" --> META["Meta"]
```

| Worker | Lee | Publica | Alcanza en la API del gateway |
|---|---|---|---|
| Clasificador | `gateway.message.>` (MENSAJES) | `motor.clasificado.…` | Reglas |
| Coordinador | `motor.clasificado.…`, `motor.resultado.…` | `motor.pedido.…`, `motor.accion.…` | Reglas, herramientas, vueltas |
| Enlace | `motor.accion.…` | `motor.resultado.…` | Herramientas |
| Respuesta | `motor.pedido.…` | `motor.respuesta.…` | Reglas |
| Evaluador | `motor.respuesta.…` | `motor.aprobada.…` | Reglas |
| Notificador | `motor.aprobada.…` | `engine.reply.sent.{botId}` (RESPUESTAS) y la Graph API de Meta | Tokens de Meta |

1. **Un stream propio, `MOTOR`**, con los sujetos `motor.>`.
   - Guarda los mensajes **24 horas**, que basta para reintentar y para mirar qué pasó.
   - Llevan el texto de la persona, así que no se guardan más: es un sitio más en el derecho de supresión
     (Datos, abajo).
   - Usa `sync_interval: always`, como los demás streams de Bifröst (ADR-0019 del gateway).
2. **Cada sujeto lleva la partición, el bot y el contacto**, como los del gateway. La partición es la del evento
   original y nadie la vuelve a calcular.
3. **Un consumidor duradero por worker**, de tipo pull, con su filtro de sujetos.
   - Lo crea el aprovisionamiento de Bifröst, no el worker, que solo se ata a él, igual que la ingesta del gateway.
   - Confirma después de publicar lo que sigue. Si un mensaje falla, se reintenta con `max_deliver` y con espera
     creciente; al agotar los intentos, sale en un aviso. Nunca se pierde en silencio.
4. **Cada paso deduplica al publicar**, con `Nats-Msg-Id = {paso}:{botId}:{messageId}`. Un reintento dentro de la
   ventana de Bifröst no duplica la respuesta. El notificador comprueba además, antes de enviar, que esa respuesta
   no salió ya: Meta reintenta durante días, y la ventana es de minutos.
5. **El orden por conversación.** Dos mensajes seguidos de la misma persona se responden en orden: cada consumidor
   tiene un solo mensaje en vuelo por partición. En el esqueleto, `max_ack_pending: 1` por consumidor ordena todo;
   las particiones paralelas llegan cuando haga falta el caudal (su condición de revisión).
6. **El contexto viaja en el mensaje.** Cada paso añade lo suyo a un sobre que crece y no guarda nada en memoria
   entre mensajes:
   - el clasificador, la intención;
   - el coordinador, las reglas y las vueltas;
   - el de respuesta, el texto;
   - el evaluador, su veredicto.

   Así cualquier réplica puede atender cualquier mensaje. El sobre es el contrato interno del motor, versionado
   con `schemaVersion`. Se escribe con el primer worker, en `engine/docs/02-design/contracts/asyncapi-motor.yaml`.
7. **La traza sigue un solo hilo.** Cada worker abre su span desde el `traceparent` que llega y lo pasa al mensaje
   que publica, con la lista cerrada de atributos del gateway (I5).
8. **El código que comparten el gateway y los workers va a `shared/`** (ADR-0023 del gateway):
   - el contrato v2;
   - la firma de los tokens y el almacén de claves de Andvari;
   - el publicador y el consumidor de NATS;
   - las trazas;
   - la regla de los destinos.
9. **Lo que hay hoy en `src/` se reaprovecha en el clasificador:** el normalizador, el saneado y el enum de
   orígenes. El consumidor AMQP y el publicador se retiran cuando el clasificador lea de Bifröst. Su ADR-0003
   queda superada por esta.

### El esqueleto primero (decisión de Jeremi, 2026-10-20)

El primer objetivo es que un mensaje de WhatsApp reciba una respuesta real, de punta a punta. Para eso, en el
esqueleto el coordinador no llama al enlace y el notificador lee `motor.respuesta.…` directamente, sin evaluador.
El enlace y el evaluador llegan después: el evaluador entra cambiando el sujeto que lee el notificador, no su
código. El orden está en `engine/docs/00-project/plan-de-los-workers.md`.

## Alternativas consideradas

| Opción | Pros | Contras | Riesgo de seguridad |
|---|---|---|---|
| **Seis workers por Bifröst, una imagen, un stream propio** | Cada worker escala y falla por su cuenta, y solo alcanza lo suyo (clave, rol, usuario). Un solo CI y una sola cadena de dependencias | Siete saltos por el bus, con un `fsync` cada uno en el disco de midgard (44,5 ms medidos) | El texto vive 24 h en el stream. Lo mitiga la retención corta, el TLS y un usuario por worker |
| Un solo proceso con las etapas en memoria | Sin saltos, la latencia más baja | Una credencial con todas las rutas (lo que ADR-0022 cierra). Un fallo de una etapa tumba todas, y no escala por etapas | Una clave robada alcanza los tokens de Meta y todo lo demás |
| Seis workers por RabbitMQ (Hermod) | Ya está desplegado | Hermod se retira (D1 del gateway). Sin deduplicación al publicar | Igual que la elegida, con un broker que se va |
| Llamadas HTTP síncronas entre workers | Sencillo de razonar | Un worker caído pierde el mensaje o bloquea al anterior; habría que inventar los reintentos | Cada worker expone un puerto |

## Consecuencias

- **Bifröst crece:**
  - el stream `MOTOR`;
  - un consumidor por worker y un usuario por worker con sus sujetos (I6c del gateway);
  - el consumidor del clasificador sobre `MENSAJES`.

  Se aprovisiona en el repo `bifrost`.
- **Andvari:** emitir el `secret_id` de cada rol `motor-<worker>` al desplegarlo, con `raiz-temporal.sh`.
- **La latencia:** el presupuesto de RNF01 (250 ms) es del webhook, no de la respuesta. La respuesta la dominan las
  llamadas a Claude. Los siete `fsync` (unos 300 ms en el HDD de midgard) se miden en el esqueleto.
- **El sobre interno tiene contrato y pruebas de contrato**, como el v2 del gateway.
- **Supresión:** los workers no guardan estado. El stream `MOTOR` caduca a las 24 h y no se purga por contacto: lo
  que se borra antes de caducar lo dice la clasificación de datos.

## Construido (E3a, el clasificador, 2026-10-20)

- **El primer worker:** `python -m src.workers.classifier`. Lee `MENSAJES` con el consumidor `classifier` de
  Bifröst (E2) y publica en `motor.clasificado.{partición}.{botId}.{contactId}`, con `Nats-Msg-Id =
  clasificado:{botId}:{messageId}`. Confirma después de publicar.
- **El bucle común** de los workers está en `src/workers/runtime.py`. Se ata al consumidor sin crearlo, lee un
  mensaje cada vez y hace con él lo que diga su resultado:
  - **confirmado:** se confirma;
  - **descartado:** se termina;
  - **en espera:** no se confirma, y Bifröst lo entrega otra vez.

  Un fallo no previsto lo deja en espera. Mientras vive, el worker toca `/tmp/<worker>-alive`, para la comprobación
  de salud del contenedor.
- **Lo que se descarta:** un mensaje que no es JSON, que no cumple el contrato v2, o cuyo sujeto no dice el mismo
  bot y contacto que el evento.
- **El sobre, versión 1**, en `src/domain/models/sobre.py` y en
  [`asyncapi-motor.yaml`](../contracts/asyncapi-motor.yaml), validado por `tests/contract/test_contrato_motor.py`:
  - lleva los campos del mensaje identificado con sus nombres del contrato v2, menos el `message` de Meta;
  - el contacto va sin el nombre;
  - añade `entrada`, con el texto saneado y el tipo de los adjuntos, y `clasificacion`.

  El texto lo lee `shared.contract.what_was_said`, el mismo código con el que el gateway lo guarda en el historial.
- **La traza** sigue el `traceparent` del evento, en un span `engine.classifier` con los atributos de la lista del
  gateway, y lo pasa al sobre.
- **Las pruebas de integración** corren contra un NATS con el usuario `classifier` y sus permisos de Bifröst
  (`tests/integration/nats-test.conf`), también en el CI. Comprueban también que ese usuario no puede publicar el
  paso de otro.

## Construido (E4, el coordinador, 2026-10-20)

- **El segundo worker:** `python -m src.workers.coordinator`. Lee `motor.clasificado.…` con el consumidor
  `MOTOR/coordinator` y publica en `motor.pedido.{partición}.{botId}.{contactId}`, con `Nats-Msg-Id =
  pedido:{botId}:{messageId}`. Usa el mismo bucle que el clasificador.
- **Lo que pide**, en el sobre (versión 1, con el canal `pedido` en `asyncapi-motor.yaml` 1.1.0):
  - **si el mensaje sigue**, `respuesta: "claude"`, con la persona del bot y sus reglas y las últimas vueltas (40
    como mucho, la más antigua primero, sin el mensaje que se responde);
  - **si no sigue**, un texto fijo según el motivo, sin preguntar nada al gateway (decisión de Jeremi del
    2026-10-20): `no_puedo` para inyección, abuso, otro y rechazo; `solo_texto` sin texto; `no_disponible` sin
    veredicto. Los textos los escribe el de respuesta (E5). Así la persona siempre recibe algo.
- **El gateway lo da por su API**, con la clave del coordinador para el cliente dueño del bot
  (`src/infrastructure/gateway/api.py`).
  - Cada llamada lleva un token nuevo, firmado y cifrado con `shared.auth.client`.
  - La clave privada de cada cliente se lee de Andvari la primera vez y se guarda en memoria; si el gateway la
    rechaza (401), se lee otra vez.
  - La clave pública del gateway es la última de `GATEWAY_PUBLIC_KEYS_FILE`.
  - Las redirecciones no se siguen.
- **`GET /chat/{bot_id}/rules` cambia en el gateway** (decisión de Jeremi del 2026-10-20). Devolvía un prompt para
  Llama 2, pensado para el motor de antes, y ahora devuelve los datos: `name`, `role`, `tone`, `description` y
  `rules`.
- **Si el gateway falla**, el mensaje queda en espera, y en la última entrega se pide `no_disponible`. Si el bot o el
  contacto ya no existen (404), el mensaje se descarta: no hay a quién responder.
- **Lo que el esqueleto deja para después:**
  - el bucket `contexto` de ADR-0017 del gateway, con el resumen, su cifrado con Transit y la supresión por el bus.
    El coordinador pide las vueltas al gateway en cada mensaje, que es la fuente de verdad: son dos llamadas al
    gateway, cada una con su consulta a Atlas (60 ms de ida y vuelta medidos desde midgard). Se mide en el E7;
  - el enlace y las herramientas.

## Construido (E6, el notificador, 2026-10-20)

- **El cuarto worker:** `python -m src.workers.notifier`. Lee `motor.respuesta.…` con el consumidor `MOTOR/notifier`,
  envía el texto por la Cloud API de WhatsApp y publica lo enviado en `engine.reply.sent.{botId}` (`RESPUESTAS`).
  - Lo publicado es un `RespuestaEnviada` del contrato v2 del gateway, con `Nats-Msg-Id = {botId}:{id de Meta}`.
  - Una prueba de contrato lo valida contra `asyncapi-v2.yaml`.
- **No envía dos veces la misma respuesta** (punto 4). Antes de enviar mira el bucket KV `enviados` de Bifröst y,
  en cuanto Meta acepta, la apunta allí (decisión de Jeremi del 2026-10-20).
  - Si Bifröst le entrega otra vez una respuesta que ya salió, no la envía: publica lo que ya se envió.
  - Sin poder mirar el bucket, no envía.
  - Si no puede apuntarla, la publica igual. Solo si además falla publicar y se entrega otra vez puede llegar dos
    veces.
  - El bucket caduca a las 24 horas, como `MOTOR`. Su clave es el bot y el SHA-256 del id del mensaje, sin datos
    personales.
  - Lo crea el repositorio `bifrost` (higerotech/bifrost#2), con los permisos del usuario `notifier`.
  - Los reintentos de Meta de días no llegan hasta aquí: la ingesta del gateway los descarta contra el historial.
- **Desde dónde responde:** el clasificador pone en el sobre `cuentaDelBot`, el `phone_number_id` al que escribió
  la persona, que trae el mensaje de Meta. El contrato del sobre pasa a la 1.3.0.
- **El token de WhatsApp** del bot lo pide al gateway (`GET /chat/{bot_id}/tokens`), con la clave del notificador,
  el único worker que lo recibe. Lo recuerda 10 minutos, para no pedirlo en cada mensaje, y lo vuelve a pedir si
  Meta lo rechaza (códigos 0 y 190).
- **Qué hace con cada error de Meta**, por su `code`, como pide su documentación (consultada el 2026-10-20):
  - **se reintenta**, con las entregas de Bifröst: saturación, límites de envío y fallos temporales (1, 2, 4,
    130429, 131000, 131016, 131056 y 80007), los de permisos (10 y 131005), y un 5xx o un 429 sin código;
  - **se descarta**, porque no irá nunca así: los demás, como 131047 (pasaron las 24 horas de la ventana de
    atención) o 131026 (el número no puede recibir).

  En la última entrega, lo que aún no salió se descarta y queda en el log.
- **Solo texto**, sin vista previa de enlaces y sin citar el mensaje de la persona (decisión de Jeremi del
  2026-10-20), con la Graph API v26.0, la más nueva el 2026-10-20 (`GRAPH_VERSION`).
- **Messenger e Instagram** se descartan hasta el E10.
- **El bucle común** pasa a cada worker su conexión con Bifröst al arrancar: el notificador abre con ella el bucket.

## Condiciones de revisión

- El caudal pide más de un mensaje en vuelo: entonces, un consumidor por grupo de particiones.
- La latencia medida de los saltos por Bifröst pesa frente a la de Claude.
- Un worker necesita estado propio entre mensajes.
