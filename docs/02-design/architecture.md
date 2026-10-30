# Diseño del sistema — Gateway BaaS de mensajería

* **Estado:** approved (Gate 1 del refactor, 2026-10-18)
* **Fecha:** 2026-10-18
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 02-design
* **Versión:** 0.3.0
* **Gate:** 1
* **Estilo arquitectónico:** monolito modular en capas (rutas → controladores → entidades), con puertos y adaptadores como objetivo para las piezas nuevas
* **ADRs relacionadas:** ADR-0002 a ADR-0023, en [`docs/00-project/adr/`](../00-project/adr/)

Este documento describe **dos cosas a la vez**: lo que el gateway es hoy y lo que el diseño pide. En cada vista se
marca qué existe y qué falta. La lista completa de diferencias está en
[Brecha entre lo construido y el diseño](#brecha-entre-lo-construido-y-el-diseño).

## Contextos acotados (DDD)

| Contexto | Responsabilidad | Entidades núcleo | Estado |
|---|---|---|---|
| Clientes y acceso | Registrar clientes, autenticar cada petición y decidir qué puede ver cada uno | Cliente, clave pública de firma, clave de datos | Construido: registro y baja de clientes, token firmado y cifrado, aislamiento por cliente y clave de datos |
| Gestión de bots | Alta y configuración de un bot: persona, reglas, números y credenciales de Meta | `ChatBot`, `ChatbotPhone`, `Rule` | Construido, sin dueño por bot |
| Mensajería entrante | Verificar a Meta, normalizar la notificación y publicarla con confirmación | `FacebookRequest`, `MessageBody`, `QueueMessage` | Construido |
| Contactos | Identificar o crear a quien escribe; exportar y borrar sus datos | `Contact` | Construido, con el número cifrado; se listan, se buscan, se exportan y se borran |
| Intents y herramientas | Lo que un bot sabe hacer, expuesto al motor como herramientas | `Intent`, `Action`, `ApiCall` | Construido |
| Trazabilidad | Registrar cada petición y sus pasos | `Event`, `EventAction`, `Jwk` | Construido y desactivado |

## Vista C4 — Container

```mermaid
C4Container
    title Contenedores - Gateway BaaS de mensajeria

    Person(cliente, "Cliente", "Administra sus bots")
    Person(operador, "Operador", "Registra clientes y opera")
    System_Ext(meta, "Meta", "WhatsApp Business y Messenger")
    System_Ext(cf, "Cloudflare", "Borde, TLS y tunel")
    System_Ext(motor, "ms-python-metamsghandler", "Motor de respuestas")
    System_Ext(cd, "GitHub y GHCR", "Pruebas, imagen y webhook de despliegue")
    ContainerDb_Ext(atlas, "MongoDB Atlas", "MongoDB", "Bots, contactos, reglas, intents y registro de clientes")

    System_Boundary(servidor, "Servidor midgard") {
        Container(tunel, "cloudflared", "Cloudflare Tunnel", "Publica solo el webhook")
        Container(webhook, "Webhook", "Python 3.12, FastAPI, Uvicorn", "Verifica, identifica y encola cada mensaje")
        Container(gateway, "API de gestion", "Python 3.12, FastAPI, Uvicorn", "Clientes, bots y la configuracion que pide el motor")
        ContainerQueue(hermod, "Hermod", "RabbitMQ 4.3", "Cola durable llm")
        Container(receptor, "Receptor de despliegue", "cd-receiver", "Levanta el tag nuevo y vuelve atras si falla")
    }

    Rel(meta, cf, "Entrega notificaciones firmadas", "HTTPS")
    Rel(cf, tunel, "Reenvia por el tunel", "QUIC saliente")
    Rel(tunel, webhook, "Pasa /chat/uuid", "HTTP en loopback")
    Rel(cliente, gateway, "Administra sus bots", "HTTPS, JWT firmado en JWE")
    Rel(operador, gateway, "Registra clientes", "Red privada")
    Rel(gateway, atlas, "Lee y escribe", "TLS")
    Rel(webhook, atlas, "Lee el bot, guarda el contacto", "TLS")
    Rel(webhook, meta, "Consulta el perfil del contacto", "HTTPS")
    Rel(webhook, hermod, "Publica cada mensaje", "AMQP con confirmacion")
    Rel(motor, hermod, "Consume la cola llm", "AMQP")
    Rel(motor, gateway, "Pide la configuracion por bot_id", "JWT firmado en JWE")
    Rel(motor, meta, "Envia la respuesta", "HTTPS")
    Rel(cd, receptor, "Avisa del build", "Webhook firmado")
    Rel(receptor, webhook, "Despliega los dos", "Docker Compose")

    UpdateElementStyle(webhook, $bgColor="#1168bd", $fontColor="#ffffff")
    UpdateElementStyle(gateway, $bgColor="#1168bd", $fontColor="#ffffff")
    UpdateLayoutConfig($c4ShapeInRow="3", $c4BoundaryInRow="1")
```
*Eje estructura · fase 02 · Gate 1. Es el objetivo. El webhook y la API de gestión son dos contenedores de la misma
imagen ([ADR-0018](../00-project/adr/0018-webhook-y-gestion-por-separado.md)). El acceso de los clientes ya existe, con su token. No existen
todavía: las dos relaciones del motor con el gateway y con Meta, la publicación de la gestión, y el propio
despliegue del gateway en el servidor.*

## Vista C4 — Component (gateway)

```mermaid
C4Component
    title Componentes - Gateway

    Container_Ext(tunel, "cloudflared", "Tunel", "Entrega /chat/uuid")
    Container_Ext(llamante, "Cliente, operador o motor", "HTTP", "Usa la API de gestion")
    ContainerDb_Ext(atlas, "MongoDB Atlas", "MongoDB", "Datos")
    ContainerQueue_Ext(hermod, "Hermod", "RabbitMQ", "Cola llm")
    System_Ext(meta, "Meta Graph API", "Perfiles de contacto")

    Container_Boundary(gw, "Gateway") {
        Component(interceptor, "Interceptor", "Middleware HTTP", "Tiempos y respuesta 500 por defecto")
        Component(firma, "Verificacion del webhook", "Dependencia FastAPI", "Token de verificacion y X-Hub-Signature-256")
        Component(auth, "Autenticacion", "Dependencia FastAPI", "JWT firmado por el cliente dentro de un JWE")
        Component(rutas, "Rutas", "FastAPI", "Webhook, bots, reglas, intents, salud")
        Component(mensajes, "Mensajeria entrante", "controller.messages", "Normaliza la notificacion y arma el sobre")
        Component(contactos, "Contactos", "controller.facebook", "Identifica o crea al contacto")
        Component(bots, "Bots e intents", "controller.chatbot, intents", "Configuracion de cada bot")
        Component(publicador, "Publicador", "controller.amqp_broker, pika", "Publica con confirmacion del broker")
        Component(registro, "Registro de clientes", "Repositorio", "Clientes y sus claves publicas de firma")
        Component(repos, "Repositorios por cliente", "Por construir", "Unico acceso a datos, siempre con clientId")
        Component(cifrado, "Cifrado de campos", "AES-256-GCM", "Clave de datos del cliente e indice ciego")
        Component(entidades, "Entidades", "mongoengine", "Documentos de MongoDB")
    }

    Rel(tunel, interceptor, "Notificaciones", "HTTP")
    Rel(llamante, interceptor, "Gestion", "HTTP")
    Rel(interceptor, rutas, "Pasa la peticion")
    Rel(rutas, firma, "Exige en el webhook")
    Rel(rutas, auth, "Exige en la gestion")
    Rel(auth, registro, "Busca la clave publica del cliente")
    Rel(rutas, mensajes, "Notificacion verificada")
    Rel(rutas, bots, "Operaciones de gestion")
    Rel(mensajes, contactos, "Identifica al remitente")
    Rel(mensajes, publicador, "Entrega el sobre")
    Rel(contactos, meta, "Pide el perfil", "HTTPS")
    Rel(contactos, repos, "Busca y guarda")
    Rel(bots, repos, "Busca y guarda")
    Rel(repos, cifrado, "Cifra y descifra campos")
    Rel(repos, entidades, "Usa")
    Rel(entidades, atlas, "Lee y escribe", "TLS")
    Rel(publicador, hermod, "Publica", "AMQP")

    UpdateLayoutConfig($c4ShapeInRow="3", $c4BoundaryInRow="1")
```
*Eje estructura · fase 02 · Gate 1. Los cuatro componentes que el diseño añadía ya están construidos: la
autenticación, el registro de clientes, la capa de repositorio y el cifrado de campos.*

## Flujos críticos (comportamiento)

### Notificación entrante

Construido tal cual, salvo la caducidad del mensaje.

```mermaid
sequenceDiagram
    autonumber
    participant M as Meta
    participant T as Tunel
    participant G as Gateway
    participant D as MongoDB
    participant Q as Hermod
    M->>T: POST /chat/bot_id con X-Hub-Signature-256
    T->>G: Reenvia la notificacion
    G->>D: Busca el bot por bot_id
    alt El bot no existe o el id no es un UUID
        G-->>M: 404
    else Firma ausente o invalida
        G-->>M: 403
    else Firma valida
        G->>D: Busca o crea el contacto
        opt Contacto nuevo de Messenger
            G->>M: Pide el perfil con el token de pagina
        end
        G->>Q: Publica el sobre, persistente y con confirmacion
        alt El broker confirma
            Q-->>G: ack
            G-->>M: 200
        else El broker no responde o rechaza
            G-->>M: 503
            Note over M,G: Meta reintenta mas tarde
        end
    end
```
*Eje comportamiento · fase 02 · Gate 1. La firma se comprueba sobre el cuerpo crudo, antes de procesar nada.*

### El motor responde a un mensaje

Flujo objetivo. Hoy el motor se detiene tras clasificar el mensaje.

```mermaid
sequenceDiagram
    autonumber
    participant Q as Hermod
    participant R as Motor de respuestas
    participant G as Gateway
    participant D as MongoDB
    participant M as Meta
    Q->>R: Entrega el sobre con botId y messageId
    R->>R: Descarta si botId y messageId ya se procesaron
    R->>R: Firma un JWT y lo cifra para el gateway
    R->>G: Pide la configuracion del bot con el token
    G->>G: Descifra, verifica firma, aud, caducidad y jti
    alt Token invalido, caducado o reutilizado
        G-->>R: 401
    else Token valido
        G->>D: Lee el bot, sus reglas e intents
        G->>G: Descifra el token de Meta con la clave de datos del cliente
        G-->>R: Reglas, herramientas y token de Meta
        R->>R: Genera la respuesta o ejecuta la accion
        R->>M: Envia la respuesta al usuario
        R->>Q: Confirma el mensaje
    end
```
*Eje comportamiento · fase 02 · Gate 1. Ver ADR-0002, ADR-0003, ADR-0009 y ADR-0011.*

## Ciclo de vida de la entidad núcleo

La entidad núcleo es el mensaje entrante, desde que Meta lo entrega hasta que se responde o caduca.

```mermaid
stateDiagram-v2
    [*] --> Recibido: Meta entrega la notificacion
    Recibido --> Rechazado: firma invalida o bot desconocido
    Recibido --> Verificado: firma valida
    Verificado --> Encolado: el broker confirma
    Verificado --> Reintentable: el broker no confirma, respuesta 503
    Reintentable --> Recibido: Meta reintenta
    Encolado --> Duplicado: botId y messageId ya procesados
    Encolado --> Caducado: 24 horas sin consumir
    Encolado --> EnProceso: el motor lo toma
    EnProceso --> Bloqueado: los guardrails lo rechazan
    EnProceso --> Respondido: respuesta enviada al usuario
    EnProceso --> Fallido: error del motor
    Rechazado --> [*]
    Duplicado --> [*]
    Caducado --> [*]
    Bloqueado --> [*]
    Respondido --> [*]
    Fallido --> [*]
```
*Eje comportamiento · fase 02 · Gate 1. Hasta `Encolado` es responsabilidad del gateway y está construido. `Caducado`
lo aplica el broker, con la caducidad que el gateway pone en cada mensaje. `Duplicado` es del motor y no existe todavía
(ADR-0011). De `EnProceso` en adelante es del motor.*

## Modelo de datos y dominio

```mermaid
erDiagram
    CLIENTE ||--o{ CHATBOT : "es dueno de"
    CLIENTE ||--o{ CLAVE_PUBLICA : registra
    CHATBOT ||--o{ CHATBOT_PHONE : tiene
    CHATBOT ||--o{ CONTACT : atiende
    CHATBOT ||--o{ RULE : sigue
    CHATBOT ||--o{ INTENT : ofrece
    INTENT ||--o{ ACTION : contiene
    CLIENTE {
        uuid clientId
        string nombre
        bytes claveDeDatosEnvuelta
        int status
    }
    CLAVE_PUBLICA {
        string kid
        uuid clientId
        string jwkPublica
    }
    CHATBOT {
        uuid uuid
        uuid clientId
        string name
        string verifyToken
        string facebookToken
        string whatsappToken
        string appSecret
    }
    CHATBOT_PHONE {
        uuid uuid
        uuid bot_id
        string environment
        string phoneNumber
        string phoneId
    }
    CONTACT {
        uuid _uuid
        uuid botId
        string firstName
        string fbId
        string waId
        string waIdIndice
    }
    RULE {
        uuid uuid
        uuid botId
        string rule
    }
    INTENT {
        uuid uuid
        uuid botId
        string name
    }
    ACTION {
        string name
        string type
        string apiCallUrl
    }
```
*Eje estructura · fase 02 · Gate 1. `CLIENTE`, `CLAVE_PUBLICA`, el campo `clientId` y `waIdIndice` son del diseño y no
existen. En el diseño, `verifyToken`, `facebookToken`, `whatsappToken`, `appSecret`, `phoneNumber` y `waId` se guardan
cifrados (ADR-0010). No se muestran `Event`, `EventAction`, `Jwk` ni `Credentials`, que están desactivados.*

```mermaid
classDiagram
    class VerificadorDeToken {
        <<interface>>
        +verificar(token) Identidad
    }
    class RegistroDeClientes {
        <<interface>>
        +clavePublica(clientId, kid) Jwk
        +claveDeDatos(clientId) Clave
    }
    class CifradorDeCampos {
        <<interface>>
        +cifrar(clientId, valor) Cifrado
        +descifrar(clientId, cifrado) Valor
        +indice(clientId, valor) Hash
    }
    class RepositorioDeBots {
        <<interface>>
        +obtener(clientId, botId) Bot
        +guardar(clientId, bot)
    }
    class PublicadorDeMensajes {
        <<interface>>
        +publicar(sobre)
    }
    class TokenJwtEnJwe {
        +verificar(token) Identidad
    }
    class RepositorioMongo {
        +obtener(clientId, botId) Bot
        +guardar(clientId, bot)
    }
    class PublicadorRabbit {
        +publicar(sobre)
    }
    class EventPublisher {
        <<interface>>
        +publish(sujeto, evento, messageId) Published
    }
    class NatsPublisher {
        +publish(sujeto, evento, messageId) Published
    }
    TokenJwtEnJwe ..|> VerificadorDeToken
    TokenJwtEnJwe --> RegistroDeClientes
    RepositorioMongo ..|> RepositorioDeBots
    RepositorioMongo --> CifradorDeCampos
    CifradorDeCampos --> RegistroDeClientes
    PublicadorRabbit ..|> PublicadorDeMensajes
    NatsPublisher ..|> EventPublisher
```
*Eje estructura · fase 02 · Gate 1. Puertos del diseño. El publicador de RabbitMQ existe como función, no como puerto,
y se va con Hermod. `EventPublisher` y su adaptador `NatsPublisher` existen desde I2 (`shared/bus`): el webhook pasa a
usarlos en I3.*

## Contratos de API

| Contrato | Fichero | Cómo se mantiene |
|---|---|---|
| API HTTP | [`contracts/openapi.yaml`](contracts/openapi.yaml) | Se genera de la aplicación con `.ai-dlc/scripts/gen_openapi.py`. Una prueba de contrato falla si deja de coincidir |
| Cola `llm` | [`contracts/asyncapi.yaml`](contracts/asyncapi.yaml) | Escrito a mano. Una prueba de contrato valida contra él el mensaje que el código publica de verdad |

Los dos describen lo que hay **hoy**. El OpenAPI no declara esquemas de seguridad porque la aplicación no los
declara. El AsyncAPI describe el sobre versión 1 de ADR-0011, que es lo que el gateway publica: un sobre por cada
mensaje, con el identificador de Meta y una caducidad de 24 horas.

Autenticación por superficie:

| Superficie | Hoy | Diseño |
|---|---|---|
| `GET` y `POST /chat/{bot_id}` | Token de verificación y firma de Meta | Igual (ADR-0004) |
| `GET /health` | Sin autenticación; solo en loopback | Igual |
| Resto de endpoints | JWT firmado por el cliente dentro de un JWE: construido | JWT firmado por el cliente dentro de un JWE (ADR-0005, ADR-0009) |

## Objetivo del refactor (Gate 1 del refactor, 2026-10-18)

Lo que cambia con la topología de ocho workers del [plan](../00-project/plan-topologia-de-workers.md). El gateway
pasa a ser tres servicios de la misma imagen alrededor de un bus de eventos: el webhook, el worker de ingesta y la
API de gestión. Las vistas de arriba describen lo construido hasta la 0.3.0; estas, lo que se construye en los
incrementos I0 a I8.

### Vista C4 — Container (objetivo)

```mermaid
C4Container
    title Contenedores - objetivo del refactor

    Person(cliente, "Cliente", "Administra sus bots")
    System_Ext(meta, "Meta", "WhatsApp, Messenger e Instagram")
    System_Ext(cf, "Cloudflare", "Borde, TLS y tunel")
    ContainerDb_Ext(atlas, "MongoDB Atlas", "MongoDB", "Bots, contactos, historial cifrado y registro de clientes")

    System_Boundary(servidor, "Servidor midgard") {
        Container(webhook, "Webhook", "Python 3.12, FastAPI", "Comprueba la firma y publica cada notificacion como un evento")
        Container(ingesta, "Worker de ingesta", "Python 3.12", "Identifica al contacto, guarda el historial y publica en la particion")
        Container(gestion, "API de gestion", "Python 3.12, FastAPI", "Clientes, bots, exportar y borrar, configuracion para el motor")
        ContainerQueue(bifrost, "Bifrost", "NATS JetStream 2.14", "Streams WEBHOOK, MENSAJES, RESPUESTAS y SUPRESIONES")
        ContainerDb(kv, "KV contexto", "NATS JetStream KV", "Contexto de cada conversacion, 24 horas")
        Container(andvari, "Andvari", "OpenBao", "Claves de datos y credenciales")
        Container_Ext(motor, "Workers del motor", "ms-python-metamsghandler", "Clasificador, coordinador, enlace, respuesta, evaluador y notificador")
    }

    Rel(meta, cf, "Notificaciones firmadas", "HTTPS")
    Rel(cf, webhook, "Solo el webhook", "Tunel")
    Rel(webhook, bifrost, "Publica y espera el acuse", "NATS, TLS")
    Rel(bifrost, ingesta, "Entrega cada evento", "NATS, TLS")
    Rel(ingesta, atlas, "Contacto e historial", "TLS")
    Rel(ingesta, bifrost, "Publica el mensaje identificado", "NATS, TLS")
    Rel(bifrost, motor, "Mensajes por particion", "NATS, TLS")
    Rel(motor, kv, "Lee y escribe el contexto", "NATS, TLS")
    Rel(motor, gestion, "Reglas, herramientas, tokens, vueltas", "JWT en JWE, una clave por worker")
    Rel(motor, meta, "Envia la respuesta", "HTTPS")
    Rel(cliente, gestion, "Administra sus bots", "JWT en JWE")
    Rel(gestion, atlas, "Lee y escribe", "TLS")
    Rel(webhook, andvari, "Abre el App Secret", "HTTPS")

    UpdateElementStyle(webhook, $bgColor="#1168bd", $fontColor="#ffffff")
    UpdateElementStyle(ingesta, $bgColor="#1168bd", $fontColor="#ffffff")
    UpdateElementStyle(gestion, $bgColor="#1168bd", $fontColor="#ffffff")
    UpdateLayoutConfig($c4ShapeInRow="3", $c4BoundaryInRow="1")
```
*Eje estructura · fase 02 · Gate 1 del refactor. En azul, los tres servicios del gateway
([ADR-0018](../00-project/adr/0018-webhook-y-gestion-por-separado.md), [ADR-0021](../00-project/adr/0021-el-webhook-solo-publica.md)).
Bifröst es [ADR-0019](../00-project/adr/0019-nats-jetstream-como-bus.md) y [ADR-0020](../00-project/adr/0020-placement-de-bifrost.md);
el bucket de contexto, [ADR-0017](../00-project/adr/0017-contexto-de-conversacion-e-historial.md). Hermod se retira
cuando el motor lea de Bifröst.*

### Notificación entrante (objetivo)

```mermaid
sequenceDiagram
    autonumber
    participant M as Meta
    participant W as Webhook
    participant B as Bifrost
    participant I as Worker de ingesta
    participant A as Atlas
    participant C as Coordinador (motor)
    M->>W: POST /chat/{bot_id} firmado
    W->>W: Bot y App Secret en memoria, firma comprobada
    W->>B: Un evento con los mensajes de la notificacion, Nats-Msg-Id y traceparent
    alt Bifrost acusa
        B-->>W: PubAck
        W-->>M: 200
    else Bifrost no acusa
        W-->>M: 503, Meta reenvia
    end
    B->>I: Evento de WEBHOOK
    loop Cada mensaje de la notificacion
        I->>A: Ya esta en el historial?
        alt Es nuevo
            I->>A: Contacto y mensaje en el historial, cifrados
            I->>B: Mensaje en gateway.message.particion.bot.contacto
        else Es un reintento de Meta
            I->>I: Lo salta
        end
    end
    I->>B: Confirma el evento
    alt La base no responde
        I-->>B: Sin confirmar, se reentrega con espera creciente, hasta 7 dias
    end
    B->>C: Mensaje de su particion, uno en vuelo
```
*Eje comportamiento · fase 02 · Gate 1 del refactor. El plazo de Meta (RNF01) solo incluye los pasos 1 a 5: Atlas y
la Graph API salen de él. Lo que hace el motor con el mensaje está en la secuencia de ADR-0017.*

### Ciclo de vida de un evento en el stream `WEBHOOK`

```mermaid
stateDiagram-v2
    [*] --> Publicado: el webhook publica y Bifrost acusa
    Publicado --> Entregado: la ingesta lo recibe
    Entregado --> Confirmado: identificado, guardado y publicado, o reintento de Meta
    Entregado --> EnEspera: la base, Andvari o Bifrost no responden
    EnEspera --> Entregado: vence la espera creciente
    Entregado --> Terminado: falla por si mismo o el bot ya no existe
    EnEspera --> Caducado: pasan 7 dias
    Confirmado --> [*]
    Terminado --> [*]
    Caducado --> [*]
```
*Eje comportamiento · fase 02 · Gate 1 del refactor. "Terminado" queda en el log; "Caducado" es un mensaje perdido
después de que Meta recibiera su `200` (T27), y Gjallarhorn avisa mucho antes, a los 15 minutos sin confirmar.*

### Qué tiene cada servicio del gateway

| Servicio | Rutas o sujetos | Credenciales | Lo que no tiene |
|---|---|---|---|
| Webhook | `GET` y `POST /chat/{bot_id}`; publica en `webhook.meta.>` | Atlas (lee el bot), Andvari (abre el App Secret), su usuario de Bifröst | Las claves del gateway, el registro de supresiones, la Graph API |
| Worker de ingesta | Consume `webhook.meta.>` y `engine.reply.sent.>`; publica en `gateway.message.>` | Atlas (contactos e historial), Andvari, la Graph API con el token de página, su usuario de Bifröst | Rutas HTTP |
| API de gestión | Las 30 rutas de gestión y la de las 10 últimas vueltas; publica en `gateway.*.erased` | Atlas, Andvari, las claves del gateway, el registro de supresiones, su usuario de Bifröst | — |

### Brecha del refactor

| Incremento | Trabajo | ADR | Estado |
|---|---|---|---|
| IS | El webhook y la gestión, en dos servicios | ADR-0018 | **Hecho** |
| I0 | Bifröst en midgard, con la medición del `fsync` en su disco | ADR-0019, ADR-0020 | **Hecho** (2026-10-19): 44,5 ms por escritura; con un evento por notificación, hasta 3 MB caben en 196 ms de mediana |
| I1 | Medir el webhook actual en midgard | — | **Hecho** (2026-10-19): 185 ms de mediana con un contacto conocido, 415 ms con uno nuevo, de 357 a 472 ms con 4 a la vez ([medición](../04-testing/medicion-webhook-i1.md)) |
| I2 | Puerto de publicación sobre NATS | ADR-0019 | **Hecho** (2026-10-19): `EventPublisher` y `NatsPublisher`, una conexión por proceso; contra Bifröst, 2,1 ms de mediana por evento de 1 KB, frente a los 8,3 ms de Hermod con conexión por mensaje ([guía](../03-implementation/bus-de-eventos.md)) |
| I3 | Contrato v2 y publicación de un evento por notificación | ADR-0021, `asyncapi-v2.yaml` | **Hecho** (2026-10-19): el webhook publica `EventoWebhook` en `webhook.meta.<canal>` y, hasta I4, sigue enviando los sobres v1 a Hermod; añade unos 4 ms ([guía](../03-implementation/bus-de-eventos.md#el-evento-del-webhook-i3)) |
| I4 | Worker de ingesta, historial y webhook que solo publica | ADR-0017, ADR-0021 | **Hecho** (2026-10-19), en dos partes: I4a, el historial; I4b, el worker de ingesta y el webhook que solo publica. El webhook pasa de 185 ms a 8,3 ms de mediana; la ingesta trata 2,1 notificaciones por segundo ([guía](../03-implementation/bus-de-eventos.md#el-worker-de-ingesta-i4)) |
| I5 | OpenTelemetry con la regla de los logs | — | **Hecho** (2026-10-19): spans en los tres servicios y una traza del webhook al motor, con la regla de los logs; sin colector todavía ([guía](../03-implementation/trazas.md)) |
| I6 | Una credencial por worker | ADR-0022 | **I6a hecho** (2026-10-19): cada clave del motor dice su worker y alcanza solo sus rutas (`WORKER_ROUTES`); las claves sin worker siguen con aviso en el log ([guía](../03-implementation/autenticacion.md#dar-paso-al-motor-de-respuestas)). **I6b hecho** (2026-10-19): `ops.engine_keys` da de alta las seis claves de un cliente, con la privada en Andvari (`motor/`) y la pública por la API. Pendiente: I6c, un usuario de Bifröst por worker |
| I7 | Destinos de los intents | — | **Hecho en el gateway** (2026-10-20): `shared.destinations` valida `apiCall` al guardar y filtra las herramientas; el worker de enlace tendrá que comprobar la resolución al llamar ([guía](../03-implementation/destinos-de-los-intents.md)) |
| I8 | Instagram | — | Pendiente |

## Patrones de seguridad seleccionados (por amenaza DREAD priorizada)

| Amenaza | Patrón / Control | OWASP | ADR |
|---|---|---|---|
| T2 Acceso cruzado entre clientes | Denegar por defecto; `clientId` obligatorio en una capa de repositorio única; lo ajeno responde `404` | A01 | ADR-0012 |
| T4 Gestión expuesta | Superficie mínima: el túnel publica una sola ruta y el contenedor escucha en loopback | A02 | ADR-0008 |
| T3 Fuga de la base | Cifrado en sobre con clave por cliente; índice ciego para buscar | A04 | ADR-0010 |
| T12 Tokens al alcance del motor | Mínimo privilegio: cliente de servicio de solo lectura, con registro de cada acceso | A01 | ADR-0003, ADR-0009 |
| T8 Token fabricado o reenviado | Firmar y después cifrar; lista cerrada de algoritmos; caducidad corta y un solo uso | A07 | ADR-0005, ADR-0009 |
| T6 Datos personales en logs | Los logs registran identificadores y resultados, nunca contenido | A09 | — |
| T14 Cadena de suministro | Imagen por digest, dependencias fijadas con hashes, análisis en el pipeline y SBOM | A03 | — |
| T7 Duplicados | Consumidor idempotente, con el identificador de Meta como clave | A10 | ADR-0006, ADR-0011 |
| T1 Notificación falsa | Verificar la firma sobre el cuerpo crudo, antes de procesar; fallar cerrado | A08 | ADR-0004, ADR-0007 |
| T16 Mensaje perdido | Confirmación del broker antes de responder; reintento a cargo del emisor | A10 | ADR-0006 |
| T22 Sujeto ajeno en el bus | Mínimo privilegio en el bus: un usuario por servicio con lista `allow` de sus sujetos; TLS | A01 | ADR-0019 |
| T23, T31 Copia del disco del bus o del contexto | Cifrado en reposo: almacén de Bifröst con clave en Andvari; contexto con una clave de Transit propia | A04 | ADR-0019, ADR-0017 |
| T25 Mensajes acusados perdidos en un corte de luz | Durabilidad antes del acuse: `fsync` en cada escritura | A10 | ADR-0019 |
| T26 Duplicados y desorden | Consumidor idempotente por el historial; particiones por conversación con un mensaje en vuelo | A10 | ADR-0019, ADR-0021 |
| T27 Caída larga de la base | El bus absorbe la caída 7 días; alerta a los 15 minutos sin confirmar | A10 | ADR-0021 |
| T28 Datos personales en trazas | Las trazas siguen la regla de los logs: identificadores, nunca contenido | A09 | — |
| T29 Credencial de worker con rutas de más | Mínimo privilegio por worker: una clave por cliente y por worker | A01 | ADR-0022 |

## Brecha entre lo construido y el diseño

Lo que hay que construir para que el código cumpla este diseño, en el orden que marca el threat model.

| Orden | Trabajo | Cubre | ADR |
|---|---|---|---|
| 1 | Entidad cliente, `clientId` en cada documento y capa de repositorio. **Hecho** | RF01, RS01 | ADR-0012 |
| 2 | Autenticación con JWT firmado dentro de un JWE; retirar `X-API-Key`. **Hecho**, con el alta de clientes y la credencial del motor, una por cliente | RS02, RS06 | ADR-0005, ADR-0009, ADR-0016 |
| 3 | Clave de datos por cliente, cifrado de campos e índice ciego; migrar lo que está en claro. **Hecho**; la migración se ejecuta a mano | RS05, RS06 | ADR-0010 |
| 4 | Sobre versión 1: identificadores y fechas reales, `metadata` como objeto, `messageId`, caducidad. **Hecho** del lado del gateway; el motor tiene su parte | RF12, RNF04, RNF05 | ADR-0011 |
| 5 | Endpoints para cargar credenciales de Meta, y para exportar y borrar contactos, bots y clientes. **Hecho**; en los respaldos la supresión se cumple por caducidad, a los 365 días como mucho | RF06, RF08, RF09, RF10 | ADR-0014 |
| 6 | Logs sin datos personales, errores sin detalles, esquema estricto de la notificación y límite de tamaño. **Hecho**; los modelos de gestión siguen sin acotar | RS07, RS11, RS15 | ADR-0015 |
| 7 | Retirar el envío por WhatsApp y la mención a `/answers`, que ya no son del gateway. **Hecho** | — | ADR-0002 |

Del lado del motor, en su repositorio: aceptar el sobre para Messenger, conservar el `botId`, deduplicar, pedir la
configuración al gateway y enviar la respuesta (ADR-0002, ADR-0003, ADR-0011).
