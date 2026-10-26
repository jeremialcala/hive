# Matriz rol × acción de Hive

* **Estado:** draft
* **Fecha:** 2026-10-26
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 02-design
* **Versión:** 0.1.0
* **Gate:** 1
* **ADRs relacionadas:** [ADR-0012](../00-project/adr/0012-aislamiento-por-cliente-en-la-capa-de-datos.md) (aislamiento por cliente), [ADR-0016](../00-project/adr/0016-credencial-del-motor-por-cliente.md) (credencial del motor por cliente), [ADR-0022](../00-project/adr/0022-una-credencial-por-worker.md) (una credencial por worker), [ADR-0025](../00-project/adr/0025-oauth2-client-credentials-con-private-key-jwt.md) (OAuth 2.0), [ADR-0027](../00-project/adr/0027-el-mapa-de-estilo-de-hive.md) (las superficies)
* **Requisitos origen:** [PRD del MVP](../01-requirements/mvp-baas-gateway.md): RF01, RF02, RF05, RF06, RF08–RF10, RF15; RS01, RS06, RS14, RS18
* **Contrato:** [`contracts/openapi.yaml`](contracts/openapi.yaml), generado de la aplicación

## Propósito

Reunir en una tabla qué puede hacer cada rol sobre cada recurso de `hive-api`, separando lo que el código ya
autoriza de lo que se propone. Hasta hoy la autorización estaba repartida entre tres dependencias de
[`src/app.py`](../../src/app.py) (`require_operator`, `require_service` y el repositorio del cliente), la lista
cerrada `WORKER_ROUTES` y el texto de las ADR.

Parte de un borrador de Jeremi del 2026-10-26 que traía capacidades que Hive todavía no tiene: usuarios humanos
dentro de un cliente, sesión con un IdP, guardrails, versiones de un bot y procesos MCP. Por decisión suya del mismo
día, la matriz se escribe sobre el Hive real, y lo nuevo del borrador entra como propuesto, con su decisión pendiente.

## Roles

| Rol | En el código | Quién es hoy | Estado |
|---|---|---|---|
| `operador` | `Role.OPERATOR` | Quien opera la plataforma: registra clientes y da paso al motor. También es un cliente, con sus propios bots. | Construido |
| `cliente-admin` | `Role.CLIENT` | El dueño de un cliente. Hoy es una identidad de máquina: una clave por cliente, que obtiene sus tokens por OAuth 2.0 `client_credentials` (ADR-0025). | Construido, como `cliente` |
| `cliente-analista` | — | Un usuario del cliente con permisos acotados: lee y califica, pero no configura. Es el «operador» del borrador, renombrado para que no choque con `Role.OPERATOR`. | **Propuesto** |
| `motor` | `Role.SERVICE` + `Worker` | Los workers del motor, cada uno con una clave por cliente (ADR-0016, ADR-0022): clasificador, coordinador, enlace, de respuesta, evaluador y notificador. | Construido |

Meta no es un rol: llama al webhook, que se autentica por bot con el `verifyToken` y la firma `X-Hub-Signature-256`
(ADR-0007), fuera de esta matriz.

### Principios que ya aplica el código

- **Denegado por defecto.** Toda ruta de `hive-api`, salvo `/health` y `/oauth/token`, pide un token de acceso. Una
  ruta del motor que no esté en `WORKER_ROUTES` está cerrada para todos los workers, también las que se añadan
  después hasta que se escriban allí.
- **El cliente sale del token y se aplica en la capa de datos** ([ADR-0012](../00-project/adr/0012-aislamiento-por-cliente-en-la-capa-de-datos.md)).
  `TenantRepository` añade el cliente a cada consulta: lo de otro cliente responde `404`, igual que lo que no existe
  (RS01).
- **El `bot_id` identifica, pero no autoriza** (RS18). Conocer un `uuid` no da nada.
- **El operador no entra en los datos de un cliente.** En las rutas de los bots es un cliente más y solo alcanza los
  suyos. Lo único que hace sobre otro cliente es darlo de alta, gestionar sus claves y darlo de baja, y la baja borra
  todo lo suyo.
- **Los secretos de Meta no vuelven** (RS06). Ningún cliente los recupera; solo el notificador del motor los obtiene,
  y cada entrega queda en el log.
- **Registro:** quedan en el log los rechazos, el alta y la baja de cada clave y las entregas al motor de tokens y
  vueltas. No hay auditoría consultable por cliente (RS14, parcial).

## Leyenda

| Símbolo | Significado |
|---|---|
| ✅ | Permitido y **construido**: lo aplica el código y lo cubren las pruebas |
| ⛔ | Denegado y **construido** (`403`, o `404` si el recurso es de otro cliente) |
| 🟡 ✅ / 🟡 ⛔ | **Propuesto**, pendiente de decisión. El símbolo que sigue es el valor sugerido |
| — | No aplica a ese rol |

Columna **Contrato**: ✅ la ruta está en `openapi.yaml` · ⚠️ hay requisito en el PRD, pero no ruta · ❌ no hay
requisito: viene del borrador o del mapa de estilo (ADR-0027).

Lo que hoy puede `cliente-admin` lo puede cualquier credencial de cliente, porque hoy solo existe ese tipo. La columna
`cliente-analista` es entera una propuesta (D1).

## Matriz

### 1. Autenticación y claves propias

| Acción | operador | cliente-admin | cliente-analista | motor | Fuente | Contrato |
|---|---|---|---|---|---|---|
| Pedir un token de acceso con una aserción firmada | ✅ | ✅ | 🟡 — (D1: inicia sesión de otro modo) | ✅ | ADR-0025 | ✅ `POST /oauth/token` |
| Leer la audiencia y las claves públicas del gateway | ✅ | ✅ | 🟡 — | ✅ todos los workers | ADR-0025 | ✅ `GET /gateway/keys` |
| Ver sus claves de firma | ✅ | ✅ | 🟡 ⛔ | ⛔ | RF01 | ✅ `GET /keys` |
| Añadir una clave propia (con prueba de posesión) | ✅ | ✅ | 🟡 ⛔ | ⛔ | RF01 | ✅ `POST /keys` |
| Revocar una clave propia (nunca la última) | ✅ | ✅ | 🟡 ⛔ | ⛔ | RF01 | ✅ `DELETE /keys/{kid}` |
| Iniciar y cerrar sesión una persona (IdP, MFA) | 🟡 ✅ | 🟡 ✅ | 🟡 ✅ | — | Borrador (D1) | ❌ |

### 2. Registro de clientes y paso al motor

| Acción | operador | cliente-admin | cliente-analista | motor | Fuente | Contrato |
|---|---|---|---|---|---|---|
| Registrar un cliente con su primera clave | ✅ | ⛔ | 🟡 ⛔ | ⛔ | RF01 | ✅ `POST /clients` |
| Listar los clientes y sus claves | ✅ | ⛔ | 🟡 ⛔ | ⛔ | RF01 | ✅ `GET /clients` |
| Dar de baja a un cliente, con todo lo suyo | ✅ | ⛔ | 🟡 ⛔ | ⛔ | RF10 | ✅ `DELETE /clients/{client_id}` |
| Registrar o revocar una clave de un cliente (también la última) | ✅ | ⛔ | 🟡 ⛔ | ⛔ | RF01 | ✅ `POST /clients/{client_id}/keys`, `DELETE …/keys/{kid}` |
| Registrar o quitar la clave de un worker para un cliente | ✅ | ⛔ | 🟡 ⛔ | ⛔ | ADR-0016, ADR-0022 | ✅ `POST /clients/{client_id}/service-keys`, `DELETE …/{kid}` |
| Suspender un cliente sin borrarlo | 🟡 ✅ | ⛔ | 🟡 ⛔ | — | Borrador | ❌ |

### 3. Bots

En esta sección y las siguientes, la columna `operador` dice lo que puede sobre los bots **de otro cliente**: sobre
los suyos tiene lo mismo que `cliente-admin`.

| Acción | operador | cliente-admin | cliente-analista | motor | Fuente | Contrato |
|---|---|---|---|---|---|---|
| Crear un bot | ⛔ | ✅ | 🟡 ⛔ | ⛔ | RF02 | ✅ `POST /bot` |
| Ver la ficha de un bot (sin secretos, con el `verifyToken`) | ⛔ `404` | ✅ | 🟡 ✅ sin el `verifyToken` (D6) | ⛔ | RF02, RF03 | ✅ `GET /chat/{bot_id}/info` |
| Borrar un bot con todo lo suyo | ⛔ `404` | ✅ | 🟡 ⛔ | ⛔ | RF10 | ✅ `DELETE /bot/{bot_id}` |
| Registrar un número de WhatsApp | ⛔ `404` | ✅ | 🟡 ⛔ | ⛔ | RF02 | ✅ `POST /chat/{bot_id}/phones` |
| Ver los números del bot | ⛔ `404` | ✅ | 🟡 ✅ | ⛔ | RF02 | ✅ `GET /chat/{bot_id}/phones` |
| Editar la persona del bot (nombre, rol, tono, descripción) | ⛔ | 🟡 ✅ | 🟡 ⛔ | ⛔ | RF02 | ⚠️ |
| Ver el estado del bot (REG, ACT, ERR) | ⛔ | 🟡 ✅ | 🟡 ✅ | ⛔ | ADR-0027 (HIG-74) | ❌ |
| Publicar o despublicar un bot | ⛔ | 🟡 ✅ | 🟡 ⛔ | ⛔ | Borrador, ADR-0027 (HIG-75) | ❌ |
| Ver el historial de versiones y volver a una | ⛔ | 🟡 ✅ | 🟡 ✅ ver / ⛔ volver | ⛔ | Borrador | ❌ |

### 4. Reglas, intents y herramientas

| Acción | operador | cliente-admin | cliente-analista | motor | Fuente | Contrato |
|---|---|---|---|---|---|---|
| Ver la persona y las reglas como datos | ⛔ `404` | ✅ | 🟡 ✅ | ✅ clasificador, coordinador, de respuesta, evaluador | RF05 | ✅ `GET /chat/{bot_id}/rules` |
| Añadir reglas | ⛔ `404` | ✅ | 🟡 ⛔ | ⛔ | RF02 | ✅ `POST /chat/{bot_id}/rules` |
| Editar o borrar una regla | ⛔ | 🟡 ✅ | 🟡 ⛔ | ⛔ | RF02 | ⚠️ |
| Ver los intents | ⛔ `404` | ✅ | 🟡 ✅ | ⛔ | RF02 | ✅ `GET /chat/{bot_id}/intents`, `…/{intent_id}` |
| Crear, editar o borrar un intent | ⛔ `404` | ✅ | 🟡 ⛔ | ⛔ | RF02 | ✅ `POST`, `PUT`, `DELETE /chat/{bot_id}/intents…` |
| Ver las herramientas del bot | ⛔ `404` | ✅ | 🟡 ✅ | ✅ coordinador, enlace | RF05 | ✅ `GET /chat/{bot_id}/tools` |

### 5. Credenciales de Meta

| Acción | operador | cliente-admin | cliente-analista | motor | Fuente | Contrato |
|---|---|---|---|---|---|---|
| Cargar o rotar los tokens y el App Secret | ⛔ `404` | ✅ | 🟡 ⛔ | ⛔ | RF06 | ✅ `PUT /chat/{bot_id}/credentials` |
| Ver cuáles están cargados (nunca su valor) | ⛔ `404` | ✅ | 🟡 ✅ | ⛔ | RF06, RS06 | ✅ en `GET /chat/{bot_id}/info` |
| Obtener los tokens en claro para enviar | ⛔ | ⛔ | ⛔ | ✅ solo el notificador | RF07, RS06 | ✅ `GET /chat/{bot_id}/tokens` |
| Rotar el `verifyToken` | ⛔ | 🟡 ✅ | 🟡 ⛔ | ⛔ | Borrador | ❌ |

### 6. Contactos y derechos del titular

| Acción | operador | cliente-admin | cliente-analista | motor | Fuente | Contrato |
|---|---|---|---|---|---|---|
| Listar los contactos de un bot | ⛔ `404` | ✅ | 🟡 ✅ | ⛔ | RF08 | ✅ `GET /chat/{bot_id}/contacts` |
| Buscar a una persona en todos sus bots | ⛔ | ✅ | 🟡 ✅ | ⛔ | RF08 | ✅ `POST /contacts/search` |
| Exportar lo guardado de un contacto, con su historial | ⛔ `404` | ✅ | 🟡 ⛔ | ⛔ | RF09, RF14 | ✅ `GET /chat/{bot_id}/contacts/{contact_id}` |
| Borrar un contacto (registro de supresiones) | ⛔ `404` | ✅ | 🟡 ⛔ | ⛔ | RF09 | ✅ `DELETE /chat/{bot_id}/contacts/{contact_id}` |
| Leer las últimas 10 vueltas de una conversación | ⛔ | ⛔ | ⛔ | ✅ solo el coordinador | RF15 | ✅ `GET …/contacts/{contact_id}/turns` |

### 7. Lo que el borrador añade y Hive no tiene

| Acción | operador | cliente-admin | cliente-analista | motor | Fuente | Contrato |
|---|---|---|---|---|---|---|
| Invitar usuarios al cliente; darlos de alta, de baja o bloquearlos; cambiar su rol | — | 🟡 ✅ | 🟡 ⛔ | — | Borrador (D1, D2) | ❌ |
| Ver la auditoría de su cliente | 🟡 ✅ de la plataforma | 🟡 ✅ lectura | 🟡 ⛔ | — | RS14 (D5) | ⚠️ |
| Definir guardrails y la acción ante una violación | — | 🟡 ✅ | 🟡 ⛔ | — | Borrador | ❌ |
| Calificar respuestas y ver métricas de cumplimiento | — | 🟡 ✅ | 🟡 ✅ | — | Borrador (el evaluador de la ADR-0008 del motor) | ❌ |
| Ver trazas y eventos del bus | 🟡 ✅ | ⛔ | ⛔ | — | Mapa de estilo (ADR-0027) | ❌ (hoy, en Tempo) |
| Registrar servidores MCP y ejecutar procesos sobre datos agregados | 🟡 ✅ con aprobación humana si es destructivo | — | — | — | Borrador (D7) | ❌ |
| Reprocesar mensajes que no se pudieron entregar y mantenimiento | 🟡 ✅ con aprobación humana si es destructivo | — | — | — | Borrador | ❌ |
| Entrar directamente en los datos de un cliente | ⛔ | — | — | — | RS01, ADR-0012 | — |

## El motor, worker por worker

Esta tabla resume `WORKER_ROUTES` (ADR-0022). Cualquier otra ruta le responde `403` al worker, y lo que alcanza es
siempre del cliente para el que tiene la clave.

| Worker | `GET /gateway/keys` | `…/rules` | `…/tools` | `…/tokens` | `…/turns` |
|---|---|---|---|---|---|
| Clasificador | ✅ | ✅ | ⛔ | ⛔ | ⛔ |
| Coordinador | ✅ | ✅ | ✅ | ⛔ | ✅ |
| Enlace | ✅ | ⛔ | ✅ | ⛔ | ⛔ |
| De respuesta | ✅ | ✅ | ⛔ | ⛔ | ⛔ |
| Evaluador | ✅ | ✅ | ⛔ | ⛔ | ⛔ |
| Notificador | ✅ | ⛔ | ⛔ | ✅ | ⛔ |

Una clave del motor registrada antes de que las claves dijeran su worker (ADR-0016) alcanza las cinco rutas, y cada
uso deja un aviso en el log. Esas claves se retiran cuando cada worker tenga la suya.

## Perfil propuesto para `cliente-analista`

Lee lo de su cliente: bots, números, reglas, intents, herramientas, qué credenciales están cargadas y contactos.
Califica respuestas cuando exista la evaluación. No crea ni cambia configuración, no toca credenciales, no ve el
`verifyToken`, no gestiona usuarios ni claves, y no exporta ni borra contactos. Es el perfil de un analista de
calidad o de un agente de soporte.

## Decisiones pendientes

| # | Decisión | Opción por defecto propuesta | Bloquea |
|---|---|---|---|
| D1 | ¿Hive tiene usuarios humanos dentro de un cliente, con sesión en un IdP, además de la credencial de máquina? | Sí, para las superficies de la ADR-0027. Necesita requisitos nuevos en el PRD y una ADR de autenticación de personas | Toda la columna `cliente-analista`, el área del cliente |
| D2 | ¿Un catálogo fijo de roles, o roles que cada cliente define? | Catálogo fijo en la v1: `cliente-admin` y `cliente-analista` | El modelo de datos de usuarios |
| D3 | ¿Cómo convive la credencial de máquina (ADR-0025) con los usuarios? | La credencial de máquina conserva los permisos de `cliente-admin`, para integraciones; los usuarios tienen el rol que se les dé | ADR de autenticación de personas |
| D4 | ¿Qué pone un bot en ERR, y quién publica o despublica? | Publicar y despublicar, solo `cliente-admin`. ERR lo pone el sistema ante un fallo de firma o de token de Meta | HIG-74, HIG-75 |
| D5 | ¿El cliente ve la auditoría de lo suyo? | Sí, `cliente-admin`, solo lectura | RS14 |
| D6 | ¿`cliente-analista` ve el `verifyToken`? | No: la ficha se lo quita. Es el secreto con el que se suscribe una app de Meta a la URL del bot | `GET /chat/{bot_id}/info` |
| D7 | ¿Entran los procesos MCP del operador? | Fuera de la v1. Si entran, solo sobre datos agregados y con una ADR propia | — |

## Próximos pasos

1. Cerrar D1–D7, convertir cada 🟡 en ✅ o ⛔, y pasar este documento a la v1.0.0 en estado `accepted`.
2. Una prueba que compare la matriz con el código: que cada ✅ y ⛔ construido coincida con la dependencia de su ruta
   y con `WORKER_ROUTES`. Así la matriz no se queda atrás cuando cambie una ruta.
3. Marcar en el contrato qué roles alcanzan cada operación (`x-roles`), generado de las mismas dependencias.
4. Derivar de aquí los escenarios de abuso de autorización que faltan en el PRD: un analista que intenta configurar y
   un usuario que intenta otro cliente. Las pruebas de hoy cubren el aislamiento entre clientes
   (`tests/unit/test_client_isolation.py`), el registro (`test_client_registry.py`) y las claves del motor
   (`test_service_credential.py`).
