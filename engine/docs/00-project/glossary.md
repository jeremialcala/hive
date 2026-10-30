# Glosario / Lenguaje Ubicuo (DDD)

* **Estado:** review
* **Fecha:** 2026-10-12
* **Decisores:** Jeremi Alcalá
* **Fase AI-DLC:** 00-project
* **Versión:** 0.1.0
* **Contextos acotados:** Ingesta y normalización · Seguridad de IA · Clasificación de intención · Publicación

Los términos en `código` son nombres reales de clases, campos o enums de `src/`. El código
está en español: el glosario fija el término y no introduce sinónimos.

| Término | Definición | Contexto acotado (Bounded Context) |
|---|---|---|
| Webhook | Notificación JSON que Meta envía cuando un usuario escribe. Llega a este servicio ya encolada, no por HTTP. | Ingesta y normalización |
| Origen (`Origen`) | Plataforma de la que proviene el mensaje: `messenger`, `whatsapp`, `instagram` o `desconocido`. Se deduce del campo `object` del webhook. | Ingesta y normalización |
| Sobre custom (`jwe_body`) | Envoltura que emite el receptor aguas arriba para WhatsApp: `{ "jwe_body": { "type", "message": { "messages": [...] } } }`. Pese al nombre, hoy viaja en claro. | Ingesta y normalización |
| Parser (`WebhookParser`) | Puerto que traduce el webhook de una plataforma a un `MensajeEntrante`. Declara si soporta un payload con `soporta`. | Ingesta y normalización |
| Resolver (`WebhookResolver`) | Adaptador que deserializa el payload y delega en el primer parser que lo soporta. Implementa `MessageNormalizer`. | Ingesta y normalización |
| Normalización | Conversión de un webhook específico de plataforma al modelo común `MensajeEntrante`. | Ingesta y normalización |
| Sanitización | Limpieza de la entrada: Unicode NFKC, sin caracteres de control, texto recortado a 4096 caracteres e identificadores limitados a una lista de caracteres permitidos. | Ingesta y normalización |
| Mensaje entrante (`MensajeEntrante`) | Mensaje ya normalizado: `mensaje_id`, `origen`, `remitente`, `texto`, `adjuntos`, `timestamp`. | Ingesta y normalización |
| Remitente | Identificador del usuario en la plataforma: PSID (Messenger), `wa_id` (WhatsApp, es su número de teléfono) o IGSID (Instagram). Dato personal. | Ingesta y normalización |
| Adjunto (`Adjunto`) | Referencia a un archivo recibido: tipo, `url_o_id` (URL en Messenger e Instagram, media-id en WhatsApp) y MIME. El binario nunca pasa por el servicio. | Ingesta y normalización |
| Guardrail / rail de entrada | Control que evalúa el texto antes de que llegue al clasificador y lo bloquea si es jailbreak, manipulación del prompt o está fuera de alcance. | Seguridad de IA |
| Política de guardrails (`GuardrailsPolicy`) | Puerto que emite un veredicto sobre un texto. Lo implementa `NeMoGuardrailsPolicy`. | Seguridad de IA |
| Veredicto (`ResultadoGuardrail`) | Resultado de la política: `permitido` y, si no, `motivo`. | Seguridad de IA |
| Sentinel de bloqueo | Cadena `GUARDRAIL_BLOQUEADO` que el flujo Colang emite cuando el rail rechaza la entrada; es lo que detecta el adaptador. | Seguridad de IA |
| Fail-closed | Ante un fallo de la capa de seguridad, la entrada se bloquea en lugar de dejarla pasar. | Seguridad de IA |
| Intención (`Intencion`) | Lo que el usuario quiere, según el modelo: `etiqueta`, `tipo_respuesta`, `confianza`, `entidades`, `razonamiento`. | Clasificación de intención |
| Etiqueta | Nombre corto de la intención en snake_case (por ejemplo `consulta_stock`). Lo genera el LLM: es texto libre. | Clasificación de intención |
| Tipo de respuesta (`TipoRespuesta`) | Estrategia de salida: `texto` (respuesta directa), `action` (requiere lógica de negocio externa) o `adjunto` (recepción de archivos). | Clasificación de intención |
| Confianza | Certeza del modelo en [0, 1]. Por debajo de 0.3 se descarta la clasificación. | Clasificación de intención |
| Fallback seguro | Intención por defecto (`desconocida`, `texto`, confianza 0) cuando el modelo falla, responde fuera de contrato o con baja confianza. | Clasificación de intención |
| Motor de intención (`IntentEngine`) | Puerto que detecta la intención de un `MensajeEntrante`. Lo implementa `OllamaIntentEngine`. | Clasificación de intención |
| Resultado clasificado (`ResultadoClasificado`) | Salida del pipeline que se publica aguas abajo: identificadores, `tipo_respuesta`, `permitido`, `motivo_bloqueo`, `intencion` y `payload`. | Publicación |
| Cola de entrada | Cola de RabbitMQ con los webhooks crudos (`queue_name`). | Publicación |
| Cola de salida | Cola de RabbitMQ donde se publica el resultado (`output_queue_name`, por defecto `meta-classified`). | Publicación |
| ACK / NACK | Confirmación o rechazo de un mensaje AMQP. El servicio rechaza sin reencolar. | Publicación |
| DLQ | Cola de mensajes muertos a la que el broker enruta los rechazados. Hoy no está configurada. | Publicación |
| Puerto | Interfaz del dominio de la que depende la aplicación (`src/domain/ports`). | Transversal |
| Adaptador | Implementación de un puerto en infraestructura (`src/infrastructure`). | Transversal |
| Caso de uso (`ProcesarMensajeUseCase`) | Orquestador del pipeline: normaliza, evalúa guardrails, detecta intención y publica. | Transversal |
| Auditoría | Traza por etapa (`audit: ...`) con el remitente enmascarado a sus últimos 4 caracteres. | Transversal |
