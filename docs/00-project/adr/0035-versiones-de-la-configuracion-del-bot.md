# ADR-0035: Las versiones de la configuración de un bot, y cómo se entera el motor

* **Estado:** accepted
* **Fecha:** 2026-10-30
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 02-design
* **Versión:** 1.0.0
* **ID:** ADR-0035
* **Supersede:** — (decide la retención que la D9 dejó «por decidir en el diseño»)
* **Controles OWASP afectados:** A01 (el `cliente-analista` no vuelve atrás), A05 y A06 (volver no trae un destino
  que hoy no se permite), A09 (quién hizo cada cambio)
* **Requisitos:** RF26, RS16, RF10 y AB29 del [PRD](../../01-requirements/mvp-baas-gateway.md); HIG-101

## Contexto

Antes de esto, un cambio en la configuración de un bot sobrescribía la anterior y no dejaba rastro de quién lo hizo.
La configuración de un bot es su persona (nombre, rol, tono y descripción), sus reglas y sus intents. Las
credenciales de Meta y el `verifyToken` no forman parte de ella. Editar la persona (RF23), editar y borrar reglas
(RF24) y los guardrails por bot (RF27) dependen de que cada cambio sea una versión (RF26).

Quedaban dos preguntas:

- **La retención.** La D9 proponía conservarlas mientras exista el bot.
- **El caché del motor.** El coordinador recuerda la persona y las reglas de cada bot 5 minutos (`RECORDAR_REGLAS`),
  porque en midgard la lectura de `/rules` era lo más lento de un mensaje después del LLM. Volver a una versión
  tardaría hasta esos 5 minutos en notarse en las respuestas.

## Decisión

Jeremi lo decidió el 2026-10-30.

1. **Una versión por cambio, con la configuración entera.**
   - **Qué guarda cada versión:** la copia de la persona, las reglas y los intents tal como quedan justo después del
     cambio, con quién lo hizo y cuándo, en UTC. Quien lo hizo se escribe como en el log: `person <uuid>` o
     `key <kid>`.
   - **Dónde:** la colección `bot_versions`, con la clave primaria `<botId>:<número>`.
   - **El número:** sale de `configVersion`, un campo del bot que se incrementa en una sola operación. Dos cambios a la
     vez reciben dos números, sin depender de los índices de la base.
   - **Qué cambios la escriben hoy:**
     - crear el bot (versión 1);
     - añadir reglas;
     - crear, editar o borrar un intent;
     - volver a una versión.

     RF23, RF24 y RF27 usarán el mismo `versioned` de `src/controller/bot_versions.py`.
2. **Un bot de antes de las versiones:** en su primer cambio, recibe primero una versión con lo que tenía, escrita por
   `hive` como «before versions». Así ese cambio también se puede deshacer, y no hace falta una migración.
3. **Volver crea una versión nueva; nada se reescribe.**
   - **Revisión:** antes de aplicar la versión, sus intents pasan otra vez la validación de hoy (`IntentRequest`, con
     los destinos de `shared.destinations`). Si uno no pasa, la respuesta es `422` con los nombres de esos intents, y
     no cambia nada (RS16, AB29).
   - **Sin cambios:** volver a la configuración que el bot ya tiene es un `409`.
   - **Sin transacción:** si una vuelta falla a medias, llamar otra vez lo deja bien.
   - **Auditoría:** cada vuelta queda en el log, con quién la hizo; es la auditoría hasta RF18.
4. **Roles** (D9 de la [matriz](../../02-design/matriz-rol-accion.md)):
   - el `cliente-admin` y la credencial de máquina vuelven atrás;
   - el `cliente-analista` ve las versiones y en qué se diferencian, pero no vuelve. Su `403` sale de
     `ANALYST_ROUTES`.
5. **La retención: mientras exista el bot.** Las versiones se borran con él (RF10) y con su cliente, porque están en
   `OF_A_BOT`. Cada una ocupa unos pocos KB.
6. **El motor se entera por Bifröst.**
   - **El aviso:** después de cada cambio, la API de gestión publica `gateway.bot.configured`, con `BotConfigured` de
     `shared/contract.py` y `BotConfigurado` del [AsyncAPI v2](../../02-design/contracts/asyncapi-v2.yaml). Lleva
     el cliente, el bot y el número de la versión. Es NATS sin stream: nadie lo guarda.
   - **Quién lo oye:** el coordinador, que ya está conectado a Bifröst. Al oírlo, olvida lo que recordaba del bot si
     era de una versión anterior.
   - **Por qué lleva el número:** si el aviso llega mientras se lee `/rules`, lo leído es de antes y no se queda.
     `/rules` dice ahora su `version`.
   - **Permisos:** `gestion` publica el sujeto y `coordinator` se suscribe; ningún otro lo alcanza
     (higerotech/bifrost#9).

## Alternativas consideradas

**Cómo se entera el motor:**

| Opción | Pros | Contras |
|---|---|---|
| **Aviso por Bifröst** (elegida) | Inmediato para quien escucha. Sin sujetos de stream ni almacenamiento | Un sujeto y dos permisos nuevos en Bifröst. Quien no escucha en ese momento espera a que caduque su caché |
| Versión en el sobre de cada mensaje | Sin cambios en Bifröst. El motor compara y vuelve a leer | El cambio tarda lo que la ingesta recuerda el bot (30 s), y añade un campo al contrato de los mensajes |
| Bajar el caché a 30 s | Lo más simple | Diez veces más lecturas de `/rules`, y un cambio sigue tardando hasta 30 s |

**Cuánto se guardan las versiones:**

| Opción | Pros | Contras |
|---|---|---|
| **Mientras exista el bot** (elegida) | Toda la historia se puede ver y recuperar. Es lo que proponía la D9 | Crece sin límite con los cambios, aunque cada versión es pequeña |
| Las últimas 50 | Acota lo guardado | Se pierde la historia larga |
| 12 meses, siempre con la última | Igual que un contacto sin actividad (RNF05) | Una tarea más de caducidad, y la historia de un bot estable se pierde |

**Qué guarda una versión:**

| Opción | Pros | Contras |
|---|---|---|
| **Copia entera** (elegida) | Volver es aplicar una copia; comparar dos es directo | Repite lo que no cambió |
| Solo el cambio (diff) | Ocupa menos | Para volver hay que reconstruir desde el principio, y un diff mal aplicado se arrastra |

## Consecuencias

- **El aviso no es una garantía.** Si Bifröst no lo toma, o el coordinador no está escuchando, el cambio llega igual
  cuando caduca el caché, en 5 minutos como mucho. El log de la API lo dice como aviso, y el cambio no se deshace.
- **Otro worker que guarde la configuración** (el evaluador o el enlace, cuando lean `/rules` o `/tools`) tiene que
  suscribirse al mismo sujeto, y Bifröst tiene que darle el permiso.
- **Una versión que no se puede escribir no deshace el cambio.** Si la base falla justo después de aplicarlo, la
  respuesta dice lo que pasó con el cambio y el error queda en el log. La historia tendrá un hueco que la versión
  siguiente cubre.
- **Las versiones repiten lo que el cliente escribió:** nombres, descripciones y destinos de sus intents, como el
  propio intent. No llevan secretos: `authRef` es el nombre de un secreto, nunca su valor.
- **Volver a una versión puede traer de vuelta un intent borrado:** se reactiva con el mismo identificador. Un intent
  creado después de la versión se borra como lo hace `DELETE`: queda deshabilitado.
- **Compatibilidad:** `/rules` lleva un campo más, `version`, y un motor de antes lo ignora. El aviso no toca el
  contrato de los mensajes.
