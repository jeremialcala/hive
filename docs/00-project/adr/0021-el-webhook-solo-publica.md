# ADR-0021: El webhook solo comprueba la firma y publica; la ingesta identifica al contacto

* **Estado:** accepted
* **Fecha:** 2026-10-18
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 02-design
* **Versión:** 0.2.0
* **ID:** ADR-0021
* **Supersede:** [ADR-0006](0006-publicacion-confirmada-y-503.md) y la tabla de [ADR-0015](0015-que-se-responde-a-meta.md),
  cuando se acepte. Lo demás de ADR-0015 sigue: el cuerpo no se lee antes de la firma, y lo firmado que no se
  entiende recibe `200`.
* **Controles OWASP afectados:** A04 (disponibilidad del webhook), A08 (integridad de lo que se publica), A10
  (fallos, duplicados y pérdida)

Aceptada el 2026-10-18 al aprobar el Gate 1 del refactor. Se construye en I3 e I4.

Propuesta como entregable del Gate 1 del refactor ([plan](../plan-topologia-de-workers.md), R1). Decisión D2 del 2026-10-18.

## Contexto

Hoy el webhook hace todo antes de contestar a Meta:

- busca el bot;
- abre el App Secret con la clave del cliente;
- busca o crea el contacto, preguntando además a la Graph API en Messenger;
- publica en Hermod.

Con 60 ms de ida y vuelta a Atlas, un contacto nuevo de WhatsApp cuesta unos 300 ms solo de red, por encima de los
250 ms de mediana de RNF01. Cualquier fallo de la base o de Andvari acaba en un `503`, y Meta lo reintenta. En
Messenger, una hora de fallos da de baja la página.

El 2026-10-18 se decidió sacar la identificación del contacto del webhook (D2). El webhook ya corre aparte de la
gestión ([ADR-0018](0018-webhook-y-gestion-por-separado.md)), y el bus es NATS JetStream
([ADR-0019](0019-nats-jetstream-como-bus.md)).

## Decisión

### El webhook

1. **Comprueba la firma**, con el bot y su App Secret. Los dos se guardan en memoria, el bot 30 segundos (#29) y la
   clave de datos del cliente cinco minutos, así que casi siempre no cuesta ningún viaje.
2. **Lee la notificación y, si trae algún mensaje de una persona, la publica como un solo evento** en
   `webhook.meta.<canal>`, con todos sus mensajes. Los acuses de entrega y de lectura y los ecos no se publican, como
   hoy. El evento lleva en `Nats-Msg-Id` el bot y un resumen de los identificadores de Meta de sus mensajes, y el
   contexto de traza (`traceparent`). Un evento por notificación, y no por mensaje, lo decidió Jeremi el 2026-10-19:
   en el disco de midgard cada escritura cuesta un `fsync` de unos 45 ms, y una notificación de diez mensajes no
   cabría en el plazo (ver ADR-0019).
3. **Responde `200` cuando Bifröst acusa el evento.**

No escribe en la base ni llama a la Graph API.

| Situación | Respuesta a Meta |
|---|---|
| El cuerpo pasa de 3 MiB | `413` |
| Sin firma válida | `403`, sin leer el cuerpo |
| Bot inexistente | `404` |
| Firmado, pero no se entiende o es de un objeto que no se atiende | `200` y aviso en el log |
| Firmado, con mensajes incompletos entre otros que están bien | `200`; se publican los que están bien |
| No responden la base o Andvari al buscar el bot o abrir su App Secret | `503` |
| Firmado, sin ningún mensaje de una persona (acuses, ecos) | `200`, sin publicar nada |
| Bifröst no acusa el evento | `503`. Meta reenvía la notificación; si el primer evento sí llegó a guardarse, el segundo se descarta por `Nats-Msg-Id` dentro de 2 minutos, y si no, la ingesta lo reconoce por el historial |

### El worker de ingesta

Es un servicio nuevo del gateway, de la misma imagen. Consume el stream `WEBHOOK` con confirmación explícita. Cada
evento es una notificación, y la ingesta trata cada uno de sus mensajes por orden:

1. Comprueba en el historial si el mensaje ya se recibió, por bot e identificador de Meta. Si ya está, pasa al
   siguiente: es un reintento de Meta.
2. Identifica o crea el contacto, como hoy, con la Graph API en Messenger e Instagram.
3. Guarda el mensaje en el historial ([ADR-0017](0017-contexto-de-conversacion-e-historial.md)).
4. Publica el mensaje identificado en la partición de su conversación (`gateway.message.<partición>.<bot>.<contacto>`).
5. Cuando ha tratado todos los mensajes de la notificación, confirma el evento.

Si cae a mitad de una notificación, el evento se reentrega entero, y el paso 1 salta los mensajes que ya trató.

| Situación en la ingesta | Qué hace |
|---|---|
| No responden la base, Andvari o Bifröst | No confirma: el evento se reentrega con una espera creciente, hasta que caduca en el stream |
| La Graph API no da el perfil | Sigue sin el nombre, como hoy |
| Un mensaje falla por sí mismo | Lo deja en el log y sigue con los demás de la notificación |
| El bot ya no existe | Da el evento por terminado y lo deja en el log |

## Alternativas consideradas

| Opción | Pros | Contras |
|---|---|---|
| **El webhook solo publica; la ingesta identifica** | El plazo del webhook ya no depende de Atlas ni de la Graph API; las caídas de la base las absorbe el bus | Un servicio más; si la base cae más tiempo del que guarda el stream, el mensaje se pierde, aunque Meta ya recibió `200` |
| Seguir identificando en el webhook (lo que hay) | Nada nuevo | Con un contacto nuevo no cabe en 250 ms, y una caída de la base da de baja las páginas de Messenger |
| Guardar la notificación en la base y procesarla después (outbox) | Sin bus en el camino | Vuelve a poner a Atlas, a 60 ms, en el plazo del webhook |

## Consecuencias

- **El plazo del webhook depende de Bifröst**, en una red Docker local, y del `fsync` en cada escritura. Se mide en I0
  y en I1.
- **Una caída de la base ya no la reintenta Meta, sino la ingesta.** Meta reintenta 7 días en WhatsApp. La ingesta
  solo reintenta mientras el evento siga en el stream `WEBHOOK`.
- **El stream `WEBHOOK` guarda 7 días**, lo mismo que reintenta Meta en WhatsApp. Lo decidió Jeremi el 2026-10-18,
  frente a las 24 horas que proponía esta ADR. Una caída de Atlas tiene así el mismo margen que antes, a cambio de
  que, mientras dura, los mensajes sin procesar esperen en el bus hasta una semana. En condiciones normales la
  ingesta los confirma en segundos y se borran. Gjallarhorn avisa en cuanto haya eventos sin confirmar de más de 15
  minutos.
- **Desaparece RD-05 en el webhook:** ya no tiene llamadas bloqueantes por cada mensaje, salvo al buscar el bot sin
  caché.
- **La deduplicación tiene un sitio:** el historial (paso 1 de la ingesta). El motor sigue teniendo que tolerar un
  duplicado entre la publicación y la confirmación.
- **El contrato cambia** a la versión 2 (`asyncapi-v2.yaml`): `WEBHOOK` lleva notificaciones, con entre 1 y 1000
  mensajes; `MENSAJES`, un mensaje identificado cada uno.
- **El techo del bus lo pone el disco:** cada mensaje cuesta dos escrituras con `fsync` (su notificación en
  `WEBHOOK`, compartida con los demás mensajes de la notificación, y el mensaje identificado en `MENSAJES`), más la
  respuesta en `RESPUESTAS`. En el disco mecánico de midgard son unas 22 escrituras por segundo; el SSD que Jeremi
  tiene para midgard lo sube mucho (se mide al instalarlo).

## Construido (I3 e I4, 2026-10-19)

- **Un cambio respecto del orden de arriba: la ingesta publica el mensaje antes de guardarlo en el historial**, no
  después.
  - Con el orden original, un corte entre los dos pasos dejaba el mensaje en el historial sin haberse publicado. En
    la siguiente entrega, el paso 1 lo saltaba y se perdía.
  - Con este orden, el corte repite la publicación, y Bifröst la descarta por `Nats-Msg-Id` si llega dentro de su
    ventana. El motor ya tenía que tolerar un duplicado.
  - Lo fija `tests/unit/test_ingestion.py`.
  - Lo aprobó Jeremi el 2026-10-19 («De acuerdo con el cambio en el orden que se publique antes de guardar»).
- **Mientras el motor lea Hermod, la ingesta envía también el sobre v1 a la cola `llm`.** Lo hace la ingesta y no el
  webhook, para que el motor no reciba cada mensaje dos veces.
- **Sin bus, el webhook ejecuta la ingesta él mismo**, sobre el evento que habría publicado. Solo ocurre en desarrollo
  y en las pruebas: fuera de desarrollo, el webhook no arranca sin `NATS_URL`.
- **Medido en midgard:** el webhook responde en 8,3 ms de mediana, frente a los 185 ms de I1 con un contacto conocido
  y los 415 ms con uno nuevo. La ingesta trata 2,1 notificaciones por segundo
  ([medición](../../04-testing/medicion-webhook-i1.md)).

## Enmienda del 2026-10-30: el webhook apunta que Meta lo verificó (HIG-75)

Decisión de Jeremi del 2026-10-30. El webhook escribe en la base una sola cosa: el paso de un bot de `REG` a `ACT`
cuando Meta verifica su webhook (`GET /chat/{bot_id}` con el `verifyToken` bueno).

- **Por qué no va por el bus:** una verificación pasa una vez por suscripción y está fuera del camino de los mensajes,
  así que no toca la latencia que motivó esta ADR. Escrita aquí, el bot está en `ACT` en cuanto Meta dice
  «verificado». Por el bus habría hecho falta otro sujeto y otros permisos en Bifröst.
- **Una escritura condicional:** solo desde `REG`. No saca a nadie de `LOK`, `DIS` ni `ERR`: la verificación prueba
  el webhook, no el token (`TenantRepository.move_bot`).
- **Si la escritura falla, Meta recibe igual su `challenge`**, y el aviso queda en el log. La ingesta, que ya escribe
  en la base, también pasa a `ACT` un bot en `REG` con su primera notificación firmada. Eso cubre ese caso y los bots
  que se verificaron antes de esta enmienda.
- **Lo demás no cambia:** el webhook no escribe nada al recibir mensajes.
- La regla entera del estado, con `ERR`, está en `src/controller/bot_state.py`.

## Condiciones de revisión

- La mediana del webhook medida en I1 pasa de 250 ms con Bifröst en el camino. Medida con I4: 8,3 ms.
- Hay que guardar el evento más de 7 días.
