# ADR-0011: Leído y «escribiendo…» en WhatsApp, desde el notificador

* **Estado:** accepted
* **Fecha:** 2026-10-21
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 02-design
* **Versión:** 0.1.0
* **ID:** ADR-0011
* **Supersede:** —
* **OWASP:** A01 (solo el notificador tiene el token de Meta del bot) · LLM10 (nada se reintenta: un extra no debe
  multiplicar las llamadas)

Decisiones de Jeremi del 2026-10-21.

## Contexto

Con Hive respondiendo de verdad (E7), la persona escribe y no ve nada hasta que llega la respuesta, unos 9 segundos
después (mediana medida en midgard). WhatsApp tiene dos señales para eso, y la Cloud API las da con una sola llamada
(developers.facebook.com, «Typing indicators» y «Mark messages as read», consultadas el 2026-10-21):

```json
POST /{phone_number_id}/messages
{"messaging_product": "whatsapp", "status": "read", "message_id": "<wamid>",
 "typing_indicator": {"type": "text"}}
```

- `status: read` pone en azul los ticks de ese mensaje y los de los anteriores de la conversación.
- `typing_indicator` muestra «escribiendo…» hasta que se responde, o 25 segundos. Meta pide mostrarlo solo si de verdad
  se va a responder.

## Decisión

1. **Lo hace el notificador**, el único worker con el token de Meta del bot y salida a `graph.facebook.com`. Ni el
   clasificador ni la ingesta del gateway lo necesitan para esto.
2. **Leído al llegar, «escribiendo…» después:**
   - el consumidor **`notifier-leido`**, sobre `MENSAJES`, marca como leído cada mensaje de WhatsApp en cuanto entra;
   - el consumidor **`notifier-escribiendo`**, sobre `motor.clasificado.>`, pide «escribiendo…» cuando el clasificador
     deja seguir el mensaje. Un mensaje que no sigue recibe enseguida su texto fijo y no lo necesita.
3. **Los atiende el mismo proceso**, a la vez que su consumidor de las respuestas: el bucle común (`runtime.ejecutar`)
   acepta otros consumidores. Comparten el cliente del gateway, así que el token de cada bot se pide una vez para
   todos. Si un bucle se cae, se cae el worker y el contenedor se reinicia.
4. **Es un extra, sin reintentos.** Sus consumidores tienen `max_deliver` 1 y `deliver_policy` `new`: al crearlos no
   marcan lo que ya había. Si Meta, el gateway o el bucket no responden, queda en el log y el mensaje se confirma.
   Un token rechazado se pide otra vez al gateway, una vez, como al enviar.
5. **«Escribiendo…» no se pide si ya no sirve:**
   - si el mensaje llegó hace más de 20 segundos: la respuesta ya estaría al caer o el indicador se apagaría solo;
   - si su respuesta ya salió (bucket `enviados`), o no se puede saber: se quedaría encendido después de ella.
6. **Solo WhatsApp.** Messenger tiene `mark_seen` y `typing_on`; llegan con el canal, en el E10.

## Alternativas consideradas

| Opción | Pros | Contras |
|---|---|---|
| **El notificador, leído al llegar y «escribiendo…» al seguir** | Todo lo que va a Meta sale de un worker; «escribiendo…» solo cuando habrá respuesta del modelo | Dos consumidores más y el bucle común con varios |
| La ingesta del gateway, las dos cosas al llegar | Lo más pronto (~0,5 s); ya tiene el token | La ingesta pasa a llamar a Meta; «escribiendo…» también para lo que no sigue |
| El clasificador, al terminar | Sin consumidores nuevos | Volvería a necesitar Andvari y la ruta de los tokens |

## Consecuencias

- **Bifröst:** dos consumidores nuevos y sus permisos para el usuario `notifier` (repositorio `bifrost`).
- **Llamadas a Meta:** dos más por mensaje que sigue, una por el que no. No tienen coste propio según la documentación
  de Meta consultada, que tampoco publica un límite aparte para ellas.
- **La persona sabe que se leyó.** Ya lo sabía al recibir la respuesta; ahora, unos segundos antes.
- Las trazas tienen dos spans más: `engine.notifier.read` y `engine.notifier.typing`.

## Condiciones de revisión

- La mediana de respuesta pasa de 20 segundos: el indicador se apagaría antes. Entonces, renovarlo mientras se redacta.
- Meta cobra o limita estas llamadas.
