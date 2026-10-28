# Threat model — Gateway BaaS de mensajería

* **Estado:** approved (Gate 1 del refactor, 2026-10-18)
* **Fecha:** 2026-10-18
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 02-design
* **Versión:** 0.3.0
* **Gate:** 1
* **Alcance:** el gateway (webhook, ingesta y gestión), su cola en Hermod y, en el objetivo, su bus en Bifröst con el bucket de contexto, su base en MongoDB Atlas, el túnel y el despliegue; el motor de respuestas solo en su frontera con el gateway
* **Metodología:** STRIDE por elemento del DFD + DREAD para priorizar
* **Clasificación de datos (ref):** [`docs/00-project/data-classification.md`](../00-project/data-classification.md)

Parte del threat assessment inicial del [PRD](../01-requirements/mvp-baas-gateway.md). Cada amenaza indica si su
control está **construido**, **parcial** o **pendiente**: este documento describe el diseño, no afirma que todo
esté hecho.

## Diagrama de flujo de datos (DFD)

```mermaid
flowchart LR
    U([Usuario final]) -->|"1 mensaje"| META([Meta])
    ATK([Atacante]) -->|"2 HTTPS"| CF
    META -->|"2 notificacion firmada"| CF([Cloudflare edge])
    CLI([Cliente]) -.->|"9 gestion con token: hoy sin ruta publica"| CF
    CF -->|"3 tunel saliente, solo /chat/uuid"| TUN
    subgraph SRV [TB2: servidor midgard]
      TUN[cloudflared] -->|"4 HTTP loopback"| GW
      OP([Operador]) -->|"10 gestion, red privada"| GW
      RCV[Receptor de despliegue] -->|"12 compose up"| GW
      subgraph NET [TB3: red Docker hermod]
        GW[Gateway] -->|"6 AMQP confirmado"| Q[(Hermod: cola llm)]
      end
      ENV[(Secretos: .env del servidor)] -.-> GW
    end
    GW -->|"5 TLS"| DB[(MongoDB Atlas)]
    GW -->|"7 perfil del contacto, HTTPS"| META
    Q -->|"8 AMQP"| MOTOR([Motor de respuestas])
    MOTOR -.->|"9 configuracion por bot_id, con token"| GW
    MOTOR -.->|"13 respuesta al usuario"| META
    MOTOR -->|"14 texto del mensaje"| LLM([LLM])
    GH([GitHub: main, GHCR]) -->|"11 webhook firmado e imagen"| RCV
```
*Eje comportamiento · DFD STRIDE · Gate 1. TB1 = internet ↔ Cloudflare; TB2 = el túnel entra en el servidor; TB3 =
red Docker privada del broker; TB4 = servidor ↔ Atlas; TB5 = gateway ↔ motor de respuestas; TB6 = motor ↔ LLM. Las
líneas discontinuas son flujos del diseño que hoy no existen.*

### DFD del objetivo del refactor

```mermaid
flowchart LR
    META([Meta]) -->|"R1 notificacion firmada"| CF([Cloudflare edge])
    ATK([Atacante]) -->|"R1 HTTPS"| CF
    CF -->|"R2 tunel, solo el webhook"| WH
    subgraph SRV [TB2: servidor midgard]
      WH[Webhook] -->|"R3 evento, usuario webhook"| BUS
      subgraph TB7 [TB7: red Docker bifrost, TLS y un usuario por servicio]
        BUS[(Bifrost: streams en disco cifrado)]
        KV[(KV contexto)]
      end
      BUS -->|"R4 evento"| ING[Worker de ingesta]
      ING -->|"R5 mensaje identificado"| BUS
      GES[API de gestion] -->|"R9 supresiones"| BUS
      AND[Andvari]
    end
    WH -.->|"R6 App Secret"| AND
    ING -->|"R7 TLS: contacto e historial"| DB[(MongoDB Atlas)]
    GES -->|TLS| DB
    BUS -->|"R8 mensajes por particion"| MOTOR([Workers del motor])
    MOTOR -->|"R10 contexto cifrado"| KV
    MOTOR -->|"R11 configuracion, una clave por worker"| GES
    MOTOR -->|"R12 respuesta enviada"| BUS
    WH -.->|"R13 spans"| OTEL([Colector de trazas])
    ING -.-> OTEL
    MOTOR -.-> OTEL
```
*Eje comportamiento · DFD STRIDE · Gate 1 del refactor. Frontera nueva TB7: la red de Bifröst, donde cada servicio
entra con su usuario y sus sujetos. Las trazas (R13) cruzan hacia un colector que todavía no está decidido. Los
flujos R* se analizan en T22–T31.*

## Análisis STRIDE

| ID | Elemento | STRIDE | Amenaza | Control | Estado | Traza |
|---|---|---|---|---|---|---|
| T1 | Flujo 2/3 | S | Un tercero envía una notificación falsa para inyectar mensajes en la cola | Firma `X-Hub-Signature-256` sobre el cuerpo crudo, con el App Secret del bot; sin secreto se rechaza | Construido | RS03, ADR-0004, ADR-0007 |
| T2 | Gateway, flujo 9 | E, I | Un cliente lee o modifica bots, contactos o credenciales de otro usando su UUID | `clientId` en cada documento y capa de repositorio que lo exige; lo ajeno responde `404` | Construido | RS01, ADR-0012 |
| T3 | Base, flujo 5 | I | Una copia de la base expone números, tokens de Meta y App Secret | Cifrado de esos campos con la clave de datos del cliente e índice ciego para buscar por número | Construido | RS05, RS06, ADR-0010 |
| T4 | Flujo 3 | I, E | La gestión o la documentación de la API quedan alcanzables desde internet | El webhook y la gestión son dos servicios y el túnel publica solo el del webhook; los dos escuchan en loopback; la documentación solo en `development` | Construido sin desplegar (ADR-0018) | RS09, ADR-0008, ADR-0018 |
| T5 | Flujo 2/3, 9 | S | Fuerza bruta contra el token de verificación o las credenciales de gestión | Comparación en tiempo constante del token de verificación; para la gestión no hay secreto que adivinar, sino una firma `ES256`; límite de tasa en el borde | Parcial: `/oauth/token` tiene límite por origen, por fallos y por clave, en Cloudflare y en `hive-api` (ADR-0028); el webhook, todavía no | RS02, RS04, RS10 |
| T6 | Gateway | I | Los logs contienen números, nombres y contenido de mensajes | Sin datos personales en ningún nivel de log | Construido: nada de lo que envía quien llama, ni de lo que Meta envía sobre una persona, se escribe en el log; de un fallo, su tipo y su línea, nunca su texto. Lo comprueba `tests/unit/test_logs_and_errors.py` | RS07 |
| T7 | Flujo 2/3, 6 | T | Una notificación legítima se entrega o se reenvía dos veces y se procesa dos veces | Identificador de Meta en el sobre y deduplicación en el motor durante 24 horas | Parcial: el gateway publica el identificador de Meta en `messageId`; el motor no deduplica todavía | RNF04, ADR-0011 |
| T8 | Flujo 9 | S | Alguien fabrica un token de gestión, o captura uno válido y lo reenvía | JWT firmado por el cliente dentro de un JWE; caducidad de 60 segundos y un solo uso | Construido | RS02, ADR-0005, ADR-0009 |
| T9 | Flujo 2/3 | D | Una ráfaga de notificaciones agota el worker o llena la cola | Límite de tasa en el borde; tope de memoria y CPU del contenedor; caducidad de los mensajes | Parcial: solo el tope del contenedor | RS10, RNF03, RNF05 |
| T10 | Flujo 14 | E, T | Un usuario final escribe instrucciones para que el LLM invoque herramientas del cliente | Guardrails de entrada y salida del LLM tratada como no confiable, en el motor | Parcial: según la documentación del motor; no verificado aquí | RS16 |
| T11 | Gateway, motor | E, I | Un cliente define un intent cuya llamada a API apunta a la red interna | Validar el destino de `ApiCall` al guardarlo y al ejecutarlo: solo HTTPS y sin direcciones internas | Mitigado en el gateway (I7): solo `https`, host público escrito, sin usuario ni `Host`; al guardar y al entregar las herramientas. Falta la comprobación del worker de enlace al llamar (resolver y comprobar cada dirección) | RS16 |
| T12 | Flujo 9 | I, E | El motor, o quien robe su credencial, obtiene los tokens de Meta de todos los clientes | Una clave del motor por cliente, con alcance de solo lectura sobre los bots de ese cliente, cada entrega de tokens registrada y clave rotable | Construido en el gateway; el motor no lo usa todavía, y dónde custodia sus claves es cosa de su repositorio | RS06, ADR-0003, ADR-0016 |
| T13 | Cola, flujo 6/8 | I, T | Quien entre en la red del broker lee o altera los mensajes | Red Docker privada y un usuario del broker por servicio; cifrado del cuerpo en una segunda fase | Parcial: la red y el broker existen; el usuario del gateway y el cifrado, no | RS08, ADR-0011 |
| T14 | Flujo 11 | T | Una dependencia vulnerable o una imagen manipulada llegan a producción | Imagen base por digest; dependencias fijadas con hashes; análisis de dependencias, de secretos y estático en el pipeline; SBOM | Parcial: falta el SBOM | RS12 |
| T15 | Secretos | I | Se filtran el `.env` del servidor, la clave privada del gateway o la clave maestra | Fuera del repo y de la imagen, permisos restringidos, y nunca impresos al diagnosticar | Parcial: la clave privada del gateway y la credencial con la que entra en Andvari son ficheros del servidor, fuera del repo y de la imagen; la clave maestra vive en Andvari y no sale de él | RS13, ADR-0008, ADR-0010 |
| T16 | Flujo 6 | D | El broker, la base de datos o el servicio de claves no están disponibles y el mensaje se pierde con un `200` a Meta | Publicación confirmada; `503` si falta algo que el servicio necesita | Construido. Una caída de más de una hora da de baja la suscripción de Messenger, y nada lo vigila | RNF02, ADR-0006, ADR-0015 |
| T17 | Base, cola, logs | I | Un interesado pide la supresión y sus datos siguen en la cola, los logs o los respaldos | Borrado por contacto, caducidad en la cola, logs sin datos personales y caducidad de los respaldos a los 365 días | Parcial: el contacto, el bot y el cliente se borran de la base y la cola caduca a las 24 horas. Los logs ya no llevan datos personales, y cada borrado se apunta en un registro con el que se repite tras restaurar un respaldo. Queda lo que guarde el motor; y en los respaldos, la retención de 365 días está decidida pero sin configurar ni comprobar en Atlas | RF09, RF10, RNF05, ADR-0014 |
| T18 | Gateway | I | Un error devuelve detalles internos en la respuesta | Respuesta de error genérica y fallo cerrado | Construido: con un fallo forzado en cada ruta de gestión, la respuesta es un `500` sin el texto del fallo; la respuesta a una petición mal formada no repite lo enviado (H12) | RS15 |
| T19 | Flujo 11/12 | T, E | Un commit malicioso en `main` se construye y se despliega solo | `main` solo admite pull requests, desde ramas permitidas; las pruebas condicionan la imagen; rollback si falla la sonda de vida | Construido; el despliegue en el servidor está sin ejercitar | RF11, ADR-0008 |
| T20 | Gateway | R | No se puede saber qué cliente hizo qué | Registro de eventos de seguridad atribuido al cliente autenticado | Parcial: se registran los rechazos, y cada borrado queda en el log y en el registro de supresiones | RS14 |
| T21 | Flujo 2/3 | D, T | Un cuerpo malformado o enorme provoca errores o consume memoria | Esquema estricto de la notificación y límite de tamaño | Construido en el servicio: ninguna petición pasa de 3 MiB, el cuerpo del webhook no se lee sin firma válida, y una notificación tiene que traer su tipo y sus entradas. El límite en el borde sigue sin desplegar | RS11, ADR-0015 |
| T22 | Bifröst, R3/R5/R9/R12 | S, T | Un servicio comprometido publica en un sujeto ajeno, por ejemplo mensajes como si los hubiera verificado el webhook, o lee uno que no le toca | Un usuario por servicio con lista `allow` de sus sujetos, TLS y credenciales en Andvari | Pendiente | RS20, ADR-0019 |
| T23 | Bifröst | I | Una copia del disco de midgard expone los eventos, con el contenido de los mensajes y los números | Almacén cifrado con `encryption_key` en Andvari; cola de trabajo que borra al confirmar; 7 días como máximo | Pendiente | RS08, ADR-0019, ADR-0020 |
| T24 | Bifröst, R3 | D | Bifröst cae o se llena el disco, y el webhook no puede publicar | El webhook responde `503` y Meta reenvía; `max_file_store` acotado; Gjallarhorn vigila Bifröst | Pendiente | RNF02, ADR-0020, ADR-0021 |
| T25 | Bifröst | T, D | Un corte de luz pierde mensajes que el webhook ya confirmó a Meta | `sync_interval: always`: `fsync` antes de cada acuse | Pendiente | ADR-0019 |
| T26 | Ingesta, R4/R8 | T | Meta reintenta un mensaje ya procesado, o dos mensajes de una conversación se procesan a la vez y se responden al revés | Índice único del historial por bot, sentido e identificador de Meta; 16 particiones por conversación con un mensaje en vuelo | Pendiente | RNF04, RNF08, ADR-0017, ADR-0021 |
| T27 | Ingesta, R7 | D | La base cae más tiempo del que el bus guarda los eventos, y se pierden mensajes por los que Meta ya recibió `200` | Retención de 7 días en `WEBHOOK`, la misma que reintenta Meta; alerta a los 15 minutos sin confirmar | Pendiente | ADR-0021 |
| T28 | R13 | I | Un dato personal viaja en un atributo de una traza y lo lee quien tiene acceso al colector | Las trazas siguen la regla de los logs, con una prueba como la de los logs | Mitigado (I5, 2026-10-19): `shared/tracing` admite una lista cerrada de atributos, con la forma de cada valor; no hay instrumentación automática ni excepciones en los spans; lo prueba `tests/unit/test_traces.py` | RS19 |
| T29 | Gestión, R11 | E, I | La credencial robada de un worker alcanza rutas que ese worker no necesita, como los tokens de Meta | Una clave por cliente y por worker, con sus rutas | Mitigado en la API (I6a): `WORKER_ROUTES`; las claves sin worker de ADR-0016 siguen hasta que el motor se parta, con aviso en cada uso. En el bus, pendiente (I6c) | RS21, ADR-0022 |
| T30 | Gestión, KV | I | La conversación exportada, o el contexto, llega a otro cliente o a otra conversación | `clientId` en el historial y capa de repositorio; clave del contexto con cliente, bot y contacto; clave de Transit derivada por cliente | Pendiente | RS01, RF14, ADR-0012, ADR-0017 |
| T31 | KV | I | Quien lea el bucket de contexto lee el resumen y las últimas vueltas | Cifrado con una clave de Transit propia del coordinador; solo su usuario alcanza `$KV.contexto.>` | Pendiente | RS20, ADR-0017 |

## Amenazas priorizadas (DREAD)

Puntuación de 1 a 10 por criterio, con los controles tal como están **hoy**. La puntuación es la media.

| ID | Amenaza | Daño | Reproducible | Explotable | Afectados | Descubrible | Score |
|---|---|---|---|---|---|---|---|
| T2 | Acceso cruzado entre clientes | 9 | 9 | 7 | 9 | 6 | 8,0 |
| T4 | Gestión expuesta en internet | 9 | 9 | 6 | 9 | 7 | 8,0 |
| T3 | Fuga de la base | 9 | 8 | 4 | 10 | 5 | 7,2 |
| T8 | Token fabricado o reenviado | 8 | 8 | 5 | 8 | 5 | 6,8 |
| T12 | Tokens al alcance del motor | 9 | 7 | 4 | 10 | 4 | 6,8 |
| T6 | Datos personales en logs | 6 | 9 | 5 | 8 | 5 | 6,6 |
| T14 | Cadena de suministro | 8 | 6 | 5 | 9 | 5 | 6,6 |
| T9 | Inundación del webhook | 5 | 8 | 6 | 7 | 6 | 6,4 |
| T10 | Prompt injection | 7 | 7 | 6 | 5 | 6 | 6,2 |
| T11 | SSRF por intents | 8 | 7 | 5 | 6 | 5 | 6,2 |
| T15 | Secretos del servidor | 9 | 5 | 3 | 10 | 3 | 6,0 |
| T17 | Supresión incompleta | 6 | 9 | 3 | 7 | 5 | 6,0 |
| T21 | Entrada malformada | 4 | 8 | 6 | 5 | 6 | 5,8 |
| T13 | Lectura de la cola | 7 | 6 | 3 | 8 | 4 | 5,6 |
| T7 | Duplicados | 4 | 8 | 5 | 5 | 5 | 5,4 |
| T19 | Despliegue malicioso | 9 | 3 | 2 | 10 | 3 | 5,4 |
| T18 | Errores con detalles | 3 | 8 | 6 | 3 | 6 | 5,2 |
| T5 | Fuerza bruta | 6 | 5 | 3 | 6 | 5 | 5,0 |
| T20 | Sin auditoría por cliente | 5 | 9 | 2 | 6 | 3 | 5,0 |
| T1 | Notificación falsa | 7 | 2 | 2 | 6 | 5 | 4,4 |
| T16 | Mensaje perdido | 5 | 2 | 2 | 5 | 3 | 3,4 |
| T22 | Sujeto ajeno en el bus | 8 | 6 | 4 | 9 | 4 | 6,2 |
| T23 | Copia del disco del bus | 8 | 6 | 3 | 9 | 4 | 6,0 |
| T25 | Acusados perdidos en un corte | 6 | 7 | 2 | 7 | 4 | 5,2 |
| T26 | Duplicados y desorden | 4 | 9 | 5 | 6 | 6 | 6,0 |
| T27 | Caída larga de la base | 5 | 3 | 2 | 8 | 3 | 4,2 |
| T28 | Datos en trazas | 6 | 8 | 4 | 8 | 4 | 6,0 |
| T29 | Credencial de worker | 9 | 6 | 4 | 7 | 4 | 6,0 |
| T30 | Historial a quien no es | 8 | 5 | 4 | 6 | 4 | 5,4 |
| T31 | Lectura del contexto | 7 | 5 | 3 | 7 | 3 | 5,0 |
| T24 | Bifröst caído o lleno | 4 | 6 | 3 | 8 | 5 | 5,2 |

T2 y T4 puntúan tan alto porque dependen de algo que todavía no existe: con un solo operador y la gestión en red
privada ninguna de las dos se puede explotar, pero bastan un segundo cliente o una regla de túnel mal puesta. T1 y
T16 quedan al final porque sus controles están construidos y probados. T22 a T31 son las amenazas del refactor,
puntuadas con los controles del diseño sin construir; T26 es casi seguro sin ellos, con reintentos de días y ocho
workers asíncronos.

```mermaid
quadrantChart
    title DREAD - Gateway BaaS, con los controles de hoy
    x-axis Baja probabilidad --> Alta probabilidad
    y-axis Bajo impacto --> Alto impacto
    quadrant-1 Atender ya
    quadrant-2 Monitorear
    quadrant-3 Aceptar
    quadrant-4 Planear
    T2 acceso cruzado: [0.72, 0.92]
    T4 gestion expuesta: [0.68, 0.88]
    T3 fuga de la base: [0.45, 0.95]
    T12 tokens y motor: [0.42, 0.86]
    T8 token reenviado: [0.55, 0.78]
    T6 datos en logs: [0.62, 0.6]
    T14 suministro: [0.5, 0.82]
    T11 SSRF: [0.52, 0.72]
    T9 inundacion: [0.6, 0.5]
    T17 supresion: [0.48, 0.62]
    T7 duplicados: [0.55, 0.38]
    T19 despliegue: [0.15, 0.9]
    T1 notificacion falsa: [0.12, 0.66]
    T16 mensaje perdido: [0.1, 0.45]
    T22 sujeto ajeno: [0.4, 0.8]
    T26 duplicados y desorden: [0.75, 0.45]
    T28 datos en trazas: [0.6, 0.62]
    T29 credencial de worker: [0.42, 0.84]
    T27 caida larga de la base: [0.2, 0.55]
```
*Eje trazabilidad · DREAD · Gate 1.*

## Hallazgos del diseño

Defectos que salieron al contrastar el código con el diseño. H1 a H4, H7 a H9, H12 y H13 se comprobaron ejecutándolo; H10, solo en el sobre de la cola; H11, con un servicio de claves simulado.

| ID | Hallazgo | Impacto | Estado |
|---|---|---|---|
| H1 | **Los valores por defecto se calculan una vez, al importar.** Los campos de las entidades usan `default=uuid4()` y `default=datetime.now()`, con paréntesis: el valor queda fijado al arrancar. Hay 28 campos así | `createdAt` y `statusDate` de todos los documentos guardan la hora de arranque del proceso, no la de creación. El `_uuid` de los mensajes de la cola es el mismo en todos. Los demás identificadores se salvan porque el código los asigna de forma explícita | **Corregido** en la base del Gate 2: los valores por defecto se calculan por objeto, con pruebas que lo exigen |
| H2 | **`metadata` de WhatsApp sale mal serializado** hacia la cola: una lista de pares en vez de un objeto | El motor no puede leer `phone_number_id`, que necesita para responder | **Corregido**: `metadata` viaja como objeto, y el contrato rechaza la lista |
| H3 | **El motor rechaza los mensajes de Messenger** que publica el gateway y **descarta el `botId`** | Messenger no funciona de extremo a extremo; el motor no puede pedir la configuración del bot | Abierto; ADR-0011, en el repositorio del motor |
| H4 | **`GET /chat/{bot_id}/info` devuelve los tokens de Meta** | Quien se autentique como dueño del bot los lee; el aislamiento impide que los lea otro cliente | **Corregido**: `/info` ya no devuelve los tokens de Meta, solo cuáles están cargados |
| H5 | **El contrato OpenAPI no declara ningún esquema de seguridad**: la autenticación se hace en dependencias que OpenAPI no ve | Quien lea el contrato no sabe que hace falta credencial; las herramientas de análisis tampoco | **Corregido** con la autenticación por cliente: el contrato declara el token en todas las rutas que lo piden. La firma del webhook sigue sin aparecer, porque se comprueba en una dependencia |
| H6 | **Los valores por defecto y los ejemplos de los DTO son aleatorios**, generados con Faker al importar | La documentación de la API cambia en cada arranque. El contrato versionado los omite por eso | Abierto |
| H7 | **De una notificación con varios mensajes no salían todos.** Con varias entradas, o con varios eventos de Messenger, solo se publicaba lo último. Varios mensajes de WhatsApp en un mismo cambio viajaban juntos en un solo sobre, del que el motor lee el primero | Los demás se perdían con un `200`, sin rastro. Meta puede agrupar varios mensajes en una notificación | **Corregido**: un sobre por cada mensaje de la notificación |
| H8 | **El filtro del eco de Messenger no filtraba nada**: buscaba `is_echo` en un objeto que nunca lo contiene | El eco de lo que envía la propia página seguía el camino de un mensaje de una persona: con una Graph API simulada que devuelve un perfil, se publica y su remitente queda guardado como contacto. No está comprobado qué responde Meta al pedirle el perfil de una página. Con el motor respondiendo, el bot se contestaría a sí mismo | **Corregido**: el eco no se publica ni crea contacto |
| H9 | **Un fallo al procesar que no sea del broker ni del servicio de claves acaba en `200`**: la base de datos caída al buscar el contacto, o la Graph API sin responder para un contacto nuevo de Messenger | Meta da el mensaje por entregado y no lo reenvía: se pierde. Contradice ADR-0006. En una notificación con varios mensajes se pierden ese y los siguientes | **Corregido** con [ADR-0015](../00-project/adr/0015-que-se-responde-a-meta.md): la base de datos caída responde `503` y Meta reenvía; sin el perfil de la Graph API el mensaje se publica sin el nombre; y un mensaje que falla por sí mismo se descarta con un error en el log, sin llevarse a los demás |
| H10 | **Las fechas de los documentos se guardan en hora local sin zona**, y MongoDB las toma por UTC | En una máquina que no esté en UTC quedan desplazadas tantas horas como su desfase. La imagen corre en UTC, así que en el despliegue no ocurre | Abierto en las entidades guardadas; **corregido** en el sobre de la cola |
| H11 | **Borrar la clave de datos de un cliente no deja ilegibles sus datos en los respaldos**, al contrario de lo que cuenta ADR-0010. La clave envuelta se guarda en la misma base que los datos, y la clave maestra, una sola derivada por cliente, la sigue abriendo. Lo que no está cifrado (nombres, identificador de Messenger, reglas e intents) no depende de ninguna clave | Quien conserve un respaldo anterior a la baja y pueda usar el servicio de claves lee los números y las credenciales de un cliente dado de baja. La supresión en respaldos depende solo de que el respaldo caduque | **Aceptado** con [ADR-0014](../00-project/adr/0014-supresion-en-respaldos-por-caducidad.md): la supresión en respaldos se cumple por caducidad, a los 365 días como mucho. Falta configurar y comprobar esa retención en Atlas, y el registro de supresiones para repetirlas tras una restauración |
| H12 | **La respuesta a una petición mal formada repetía lo enviado.** El `422` devolvía el valor rechazado: un App Secret demasiado largo, o de otro tipo, volvía en la respuesta | Una credencial o un número de teléfono acababan en la respuesta y en lo que la registre por el camino | **Corregido**: el `422` dice dónde está el error y por qué, sin el valor |
| H13 | **El log de accesos del servidor escribía cada petición con su query string.** Uvicorn lo trae activo, y la aplicación no lo desactivaba: salía también en `production` | El token de verificación que Meta envía en `hub.verify_token` quedaba escrito en el log del contenedor, junto con la dirección de quien llama | **Corregido**: el log de accesos está apagado en la configuración de la aplicación y en la imagen; cada petición deja una línea con la ruta tal como está declarada, sin query string |
| H14 | **Las llamadas al servicio de claves y a MongoDB bloquean el bucle de eventos.** Se hacen de forma síncrona dentro de funciones asíncronas (RD-05 de la [revisión dual](../03-implementation/revision-dual-gate-2.md)) | Si Andvari o la base tardan, se paran todas las peticiones del proceso, `/health` incluido, y Meta ve plazos vencidos en mensajes que no tenían nada que ver | Aceptado el 2026-10-18 al cerrar el Gate 2, con una condición que ya se cumple: el webhook y la API de gestión corren por separado ([ADR-0018](../00-project/adr/0018-webhook-y-gestion-por-separado.md)), así que una petición de gestión lenta no para el webhook. Dentro de cada proceso lo resuelven el worker de ingesta del [plan de la topología](../00-project/plan-topologia-de-workers.md) y un incremento propio en la gestión Desde I4 (2026-10-19), el webhook no llama a la base por cada mensaje: con el bot en memoria no hace ningún viaje, y responde en 8,3 ms de mediana. Las llamadas síncronas siguen en la ingesta, que trata las notificaciones una a una y no tiene a nadie esperando su respuesta |
| H15 | **Los gates de seguridad no son obligatorios para fusionar ni para publicar** (RD-01) | Un hallazgo alto de CodeQL, un secreto o una dependencia vulnerable llegan a `main` y a la imagen si alguien fusiona con el check en rojo | Corregido el 2026-10-18: `Protect-MAIN` y `Protect-DEVELOP` exigen los cinco checks para fusionar, y `Protect-TAGS` impide mover o borrar las etiquetas de versión. El 2026-10-28 se cerró un hueco que abrió el monorepo (ADR-0023): los jobs `test` e `image` del gateway y del motor se llamaban igual, y uno en verde cumplía la regla aunque el otro estuviera en rojo. Además, `docs` no se exigía. Ahora cada job tiene su nombre, los dos workflows corren en todo PR y se exigen ocho checks |

## Controles y trazabilidad

Cada amenaza tiene su control en un requisito del PRD y, donde hubo que elegir, en una ADR: son las columnas
"Control" y "Traza" de la tabla STRIDE. El mapeo general a OWASP Top 10:2025 está en
[`.ai-dlc/owasp-mapping.md`](../../.ai-dlc/owasp-mapping.md), todavía sin adaptar a este servicio.

Orden de construcción que se desprende de la prioridad:

1. **Registro de clientes y aislamiento** (T2): ADR-0012. Sin él no puede haber un segundo cliente.
2. **Autenticación con token** (T8, T12, T5): ADR-0005 y ADR-0009. Habilita publicar la gestión (T4).
3. **Cifrado de datos por cliente** (T3, T17, T15): ADR-0010.
4. **Contrato de la cola, versión 1** (T7, T13, H1, H2, H3): ADR-0011.
5. **Higiene**: logs sin datos personales (T6), errores sin detalles (T18), esquema estricto (T21), cadena de
   suministro (T14), validación de destinos de los intents (T11).

Para el refactor, en el orden de los incrementos del plan: Bifröst con sus usuarios, TLS, cifrado y `fsync` (T22,
T23, T25, en I0); la ingesta con el historial, la deduplicación y las particiones (T26, T27, T30, en I3 e I4); las
trazas (T28, en I5); y una credencial por worker (T29, en I6).

## Verificación prevista (Gate 3)

| Amenaza | Prueba |
|---|---|
| T1, T16 | Ya cubiertas por `tests/unit/test_webhook_security.py` |
| T2 | Ya cubierta por `tests/unit/test_client_isolation.py` (todas las rutas con otro cliente: `404`), `test_tenant_repository.py` y `test_data_access_boundary.py` (nada consulta la base fuera del repositorio). Las rutas se recorren con tokens reales de dos clientes |
| T3 | Ya cubierta por `tests/unit/test_client_encryption.py`: se vuelca la base de pruebas y ningún número, token ni App Secret aparece en claro. `tests/integration` prueba el cliente del servicio de claves contra un OpenBao real |
| T4 | Desde fuera de la red, cualquier ruta que no sea `/chat/<uuid>` no responde |
| T7 | Publicar dos veces el mismo mensaje y comprobar que el motor lo procesa una |
| T8 | Ya cubierta por `tests/unit/test_authentication.py`: caducado, con `aud` ajeno, con `jti` repetido, con algoritmo no permitido, firmado con otra clave o en nombre de otro cliente; todos rechazados, y cada uno por la comprobación que le toca |
| T6 | Procesar un mensaje en nivel `DEBUG` y comprobar que el log no contiene el número ni el texto |
| Contratos | `tests/contract`: el OpenAPI versionado coincide con la aplicación, y el mensaje real de la cola cumple el AsyncAPI |
| T22 | Con el usuario de cada servicio, publicar en un sujeto ajeno y suscribirse a uno ajeno: Bifröst lo rechaza |
| T25 | Cortar la luz, o matar el contenedor, con publicaciones en curso: tras volver, ningún evento acusado falta |
| T26 | Publicar dos veces el mismo mensaje con días de diferencia: un solo mensaje en el historial y en `MENSAJES`; dos mensajes seguidos de una conversación llegan al motor en orden |
| T28 | Procesar un mensaje con la traza activa y comprobar que ningún atributo lleva el número ni el texto |
| Contratos v2 | `tests/contract/test_bus_contract.py` mantiene coherente `asyncapi-v2.yaml`; al construir I3, el mensaje real se valida contra él |
