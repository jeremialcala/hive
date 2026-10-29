# PRD — MVP del gateway BaaS de mensajería

* **Estado:** approved (Gate 0 del refactor, 2026-10-18; personas, decisiones de Jeremi del 2026-10-28). Las
  capacidades de la D9, los mensajes salientes (D10), las integraciones (D7, D8) y DPoP, en review desde el 2026-10-29
* **Fecha:** 2026-10-29
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 01-requirements
* **Versión:** 0.8.0
* **Gate:** 0
* **Feature ID:** BAAS-001
* **Nivel ASVS objetivo:** L2

## Problema y contexto

Una empresa que quiere atender por WhatsApp o Messenger con un bot tiene que montar un webhook público, validar a
Meta, guardar credenciales, identificar a quien escribe y conectar todo eso con un modelo de lenguaje. El gateway
ofrece esa parte como servicio (BaaS, Bot as a Service): el cliente crea su bot, lo conecta a su cuenta de Meta y
administra sus datos; la plataforma opera el webhook y entrega cada mensaje a un motor de respuestas.

El servicio existe desde 2024 como un gateway de un solo operador. Este documento es **retroactivo**: recoge lo que
ya está construido y lo que el modelo BaaS y el GDPR exigen y todavía falta. Cada requisito lleva su estado:

- **Construido** — implementado y cubierto por pruebas.
- **Parcial** — existe, pero no cumple el requisito completo.
- **Pendiente** — no existe.

"Construido" no significa probado en producción: hoy todas las pruebas son unitarias y contra dobles.

### Cambios del refactor (2026-10-18)

El gateway pasa a ser una pieza de la topología de ocho workers del [plan](../00-project/plan-topologia-de-workers.md). Las decisiones del 2026-10-18 cambian
los requisitos así:

| Decisión | Qué cambia aquí |
|---|---|
| D1: NATS JetStream en lugar de RabbitMQ | El mensaje va a un bus de eventos con un sujeto por canal; Hermod se retira al final. RS20 |
| D2: la identificación del contacto sale del webhook | El webhook solo comprueba la firma y publica; un worker de ingesta identifica al contacto. RNF01 pasa a tener cifra |
| D3: una credencial por worker | RS21 |
| D4: Instagram | RF13 |
| D6: historial exportable y contexto de 24 horas | RF14, RF15 y RNF05 ([ADR-0017](../00-project/adr/0017-contexto-de-conversacion-e-historial.md)) |
| El webhook y la gestión, por separado (RD-05) | Hecho: [ADR-0018](../00-project/adr/0018-webhook-y-gestion-por-separado.md) |

Se añaden además el orden por conversación (RNF08) y la traza de cada mensaje (RNF09, RS19), y seis escenarios de
abuso (AB17–AB22). Lo demás sigue como se aprobó el 2026-10-12.

### Personas dentro de un cliente (2026-10-28)

Cada cliente deja de ser solo una credencial de máquina: tiene personas que entran al área del cliente. Lo deciden
la [matriz rol × acción](../02-design/matriz-rol-accion.md) (D1–D3, D5) y la [ADR-0029](../00-project/adr/0029-autenticacion-de-personas.md): Zitadel en Nidavellir (enmienda del 2026-10-28), MFA para todas las personas, intercambio de tokens en
`/oauth/token` y un BFF para la sesión. Se añaden RF16–RF18, RS22–RS23 y cinco escenarios de abuso (AB23–AB27).

### Capacidades nuevas de la v1 (D9, 2026-10-29)

La D9 de la [matriz rol × acción](../02-design/matriz-rol-accion.md) metió en la v1 diez capacidades del borrador,
con los permisos que marca la matriz.

| Para quién | Capacidades | Requisitos |
|---|---|---|
| El operador | suspender un cliente, la auditoría de la plataforma, las trazas y los eventos del bus, y el reproceso y el mantenimiento | RF19–RF22 |
| El `cliente-admin` | editar la persona del bot, editar o borrar reglas, rotar el `verifyToken`, las versiones y los guardrails | RF23–RF27 |
| El `cliente-admin` y el `cliente-analista` | calificar respuestas y ver las métricas de cumplimiento | RF28 |

Se añaden también tres requisitos de seguridad (RS24–RS26) y ocho escenarios de abuso (AB28–AB35). Jeremi tomó el
2026-10-29 las cuatro decisiones que los requisitos necesitaban ([Decisiones](#tomadas-el-2026-10-29-para-las-capacidades-de-la-d9-jeremi-alcala)).

### Mensajes a contactos (D10, 2026-10-29)

Con la D10, el cliente escribe por iniciativa propia a los contactos que ya le escribieron, solo por WhatsApp: texto
dentro de la ventana de 24 horas, y plantillas de Meta dentro o fuera de ella ([ADR-0031](../00-project/adr/0031-mensajes-salientes.md)). Se añaden RF29–RF31,
RS27 y siete escenarios de abuso (AB36–AB42), y RS06 se matiza: la API de gestión usa el token del bot para
gestionar las plantillas, sin devolverlo.

### Integraciones con servicios de terceros (D7, D8, 2026-10-29)

Con la D7 y la D8, el agente consulta servicios de terceros y las APIs del propio cliente al responder. Los
servicios de terceros entran por un catálogo MCP del operador ([ADR-0032](../00-project/adr/0032-integraciones-de-terceros-con-mcp.md)). Las APIs del cliente siguen siendo las
llamadas declaradas de los intents. Las dos comparten un almacén de credenciales, y solo el enlace del motor las
llama. Se añaden RF32–RF35, RS28–RS29 y siete escenarios de abuso (AB43–AB49).

## Objetivos / No-objetivos

Objetivos:

1. Un cliente administra sus bots y los datos de sus bots, y nadie más puede hacerlo.
2. Los números de teléfono se guardan cifrados con una clave propia de cada cliente.
3. Ningún mensaje aceptado se pierde, y ninguna notificación falsa se procesa.
4. El tratamiento de datos personales se puede defender frente al GDPR: minimización, retención, y derechos de
   acceso, supresión y portabilidad.
5. El servicio se despliega de forma reproducible, con rollback.

No-objetivos: los del no-scope del [charter](../00-project/charter.md). En particular, generar las respuestas no
es parte del gateway.

## Contexto del sistema (C4 Context)

```mermaid
C4Context
    title Contexto - Gateway BaaS de mensajeria

    Person(cliente, "Cliente", "Empresa duena de sus bots. Responsable del tratamiento")
    Person(operador, "Operador", "Opera la plataforma. Encargado del tratamiento")
    Person_Ext(usuario, "Usuario final", "Escribe al bot por WhatsApp, Messenger o Instagram. Interesado")
    Person_Ext(atacante, "Atacante en internet", "Envia notificaciones falsas y prueba credenciales")

    Enterprise_Boundary(plataforma, "Plataforma (trust boundary: red privada del servidor)") {
        System(gateway, "Gateway BaaS", "Verifica a Meta, identifica contactos, publica cada mensaje y guarda el historial")
        SystemQueue(hermod, "Bus de eventos", "NATS JetStream: un sujeto por canal")
    }

    System_Ext(meta, "Meta", "WhatsApp Business, Messenger e Instagram: Graph API y webhooks")
    System_Ext(motor, "ms-python-metamsghandler", "Motor de respuestas: sus workers consumen el bus, mantienen el contexto y responden")
    SystemDb_Ext(atlas, "MongoDB Atlas", "Bots, contactos, reglas, intents e historial")
    System_Ext(cf, "Cloudflare", "Tunel y TLS del hostname publico")
    System_Ext(cd, "GitHub y receptor", "Build de la imagen y despliegue continuo")

    Rel(usuario, meta, "Escribe al bot", "WhatsApp / Messenger / Instagram")
    Rel(meta, cf, "Entrega notificaciones firmadas", "HTTPS")
    Rel(atacante, cf, "Ataca el webhook", "HTTPS")
    Rel(cf, gateway, "Entrega solo /chat/uuid", "Tunel saliente")
    Rel(cliente, gateway, "Administra sus bots", "API autenticada con JWE")
    Rel(operador, gateway, "Opera y despliega", "Red privada")
    Rel(gateway, atlas, "Lee y escribe", "TLS")
    Rel(gateway, meta, "Consulta perfiles de contacto", "HTTPS, token del cliente")
    Rel(motor, meta, "Envia las respuestas", "HTTPS, token del cliente")
    Rel(gateway, hermod, "Publica cada mensaje", "NATS, con acuse")
    Rel(motor, hermod, "Consume y publica las respuestas enviadas", "NATS")
    Rel(motor, gateway, "Pide la configuracion del bot por bot_id", "API autenticada con JWE")
    Rel(cd, gateway, "Despliega", "Webhook firmado")

    UpdateElementStyle(gateway, $bgColor="#1168bd", $fontColor="#ffffff")
    UpdateElementStyle(motor, $borderColor="#b30000", $fontColor="#b30000")
    UpdateLayoutConfig($c4ShapeInRow="3", $c4BoundaryInRow="1")
```
*Eje estructura · fase 01 · Gate 0, actualizado con el refactor el 2026-10-18. Es el objetivo: hoy el bus es todavía
Hermod (RabbitMQ, cola `llm`), Instagram y el historial no existen, la gestión solo es alcanzable desde la red privada
y el motor de respuestas (en rojo) está en implementación: no está desplegado ni partido en workers, no lee las
reglas ni las herramientas del bot y todavía no envía respuestas.*

## Usuarios y escenarios

### Journey del usuario

```mermaid
journey
    title Un cliente conecta su bot de WhatsApp
    section Alta
      Recibe su credencial de cliente: 2: Cliente, Operador
      Crea el bot con su persona: 4: Cliente
      Anade reglas e intents: 4: Cliente
    section Conexion con Meta
      Registra su numero de WhatsApp: 3: Cliente
      Carga el token y el app secret: 1: Cliente, Operador
      Meta verifica el webhook: 5: Cliente
    section Operacion
      Un usuario escribe al bot: 5: Usuario final
      El mensaje llega a la cola: 5: Gateway
      El usuario recibe respuesta: 1: Usuario final
    section Datos
      Pide borrar los datos de un contacto: 1: Cliente
```
*Eje trazabilidad · fase 01 · Gate 0. Las puntuaciones bajas son los huecos de hoy: no hay credencial por cliente,
los tokens se cargan a mano en la base, el motor clasifica el mensaje pero nadie envía la respuesta, y no existe el borrado de un contacto.*

### Escenarios positivos

| ID | Escenario |
|---|---|
| ES01 | Un cliente crea un bot, le añade reglas e intents y obtiene la URL del webhook y su token de verificación. |
| ES02 | El cliente pega esa URL y ese token en su app de Meta, y Meta verifica el webhook a la primera. |
| ES03 | Un usuario final escribe por WhatsApp; el gateway valida la firma, identifica o crea el contacto, publica el mensaje y responde `200` a Meta. |
| ES04 | El motor de respuestas pide las reglas y las herramientas del bot, genera la respuesta o ejecuta la acción que pide el usuario, y envía la respuesta por el mismo canal. |
| ES05 | El cliente exporta o borra los datos de un contacto que ha ejercido sus derechos. |
| ES06 | RabbitMQ no está disponible; el gateway responde `503`, Meta reintenta y el mensaje llega cuando vuelve. |
| ES07 | Un usuario final escribe por Instagram a la cuenta profesional del cliente; el mensaje sigue el mismo camino que los de los otros dos canales. |
| ES08 | Un usuario escribe dos mensajes seguidos; el bot responde a los dos en orden y recordando lo que se dijo antes, en la misma conversación. |
| ES09 | El cliente exporta la conversación de un contacto con el resto de sus datos. |

### Escenarios negativos / abuso (requerido por Gate 0)

| ID | Escenario | Control | Estado |
|---|---|---|---|
| AB01 | Un tercero envía al webhook una notificación sin firma, o firmada con otro secreto, para inyectar mensajes en la cola | RS03 | Construido |
| AB02 | El cliente A lee o modifica los bots, contactos o tokens del cliente B usando un UUID que conoce o adivina | RS01 | Construido |
| AB03 | Una copia de la base (volcado, respaldo, acceso indebido a Atlas) expone números de teléfono y tokens de Meta | RS05, RS06 | Construido |
| AB04 | Un tercero recorre el hostname público y encuentra `/docs`, `/openapi.json` o los endpoints de gestión | RS09 | Construido sin desplegar: el servicio que se publica no tiene esas rutas (ADR-0018), y la documentación solo se sirve en `development` |
| AB05 | Un bot en internet prueba credenciales de gestión o tokens de verificación | RS02, RS04, RS10 | Parcial |
| AB06 | Alguien reenvía una notificación legítima capturada: la firma de Meta no caduca | RNF04 | Parcial: el sobre lleva el identificador de Meta; falta que el motor deduplique |
| AB07 | Una ráfaga de notificaciones agota el worker o llena la cola | RS10, RNF03 | Pendiente |
| AB08 | Un usuario final escribe instrucciones para que el LLM invoque herramientas del cliente en su contra (prompt injection) | RS16 | Pendiente |
| AB09 | Un cliente define un intent cuya llamada a API apunta a la red interna de la plataforma (SSRF a través del ejecutor de herramientas) | RS16 | Parcial (I7): el gateway no guarda ni entrega destinos que puedan ser la plataforma; falta la comprobación del worker al llamar |
| AB10 | Quien tiene acceso a los logs lee conversaciones, nombres y números | RS07 | Construido |
| AB11 | El broker cae y el gateway responde `200` a un mensaje que nunca se encoló | RNF02 | Construido |
| AB12 | Un cuerpo malformado o enorme provoca un error que revela detalles internos | RS11, RS15 | Construido: `413` por encima de 3 MiB, y ningún error repite lo enviado ni el texto del fallo |
| AB13 | Una dependencia vulnerable o una imagen manipulada llegan a producción | RS12 | Parcial |
| AB14 | Un interesado pide la supresión y sus datos siguen en la cola, los logs o los respaldos | RF09, RNF05 | Parcial: el contacto se borra de la base y la cola caduca a las 24 horas; en los respaldos desaparece al caducar, a los 365 días como mucho (ADR-0014), con la retención aún sin configurar en Atlas; los logs ya no llevan datos personales. Queda lo que guarde el motor |
| AB15 | El motor de respuestas, o quien robe su credencial, obtiene los tokens de Meta de todos los clientes | RS06 | Construido en el gateway: el motor tiene una clave por cliente, que solo alcanza los bots de ese cliente |
| AB16 | Alguien captura un JWE de autenticación válido y lo reenvía, o fabrica uno con la clave pública del gateway | RS02 | Construido |
| AB17 | Meta entrega la misma notificación dos veces (reintenta 7 días en WhatsApp y 36 horas en Instagram) y el bot responde dos veces | RNF04 | Parcial: cada evento lleva el identificador de Meta; falta que alguien deduplique |
| AB18 | Dos mensajes seguidos de la misma persona se procesan en paralelo y se responden en orden inverso, o con el contexto del otro | RNF08 | Pendiente |
| AB19 | Un dato personal viaja en un atributo de una traza, o en un evento del bus sin protección, y lo lee quien tiene acceso al colector o al bus | RS19 | Cubierto en las trazas (I5): lista cerrada de atributos con su forma, sin instrumentación automática y sin excepciones en los spans, comprobado por `tests/unit/test_traces.py`. En el bus, el contenido sigue en claro, como en la cola (RS08) |
| AB20 | Un servicio comprometido publica en un sujeto ajeno, por ejemplo mensajes como si vinieran del webhook, o lee uno que no le toca | RS20 | Pendiente |
| AB21 | La credencial robada de un worker alcanza rutas que ese worker no necesita, por ejemplo los tokens de Meta desde el clasificador | RS21 | Mitigado en la API (I6a): cada clave dice su worker y alcanza solo sus rutas; las claves sin worker siguen, con aviso, hasta que el motor se parta |
| AB22 | La conversación exportada, o el contexto de una conversación, llega a otro cliente o a otra conversación | RS01, RF14 | Pendiente |
| AB23 | Un `cliente-analista` intenta cambiar la configuración de un bot, cargar credenciales, gestionar personas o exportar un contacto | RS23 | Pendiente |
| AB24 | Una persona de un cliente usa su sesión, o una invitación, para entrar en los bots o los datos de otro cliente | RS01, RS23 | Pendiente |
| AB25 | Alguien roba la cookie de sesión del área del cliente o el token de acceso de una persona, por XSS o desde una máquina comprometida | RS22 | Pendiente: el BFF de la ADR-0029 deja los tokens fuera del navegador |
| AB26 | Una persona dada de baja o bloqueada sigue entrando con la sesión que tenía | RS23 | Pendiente: como mucho 10 minutos, hasta el siguiente intercambio (ADR-0029) |
| AB27 | Una cuenta de Zitadel sin persona en Hive, o con la contraseña robada pero sin el segundo factor, intenta conseguir un token | RS22, RS23 | Pendiente |
| AB28 | Un cliente suspendido sigue operando con un token emitido antes de la suspensión, o sus bots siguen respondiendo | RF19, RS23 | Pendiente: como mucho 10 minutos, lo que dura un token |
| AB29 | Alguien con la sesión de un `cliente-admin` vuelve a una versión antigua para recuperar un intent con un destino ya prohibido, o borra reglas para dejar el bot sin límites | RS16, RS25, RS26 | Pendiente: al volver se validan otra vez los destinos, la base de guardrails no cambia y todo queda en la auditoría |
| AB30 | Un `cliente-analista`, o una sesión robada, lee el `verifyToken` o lo rota para cortar la suscripción del bot a Meta | RS23 | Pendiente |
| AB31 | Quien entra en la superficie del operador lee el contenido de los mensajes de un cliente en las trazas, en los eventos del bus o en lo que espera reproceso | RS01, RS19 | Pendiente |
| AB32 | Un reproceso o una purga, por error o con la sesión robada del operador, borra mensajes de varios clientes o hace que un bot responda dos veces | RS24, RNF04 | Pendiente |
| AB33 | Un `cliente-analista` usa la calificación para leer conversaciones de otro cliente, o las lee sin dejar rastro | RS01, RS23, RF28 | Pendiente |
| AB34 | Un `cliente-admin`, o quien robe su sesión, desactiva los guardrails para usar el bot contra sus usuarios o para sacar datos | RS26 | Pendiente: la base de la plataforma no se puede desactivar |
| AB35 | Alguien borra o cambia una entrada de la auditoría, o una versión de un bot, para ocultar lo que hizo | RS25 | Pendiente |
| AB36 | Alguien usa el bot de un cliente, con una sesión o una credencial robadas, para mandar spam o phishing a sus contactos | RS27, RF31 | Pendiente: límites por bot y por cliente, plantillas solo con consentimiento fuera de la ventana, y cada envío en la auditoría |
| AB37 | Una persona de un cliente envía a un contacto de otro cliente, o a un número que no es un contacto | RS01, RF29 | Pendiente: el pedido lleva el identificador del contacto, que se busca dentro del cliente, nunca un número |
| AB38 | Se envía a un contacto que retiró su consentimiento, o a uno que pidió que lo borraran | RF31, RF09 | Pendiente: sin consentimiento vigente, ninguna plantilla sale fuera de la ventana; un contacto borrado ya no existe |
| AB39 | El mismo envío sale varias veces, por un reintento del cliente o una reentrega del bus | RF29, RNF04 | Pendiente: `Idempotency-Key` en la API y el bucket `enviados` del notificador |
| AB40 | Una plantilla lleva contenido que Meta prohíbe, o se envía una que Meta no aprobó o pausó | RF30 | Pendiente: Meta revisa cada plantilla, y Hive solo envía las `APPROVED` |
| AB41 | Un `cliente-analista` envía texto libre, o crea o cambia plantillas | RS23 | Pendiente |
| AB42 | Quien tiene acceso al log o a la auditoría lee el texto que se envió | RS07 | Pendiente: el texto solo va cifrado al historial |
| AB43 | Un servidor del catálogo cambia la descripción de una herramienta para meter instrucciones al modelo, o añade una herramienta nueva | RS29 | Pendiente: solo se ofrece lo aprobado, con su huella |
| AB44 | Lo que responde una herramienta trae instrucciones para que el modelo llame a otra o saque datos | RS26, RS29 | Pendiente: entra como dato no confiable, recortado y por la base de guardrails |
| AB45 | Una inyección de instrucciones hace que el bot cancele un pedido o haga una reserva sin que el usuario lo quiera | RS29, RF35 | Pendiente: sin la confirmación del usuario con un botón, no se ejecuta |
| AB46 | Alguien roba las credenciales o los tokens de OAuth de las integraciones de un cliente | RS28 | Pendiente |
| AB47 | Un cliente usa un `authRef` o una integración para que el enlace llame con la credencial de otro cliente | RS01, RS28 | Pendiente: el almacén es por cliente, y el enlace solo alcanza el del cliente de su clave |
| AB48 | Se piden en OAuth más permisos de los que declara el catálogo, o se secuestra el flujo (CSRF en la redirección, código robado) | RS28, RF33 | Pendiente: los permisos los fija el catálogo, con PKCE y `state` |
| AB49 | Datos personales de la respuesta de una herramienta quedan en el historial, los logs o las trazas | RS07, RS19, RNF05 | Pendiente: solo viven en el contexto de la conversación, 24 horas |
| AB50 | Alguien roba un token de acceso, del notificador o de un cliente, y lo usa desde otra máquina antes de que caduque | RS30 | Construido (HIG-99): sin una prueba firmada con la clave que lo pidió, no sirve. `tests/unit/test_dpop.py` |
| AB51 | Alguien captura una petición con su prueba DPoP y la repite, o usa la prueba en otra ruta, con otro método o con otro token | RS30 | Construido (HIG-99): el `jti` es de un solo uso, y la prueba dice método, URL, hora y hash del token. `tests/unit/test_dpop.py` |

## Requisitos funcionales

| ID | Requisito | Estado | Verificación |
|---|---|---|---|
| RF01 | El operador registra a un cliente, y el registro le da material de clave propio con el que se autentica y con el que se controlan sus bots y sus datos por separado ([ADR-0004](../00-project/adr/0004-firma-en-el-webhook-jwe-en-el-resto.md)) | Construido: el operador da de alta al cliente con su clave pública de firma. La clave de datos por cliente llega con RS05 | — |
| RF02 | Un cliente crea y administra sus bots: persona, reglas, intents y números | Construido: cada cliente administra los suyos con su token | Pruebas unitarias de autenticación |
| RF03 | Meta verifica el webhook de cada bot con su token de verificación | Construido | `tests/unit/test_webhook_security.py` |
| RF04 | El gateway recibe notificaciones de Messenger y de WhatsApp, identifica o crea el contacto y publica el mensaje en la cola `llm` | Construido | Pruebas de los dos canales a través de la aplicación, e integración con un RabbitMQ real. Contra un MongoDB real, solo en local |
| RF05 | Con el `bot_id` del mensaje, el motor de respuestas obtiene del gateway la configuración del bot: system prompt, herramientas y token de Meta ([ADR-0003](../00-project/adr/0003-el-bot-id-es-la-llave-de-la-configuracion.md)) | Parcial: el gateway la entrega al motor en `/rules`, `/tools` y `/tokens`, con la clave que el motor tiene para el cliente dueño del bot ([ADR-0016](../00-project/adr/0016-credencial-del-motor-por-cliente.md)); el motor no la consulta todavía y descarta el `bot_id` | `tests/unit/test_service_credential.py` |
| RF06 | El cliente carga y rota las credenciales de Meta de su bot (tokens y App Secret) por API | Construido: `PUT /chat/{bot_id}/credentials` | — |
| RF07 | El motor de respuestas dispone de lo que necesita para responder al usuario final por su canal: el bot de destino, el destinatario y el token de Meta de ese bot | Parcial: el mensaje lleva el bot, su cliente y el destinatario, y el gateway entrega el token al motor en `/tokens`. El envío en sí es del motor ([ADR-0002](../00-project/adr/0002-el-motor-entrega-las-respuestas.md)) y está en implementación | — |
| RF08 | El cliente consulta los contactos de sus bots | Construido: `GET /chat/{bot_id}/contacts`, por páginas, y `POST /contacts/search` para encontrar a una persona en todos sus bots | `tests/unit/test_export_and_delete.py` |
| RF09 | El cliente exporta y borra los datos de un contacto (GDPR, arts. 15, 17 y 20) | Construido en el gateway: `GET` y `DELETE /chat/{bot_id}/contacts/{contact_id}`. El borrado no alcanza a los respaldos ni a lo que guarde el motor (AB14) | `tests/unit/test_export_and_delete.py` |
| RF10 | La baja de un bot o de un cliente borra todos sus datos (GDPR, art. 28.3.g) | Parcial: `DELETE /bot/{bot_id}` y `DELETE /clients/{client_id}` borran todo lo guardado en la base. En los respaldos, la supresión se cumple al caducar, a los 365 días como mucho ([ADR-0014](../00-project/adr/0014-supresion-en-respaldos-por-caducidad.md)), y un registro de supresiones permite repetirlas tras una restauración; falta configurar y comprobar esa retención en Atlas | `tests/unit/test_export_and_delete.py` |
| RF11 | Fusionar a `main` construye la imagen y la despliega, con rollback si la sonda de vida falla | Parcial: construido, sin ejercitar en el servidor | Prueba de despliegue con la sonda rota a propósito |
| RF12 | Todo mensaje publicado en la cola `llm`, de Messenger o de WhatsApp, lo procesa el motor de respuestas y conserva el bot al que iba dirigido | Parcial: el motor acepta los de WhatsApp, rechaza los de Messenger y descarta el bot | Comprobado el 2026-10-12 pasando al parser del motor los sobres versión 1 que el gateway publica en un RabbitMQ real: lee los de WhatsApp, rechaza los de Messenger y no conserva el bot. El lado del gateway lo fija `tests/contract/test_queue_contract.py` |
| RF13 | El gateway recibe notificaciones de Instagram (`object=instagram`), firmadas igual que las otras, identifica al contacto por su IGSID y publica cada mensaje como los de los otros dos canales (D4). La cuenta profesional se conecta con *Facebook Login* a través de la página del cliente: el bot usa el mismo token de página que para Messenger | Pendiente (I8 del plan) | Pruebas del canal a través del servicio del webhook |
| RF14 | El historial de cada conversación, mensajes entrantes y respuestas enviadas, se guarda cifrado con la clave del cliente, se exporta con el contacto y se borra con él. De un adjunto solo se guarda su tipo, nunca la URL de Meta (D6, [ADR-0017](../00-project/adr/0017-contexto-de-conversacion-e-historial.md)) | Construido: los mensajes entrantes (I4) y las respuestas que Meta aceptó (D6a) se guardan cifrados en el historial, los escribe el worker de ingesta, y se exportan y se borran con el contacto, el bot y el cliente. De un adjunto solo se guarda el tipo | — |
| RF15 | El coordinador del motor obtiene del gateway las 10 últimas vueltas de una conversación cuando su contexto no está, y recibe aviso de cada supresión para borrar el contexto que guarda (D6, [ADR-0017](../00-project/adr/0017-contexto-de-conversacion-e-historial.md)) | Construido del lado del gateway: el motor obtiene las 10 últimas vueltas con `GET /chat/{bot_id}/contacts/{contact_id}/turns` (D6b) y recibe en `SUPRESIONES` un aviso de cada contacto, bot o cliente borrado (D6c). Hasta I6, las vueltas las alcanza cualquier clave del motor, no solo la del coordinador | — |
| RF16 | El `cliente-admin` invita personas a su cliente por su correo, las bloquea, las da de baja y les cambia el rol, dentro de un catálogo fijo de dos: `cliente-admin` y `cliente-analista` (D1, D2 de la [matriz rol × acción](../02-design/matriz-rol-accion.md); [ADR-0029](../00-project/adr/0029-autenticacion-de-personas.md)) | Pendiente | — |
| RF17 | Una persona inicia sesión en el área del cliente con su IdP (Zitadel), siempre con MFA, y lo que alcanza lo decide su rol en Hive, según la matriz rol × acción ([ADR-0029](../00-project/adr/0029-autenticacion-de-personas.md)) | Pendiente | — |
| RF18 | El `cliente-admin` lee la auditoría de su cliente: quién hizo qué y cuándo, sea una persona o la credencial de máquina (D5; completa RS14) | Pendiente | — |
| RF19 | El operador suspende a un cliente y lo reactiva, sin borrar nada (D9). Mientras está suspendido, no consiguen token ni su credencial de máquina, ni sus personas, ni las claves del motor para él, y los tokens que ya tenían dejan de servir al caducar, en 10 minutos como mucho. El webhook de sus bots sigue aceptando y publicando lo que manda Meta, que espera en el bus hasta 7 días sin que nadie lo procese, ni la ingesta ni el motor (ADR-0021). Al reactivarlo, se procesa lo que no haya caducado. Suspender y reactivar llevan un motivo, que queda en la auditoría | Pendiente | — |
| RF20 | El operador lee la auditoría de la plataforma, de todos los clientes: altas, bajas y suspensiones de clientes, claves registradas y revocadas, rechazos de autenticación, entregas al motor de tokens y vueltas, y sus propias acciones de mantenimiento. No lleva datos de los contactos ni contenido de mensajes; de cada cliente, su identificador (D9; completa RS14 junto con RF18) | Pendiente | — |
| RF21 | El operador ve, desde su superficie (ADR-0027), las trazas de un mensaje y los eventos del bus: por qué servicios pasó, cuánto tardó cada paso y en qué sujeto está o dónde se paró. Ve solo los atributos que permite RS19 y los metadatos de cada evento (sujeto, identificadores, hora, intentos), nunca el contenido (D9) | Pendiente: hoy solo se ven en Tempo | — |
| RF22 | El operador ve los mensajes que no se pudieron entregar o procesar (en qué sujeto, de qué cliente, cuántos intentos y por qué fallaron) y los reprocesa. Antes de reprocesar, Hive muestra a cuántos mensajes y clientes afecta, y un mensaje reprocesado no se responde dos veces (RNF04). El mantenimiento destructivo, purgar o borrar mensajes del bus, exige además MFA de los últimos 5 minutos y un motivo (RS24). Cada reproceso y cada purga quedan en la auditoría (D9) | Pendiente | — |
| RF23 | El `cliente-admin` edita la persona de un bot: nombre, rol, tono y descripción, con los mismos límites que al crearlo (RS11). El `cliente-analista` la ve y no la cambia. Cada cambio crea una versión (RF26) | Pendiente: hoy solo se fija al crear el bot | — |
| RF24 | El `cliente-admin` edita o borra una regla de un bot. Cada cambio crea una versión (RF26) | Pendiente: hoy las reglas solo se añaden | — |
| RF25 | El `cliente-admin` rota el `verifyToken` de un bot. Hive genera uno nuevo y aleatorio, y el anterior deja de valer en el momento. Meta solo lo usa al verificar la suscripción: lo ya suscrito sigue recibiendo, y el valor nuevo se pone en la app de Meta la próxima vez que se verifique. Solo lo ve el `cliente-admin` (D6), y la rotación queda en la auditoría | Pendiente | — |
| RF26 | Cada cambio de la configuración de un bot (persona, reglas, intents y guardrails) crea una versión, con quién la hizo y cuándo. El `cliente-admin` vuelve a una versión anterior, y volver crea una versión nueva, así que nada se reescribe. Al volver, los destinos de los intents se validan otra vez (RS16). El `cliente-analista` ve las versiones y en qué se diferencian, pero no vuelve. Las credenciales de Meta y el `verifyToken` no se versionan. Las versiones se borran con el bot (RF10) | Pendiente | — |
| RF27 | Cada bot lleva una base de guardrails de la plataforma que nadie desactiva: la inyección de instrucciones, los intentos de sacar datos de otros contactos o de la plataforma, y el fallo cerrado si el filtro no responde (RS26). Sobre esa base, el `cliente-admin` añade sus propios temas y elige para cada uno qué pasa ante una violación: bloquear con una respuesta fija, o solo registrar. Cada violación queda registrada con el bot, el tema y la acción, sin el texto del mensaje (RS07) | Parcial: el clasificador del motor ya filtra la entrada con un rail fijo y falla cerrado (ADR-0005 del motor); falta la configuración por bot | — |
| RF28 | El `cliente-admin` y el `cliente-analista` califican las respuestas de los bots de su cliente. Para eso leen el historial completo de la conversación del contacto (RF14), sin su número. Cada calificación dice quién la hizo, y cada vez que se abre una conversación para calificar queda en la auditoría del cliente (RF18). Los dos ven las métricas de cumplimiento de cada bot: las respuestas que el evaluador aprobó o rechazó (ADR-0008 del motor), los mensajes bloqueados por los guardrails y las calificaciones de las personas. Las calificaciones se borran con el contacto (RF09) | Pendiente | — |
| RF29 | El `cliente-admin`, o la credencial de máquina, envía a un contacto que ya existe un texto, dentro de la ventana de 24 horas desde su último mensaje, o una plantilla aprobada, dentro o fuera de ella. El `cliente-analista` solo envía plantillas aprobadas. En la v1, solo por WhatsApp, y nunca a un número que no sea un contacto del cliente. Envía el notificador: la API responde `202` con el id del pedido, y la misma `Idempotency-Key` no envía dos veces. Lo enviado queda en el historial, cifrado y con su autor (RF14), y en la auditoría del cliente (D10, [ADR-0031](../00-project/adr/0031-mensajes-salientes.md)) | Parcial: `POST /chat/{bot_id}/contacts/{contact_id}/messages` comprueba y publica en `SALIENTES` (HIG-83), y el notificador envía el texto o la plantilla y lo deja en el historial con su origen y su autor (HIG-84). Falta la copia de las plantillas (HIG-85, HIG-86) | `tests/unit/test_outbound.py` |
| RF30 | El `cliente-admin` crea, edita, borra y lista las plantillas de la cuenta de WhatsApp de un bot por la API de Hive. Hive las gestiona en Meta con el token del bot y guarda una copia de cada una con su estado; el `cliente-analista` las lista. Solo se envía una plantilla aprobada y, en la v1, solo de las categorías `MARKETING` y `UTILITY` (D10, [ADR-0031](../00-project/adr/0031-mensajes-salientes.md)) | Pendiente (HIG-85, HIG-86) | — |
| RF31 | El `cliente-admin`, o la credencial de máquina, registra y retira el consentimiento de un contacto para recibir mensajes del cliente: sus categorías, la fecha y el origen. Una plantilla fuera de la ventana solo sale con el consentimiento vigente para su categoría. El consentimiento se ve en la ficha del contacto y se exporta y se borra con él (RF09) (decisión del 2026-10-29, [ADR-0031](../00-project/adr/0031-mensajes-salientes.md)) | Construido (HIG-83): `PUT` y `DELETE /chat/{bot_id}/contacts/{contact_id}/consent`, y va en la exportación | `tests/unit/test_outbound.py` |
| RF32 | El operador mantiene el catálogo de servidores MCP de la plataforma: remotos, por Streamable HTTP y `https`. De cada servidor guarda la lista cerrada de herramientas que se pueden ofrecer y, de cada herramienta, la huella de su descripción y de su esquema y su clase, lectura o escritura, que decide él y no el servidor. Guarda también cómo se autentica la cuenta del cliente y, con OAuth, los permisos mínimos. Una herramienta nueva o cambiada no se ofrece hasta que la apruebe otra vez (D7, D8, [ADR-0032](../00-project/adr/0032-integraciones-de-terceros-con-mcp.md)) | Pendiente | — |
| RF33 | El `cliente-admin` guarda las credenciales de su cliente para las integraciones: claves fijas, o su cuenta conectada por OAuth desde la PWA, con los permisos del catálogo. Se guardan cifradas con su clave de datos y no vuelven a mostrarse. El `authRef` de un intent apunta a una de ellas por su nombre. Al desconectar una, se revoca en el servicio si este lo admite. Se borran con el cliente, y solo el enlace las obtiene (D8, [ADR-0032](../00-project/adr/0032-integraciones-de-terceros-con-mcp.md)) | Pendiente | — |
| RF34 | El `cliente-admin` activa en cada bot los servidores del catálogo que quiere y qué herramientas de cada uno, además de las llamadas a sus propias APIs que declaran sus intents (RF02). El `cliente-analista` ve las integraciones de cada bot, sin sus credenciales. Cada cambio crea una versión del bot (RF26) (D8, [ADR-0032](../00-project/adr/0032-integraciones-de-terceros-con-mcp.md)) | Pendiente | — |
| RF35 | El enlace del motor hace las llamadas que pide el modelo, a las herramientas activas del bot o a las acciones de sus intents: como mucho tres por mensaje, y con los destinos comprobados al llamar (RS16). Una herramienta de escritura, o una acción con un método distinto de `GET`, solo se ejecuta si el usuario final la confirma con un botón en la conversación antes de 10 minutos. Cada llamada queda en la auditoría del cliente, sin argumentos ni respuesta (decisión del 2026-10-29, [ADR-0032](../00-project/adr/0032-integraciones-de-terceros-con-mcp.md)) | Pendiente (E9 del motor) | — |

## Requisitos no funcionales

| ID | Requisito | Estado |
|---|---|---|
| RNF01 | El webhook responde a Meta con una mediana de 250 ms o menos y menos de un 1 % de las respuestas por encima de 1 s, en los tres canales. Es un objetivo propio: la cifra viene de la guía de 360dialog, un proveedor oficial de WhatsApp, y la documentación actual de Meta no publica ninguna; solo dice que reintenta durante 7 días | Medido en midgard el 2026-10-19 sin pasar por internet. Con el webhook de I1, que identificaba al contacto: 185 ms de mediana con un contacto conocido y 415 ms con uno nuevo ([medición](../04-testing/medicion-webhook-i1.md)). Con I4, que solo publica: 8,3 ms con los dos, y 50–52 ms con 4 notificaciones a la vez. Cumple; falta medirlo en producción, con el túnel delante (Gate 3) |
| RNF02 | Una notificación recibe `200` solo cuando RabbitMQ ha confirmado el mensaje; si no, `503` | Construido. [ADR-0015](../00-project/adr/0015-que-se-responde-a-meta.md) lo amplía: `503` también si no responden la base de datos o el servicio de claves, y `200` con rastro en el log para lo firmado que no se entiende o no se puede procesar |
| RNF03 | Cada servicio del gateway tiene un tope de memoria y de CPU en el servidor | Construido (topes del compose): el webhook, 384 MB y 1 CPU; la gestión, 256 MB y media CPU ([ADR-0018](../00-project/adr/0018-webhook-y-gestion-por-separado.md)) |
| RNF04 | Un mensaje entregado dos veces por Meta, o reenviado por un tercero, no se procesa dos veces, durante todo el plazo en que Meta reintenta: 7 días en WhatsApp, 36 horas en Instagram | Parcial: cada sobre lleva el identificador de Meta (`messageId`), el mismo en cada reentrega; nadie deduplica todavía. La ventana de deduplicación de NATS es de minutos y no basta |
| RNF05 | Cada dato personal tiene un plazo de retención y se borra al vencer, también en cola, bus, logs y respaldos. Máximos de la plataforma, que cada cliente puede acortar: 24 horas para un mensaje en la cola o el bus, salvo lo que llega de Meta y aún no se ha procesado, que espera en el bus 7 días como mucho (ADR-0021), 12 meses sin actividad para un contacto, 12 meses por mensaje para el historial y 24 horas desde el último mensaje para el contexto del coordinador. Los respaldos de la base se conservan 365 días como mucho, igual para todos los clientes (ADR-0014) | Parcial: un mensaje caduca en la cola a las 24 horas; contactos y logs no se borran por plazo, y la retención de los respaldos está decidida pero sin configurar ni comprobar en Atlas. Un contacto se puede borrar a petición (RF09). Historial y contexto no existen ([ADR-0017](../00-project/adr/0017-contexto-de-conversacion-e-historial.md)) |
| RNF06 | El servicio arranca solo tras un apagón del servidor | Pendiente: no hay unidad de arranque para el gateway |
| RNF07 | Disponibilidad del webhook: sin cifra comprometida mientras el servidor no tenga UPS. Se mide desde el primer despliegue | Sin medir |
| RNF08 | Los mensajes de una misma conversación (bot y remitente) se procesan en el orden en que llegaron, también con varios consumidores por etapa: cada evento lleva una clave de orden y el bus la respeta | Pendiente (R3 del plan) |
| RNF09 | Cada mensaje se puede seguir de un servicio a otro: cada evento lleva el contexto de traza W3C (`traceparent`) y cada servicio emite sus spans con OpenTelemetry | Construido en el gateway (I5, [trazas](../03-implementation/trazas.md)): cada servicio emite sus spans y cada evento lleva el `traceparent` del span que lo publica. Probado en midgard: el evento de `WEBHOOK` y el mensaje identificado comparten traza. Falta dónde verlas: no hay colector |

## Trazabilidad de requisitos

```mermaid
requirementDiagram
    requirement RS01 {
      id: RS01
      text: Aislamiento entre clientes
      risk: high
      verifymethod: test
    }
    requirement RS03 {
      id: RS03
      text: Notificaciones autenticadas por firma
      risk: high
      verifymethod: test
    }
    requirement RS05 {
      id: RS05
      text: Numeros cifrados con la clave del cliente
      risk: high
      verifymethod: test
    }
    requirement RS06 {
      id: RS06
      text: Tokens de Meta cifrados y no devueltos
      risk: high
      verifymethod: test
    }
    requirement RNF02 {
      id: RNF02
      text: Sin perdida silenciosa de mensajes
      risk: high
      verifymethod: test
    }
    requirement RF09 {
      id: RF09
      text: Exportar y borrar datos de un contacto
      risk: medium
      verifymethod: test
    }
    requirement RS20 {
      id: RS20
      text: Un usuario por servicio en el bus
      risk: high
      verifymethod: test
    }
    requirement RNF08 {
      id: RNF08
      text: Orden por conversacion
      risk: medium
      verifymethod: test
    }
    element FirmaWebhook {
      type: "dependencia get_signed_webhook_bot"
    }
    element PublicacionConfirmada {
      type: "modulo amqp_broker"
    }
    element PruebasSeguridadWebhook {
      type: "prueba unitaria"
    }
    element AutorizacionPorCliente {
      type: "sin construir"
    }
    element CifradoPorCliente {
      type: "sin construir"
    }
    element DerechosDelInteresado {
      type: "sin construir"
    }
    FirmaWebhook - satisfies -> RS03
    PublicacionConfirmada - satisfies -> RNF02
    PruebasSeguridadWebhook - verifies -> RS03
    PruebasSeguridadWebhook - verifies -> RNF02
    AutorizacionPorCliente - satisfies -> RS01
    CifradoPorCliente - satisfies -> RS05
    CifradoPorCliente - satisfies -> RS06
    DerechosDelInteresado - satisfies -> RF09
    element PermisosPorSujeto {
      type: "sin construir"
    }
    element ClaveDeOrden {
      type: "sin construir"
    }
    PermisosPorSujeto - satisfies -> RS20
    ClaveDeOrden - satisfies -> RNF08
    requirement RS22 {
      id: RS22
      text: MFA para toda persona y sesion fuera del navegador
      risk: high
      verifymethod: test
    }
    requirement RS23 {
      id: RS23
      text: El rol y el cliente salen de Hive
      risk: high
      verifymethod: test
    }
    element IdentidadDePersonas {
      type: "sin construir"
    }
    IdentidadDePersonas - satisfies -> RS22
    IdentidadDePersonas - satisfies -> RS23
    requirement RS24 {
      id: RS24
      text: Lo destructivo del operador con MFA reciente y motivo
      risk: high
      verifymethod: test
    }
    requirement RS25 {
      id: RS25
      text: Auditoria y versiones de solo anadir
      risk: medium
      verifymethod: test
    }
    requirement RS26 {
      id: RS26
      text: Base de guardrails que nadie desactiva
      risk: high
      verifymethod: test
    }
    element AprobacionReforzada {
      type: "sin construir"
    }
    element RegistroDeSoloAnadir {
      type: "sin construir"
    }
    element GuardrailsDePlataforma {
      type: "rail fijo del clasificador, sin configuracion por bot"
    }
    AprobacionReforzada - satisfies -> RS24
    RegistroDeSoloAnadir - satisfies -> RS25
    GuardrailsDePlataforma - satisfies -> RS26
    requirement RS27 {
      id: RS27
      text: Envios a contactos con limite y auditados
      risk: high
      verifymethod: test
    }
    requirement RF31 {
      id: RF31
      text: Consentimiento del contacto fuera de la ventana
      risk: high
      verifymethod: test
    }
    element EnvioPorElNotificador {
      type: "sin construir"
    }
    EnvioPorElNotificador - satisfies -> RS27
    EnvioPorElNotificador - satisfies -> RF31
    requirement RS28 {
      id: RS28
      text: Credenciales de integracion cifradas y solo para el enlace
      risk: high
      verifymethod: test
    }
    requirement RS29 {
      id: RS29
      text: Herramientas aprobadas por huella y efectos confirmados
      risk: high
      verifymethod: test
    }
    element EnlaceDelMotor {
      type: "sin construir"
    }
    EnlaceDelMotor - satisfies -> RS28
    EnlaceDelMotor - satisfies -> RS29
    requirement RS30 {
      id: RS30
      text: Token de acceso atado por DPoP a la clave que lo pidio
      risk: high
      verifymethod: test
    }
    element PruebaDPoP {
      type: "sin construir"
    }
    PruebaDPoP - satisfies -> RS30
```
*Eje trazabilidad · fase 01 · Gate 0. Los requisitos de riesgo alto, RF09 y, desde el refactor, RS20 y RNF08; desde
la D9, RS24–RS26; desde la D10, RS27 y RF31; desde la D7 y la D8, RS28 y RS29; con DPoP, RS30. Los
elementos "sin construir" no tienen todavía ni componente ni prueba que los verifique; tres de ellos (autorización,
cifrado y derechos) se construyeron en el Gate 2 y el diagrama no se ha vuelto a trazar.*

## Requisitos de seguridad (mapeados a OWASP ASVS)

Numeración de ASVS 4.0.3, a nivel de sección; los ítems concretos se fijan en el plan de pruebas del Gate 3.
OWASP Top 10 en su edición 2025.

| ID | Requisito | Estado | ASVS | OWASP Top 10 |
|---|---|---|---|---|
| RS01 | Un cliente solo accede a los elementos de su propio registro: sus claves, sus bots, sus contactos y sus credenciales; todo lo demás se deniega por defecto | Construido | V4.1, V4.2 | A01 |
| RS02 | Todo endpoint que no sea el webhook se autentica con un JWT firmado por el cliente y metido dentro de un JWE ([ADR-0004](../00-project/adr/0004-firma-en-el-webhook-jwe-en-el-resto.md), [ADR-0005](../00-project/adr/0005-jwt-firmado-por-el-cliente-dentro-del-jwe.md)): el gateway verifica la firma con la clave pública registrada del cliente, y el token caduca y no se puede reutilizar. Desde la [ADR-0033](../00-project/adr/0033-dpop-en-los-tokens-de-acceso.md), el token de acceso va atado por DPoP a la clave que lo pidió (RS30) | Construido, también para el motor de respuestas, que se autentica con una clave por cliente ([ADR-0016](../00-project/adr/0016-credencial-del-motor-por-cliente.md)) | V2.10, V3.5, V6.2 | A07 |
| RS03 | Solo se procesan notificaciones con `X-Hub-Signature-256` válida sobre el cuerpo crudo | Construido | V2.10, V9.2 | A08 |
| RS04 | El token de verificación se compara en tiempo constante, y un bot sin token no se puede verificar | Construido | V2.10 | A07 |
| RS05 | El número de teléfono de un contacto y el número de WhatsApp de un bot se guardan cifrados con una clave propia del cliente dueño del bot, y el contacto se puede localizar sin guardar el número en claro | Construido | V6.2, V8.3 | A04 |
| RS06 | Los tokens de Meta y el App Secret de un bot se guardan cifrados; la API de gestión no los devuelve a los clientes, y solo el motor de respuestas obtiene los tokens, pidiéndolos al gateway por `bot_id` con una credencial de servicio propia y dejando registro. Los usan sin devolverlos la ingesta, para el perfil de un contacto de Messenger, y la API de gestión, para las plantillas de WhatsApp ([ADR-0031](../00-project/adr/0031-mensajes-salientes.md)) | Construido en el gateway: cifrados, fuera de la API de gestión, y entregados solo al motor con la clave que tiene para el cliente dueño del bot, dejando una línea en el log cada vez. `tests/unit/test_service_credential.py` | V6.4, V8.3 | A04 |
| RS07 | Ningún log contiene números, nombres, contenido de mensajes ni credenciales, en ningún nivel | Construido: no se escribe en el log nada de lo que envía quien llama ni de lo que Meta envía sobre una persona; de un fallo, su tipo y su línea. El log de accesos del servidor, que escribía el token de verificación, está apagado. `tests/unit/test_logs_and_errors.py` | V7.1 | A09 |
| RS08 | El mensaje viaja y reposa protegido entre el gateway y el motor: broker en red privada, con usuario propio, y contenido cifrado | Parcial: red privada y usuario propio previstos con Hermod; el cifrado JWE existe y está desactivado | V6.2, V9.2 | A04 |
| RS09 | En internet solo se publica `/chat/<uuid>`. La gestión y la documentación de la API se quedan en red privada hasta que existan el registro de clientes y la autenticación con JWE (RS01, RS02); a partir de entonces la gestión se publica | Construido sin desplegar: el webhook y la gestión son dos servicios, y el túnel solo publica el del webhook, sin filtrar por ruta ([ADR-0018](../00-project/adr/0018-webhook-y-gestion-por-separado.md)) | V14.3 | A02 |
| RS10 | El borde limita la tasa de peticiones por origen y por bot | Parcial: `/oauth/token` tiene límite en Cloudflare, por IP, y en `hive-api`, por origen, por fallos y por clave ([ADR-0028](../00-project/adr/0028-limite-de-peticiones-en-oauth-token.md)); el resto de rutas y el webhook, no | V11.1 | A06 |
| RS11 | Toda entrada se valida contra un esquema estricto y con límite de tamaño | Parcial: ninguna petición pasa de 3 MiB, y la notificación tiene que traer su tipo y sus entradas, leídas solo tras comprobar la firma (`tests/unit/test_webhook_answers.py`). Los modelos de gestión siguen rellenando lo que falta con valores de ejemplo y no acotan la longitud de sus campos | V5.1, V13.2 | A05 |
| RS12 | Imagen base por digest, dependencias fijadas y analizadas en cada build, y SBOM publicado | Parcial: imagen por digest, dependencias fijadas con hashes y analizadas en cada build; falta el SBOM | V14.2 | A03 |
| RS13 | Ningún secreto vive en el repo ni en la imagen, y el servicio no arranca sin el par de claves con el que abre los tokens | Construido | V2.10, V14.1 | A02 |
| RS14 | Queda registro de los eventos de seguridad (rechazos, altas, bajas y accesos de gestión), atribuibles a un cliente | Parcial: se registran los rechazos y cada borrado, con quién lo hizo, y los borrados quedan además en el registro de supresiones; no hay auditoría por cliente | V7.2 | A09 |
| RS15 | Un error no revela detalles internos y falla cerrado | Construido: con un fallo forzado en cada ruta de gestión, la respuesta es un `500` genérico; la respuesta a una petición mal formada no repite lo enviado. `tests/unit/test_logs_and_errors.py` | V7.4 | A10 |
| RS16 | El texto del usuario final se trata como entrada no confiable para el LLM, y las llamadas a API de los intents solo pueden ir a destinos permitidos | Parcial: según su documentación, el motor filtra la entrada con guardrails y trata la salida del LLM como no confiable. Los destinos se validan en el gateway al guardar el intent y al entregar las herramientas (I7, [guía](../03-implementation/destinos-de-los-intents.md)); la ejecución de acciones no está construida, y el worker de enlace tendrá que comprobar a qué resuelve cada nombre | V5.2, V12.6 | A05, A06 |
| RS17 | El contenedor corre sin privilegios y solo puede escribir donde necesita | Construido | V14.1 | A02 |
| RS18 | El `bot_id` identifica pero no autoriza: ninguna configuración de un bot se entrega solo por conocerlo | Construido: toda la configuración exige el token del cliente dueño del bot; las únicas rutas que funcionan solo con el `bot_id` son las del webhook, que se autentican con la firma y el token de verificación de Meta | V4.2 | A01 |
| RS19 | Ni las trazas ni los eventos del bus llevan más datos personales que los necesarios: ningún atributo de un span contiene números, texto de mensajes ni nada que mande el llamante, como en los logs (RS07); el contenido de los mensajes en el bus se protege como en la cola (RS08) | Pendiente | V7.1, V8.3 | A09 |
| RS20 | Cada servicio entra al bus con su propio usuario, por TLS, y solo publica y consume en sus sujetos | Pendiente (R8 del plan) | V4.1, V9.2 | A01 |
| RS21 | Cada worker del motor tiene su propia credencial, que dice qué rutas de la API de gestión alcanza: el notificador, los tokens de Meta; el clasificador y el coordinador, reglas y herramientas (D3) | Construido en la API (I6a, [ADR-0022](../00-project/adr/0022-una-credencial-por-worker.md)): una clave por cliente y por worker, con sus rutas. Las seis claves de un cliente las da de alta `ops.engine_keys` (I6b), con la parte privada en Andvari. Pendiente: los usuarios de Bifröst por worker (I6c) | V4.1, V4.2 | A01 |
| RS22 | Toda persona entra con MFA, passkey o TOTP. Hive lo exige también en el intercambio del token, por el `amr`. La sesión del área del cliente vive en un BFF y el navegador solo tiene una cookie `__Host-` `HttpOnly`, `Secure` y `SameSite=Lax`, con protección contra CSRF ([ADR-0029](../00-project/adr/0029-autenticacion-de-personas.md)) | Pendiente | V2.2, V3.4, V3.5 | A07 |
| RS23 | El cliente y el rol de una persona salen de Hive y no del IdP. Una persona sin registro en Hive, bloqueada o de baja no consigue token. Cada acción de una persona queda atribuida a ella ([ADR-0029](../00-project/adr/0029-autenticacion-de-personas.md)) | Pendiente | V4.1, V4.2, V7.1 | A01 |
| RS24 | El mantenimiento destructivo del operador (purgar o borrar mensajes del bus) muestra antes a qué afecta y exige MFA de los últimos 5 minutos y un motivo escrito, que quedan en la auditoría (RF22) | Pendiente | V3.7, V7.2 | A01 |
| RS25 | La auditoría y las versiones de un bot son de solo añadir: ninguna persona ni credencial las edita ni las borra. Solo desaparecen al caducar o con la baja de su bot o su cliente (RF10). La auditoría del operador no lleva datos de los contactos ni contenido de mensajes (RF20) | Pendiente | V7.2, V7.3 | A09 |
| RS26 | La base de guardrails de la plataforma se aplica a todo bot, ningún cliente la desactiva, y falla cerrado: si el filtro no responde, el mensaje se trata como bloqueado (RF27) | Parcial: el rail fijo del clasificador falla cerrado (ADR-0005 del motor); falta que la base y los temas del cliente sean por bot | V5.2, V11.1 | A05, A06 |
| RS27 | Los envíos a contactos tienen límite: 60 por minuto por bot y 1 000 al día por cliente, de texto y de plantilla juntos, que el operador puede subir para un cliente. Pasarlos responde `429` con `Retry-After`. Cada envío queda en la auditoría del cliente, sin su texto (decisión del 2026-10-29, [ADR-0031](../00-project/adr/0031-mensajes-salientes.md)) | Parcial (HIG-83): los dos límites, con sus contadores en el bucket `limites` de Bifröst, y cada envío en el log sin su texto. Falta que el operador pueda subirlos para un cliente | V11.1, V7.2 | A06, A09 |
| RS28 | Las credenciales de integración de un cliente, claves y tokens de OAuth, se guardan cifradas con su clave de datos. No vuelven a nadie salvo al enlace, que las pide por una ruta suya y deja registro. El secreto de cada aplicación OAuth de Hive vive en Andvari ([ADR-0032](../00-project/adr/0032-integraciones-de-terceros-con-mcp.md)) | Pendiente | V6.4, V8.3 | A04 |
| RS29 | Ninguna herramienta llega al modelo sin estar en el catálogo con la misma huella, y su clase la decide el operador, no el servidor. Lo que responde entra como dato no confiable, recortado, y no se guarda fuera del contexto de la conversación. Lo que tiene efectos solo se ejecuta con la confirmación del usuario final ([ADR-0032](../00-project/adr/0032-integraciones-de-terceros-con-mcp.md)) | Pendiente | V4.1, V5.2, V10.3 | A05, A06, A08 |
| RS30 | Todo token de acceso de Hive va atado por DPoP (RFC 9449) a una clave. Para una credencial de máquina, de un cliente o de un worker, es la clave registrada que firmó su aserción; para el BFF, la suya. Cada petición lleva una prueba de un solo uso para su método y su URL, de los últimos 60 segundos, con el hash del token. Un token sin su prueba se rechaza ([ADR-0033](../00-project/adr/0033-dpop-en-los-tokens-de-acceso.md)) | Construido (HIG-99): `src/auth/dpop.py`, en el gateway, el motor y `ops`; `tests/unit/test_dpop.py` | V3.5, V2.10 | A07 |

## Threat assessment inicial

```mermaid
flowchart LR
    U([Usuario final]) -->|mensaje| META([Meta])
    ATK([Atacante]) -->|HTTPS| CF
    META -->|"notificacion firmada, HTTPS"| CF([Cloudflare edge])
    CF -->|"tunel saliente, solo /chat/uuid"| GW
    CLI([Cliente]) -.->|"gestion: hoy sin ruta publica"| GW
    subgraph SRV [Trust boundary: servidor midgard]
      subgraph NET [Red Docker hermod]
        GW[Gateway] -->|"AMQP con confirmacion"| Q[(Hermod: cola llm)]
      end
    end
    GW -->|"TLS: bots, contactos, tokens"| DB[(MongoDB Atlas)]
    GW -->|"HTTPS con el token del cliente"| META
    Q -->|AMQP| MOTOR([ms-python-metamsghandler])
    MOTOR -->|"texto del mensaje"| LLM([LLM: local o gestionado, sin decidir])
    MOTOR -->|"resultado clasificado"| Q2[(cola meta-classified)]
    MOTOR -.->|"configuracion del bot por bot_id: hoy no la pide"| GW
    MOTOR -.->|"respuesta al usuario: en implementacion"| META
```
*Eje comportamiento · DFD inicial · Gate 0. Cinco fronteras: internet ↔ Cloudflare, Cloudflare ↔ servidor (túnel
saliente), servidor ↔ Atlas (los datos personales salen del servidor), gateway ↔ motor de respuestas, que todavía
no está desplegado, y motor ↔ LLM, donde el contenido de los mensajes puede salir a un tercero. Las líneas
discontinuas son flujos que el diseño necesita y que hoy no existen. La respuesta al usuario la envía el motor,
no el gateway ([ADR-0002](../00-project/adr/0002-el-motor-entrega-las-respuestas.md)).*

DFD del objetivo del refactor, con los límites de confianza nuevos:

```mermaid
flowchart LR
    U([Usuario final]) -->|"WhatsApp, Messenger, Instagram"| META([Meta])
    ATK([Atacante]) -->|HTTPS| CF
    META -->|"notificacion firmada"| CF([Cloudflare edge])
    CF -->|"tunel: solo el webhook"| WH
    subgraph SRV [Trust boundary: servidor midgard]
      WH[Webhook] -->|"evento verificado, usuario propio"| BUS[(NATS JetStream)]
      BUS --> ING[Worker de ingesta]
      ING -->|"mensaje identificado, clave de orden"| BUS
      GES[API de gestion]
      KV[(KV contexto, 24 h)]
    end
    ING -->|"TLS: contacto e historial cifrados"| DB[(MongoDB Atlas)]
    GES -->|TLS| DB
    BUS --> MOTOR([Workers del motor])
    MOTOR -->|"contexto cifrado"| KV
    MOTOR -->|"reglas, herramientas, tokens: una credencial por worker"| GES
    MOTOR -->|"texto del mensaje"| LLM([LLM])
    MOTOR -->|"respuesta"| META
    WH -.->|"spans sin datos personales"| OTEL([Colector de trazas])
    MOTOR -.-> OTEL
```
*Eje comportamiento · DFD del refactor · Gate 0, 2026-10-18. Fronteras nuevas: cada servicio ↔ el bus, con su
usuario y sus sujetos (RS20); motor ↔ bucket de contexto (ADR-0017); y los servicios ↔ el colector de trazas (RS19).
El historial sale del servidor hacia Atlas como el resto de los datos personales.*

```mermaid
quadrantChart
    title DREAD inicial - Gateway BaaS
    x-axis Baja probabilidad --> Alta probabilidad
    y-axis Bajo impacto --> Alto impacto
    quadrant-1 Atender ya
    quadrant-2 Monitorear
    quadrant-3 Aceptar
    quadrant-4 Planear
    Acceso cruzado entre clientes: [0.7, 0.95]
    Fuga de numeros y tokens: [0.45, 0.9]
    Gestion expuesta en internet: [0.6, 0.85]
    Datos personales en logs: [0.65, 0.6]
    Prompt injection y SSRF: [0.55, 0.75]
    Notificacion falsa: [0.15, 0.8]
    Reenvio de notificaciones: [0.4, 0.4]
    Inundacion del webhook: [0.55, 0.5]
    Supresion incompleta: [0.5, 0.65]
    Dependencia vulnerable: [0.5, 0.7]
    Mensaje perdido sin aviso: [0.1, 0.6]
    Evento duplicado o fuera de orden: [0.7, 0.45]
    Datos personales en trazas o bus: [0.5, 0.6]
    Sujeto ajeno en el bus: [0.3, 0.7]
    Credencial de worker con rutas de mas: [0.4, 0.75]
```
*Eje trazabilidad · DREAD inicial · Gate 0. "Acceso cruzado" es probable porque el control no existe: basta un
segundo cliente. "Notificación falsa" y "Mensaje perdido" quedan a la izquierda porque sus controles ya están
construidos. El análisis completo, con STRIDE, es entregable del Gate 1. Los cuatro últimos puntos los añade el
refactor: duplicados y desorden son casi seguros con ocho workers asíncronos y reintentos de días.*

## Métricas de éxito

Las del [charter](../00-project/charter.md). Para este MVP, además: los dieciséis escenarios de abuso tienen un
control construido y una prueba que lo verifica.

## Dependencias y riesgos

- **Motor de respuestas.** RF05, RF07, RF12, RNF04 y RS16 dependen de `ms-python-metamsghandler`, un repositorio
  aparte que está en implementación. Tiene construidas la normalización, los guardrails y la clasificación de la
  intención; le falta generar y enviar la respuesta, que es responsabilidad suya ([ADR-0002](../00-project/adr/0002-el-motor-entrega-las-respuestas.md)), y no está
  desplegado. Dónde correrán él y su LLM está propuesto y sin aceptar en sus ADR-0006 y ADR-0007. Si el LLM acaba
  en un servicio gestionado, el contenido de los mensajes sale a un tercero.
- **Meta.** El protocolo del webhook y los términos de uso de WhatsApp Business los fija Meta. Instagram exige
  además verificación del negocio, la app en modo *Live* y acceso avanzado a los permisos de mensajería.
- **NATS JetStream.** Un servicio nuevo en midgard (I0 del plan), con su placement, sus usuarios y su
  retención, que se deciden en el Gate 1.
- **MongoDB Atlas.** Los datos personales viven fuera del servidor; su región y sus respaldos están sin confirmar.
- **Servidor sin UPS.** Limita la disponibilidad que se puede prometer (RNF07).

## Decisiones

### Tomadas al aprobar el Gate 0 (2026-10-12, Jeremi Alcala)

| Decisión | Resultado | Dónde queda |
|---|---|---|
| Quién entrega la respuesta al usuario | El motor de respuestas | [ADR-0002](../00-project/adr/0002-el-motor-entrega-las-respuestas.md), RF07 |
| Cómo obtiene el motor la configuración del bot | Se la pide al gateway con el `bot_id` del mensaje | [ADR-0003](../00-project/adr/0003-el-bot-id-es-la-llave-de-la-configuracion.md), RF05 |
| Autenticación | Firma de Meta solo en el webhook; JWT firmado por el cliente dentro de un JWE en el resto | [ADR-0004](../00-project/adr/0004-firma-en-el-webhook-jwe-en-el-resto.md), [ADR-0005](../00-project/adr/0005-jwt-firmado-por-el-cliente-dentro-del-jwe.md), RS02 |
| Exposición de la gestión | Privada hasta que existan el registro de clientes y el JWE; después, pública | RS09 |
| Alcance de "los números" | El número del contacto y el número del bot | RS05 |
| Nivel ASVS | L2 | Cabecera de este documento |
| Retención | Máximos de plataforma que el cliente puede acortar: 24 horas en la cola y 12 meses sin actividad para un contacto | RNF05 |
| Supresión en respaldos | Se cumple por caducidad: los respaldos de la base se conservan 365 días como mucho | [ADR-0014](../00-project/adr/0014-supresion-en-respaldos-por-caducidad.md), RNF05 |
| SLO del webhook | Sin cifra comprometida; se mide desde el primer despliegue | RNF01, RNF07 |

### Resueltas en el Gate 1 (2026-10-12, Jeremi Alcala)

| Decisión | Resultado | Dónde queda |
|---|---|---|
| Parámetros del token de autenticación | Firma `ES256`, 60 segundos de vida y un solo uso por token | [ADR-0009](../00-project/adr/0009-parametros-del-token-de-autenticacion.md), RS02 |
| Custodia de la clave de cifrado de datos | Una clave por cliente, generada y custodiada por la plataforma, con índice ciego para buscar por número | [ADR-0010](../00-project/adr/0010-clave-de-cifrado-de-datos-por-cliente.md), RS05, RS06 |
| Contrato de la cola | El sobre del gateway, versión 1, para los dos canales; deduplicación en el motor | [ADR-0011](../00-project/adr/0011-contrato-de-la-cola.md), RF12, RNF04 |
| Aislamiento entre clientes | `clientId` en cada documento y capa de repositorio obligatoria | [ADR-0012](../00-project/adr/0012-aislamiento-por-cliente-en-la-capa-de-datos.md), RS01 |

### Tomadas el 2026-10-18 para el refactor (Jeremi Alcala)

| Decisión | Resultado | Dónde queda |
|---|---|---|
| Bus de eventos (D1) | NATS JetStream en lugar de RabbitMQ | RS20; ADR en el Gate 1 |
| Identificación del contacto (D2) | Fuera del webhook, en un worker de ingesta | RNF01; ADR-0006 y ADR-0015 se reescriben en el Gate 1 |
| Credenciales del motor (D3) | Una por worker | RS21; ADR-0016 se amplía en el Gate 1 |
| Instagram (D4) | Entra en el alcance | RF13 |
| Historial y contexto (D6) | El historial se exporta; el contexto son 10 vueltas más un resumen y caduca a las 24 horas | RF14, RF15, RNF05, [ADR-0017](../00-project/adr/0017-contexto-de-conversacion-e-historial.md) (propuesta) |
| Plazo del webhook | 250 ms de mediana y menos de un 1 % por encima de 1 s | RNF01 |
| Cuánto espera en el bus lo que llega de Meta | 7 días, lo que reintenta Meta en WhatsApp | RNF05, ADR-0021 |
| Webhook y gestión | Dos servicios | [ADR-0018](../00-project/adr/0018-webhook-y-gestion-por-separado.md) |

### Tomadas al aprobar el Gate 0 del refactor (2026-10-18, Jeremi Alcala)

| Decisión | Resultado | Dónde queda |
|---|---|---|
| Retención del historial | 12 meses por mensaje como máximo, que cada cliente puede acortar | RNF05, ADR-0017 |
| Adjuntos en el historial | Solo su tipo: la URL de Meta caduca y puede llevar datos del contacto | RF14, ADR-0017 |
| Cómo se conecta Instagram | *Facebook Login* a través de la página del cliente (`graph.facebook.com` y token de página): la misma app y el mismo token que Messenger, sin una credencial nueva | RF13 |

### Tomadas el 2026-10-29 para las capacidades de la D9 (Jeremi Alcala)

| Decisión | Resultado | Dónde queda |
|---|---|---|
| Qué hace el webhook con los bots de un cliente suspendido | Acepta y publica como siempre. El bus lo retiene hasta 7 días sin que nadie lo procese, y al reactivar se procesa lo que no haya caducado | RF19 |
| Qué cuenta como aprobación humana con un solo operador | Ver antes a qué afecta, MFA de los últimos 5 minutos y un motivo escrito | RF22, RS24 |
| Qué ve quien califica una respuesta | La conversación completa del contacto, sin su número, y cada apertura queda en la auditoría del cliente | RF28 |
| Hasta dónde configura el cliente los guardrails | Sobre una base de la plataforma que no se desactiva, añade sus temas, cada uno con su acción: bloquear con una respuesta fija, o solo registrar | RF27, RS26 |

### Tomadas el 2026-10-29 para los mensajes salientes (Jeremi Alcala)

| Decisión | Resultado | Dónde queda |
|---|---|---|
| El consentimiento que Meta exige fuera de la ventana | Lo guarda Hive, por contacto y por categoría, con fecha y origen. Sin él, ninguna plantilla sale fuera de la ventana | RF31, [ADR-0031](../00-project/adr/0031-mensajes-salientes.md) |
| Los límites de Hive, además de los de Meta | 60 envíos por minuto por bot y 1 000 al día por cliente, que el operador puede subir | RS27, [ADR-0031](../00-project/adr/0031-mensajes-salientes.md) |

### Tomadas el 2026-10-29 para las integraciones (Jeremi Alcala)

| Decisión | Resultado | Dónde queda |
|---|---|---|
| Integraciones e intents | Conviven: las APIs del cliente siguen siendo las llamadas de los intents, MCP es solo para el catálogo del operador, y las dos comparten el almacén de credenciales | RF33, RF34, [ADR-0032](../00-project/adr/0032-integraciones-de-terceros-con-mcp.md) |
| Quién autoriza lo que tiene efectos | El usuario final, con un botón en la conversación | RF35, RS29, [ADR-0032](../00-project/adr/0032-integraciones-de-terceros-con-mcp.md) |
| Cómo se conecta la cuenta del cliente a un servicio | Con claves fijas o, si el servicio lo exige, por OAuth, ya en la v1 | RF33, RS28, [ADR-0032](../00-project/adr/0032-integraciones-de-terceros-con-mcp.md) |

### Tomadas el 2026-10-29 para DPoP (Jeremi Alcala)

| Decisión | Resultado | Dónde queda |
|---|---|---|
| A qué clave se ata el token de una credencial de máquina | A la clave registrada que firmó su aserción | RS30, [ADR-0033](../00-project/adr/0033-dpop-en-los-tokens-de-acceso.md) |
| Si el motor también usa DPoP | Sí: el token del notificador alcanza los tokens de Meta | RS30, [ADR-0033](../00-project/adr/0033-dpop-en-los-tokens-de-acceso.md) |
| Cómo se pasa a DPoP | De golpe, como en la ADR-0025 | [ADR-0033](../00-project/adr/0033-dpop-en-los-tokens-de-acceso.md) |

### Por decidir en el diseño (D9)

- **La retención de la auditoría,** de la plataforma y de cada cliente. Propuesta: 12 meses, como un contacto sin
  actividad (RNF05).
- **La retención de las versiones de un bot.** Propuesta: mientras exista el bot.
- **Cómo pide Hive a Zitadel una MFA de los últimos 5 minutos** para RS24: con `max_age` en la petición y
  comprobando `auth_time` y `amr` en el ID token. Va en una ADR.
- **Dónde se guardan las calificaciones y las métricas:** en Hive, o en el evaluador del motor.

### Datos por aportar

No cambian ningún requisito, pero siguen sin respuesta:

- Si se designa un delegado de protección de datos o se confirma que no aplica.
- La región del clúster de MongoDB Atlas, de la que depende si hay transferencia internacional.

### Entregables anteriores al primer cliente externo

- Modelo de contrato de encargo del tratamiento (GDPR, art. 28) y registro de actividades (art. 30).
- Procedimiento de aviso de brechas a los clientes (art. 33.2).
