# Clasificación de Datos

* **Estado:** review
* **Fecha:** 2026-10-12
* **Decisores:** Jeremi Alcalá · seguridad/legal <TODO: confirmar>
* **Fase AI-DLC:** 00-project
* **Versión:** 0.1.0
* **Owner de datos (DPO):** <TODO: designar>
* **Regulación aplicable:** GDPR (UE) 2016/679 · leyes de protección de datos de los países LatAm de operación <TODO: confirmar países> · políticas de plataforma de Meta

Niveles: Público < Interno < Confidencial < Restringido.

El servicio es un **procesador en tránsito**: el pipeline no persiste nada por sí mismo. Los
datos personales quedan en tres sitios fuera de su memoria: las colas durables de RabbitMQ,
los logs y las peticiones al LLM. La columna «Estado hoy» describe lo que hace el código en
`develop` (4c7bb9e), no lo deseado.

## Inventario

| Dato | Clasificación | Regulación | Cifrado en reposo | Cifrado en tránsito | Retención | Estado hoy |
|---|---|---|---|---|---|---|
| Remitente: `wa_id` (teléfono), PSID, IGSID | Confidencial | GDPR art. 4(1); ley local LatAm | No se persiste en el servicio. En el broker: <TODO: confirmar cifrado de disco> | Requerido TLS | Sin política <TODO: fijar> | AMQP sin TLS. Enmascarado en la auditoría, **en claro en el log del publicador** |
| Texto del mensaje | Confidencial; Restringido si el caso de uso del cliente trata salud o finanzas <TODO: confirmar> | GDPR art. 4(1) y, de forma incidental, art. 9 | Igual que el remitente | Requerido TLS | Sin política <TODO: fijar> | Viaja al LLM por HTTP (por defecto `http://localhost:11434`) y **aparece en claro en el log del publicador** |
| Adjuntos (solo referencia: media-id o URL, tipo, MIME) | Confidencial | GDPR art. 4(1) | No se persiste | Requerido TLS | Sin política | El binario no pasa por el servicio. La URL se propaga sin validar |
| Nombre del contacto y `botId` del sobre custom | Confidencial (nombre) · Interno (`botId`) | GDPR art. 4(1) | No se persiste | Requerido TLS | — | Llegan en `jwe_body` y se descartan: no se usan ni se publican |
| Intención: etiqueta, entidades, razonamiento | Confidencial | GDPR art. 4(1): derivado del texto, las entidades pueden contener PII | No se persiste | Requerido TLS | Sin política | Se publica aguas abajo y aparece en el log del publicador |
| Logs de aplicación y de auditoría | Interno si no contienen PII | GDPR art. 5(1)(c) y (e) | <TODO: definir> | — | Sin rotación ni caducidad | Hoy son **Confidencial**: contienen el resultado completo |
| Credenciales de RabbitMQ y MongoDB | Restringido | — | Gestor de secretos <TODO: elegir> | Requerido TLS | Rotación <TODO: fijar> | En `config.env`, fuera del repo y de la imagen. Credenciales PLAIN sobre AMQP sin TLS |
| Llaves JWK / PEM | Restringido | — | KMS o gestor de secretos | — | Rotación <TODO: fijar> | El módulo JWE no forma parte del pipeline; hoy prevé leer la llave de MongoDB |
| Prompts de sistema y reglas de guardrails | Público | — | No aplica | No aplica | — | Están en un repositorio público |
| Esquemas JSON de configuración (`entity_schema`, `dto_*`) | Interno | — | No aplica | No aplica | — | Heredados del template; el pipeline no los usa |

## Regulación

No es asesoría legal: es el punto de partida para la revisión de quien ejerza de DPO.

- **GDPR.** Los identificadores de remitente y el texto libre son datos personales. Quedan por
  decidir: el rol (lo esperable es que el negocio dueño del bot sea responsable y esta
  plataforma encargada del tratamiento), la base legal, el plazo de conservación de colas y
  logs, y el procedimiento de supresión. La región de despliegue es una decisión de residencia
  de datos, tratada en ADR-0006 y ADR-0007.
- **LatAm.** Aplica la ley de cada país donde haya usuarios finales. Candidatas según el país:
  LGPD (Brasil), Ley 25.326 (Argentina), Ley 1581 de 2012 (Colombia), LFPDPPP (México),
  Ley 19.628 y su reforma (Chile), Ley 29733 (Perú), Ley 18.331 (Uruguay), LOPDP (Ecuador).
  <TODO: confirmar países de operación para fijar la lista>
- **Meta.** Las políticas de WhatsApp Business Platform y Messenger Platform limitan el uso de
  los datos de los usuarios. <TODO: revisar cláusulas sobre envío de contenido a modelos de IA>

## Con un LLM propio en la LAN (ADR-0010, 2026-10-21)

La ADR-0010 sustituye a la ADR-0009, que mandaba el texto a Anthropic: **el texto no sale de la red de Hive.**

| Qué recibe el modelo | Qué no recibe | Dónde corre | Qué guarda |
|---|---|---|---|
| El texto del mensaje, las últimas vueltas de la conversación y la persona y las reglas del bot | El número, el PSID o IGSID, el nombre del contacto, el `clientId` y los tokens de Meta | llama.cpp en el equipo de la RTX 3090, en la LAN de midgard (192.168.10.74) | Nada en disco. La caché de la conversación vive en la memoria de la GPU mientras dura la ranura |

- **No hay encargado del tratamiento externo**, ni transferencia internacional, para la generación de respuestas.
- **El acceso:** solo midgard alcanza el servidor, con su clave, por el cortafuegos de Windows. El tráfico por la LAN
  va sin TLS: es la misma red que el resto de midgard.
- **Lo que fue a Anthropic:** nada. Todas las llamadas del E7 fueron rechazadas antes de procesarse (403).
- **El stream `MOTOR` de Bifröst** (ADR-0008) guarda el texto 24 horas, con TLS y un usuario por worker. Lo cifra
  JetStream (chachapoly); el disco de midgard no está cifrado.
- **El bucket `enviados` de Bifröst** (E6) no guarda datos personales: por cada respuesta enviada, el bot y el SHA-256
  del id de Meta del mensaje que responde, como clave, y el id que Meta dio a la respuesta, con su hora. Caduca a las
  24 horas.
- <TODO: revisar si las políticas de WhatsApp Business Platform y Messenger Platform piden informar al usuario de que
  responde un modelo de IA de un tercero, o limitan enviarle su contenido>

## Decisiones de diseño que ya protegen los datos

- El LLM se sirve en infraestructura propia (Ollama): hoy ningún dato sale a un tercero. **Deja de ser así con la
  ADR-0009** (sección anterior).
- El pipeline no persiste mensajes; el campo `raw` del `MensajeEntrante` no se publica.
- `config.env` nunca se ha commiteado (verificado con `git log --all -- config.env`: 0 commits)
  y `.dockerignore` lo excluye de la imagen.
- NeMo Guardrails arranca con la telemetría anónima desactivada.

## Brechas abiertas

| Brecha | Amenaza | Requisito |
|---|---|---|
| `publisher.py` escribe en el log el JSON completo del resultado (remitente y texto en claro) | T3 | RS-06 |
| AMQP y Ollama sin TLS | T5 | RS-04 |
| Sin política de retención para colas, DLQ y logs; `FileHandler` sin rotación | T3 | RS-12 |
| Sin gestor de secretos definido | T12 | RS-09 |
