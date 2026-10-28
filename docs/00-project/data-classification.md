# Clasificación de datos

* **Estado:** approved (Gate 0 del refactor, 2026-10-18)
* **Fecha:** 2026-10-28
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 00-project
* **Versión:** 0.3.0
* **Owner de datos (DPO):** `<TODO: designar o confirmar que no aplica>`
* **Regulación aplicable:** GDPR (Reglamento (UE) 2016/679), adoptado como modelo de diseño

Niveles: Público < Interno < Confidencial < Restringido.

## Roles

| Rol GDPR | Quién | Consecuencia para el diseño |
|---|---|---|
| Responsable del tratamiento (art. 4.7) | Cada cliente, para los datos de los usuarios finales de sus bots | Decide finalidad y base jurídica; el gateway no las decide por él |
| Encargado del tratamiento (art. 4.8) | El operador de la plataforma | Trata solo por instrucción del cliente y con las garantías del art. 28 |
| Interesado | El usuario final que escribe a un bot | Titular de los derechos de los arts. 15 a 22 |
| Subencargados | MongoDB Atlas (base de datos), Cloudflare (túnel y TLS) y, si el motor de respuestas usa inferencia gestionada, el proveedor del LLM | Deben constar en el contrato de encargo con cada cliente |
| Tercero necesario | Meta | Es el canal: recibe y entrega los mensajes bajo sus propios términos con el cliente |

## Inventario

"Protección hoy" describe lo que hace el código el 2026-10-12, no el objetivo.

| Dato | Clasificación | Dónde vive | Protección hoy | Protección requerida | Retención |
|---|---|---|---|---|---|
| Número de teléfono del contacto (`Contact.waId`) | **Restringido** (personal) | MongoDB; mensaje de la cola | Cifrado en MongoDB con la clave de datos del cliente, y localizable por un índice ciego. En claro en el mensaje de la cola. Fuera de los logs | Cifrado con la clave del cliente; localizable sin guardarlo en claro (RS05) | 12 meses sin actividad como máximo; antes si el cliente lo fija o borra el contacto |
| Nombre y foto de perfil del contacto (`firstName`, `lastName`, `profilePic`) | **Confidencial** (personal) | MongoDB; el nombre viaja también en el mensaje de la cola | En claro | Acceso solo del cliente dueño (RS01) | 12 meses sin actividad como máximo; antes si el cliente lo fija o borra el contacto |
| Identificador de Messenger del contacto (`fbId`, PSID) | **Confidencial** (personal) | MongoDB; mensaje de la cola | En claro | Acceso solo del cliente dueño (RS01) | 12 meses sin actividad como máximo; antes si el cliente lo fija o borra el contacto |
| Identificador de Instagram del contacto (IGSID) | **Confidencial** (personal) | Objetivo: MongoDB y el bus, como el PSID (RF13) | No existe todavía | Acceso solo del cliente dueño (RS01) | 12 meses sin actividad como máximo; antes si el cliente lo fija o borra el contacto |
| Contenido de los mensajes | **Restringido** (personal; puede incluir categorías especiales del art. 9) | Hoy: el bus de eventos (stream `WEBHOOK`) y la cola `llm` hasta que se consumen; después, el motor de respuestas y su LLM. Desde I4a el gateway tiene el historial en MongoDB (colección `messages`), que escribe el worker de ingesta desde I4b. Objetivo (ADR-0017): además, el contexto del coordinador en un bucket de NATS | En claro en la cola. Fuera de los logs | Fuera de los logs y de las trazas (RS07, RS19); protegido en la cola y el bus (RS08); cifrado con la clave del cliente en el historial y en el contexto; de un adjunto, solo su tipo | 24 horas en la cola o el bus, salvo lo que llega de Meta sin procesar, 7 días como mucho; 12 meses por mensaje en el historial; 24 horas desde el último mensaje en el contexto |
| Tokens de Meta del cliente (`facebookToken`, `whatsappToken`) y `appSecret` | **Restringido** (credencial) | MongoDB; el motor de respuestas necesita los tokens para enviar | Cifrados con la clave de datos del cliente; se cargan por la API y no se devuelven a quien los cargó. Los tokens solo se entregan al motor, con una clave por cliente y dejando rastro; el `appSecret` no se entrega a nadie | Cifrados en reposo, fuera de la API de gestión y accesibles solo para el motor con credencial de servicio (RS06) | Hasta la baja del bot |
| Token de verificación del bot (`verifyToken`) | **Confidencial** (credencial) | MongoDB | Cifrado con la clave de datos del cliente; comparado en tiempo constante; solo lo lee su cliente | Igual que los tokens (RS06) | Hasta la baja del bot |
| Número del bot (`ChatbotPhone.phoneNumber`, `phoneId`) | **Confidencial** | MongoDB | Cifrados con la clave de datos del cliente | Cifrado con la clave de cifrado de datos del cliente (RS05) | Hasta la baja del bot |
| Persona, reglas e intents del bot | **Confidencial** (del cliente) | MongoDB; el motor de respuestas los pide por `bot_id` | En claro | Acceso solo del cliente dueño y del motor (RS01, RS06) | Hasta la baja del bot |
| Identificador del bot (`bot_id`) | **Interno** | URL del webhook, configuración de Meta del cliente, mensajes de la cola y logs | No es un secreto | No debe bastar para acceder a nada (RS18) | Hasta la baja del bot |
| Clave privada de firma del cliente | **Restringido** | En poder del cliente; nunca llega a la plataforma | No existe todavía | El gateway solo registra la parte pública (ADR-0005) | La decide el cliente |
| Clave de cifrado de datos del cliente | **Restringido** | Registro del cliente, en MongoDB, envuelta por la clave maestra de Andvari | Solo se guarda envuelta; abierta, en la memoria del gateway cinco minutos como máximo. Se borra con la baja del cliente, pero sigue en los respaldos anteriores | Cifra los números de teléfono y las credenciales del cliente (ADR-0010, ADR-0013) | Mientras existan datos cifrados con ella |
| Clave privada de cifrado del gateway | **Restringido** | Fichero en el servidor (`GATEWAY_KEYS_FILE`) | Fuera del repo y de la imagen; legible solo por la cuenta del contenedor | Abre los tokens de autenticación; fuera del repo y de la imagen | Hasta rotación |
| Clave pública de firma de un cliente | **Interno** | Registro del cliente, en MongoDB | En claro: es pública | Con ella se verifica quién envía; su parte privada no sale del cliente | Hasta que el cliente la revoca |
| Credenciales de MongoDB y RabbitMQ | **Restringido** | `config.env` o `.env` del servidor | Fuera del repo y de la imagen | Igual | Hasta rotación |
| Claves privadas por evento (`certs/*.pem`) | **Restringido** | Disco del contenedor | Ignoradas por git y fuera de la imagen. Función desactivada | Decidir en el Gate 1 si se mantienen | Se decide en el Gate 1, junto con la función |
| Registro de eventos (`Event`, `EventAction`) con IP de origen | **Interno** (la IP es dato personal) | MongoDB. Función desactivada | — | Definir antes de reactivarlo | Se decide en el Gate 1, junto con la función |
| Logs de la aplicación | **Confidencial** | Salida del contenedor; Docker conserva 3 ficheros de 10 MB | En `production` solo avisos y errores. En ningún nivel llevan lo que envía quien llama ni lo que Meta envía sobre una persona: rutas, identificadores del servicio y el tipo de cada fallo | Sin datos personales en ningún nivel (RS07) | La rotación de Docker |
| Trazas (spans de OpenTelemetry) | **Interno** | Objetivo: el colector de trazas (RNF09) | No existen | Ni números, ni texto de mensajes, ni nada que mande el llamante en sus atributos (RS19) | `<TODO: fijar con el colector>` |
| Credenciales de cada servicio en el bus (NATS) | **Restringido** | Objetivo: Andvari, una por servicio (RS20) | No existen | Una por servicio, con permisos por sujeto | Hasta su rotación |
| Registro de supresiones | **Interno** | Ficheros en el servidor, fuera de la base (`ERASURES_DIR`) | Identificadores del servicio y fechas, sin nombres ni números; legibles solo por la cuenta del servicio | Tiene que estar en los respaldos del servidor (ADR-0014) | 14 meses |
| Respaldos de la base | **Restringido** | Los gestiona Atlas; `<TODO: confirmar plan y región>` | Sin comprobar | Cifrados, con acceso restringido y con caducidad | 365 días como máximo (ADR-0014); sin configurar ni comprobar en Atlas |
| Correo de una persona del cliente (`ClientUser`) | **Confidencial** (personal) | Objetivo: MongoDB y Zitadel, en midgard ([ADR-0029](adr/0029-autenticacion-de-personas.md)) | No existe todavía | En Hive, cifrado con la clave de datos del cliente y con un índice ciego para buscarlo, como el número de un contacto. En Zitadel, en su PostgreSQL de midgard | Mientras la persona pertenezca al cliente; se borra con su baja definitiva y con el cliente |
| Credenciales de una persona: contraseña, segundo factor y passkeys | **Restringido** (credencial) | Objetivo: Zitadel, en midgard ([ADR-0029](adr/0029-autenticacion-de-personas.md)) | No existen todavía | Solo las guarda Zitadel: Hive nunca las ve | Hasta que la persona las cambie o se dé de baja |
| Sesión del área del cliente: cookie y tokens del BFF | **Restringido** (credencial) | Objetivo: `hive-portal` y una cookie en el navegador ([ADR-0029](adr/0029-autenticacion-de-personas.md)) | No existe todavía | Los tokens solo del lado del servidor; en el navegador, una cookie `__Host-` `HttpOnly` (RS22) | 30 minutos sin uso, 12 horas como máximo |
| Código, documentación e imagen | **Público** | GitHub y GHCR | Sin secretos | Igual | Indefinida |

## Reglas

1. Nada clasificado como Restringido o Confidencial entra en el repositorio ni en la imagen.
2. Un dato personal solo se guarda si hace falta para entregar el mensaje o para que el cliente administre su bot
   (minimización, art. 5.1.c).
3. Ningún dato personal se escribe en los logs, en ningún nivel.
4. Los datos de un cliente son accesibles solo con la credencial de ese cliente. El operador accede por necesidad
   operativa y deja rastro.
5. La baja de un bot o de un cliente borra sus datos, incluidos los de sus contactos (art. 28.3.g).

## Decidido al aprobar el Gate 0 (2026-10-12)

- **Retención.** Máximos de la plataforma que cada cliente puede acortar: 24 horas para un mensaje en la cola y
  12 meses sin actividad para un contacto. El de la cola lo aplica el broker: cada mensaje se publica con una
  caducidad de 24 horas. El de los contactos no está implementado todavía (RNF05): se borran a petición, no por plazo.
- **Alcance de "los números".** El cifrado con la clave del cliente cubre el número del contacto y el número del
  bot (RS05).

## Decidido el 2026-10-13

- **Respaldos.** Los respaldos de la base se conservan 365 días como máximo, y en ellos la supresión se cumple
  por caducidad ([ADR-0014](adr/0014-supresion-en-respaldos-por-caducidad.md)). Lo borrado de la base puede
  seguir en un respaldo hasta 365 días; sumado a los 12 meses sin actividad, los datos de un contacto pueden
  existir hasta 24 meses después de su último mensaje. Este máximo es del clúster entero: un cliente no puede
  acortarlo para sí.

## Datos por aportar

- **Delegado de protección de datos.** Sin designar, y sin confirmar que no aplique.
- **Plan del clúster y política de respaldo.** No consta el plan del clúster de Atlas. Una retención propia solo
  se puede configurar en un clúster `M10` o superior; hay que configurar los 365 días y comprobarlos.
- **Región de la base.** No consta en qué región está el clúster de Atlas; determina si hay transferencia
  internacional (arts. 44 y siguientes). Comprobado el 2026-10-12: los nodos del clúster configurado no están en
  los rangos de direcciones que publican AWS y Google Cloud, lo que apunta a Azure. La región exacta hay que
  leerla en la consola de Atlas.
- **Dónde corre el LLM.** El motor de respuestas envía el texto de los mensajes a un LLM. Si es un servicio
  gestionado fuera de la UE hay otra transferencia internacional. Se decide en el repositorio del motor, pero
  afecta a lo que este servicio le promete al cliente.

## Entregables anteriores al primer cliente externo

- **Contrato de encargo.** Un modelo de contrato del art. 28 con los clientes y un registro de actividades del
  art. 30.
- **Brechas.** Un procedimiento para avisar a los clientes de una violación de seguridad (art. 33.2).
