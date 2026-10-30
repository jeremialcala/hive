# ADR-0015: A Meta solo se le pide reintento cuando falta algo que el servicio necesita

* **Estado:** accepted
* **Fecha:** 2026-10-13
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 02-design
* **Versión:** 0.1.0
* **ID:** ADR-0015
* **Supersede:** — (amplía [ADR-0006](0006-publicacion-confirmada-y-503.md))
* **Controles OWASP afectados:** A10 (condiciones excepcionales), A05 (validación de la entrada), A08 (integridad de los datos)

Aceptada el 2026-10-13.

> **Su tabla la sustituye la de [ADR-0021](0021-el-webhook-solo-publica.md)**, aceptada el 2026-10-18; lo construido la sigue hasta I4: los fallos de la base al identificar el contacto los reintenta la ingesta, no Meta. Lo demás sigue.

## Contexto

[ADR-0006](0006-publicacion-confirmada-y-503.md) fijó que una notificación recibe `200` solo cuando el broker
confirma el mensaje, y `503` si no. Quedaba sin decidir qué responder en los demás casos, y el código lo resolvía
mal (hallazgo H9 del [threat model](../../02-design/threat-model.md)): cualquier otro fallo acababa en `200` y el
mensaje se perdía, también cuando la base de datos no respondía.

Lo que Meta hace con la respuesta, consultado en su documentación el 2026-10-13:

| Canal | Ante una respuesta que no es `200` | Fuente |
|---|---|---|
| WhatsApp | Reintenta con frecuencia decreciente durante 7 días. Una notificación ocupa 3 MB como máximo | [Webhooks de WhatsApp Cloud API](https://developers.facebook.com/docs/whatsapp/cloud-api/guides/set-up-webhooks) |
| Messenger | Reintenta enseguida unas cuantas veces. A los 15 minutos de fallos avisa a la cuenta de desarrollador; **a la hora, da de baja la suscripción de la página**, y hay que volver a suscribirla. Espera la respuesta en 5 segundos | [Webhooks de Messenger](https://developers.facebook.com/docs/messenger-platform/webhooks) |
| Ambos | Las notificaciones llegan agrupadas, hasta 1000 por envío, y todas van firmadas | [Webhooks de la Graph API](https://developers.facebook.com/docs/graph-api/webhooks/getting-started) |

De ahí salen las dos mitades del problema. Responder `200` a lo que no se pudo procesar pierde el mensaje sin
rastro. Responder otra cosa a algo que va a fallar siempre hace que Meta lo reenvíe una y otra vez y, en
Messenger, acaba desconectando el bot de todos sus usuarios.

## Decisión

**Meta recibe `503` solo cuando falta algo que el servicio necesita y que va a volver. Todo lo demás que venga
firmado recibe `200`, y lo que no se entendió o no se pudo procesar queda en el log.**

| Situación | Respuesta | Qué pasa con el mensaje |
|---|---|---|
| El cuerpo pasa de 3 MiB, en cualquier ruta | `413` | No se lee. Meta no envía nada mayor |
| Sin firma válida | `403` | No se lee nada del cuerpo, ni siquiera para ver si es JSON |
| Bot inexistente | `404` | — |
| Firmado, pero no es JSON o no tiene la forma de una notificación | `200` y aviso en el log | Se descarta |
| Firmado, de un tipo de objeto que el servicio no atiende | `200` y aviso en el log | Se descarta |
| Firmado, con mensajes incompletos entre otros que están bien | `200` y aviso con el recuento | Se publican los que están bien |
| Un mensaje falla por sí mismo al procesarlo | `200` y error en el log | Se descarta ese; los demás de la notificación siguen |
| La Graph API no da el perfil de quien escribe por Messenger | `200` | Se publica sin el nombre, y el contacto se guarda sin perfil |
| No responde la base de datos, el broker o el servicio de claves | `503` | Meta reenvía la notificación entera |

1. **El cuerpo no se lee hasta comprobar la firma.** Antes se interpretaba primero, y quien no tenía el secreto
   podía hacer que el servicio interpretara lo que quisiera.
2. **Una notificación tiene que traer su tipo de objeto y sus entradas.** No se rellena nada de lo que falte. Lo
   que Meta añada sin avisar se ignora: rechazarlo sería desconectarse solo.
3. **Fallo de la base de datos es lo que lanza su driver**, y lo que la capa de documentos hace con ello al
   guardar: que no responda, que rechace las credenciales, que una escritura no entre. Un valor que ya existe no
   lo es.
4. **Con un `503` a mitad de una notificación, lo ya publicado se publica otra vez** cuando Meta la reenvía. El
   motor de respuestas distingue los repetidos por el identificador de Meta ([ADR-0011](0011-contrato-de-la-cola.md)).

**Matiz sobre la Graph API.** La regla aprobada la contaba entre los fallos que piden reintento. No se construyó
así: la Graph API niega un perfil por motivos que no cambian al reintentar, como un permiso que la página no tiene,
y un `503` sostenido daría de baja la suscripción en una hora. El nombre no hace falta para entregar el mensaje, así
que el mensaje sigue sin él. Si lo que falla es la base de datos al leer el token de la página, sí es un `503`.

## Alternativas consideradas

| Opción | Pros | Contras | Riesgo de seguridad |
|---|---|---|---|
| **`503` solo por lo que va a volver; `200` y rastro para lo demás** | No se pierde un mensaje por una caída; un mensaje que falla siempre no bloquea ni desconecta | Un mensaje que falla por un defecto del servicio se pierde, aunque queda en el log | Bajo |
| `200` a todo salvo al broker (lo que había) | Meta nunca reintenta de más | Una caída de la base pierde todos los mensajes que lleguen mientras dura, en silencio | Medio: pérdida de datos sin rastro |
| Cualquier fallo responde `503` | Ningún mensaje se da por entregado sin estarlo | Un solo mensaje que falle siempre se reenvía 7 días en WhatsApp y da de baja la página en Messenger a la hora | Disponibilidad: un mensaje malformado desconecta el bot |
| Rechazar con `4xx` lo que no se entiende | Meta se entera de que algo va mal | Para Meta es un fallo de entrega más: reintenta y desconecta igual | El mismo que el anterior |

## Consecuencias

- Positivas:
  - Una caída de la base de datos ya no pierde mensajes: Meta los reenvía. Cierra H9.
  - Quien no tiene el secreto de la aplicación no consigue que el servicio interprete nada, y nadie consigue que
    lea más de 3 MiB.
  - Un mensaje que no se puede procesar no arrastra a los demás de su notificación.
- Negativas / deuda asumida:
  - **Una caída de más de una hora da de baja la suscripción de Messenger**, igual que ya pasaba con el broker.
    Hay que volver a suscribir la página. Meta avisa a la cuenta de desarrollador; el servicio no avisa de nada.
  - **Un mensaje que falla por un defecto del servicio se pierde.** Queda una línea de error con el tipo de fallo
    y la línea de código, sin el contenido. No hay cola de mensajes fallidos.
  - **Lo que Meta cambie en el formato se descarta con un aviso** hasta que alguien lo vea en el log.
  - Un contacto de Messenger guardado sin perfil se queda sin nombre: no se vuelve a pedir.
  - La base de datos caída hace esperar a Meta lo que tarde el driver en rendirse, que por defecto supera los 5
    segundos que Messenger concede. Para Meta es un fallo igualmente.
- Impacto en threat model: cierra H9; la amenaza T21 (cuerpo malformado o enorme) queda controlada en el servicio.
  La amenaza T16 se amplía de "el broker no está" a "falta algo que el servicio necesita".

## Condiciones de revisión

- Hay vigilancia que avise de una suscripción dada de baja, o de mensajes descartados.
- El motor necesita los mensajes que hoy se descartan: entonces haría falta una cola de fallidos.
- Meta cambia sus plazos de reintento o el tamaño máximo.
