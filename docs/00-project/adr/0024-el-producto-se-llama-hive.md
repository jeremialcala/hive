# ADR-0024: El producto se llama Hive, con su API en hive-api y su webhook en hive-webhook

* **Estado:** accepted
* **Fecha:** 2026-10-20
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 00-project
* **Versión:** 0.3.0
* **ID:** ADR-0024
* **Supersede:** —
* **Controles OWASP afectados:** A05 (cada superficie con sus propias reglas en Cloudflare), A07 (la audiencia de los
  tokens es el dominio de la API)

Decisiones de Jeremi del 2026-10-20. La primera versión (PR #72) ponía la API en `hive.higerotech.com` y dejaba el
webhook para el E7. Ese mismo día Jeremi separó los dos en subdominios propios (versión 0.2.0). El 2026-10-22 decidió
renombrar también lo que ya existía, el repositorio, las imágenes y los contenedores (versión 0.3.0, punto 6), y
describir el producto como «la plataforma para la gestión automatizada de tus bots y agentes».

## Contexto

El gateway y el motor de respuestas son un solo producto: un BaaS de chatbots para WhatsApp, Messenger e Instagram,
multi-cliente. Hasta el 2026-10-20 no tenía nombre. El repositorio se llama `wh-python-fastapi-messenger`, los contenedores
de midgard `bots-*`, y los documentos dicen «el gateway» y «el motor».

En el E7 el producto se publica por primera vez, y tiene dos superficies distintas:
- el **webhook**, que solo llama Meta;
- la **API de gestión**, que usan los clientes, el operador y los workers del motor.

El subdominio de la API es también la audiencia de los tokens (`GATEWAY_AUDIENCE`). Cada cliente y cada worker la
ponen en cada token, así que cambiarla después obliga a todos a cambiarla a la vez.

Los componentes de la plataforma llevan nombres nórdicos: Yggdrasil, Bifröst, Andvari, Hermod, Bragi, Saga, Mímir,
Heimdall, Thor, Odín.

## Decisión

1. **El producto se llama Hive.** El nombre describe lo que es el motor: seis workers, cada uno con su tarea, que se
   pasan el mensaje (ADR-0008 del motor). Es el nombre que ven los clientes, en inglés y fácil de decir. Los
   componentes de la plataforma conservan sus nombres nórdicos.
2. **Un subdominio por superficie:**

   | Superficie | URL | Quién la llama |
   |---|---|---|
   | API de gestión | `https://hive-api.higerotech.com` | Los clientes, el operador y los workers del motor |
   | Webhook de Meta | `https://hive-webhook.higerotech.com` | Solo Meta: la URL de cada bot es `https://hive-webhook.higerotech.com/chat/{bot_id}` |

   Así cada una se protege a su manera en Cloudflare, y la URL configurada en Meta no cambia si la API se mueve.
3. **La audiencia de los tokens es la URL de la API:** `GATEWAY_AUDIENCE=https://hive-api.higerotech.com`. La
   usan los clientes, el operador y los workers del motor.
4. **Un solo nivel de subdominio**, como Criterio (`criterio`, `criterio-api-dev`). El certificado gratuito de
   Cloudflare cubre `*.higerotech.com`, no `*.hive.higerotech.com`. Si hace falta publicar desarrollo:
   `hive-api-dev` y `hive-webhook-dev`.
5. **`hive.higerotech.com` queda reservado para el producto**, por ejemplo para su página. No se publica en el E7.
6. **Todo lo técnico lleva el nombre de Hive** (versión 0.3.0, 2026-10-22). Hasta entonces este punto decía que no se
   renombraba lo que ya existía; el gateway no está bajo el despliegue continuo de midgard, y GitHub redirige el
   nombre viejo del repositorio, así que el cambio no rompe enlaces:

   | Qué | Antes | Ahora |
   |---|---|---|
   | Repositorio | `jeremialcala/wh-python-fastapi-messenger` | `jeremialcala/hive` |
   | Imagen del gateway | `ghcr.io/jeremialcala/wh-python-fastapi-messenger` | `ghcr.io/jeremialcala/hive-gateway` |
   | Imagen del motor | `ghcr.io/jeremialcala/chatbot-engine` | `ghcr.io/jeremialcala/hive-engine` |
   | Proyecto compose del gateway | `bots-gateway`, en `/srv/apps/bots-gateway` | `hive-gateway`, en `/srv/apps/hive-gateway` |
   | Contenedores del gateway | `bots-webhook`, `bots-gateway`, `bots-ingest` | `hive-webhook`, `hive-api`, `hive-ingest` |

   - El de la API de gestión se llama `hive-api`, como su subdominio.
   - **No cambian** los nombres de servicio de las trazas (`gateway-webhook`, `engine-*`), que nombran el componente y
     dan la continuidad de Saga; ni los paquetes de Python; ni los usuarios de Bifröst ni los roles de Andvari, que
     nombran su función.
   - **El traslado, el 2026-10-22:** los nombres públicos del túnel pasaron a `hive-webhook` y `hive-api` en
     Cloudflare antes que los contenedores. El webhook estuvo caído unos minutos y la API desapareció del DNS hasta
     que se volvió a crear su nombre público. Unos alias de red temporales lo cubrieron hasta el traslado, que dejó
     el webhook fuera 69 s; ese mismo día se quitaron. Hive siguió respondiendo por la red interna.
   - Supera lo que la ADR-0023 decía de los nombres de las imágenes.

## Alternativas consideradas

**El nombre:**

| Opción | Pros | Contras |
|---|---|---|
| **Hive** | Describe los workers; fácil para los clientes; en inglés | Rompe la convención nórdica de la plataforma. Es un nombre muy usado (Apache Hive, Hive Social, la cadena de bloques Hive), así que es difícil de distinguir como marca propia |
| Kvasir | El ser más sabio del mito, que respondía cualquier pregunta; distintivo; nórdico | Difícil de decir para un cliente |
| Huginn | El cuervo mensajero de Odín; nórdico | Ya es el nombre de un proyecto de código abierto conocido |

**Los subdominios:**

| Opción | Pros | Contras |
|---|---|---|
| **`hive-api` y `hive-webhook`** | Cada superficie con sus reglas. La URL de Meta no depende de la API. `hive` queda libre para el producto | Dos nombres en el túnel |
| API en `hive`, webhook en `hive-webhook` (la primera versión) | El nombre más corto para la API | El nombre del producto queda atado a la API |
| Todo en `hive`, el webhook por ruta | Un solo nombre | Las reglas de Cloudflare del webhook y de la API van por ruta, sobre el mismo host |

## Consecuencias

- **En el E7, el túnel publica los dos subdominios:** `hive-api.higerotech.com` hacia el gateway y
  `hive-webhook.higerotech.com` hacia el webhook. La URL del webhook de cada bot se configura en Meta.
- **La instancia de medición de midgard cambia su `GATEWAY_AUDIENCE`:** hoy tiene otra, de 23 caracteres. Con ella
  pasan a la nueva audiencia los tokens del operador, los de `ops.engine_keys` y la configuración del coordinador y
  del notificador (`GATEWAY_AUDIENCE`).
- **Los documentos para clientes** dicen Hive. Los de diseño siguen diciendo «el gateway» y «el motor», que son sus
  partes.
- **Los documentos históricos** (mediciones, el charter del motor, su modelo de amenazas) conservan los nombres de su
  fecha.
- **Si Hive llega a ser una marca propia**, con su propio dominio o un registro de marca, se revisa el nombre: es
  entonces cuando pesa que sea un nombre tan usado.

## Condiciones de revisión

- Hive pasa a tener un dominio propio o se registra como marca.
- El producto se publica fuera de `higerotech.com`.
