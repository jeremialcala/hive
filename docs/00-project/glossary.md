# Glosario / Lenguaje ubicuo

* **Estado:** approved (Gate 0 del refactor, 2026-10-18)
* **Fecha:** 2026-10-12
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 00-project
* **Versión:** 0.2.0
* **Contextos acotados:** Clientes y acceso · Gestión de bots · Mensajería entrante · Contactos · Intents y herramientas · Trazabilidad

Entre paréntesis, el nombre que el término tiene en el código cuando existe. "Sin construir" marca los conceptos
del modelo BaaS que todavía no tienen representación en el código.

| Término | Definición | Contexto acotado |
|---|---|---|
| **Hive** | El nombre del producto: el gateway y el motor de respuestas juntos, como los ve el cliente. Su API de gestión está en `https://hive-api.higerotech.com`, que es también la audiencia de los tokens, y el webhook de Meta en `https://hive-webhook.higerotech.com` (ADR-0024). | Clientes y acceso |
| **Cliente** | Empresa que contrata el servicio. Dueña de sus bots y responsable del tratamiento de los datos de sus usuarios finales. Sin construir: hoy los bots no tienen dueño. | Clientes y acceso |
| **Operador** | Quien opera la plataforma. Encargado del tratamiento frente a los clientes. Hoy es el único con credencial de gestión. | Clientes y acceso |
| **Registro del cliente** | El alta de un cliente en la plataforma y lo que cuelga de ella: su clave pública de firma, sus bots y los datos de sus bots. Es lo que permite controlar los elementos de cada cliente por separado (ADR-0004). Sin construir. | Clientes y acceso |
| **Clave de firma del cliente** | Par de claves de un cliente. La parte privada la guarda el cliente y con ella firma sus peticiones; la parte pública consta en su registro y con ella el gateway le identifica (ADR-0005). Sin construir. | Clientes y acceso |
| **Clave de cifrado de datos del cliente** | Clave propia de un cliente con la que se cifran en reposo sus datos sensibles, empezando por los números de teléfono. Es distinta de la clave de firma, y su custodia está sin decidir. Sin construir. | Clientes y acceso |
| **Token de autenticación** | Lo que acompaña a cada petición que no es del webhook: un JWT firmado por el cliente dentro de un JWE cifrado para el gateway. Dura 60 segundos como máximo y vale una vez. Sustituyó a la clave de gestión única (`ADMIN_API_KEY`). | Clientes y acceso |
| **Token de autenticación** | Un JWT firmado por el cliente y metido dentro de un JWE cifrado para el gateway (ADR-0005). Autentica todos los endpoints que no son el webhook. Sin construir. | Clientes y acceso |
| **JWE** | JSON Web Encryption: un contenido cifrado para un destinatario. Envuelve el token de autenticación, y es el formato previsto para cifrar el mensaje de la cola. | Clientes y acceso |
| **Bot** (`ChatBot`) | Un chatbot de un cliente. Tiene una persona (nombre, rol, descripción y tono), sus credenciales de Meta y su configuración. | Gestión de bots |
| **`bot_id`** (`uuid`) | El UUID de un bot. Forma parte de la URL de su webhook, viaja en cada mensaje de la cola y es la llave con la que el motor de respuestas pide la configuración del bot (ADR-0003). Identifica, pero no autoriza: no es un secreto. | Gestión de bots |
| **Persona** | Nombre, rol, descripción y tono con los que el bot se presenta. Encabeza su system prompt. | Gestión de bots |
| **Regla** (`Rule`) | Instrucción en lenguaje natural que se añade al system prompt de un bot. | Gestión de bots |
| **Número del bot** (`ChatbotPhone`) | Número de WhatsApp Business de un cliente asociado a un bot y a un entorno. Lleva el número (`phoneNumber`) y su identificador en Meta (`phoneId`). Debe guardarse cifrado con la clave de cifrado de datos del cliente. | Gestión de bots |
| **Token de verificación** (`verifyToken`) | Secreto por bot que Meta devuelve al verificar el webhook. | Gestión de bots |
| **Token de página** (`facebookToken`) y **token de WhatsApp** (`whatsappToken`) | Credenciales de Meta del cliente con las que el gateway consulta perfiles y envía mensajes en su nombre. | Gestión de bots |
| **App Secret** (`appSecret`, `FACEBOOK_APP_SECRET`) | Secreto de la app de Meta con el que se verifica la firma de cada notificación. | Mensajería entrante |
| **Webhook** | La URL `/chat/{bot_id}` de un bot, la única superficie que se publica en internet. Es también el nombre del servicio que la atiende, aparte de la API de gestión (ADR-0018). | Mensajería entrante |
| **Verificación** | El `GET` con el que Meta comprueba que el webhook es de quien dice ser, usando el token de verificación. | Mensajería entrante |
| **Notificación** (`FacebookRequest`) | El `POST` firmado con el que Meta entrega uno o varios mensajes de un canal. | Mensajería entrante |
| **Canal** | Messenger (`object=page`), WhatsApp Business (`object=whatsapp_business_account`) o Instagram (`object=instagram`). | Mensajería entrante |
| **Mensaje** (`MessageBody`) | El contenido normalizado que sale del gateway: bot, canal, contacto y mensaje original. | Mensajería entrante |
| **Cola `llm`** | Cola durable de RabbitMQ donde el gateway publica cada mensaje. Es la frontera con el motor de respuestas hasta que la sustituya el bus de eventos (D1). | Mensajería entrante |
| **Hermod** | La instancia de RabbitMQ de la plataforma en `midgard`. | Mensajería entrante |
| **Bus de eventos** | NATS JetStream: por donde pasan los mensajes entre el gateway y los workers del motor tras el refactor (D1). Sustituye a Hermod. | Mensajería entrante |
| **Sujeto** | El nombre jerárquico al que se publica un evento en NATS, por ejemplo `webhook.meta.whatsapp`. Cada servicio solo publica y consume en los suyos. | Mensajería entrante |
| **Clave de orden** | Lo que identifica una conversación (bot y remitente) en cada evento, para que sus mensajes se procesen en orden. | Mensajería entrante |
| **Worker de ingesta** | El servicio del gateway que identifica al contacto de cada evento verificado y publica el mensaje para el motor (R1). | Mensajería entrante |
| **API de gestión** | El servicio del gateway que atienden los clientes, el operador y el motor, aparte del webhook (ADR-0018). | Clientes y acceso |
| **Traza** y **span** | El recorrido de un mensaje por los servicios (OpenTelemetry), y cada tramo de él. Viaja como `traceparent` en cada evento. | Trazabilidad |
| **Motor de respuestas** (`ms-python-metamsghandler`) | Servicio aparte, con su propio repositorio, que consume la cola `llm`: normaliza el mensaje, lo filtra con guardrails y clasifica la intención del usuario. Es también quien genera la respuesta o ejecuta la acción que el usuario pide, y quien envía la respuesta al destinatario (ADR-0002); esa parte está en implementación. | Mensajería entrante |
| **Resultado clasificado** | Lo que el motor de respuestas publica en la cola `meta-classified` por cada mensaje: si se permite, su intención y el tipo de respuesta que requiere (texto, acción o adjunto). | Mensajería entrante |
| **Usuario final** | Persona que escribe a un bot. Es el interesado en términos del GDPR. | Contactos |
| **Contacto** (`Contact`) | El registro de un usuario final para un bot: nombre, foto de perfil e identificador en el canal. | Contactos |
| **Número del contacto** (`waId`) | El número de teléfono de WhatsApp de un usuario final. Dato personal que debe guardarse cifrado con la clave del cliente. | Contactos |
| **PSID** (`fbId`) | Identificador de un usuario de Messenger, propio de cada página. | Contactos |
| **IGSID** | Identificador de un usuario de Instagram para la cuenta profesional a la que escribe (*Instagram-scoped ID*). Es su remitente en cada notificación. | Contactos |
| **Historial** | Los mensajes de una conversación, entrantes y respuestas enviadas, guardados cifrados con la clave del cliente y exportables con el contacto (ADR-0017). | Contactos |
| **Contexto de la conversación** y **vuelta** | La memoria de trabajo del coordinador del motor: las últimas 10 vueltas, cada una un mensaje y su respuesta, más un resumen. Caduca a las 24 horas (ADR-0017). | Contactos |
| **Intent** (`Intent`) | Algo que el bot sabe hacer, con sus frases de ejemplo y sus acciones. | Intents y herramientas |
| **Acción** (`Action`) | Una operación concreta de un intent, con sus parámetros y su llamada a una API (`ApiCall`). | Intents y herramientas |
| **Herramienta** | Una acción expresada como definición de herramienta para el LLM. Las entrega `/chat/{bot_id}/tools`. | Intents y herramientas |
| **Evento** (`Event`) y **acción de evento** (`EventAction`) | Registro de una petición y de sus pasos. Implementado y hoy desactivado. | Trazabilidad |
| **Responsable del tratamiento** | Quien decide para qué y cómo se tratan los datos personales (GDPR, art. 4.7). Aquí, el cliente. | Clientes y acceso |
| **Encargado del tratamiento** | Quien trata datos por cuenta del responsable (GDPR, art. 4.8). Aquí, el operador. | Clientes y acceso |
| **Interesado** | La persona a la que se refieren los datos. Aquí, el usuario final. | Contactos |
