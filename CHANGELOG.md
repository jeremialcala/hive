# Changelog

Todos los cambios notables de este proyecto se documentan en este archivo.

El formato está basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/),
y este proyecto se adhiere a [Versionado Semántico](https://semver.org/lang/es/).

## [Unreleased]

## [0.9.0] - 2026-10-30

### Añadido
- **Los logos de `assets/logos/` en la PWA y en la documentación** (ADR-0027).
  - **La PWA:** el lockup en la barra lateral, según el tema; el isotipo en la barra del teléfono; y el lockup
    vertical en la pantalla de entrada. Sustituyen al hexágono dibujado con CSS: según el brand book, el hexágono
    de `Cell` identifica bots y clientes, y no es el logo.
  - **Favicon y manifest:** la PWA tiene `hive-favicon.svg` y un manifest con los iconos de la app en PNG, `any` y
    `maskable` a 192 y 512. Antes, `/favicon.ico` y el manifest daban `404`.
  - **La documentación de la API** tiene el favicon.
  - **Las copias** son iguales byte a byte a `assets/logos/`, y lo comprueban sus pruebas.
- **La ficha del bot en la PWA** (HIG-121; RF23, RF24, RF26; las pantallas 05 a 08 del canvas).
  - **La cabecera:** el estado REG/ACT/ERR, el `bot_id` y la versión. En ERR dice qué hacer.
  - **Identidad:** el `cliente-admin` cambia la persona; guardar crea la versión siguiente y un rechazo de RS11 sale
    en su campo.
  - **Credenciales de Meta:** dice qué está cargado, sin mostrar nunca su valor, y solo deja cargarlo o reemplazarlo.
  - **Cerebro e intents:** las reglas y los intents, cada uno con su destino; se añaden, editan y borran con los
    destinos permitidos a la vista.
  - **Versiones:** la lista y lo que cambiaría al volver a una. «Volver a vN», solo para el `cliente-admin`; si algo
    ya no cumple las reglas de hoy, la ficha dice qué.
  - **El `cliente-analista`** lo ve todo en solo lectura.
- **El esqueleto de la PWA: barra lateral, cabecera de página, `TabBar` en el teléfono y tema** (HIG-126; el canvas
  «Pantallas de Hive»).
  - **La barra lateral:** 240 px y ocho secciones, con la tarjeta del cliente abajo (nombre, `client_id`, rol, «Salir»
    y el tema).
  - **En un teléfono:** la `TabBar` sustituye a la barra lateral (Inicio, Bots, Contactos, Más), y «Más» tiene el
    resto. A 390 px no hay desplazamiento horizontal.
  - **Cada página:** la ruta, el cliente como eyebrow, el título, una sola acción primaria y su cURL al pie.
  - **El tema:** oscuro por defecto. El claro se elige en la tarjeta, va en `data-theme` de la raíz y el navegador lo
    recuerda; los acentos son los mismos y el contraste del texto pasa de 4,5:1.
  - **El `cliente-analista`** no ve Personas, Credenciales ni Auditoría.
  - **`GET /client`:** el id y el nombre del cliente de quien llama, sin sus claves, para la tarjeta. El
    `cliente-analista` también lo lee.
- **Editar la persona y las reglas de un bot** (HIG-105 y HIG-106; RF23, RF24).
  - **La persona:** `PUT /chat/{bot_id}/persona` cambia el nombre, el rol, el tono y la descripción, los cuatro a la
    vez. `409` si es la que ya tiene.
  - **Las reglas:** `PUT` y `DELETE /chat/{bot_id}/rules/{rule_id}`. `GET /chat/{bot_id}/rules` trae sus ids en
    `ruleList`, y `POST /chat/{bot_id}/rules` devuelve las que añade, con su id.
  - **Versiones:** cada cambio es una versión con su autor, y el motor se entera por `gateway.bot.configured`. Una regla
    borrada por error vuelve al volver a la versión anterior, con el mismo id.
  - **Roles:** el `cliente-analista` las ve y recibe `403` al cambiarlas.

### Cambiado
- **La persona y las reglas tienen límites, los mismos al crear que al editar** (RS11; decisiones de Jeremi del
  2026-10-30).
  - **Las longitudes:** nombre, rol y tono de 1 a 100 caracteres en una línea; descripción hasta 2000; regla de 1 a
    1000; y 50 reglas por bot.
  - **Lo que se rechaza:** caracteres de control y las marcas con las que un modelo separa roles (`<|...|>`, `[INST]`,
    `<<SYS>>`, `<s>`, `</s>`), con un `422`.
  - **Sin valores de ejemplo:** `POST /bot` y `POST /chat/{bot_id}/rules` piden todos sus campos, y un campo de más es
    un `422`. Antes rellenaban lo que faltaba con valores de ejemplo.
  - **Volver a una versión** revisa también la persona y las reglas con los límites de hoy: la respuesta `422` dice qué
    no pasa (`persona`, `rules`, `intents`).

### Corregido
- **Un intent ya no se rellena con ejemplos** (HIG-142, RS11). Sin `actions`, `IntentRequest` le ponía la acción
  de ejemplo `get_order`, que llamaba a `api.example.com`, y sin `samplePhrases` tres frases inventadas.
  - **Sin ejemplos:** ahora pide lo que hace falta y deja neutro lo opcional.
  - **El texto que lee el modelo:** tiene límites y no admite caracteres de control ni marcas de rol: nombre de 100
    caracteres y en una línea, descripción de 1000, hasta 50 frases de 500 y hasta 10 acciones.
  - **Nombres:** el de cada acción es un nombre de herramienta, y no se repite.
  - **Las llamadas:** sus cabeceras y su consulta no admiten saltos de línea, y `authRef` es solo el nombre de un
    secreto.
  - **Un `handoff`** va sin `apiCall`, y una `api_call` lo lleva siempre.
  - **Volver a una versión** revisa los intents con este mismo modelo.
- **La PWA ya no pierde una acción al editar un intent.** Las acciones que no son una llamada a una API (un
  `handoff`) no tienen formulario en la ficha, y guardar las quitaba sin avisar. Desde HIG-142 `hive-api` acepta un
  `handoff` sin `apiCall`, y la ficha lo guarda como vino. En producción no hay ningún intent, así que no se perdió
  nada.
- **El README dice lo que piden hoy `POST /bot` y las reglas:** todos los campos, sin ejemplos y con los límites
  de RS11. Faltaban también en sus tablas las rutas de la persona, de editar y borrar reglas, de las versiones y
  `GET /client`.

## [0.8.0] - 2026-10-30

### Añadido
- **Versiones de la configuración de un bot, y volver a una** (HIG-101, RF26;
  [ADR-0035](docs/00-project/adr/0035-versiones-de-la-configuracion-del-bot.md)).
  - **Una versión por cambio:** crear el bot, añadir reglas y crear, editar o borrar un intent escriben cada uno una
    versión con la persona, las reglas y los intents, quién lo hizo y cuándo. Las credenciales de Meta y el
    `verifyToken` no se versionan.
  - **Las rutas:**
    - `GET /chat/{bot_id}/versions` las lista;
    - `GET /chat/{bot_id}/versions/{version}` dice qué cambió respecto de la anterior, o de `against`;
    - `POST /chat/{bot_id}/versions/{version}/restore` vuelve a una, como una versión nueva.
  - **Volver revisa los intents otra vez:** si un destino ya no se permite, la respuesta es `422` y no cambia nada
    (RS16, AB29).
  - **Roles:** el `cliente-analista` ve las versiones y no vuelve (`403`).
  - **Retención:** las versiones se guardan mientras exista el bot y se borran con él.
  - **Bots de antes:** un bot anterior a las versiones recibe, en su primer cambio, una con lo que tenía.
  - **El motor se entera enseguida:** la API publica `gateway.bot.configured` en Bifröst con el número de la versión, y
    el coordinador olvida la persona y las reglas que recordaba (higerotech/bifrost#9). `/rules` dice su `version`.
- **Hive vuelve solo tras un apagón de midgard, y Thor avisa si algo no vuelve** (HIG-116, RNF06;
  [arranque tras un apagón](docs/05-deployment/arranque-tras-apagon.md)).
  - **Cómo vuelve:** Docker arranca con el sistema y levanta los diez contenedores de Hive, que tienen
    `restart: unless-stopped`.
  - **Sin orden de arranque:** cada pieza espera a Bifröst y Andvari sin caer en bucle, y Andvari se desella solo con
    su clave de sello.
  - **`tests/unit/test_restart_after_outage.py`:** falla si un servicio de los compose de producción pierde su
    política, o si la página deja de nombrar algún contenedor.
  - **Thor vigila los contenedores de Hive:** desde el 2026-10-30, `THOR_CONTENEDORES` en midgard los incluye, y
    `THOR_UNIDADES` incluye `hive-vigia.timer`. Si falta uno, salta `ServicioDePlataformaCaido`.
  - **Probado con un reinicio real de midgard** el 2026-10-30: los 13 contenedores (10 de Hive y la infraestructura)
    quedaron sanos en 8 minutos, con 0 reinicios, y Andvari se deselló solo. RNF06 pasa a «Construido y probado».

## [0.7.0] - 2026-10-30

### Añadido
- **Mutation testing de los módulos de seguridad** (HIG-137, Gate 3;
  [mutation testing](docs/04-testing/mutation-testing.md)). Dice si las pruebas se enteran cuando cambia el código
  donde un fallo es un fallo de seguridad: `src/auth/`, la firma del webhook, el estado de un chatbot y el cifrado por
  cliente.
  - **La herramienta:** mutmut 3.8.0, configurado en `setup.cfg` y fijado en su propio lock,
    `requirements-mutation.lock`, que el workflow `Seguridad` audita como los demás.
  - **Dónde corre:** el workflow `mutation`, cada lunes sobre `main`, a mano, y en los pull requests que tocan esos
    módulos. Informa y no bloquea: deja la puntuación por módulo en el resumen del job
    (`.ai-dlc/scripts/mutation_score.py`).
  - **La puntuación:** del 84,8 % al 98,8 % sobre 977 mutantes. Ningún módulo estaba por debajo del 60 %, ni antes.
    Sobreviven 12: diez equivalentes y dos bordes de un límite de tamaño, cada uno con su motivo en el documento.
  - **Lo que sobrevivía y cambiaba la seguridad:**
    - una prueba DPoP se podía repetir en su último segundo si su jti se recordaba 55 s en vez de 65;
    - un token de una persona que dijera tener un worker del motor o el rol `service`;
    - un jti vacío o demasiado largo;
    - hacer sitio en los límites de `/oauth/token` podía olvidar al origen que sigue fallando.

    Ahora cada uno tiene su prueba.
  - **Más pruebas nuevas:**
    - los bordes de la vida de un token y de la ventana de una prueba DPoP;
    - el motivo de cada rechazo en el log y el texto de cada `422` de una clave;
    - que el índice ciego distingue campos y no cambia entre versiones;
    - el WARNING de cada movimiento del estado de un chatbot.
  - **Pruebas arregladas:** siete parametrizaciones tenían ids al azar (`uuid4()`) o que pytest no sabe volver a leer
    (una IPv6). Ahora llevan `ids=`.
- **DAST con OWASP ZAP** (HIG-90, Gate 3; [dast](docs/04-testing/dast.md)), en el job `dast · zap` del workflow
  `e2e`: en los PR a `main`, en los de `develop` que cambian la prueba de punta a punta o el despliegue, y a mano.
  - **Contra qué:** el stack de la prueba de punta a punta, con las imágenes de producción, en una red interna sin
    salida. ZAP va fijado por digest y no descarga add-ons.
  - **Qué escanea:** `hive-api` con su contrato OpenAPI y el webhook con el suyo, con escaneo activo, y el portal
    entero, con las rutas del login sembradas. Comprueba además que las 45 operaciones con token responden `401` sin
    él: ZAP no sabe firmar DPoP, y no pasa de ahí.
  - **Cuándo falla:** con un hallazgo High que `tests/e2e/zap/rules.tsv` no acepte como falso positivo, con su motivo.
    Medium, Low e Informational quedan en los informes HTML y JSON, adjuntos al job como `dast-zap`.
  - **El primer resultado,** el 2026-10-30: ningún High, Medium ni Low; cinco informativas.

### Seguridad
- **El resto de rutas de `hive-api` y el webhook tienen límite de peticiones** (HIG-139, RS10, A06 de la matriz
  OWASP). Hasta ahora solo lo tenían `/oauth/token` y los envíos.
  - **`hive-api`:** 300 peticiones por minuto por credencial, sea una clave o una persona. Además, un origen con 30
    respuestas `401` o `403` en 10 minutos espera, sin que se lea su cuerpo. El portal y el motor llaman desde
    midgard y solo cuentan por credencial: así, una persona no deja fuera a las demás.
  - **El webhook:** un origen con 20 peticiones en un minuto sin la firma de Meta, con una mala o con un verify token
    malo recibe `429` sin que se lea su cuerpo. Lo que trae una firma con la forma de la de Meta pasa siempre. Con dos
    workers, cada uno cuenta por su lado.
  - **La respuesta es la de `/oauth/token`:** `429` con `Retry-After`. `/health` no tiene límite.
  - **Dónde:** `src/auth/request_limits.py`, en memoria y con un tope de claves. La ADR-0028 sube a la 0.2.0 con la
    enmienda, y la matriz OWASP a la 1.4.0.
  - **Pruebas:** `tests/security/test_route_limits.py` y `tests/unit/test_request_limits.py`.

## [0.6.0] - 2026-10-30

### Seguridad
- **`hive-api` y el webhook mandan las cabeceras de una API JSON** (HIG-138, A02 de la matriz OWASP). Antes no
  mandaban ninguna; el portal y la documentación sí.
  - **Qué cabeceras:** HSTS (un año, con subdominios), `nosniff`, `Referrer-Policy: no-referrer`,
    `X-Frame-Options: DENY` y `Cache-Control: no-store`. Fuera de desarrollo, además
    `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`.
  - **Dónde:** en toda respuesta, también en un `404`, un `413` o un `500`. Las pone el interceptor de
    `src/service.py` sin pisar las que ya trae una ruta.
  - **Pruebas:** `tests/security/test_security_headers.py`. La matriz OWASP pasa a la 1.3.0.

### Añadido
- **El recorrido de la PWA, de punta a punta** (HIG-141, Gate 3; [e2e](docs/04-testing/e2e.md#el-recorrido-de-la-pwa)).
  Chromium, con Playwright, contra `hive-portal` construido del mismo commit, junto al gateway y al motor de la prueba
  de punta a punta.
  - **El login:** con un Zitadel falso, en `tests/e2e/fakes.py`, que da un ID token con `amr` `mfa`. Las personas
    entran por el camino real: `POST /people` las crea en Zitadel y su primer login las deja activas.
  - **El recorrido:** los bots, una plantilla con su vista previa, su aprobación al sincronizar con Meta y su envío a
    un contacto, seguido hasta que Meta lo acepta.
  - **Lo que comprueba además:** la cookie `__Host-hive-session` `HttpOnly`, la sesión sellada en Bifröst, el logout,
    ninguna violación de la CSP y que el `cliente-analista` no ve los botones de admin.
  - **Sus dependencias:** van en su propio lock, `tests/e2e/requirements.lock`, que `Seguridad` audita.
  - **Cuándo corre:** el workflow `e2e` instala Chromium, y en los PR a `develop` también corre si cambia el portal.
- **El objetivo de latencia de punta a punta, RNF10** (HIG-136, Gate 3;
  [medición](docs/04-testing/medicion-punta-a-punta.md)). Desde que el webhook recibe el mensaje hasta que Meta acepta
  la respuesta.
  - **El objetivo**, decidido por Jeremi el 2026-10-30: mediana de 10 s o menos y p95 de 20 s o menos, sobre todos los
    mensajes respondidos. Cuenta con 20 respuestas o más.
  - **`tests/performance/measure_end_to_end.py`:** el script que vivía suelto en midgard, ahora en el repositorio.
    Lee de Bifröst las últimas 24 horas de tráfico real y da la mediana y el p95 de la espera y de cada paso, con su
    veredicto. Imprime solo tiempos, y lo prueba `tests/unit/test_measure_end_to_end.py`.
  - **La primera medición** (2026-10-30, con 3 respuestas, que no cuentan todavía): mediana 9,5 s y p95 10,0 s. La
    ingesta tarda 1,8 s y el redactor 3,1 s.
- **La prueba de punta a punta del webhook a la respuesta** (HIG-135, Gate 3;
  [e2e](docs/04-testing/e2e.md)). Una persona escribe a un bot y recibe su respuesta, pasando por cada pieza de
  producción.
  - **Qué levanta:** las imágenes del gateway y del motor, construidas del mismo commit, con
    `ENVIRONMENT=production` y TLS. Bifröst va con su configuración y su aprovisionamiento reales, del repo
    `higerotech/bifrost`. OpenBao tiene TLS y la configuración de Andvari. Además, MongoDB y RabbitMQ, y un Meta y
    un LLM falsos (`tests/e2e/compose.yaml`).
  - **Cómo se prepara:** como lo haría un operador. Se registra la clave del operador y se da de alta un cliente,
    con su bot, sus credenciales y las claves del motor (`tests/e2e/conftest.py`).
  - **Qué comprueba:**
    - Meta recibe el texto del LLM desde el número del bot y con su token;
    - el historial guarda el mensaje y su respuesta;
    - el bot queda en `ACT`;
    - una firma con otro secreto da `403` y no llega nada.
  - **El workflow `e2e`** corre en los PR a `main` y a mano, y lee Bifröst con la clave de despliegue
    `BIFROST_DEPLOY_KEY`. Si falla, el informe lleva el log de cada servicio. En local tarda unos 30 s con las
    imágenes construidas.
  - La matriz OWASP pasa a la 1.2.0.

## [0.5.0] - 2026-10-30

### Seguridad
- **Una respuesta ya no devuelve el id ni el nombre que le llegaron** (HIG-134). Trece respuestas los interpolaban
  en su `message`, por ejemplo «This intent ' OR '1'='1 was not found»: diez `404` y tres `400` de bots e intents
  (`src/controller/chatbot.py`, `src/controller/intents.py`). Ahora el texto es fijo.
- **La suite de seguridad** (HIG-134, Gate 3), con 654 casos en `tests/security/test_hostile_input.py`:
  - **Qué prueba:** un id hostil en cada parámetro de la dirección de cada ruta del cliente, operadores de MongoDB
    donde se espera texto, una `Idempotency-Key` de 10 000 caracteres y una notificación firmada con 200 000
    niveles de anidamiento.
  - **Qué exige:** siempre un 4xx, nada que coincida y nada repetido.
  - **El marcador `security`:** `pytest -m security` corre esa suite junto a las pruebas unitarias de la matriz
    OWASP, 1 752 casos en total. La matriz pasa a la 1.1.0.

### Cambiado
- **La matriz OWASP describe lo que Hive hace de verdad** (`.ai-dlc/owasp-mapping.md` 1.0.0, HIG-133, Gate 3). Era la
  copia de la plantilla, «sin adaptar todavía».
  - **Qué trae:** A01–A10, cada uno con sus controles, la prueba que los verifica y la ADR o el RS que los decide. Al
    principio va un resumen con el estado de cada riesgo.
  - **Lo que falta** queda como hueco con su tarea: DAST, payloads hostiles, cabeceras en `hive-api`, límites en el
    resto de rutas, el pipeline del Gate 4 y las alertas.
  - **`tests/unit/test_owasp_mapping.py`** falla si una prueba, un fichero o una ADR citados dejan de existir, si falta
    uno de los diez riesgos o si vuelve la plantilla.

## [0.4.0] - 2026-10-30

### Seguridad
- **Los tokens de acceso van atados por DPoP a la clave que los pidió** (RFC 9449,
  [ADR-0033](docs/00-project/adr/0033-dpop-en-los-tokens-de-acceso.md), HIG-99; RS30). **Cambio incompatible:** el
  gateway, el motor, `ops.client`, `ops.engine_keys` y el cliente externo se despliegan juntos.
  - `POST /oauth/token` exige la cabecera `DPoP`, firmada por la misma clave que la aserción. Si no, `400
    invalid_dpop_proof`. El token sale con `cnf.jkt` y `token_type: DPoP`.
  - Cada ruta pide `Authorization: DPoP <token>` y una prueba nueva: firma, `htm`, `htu`, `iat` (60 s, 5 de margen y
    posterior al arranque), `jti` de un solo uso y `ath`, y que la huella sea la del token (`src/auth/dpop.py`).
    `Bearer` deja de valer. Un rechazo da `401` con `WWW-Authenticate: DPoP algs="ES256"`, sin decir qué falló.
  - **Los `jti`** se guardan en memoria, no en Atlas. Si se llena, rechaza en lugar de olvidar.
  - **`GATEWAY_URLS`:** las direcciones que valen como `htu` además de la audiencia. En midgard, `http://hive-api:5002`
    para el motor y `http://127.0.0.1:5002` para `ops`.
  - **El motor** firma una prueba por petición con la clave de cada worker para el cliente.
  - **`ops.client`** gana el comando `proof`.
  - **`shared.auth.client.dpop_proof`** es la referencia para un cliente.
  - **Pruebas:** `tests/unit/test_dpop.py`, con el token sin prueba, la prueba de otra clave, la repetida, la de otra
    ruta, otro método, otro host, otro token, fuera de su ventana o anterior al arranque, y las que no llevan la clave
    de la aserción. El test client firma la prueba de cada petición como un cliente (`tests/conftest.py`). El falso
    gateway del motor exige la prueba.
  - **Documentación:** la guía de autenticación (0.3.0), la descripción pública de la API, el README y el contrato
    OpenAPI.
- **Los controles obligatorios, sin ambigüedad (H15, RD-01).** Desde el monorepo (ADR-0023), el gateway y el motor
  tenían jobs llamados igual, `test` e `image`, y los rulesets exigían «`test`» sin decir de cuál. En un PR que
  tocara los dos, uno en verde cumplía la regla aunque el otro estuviera en rojo. Además, `docs` no se exigía.
  Ahora cada job tiene su nombre (`gateway · test`, `engine · test`…), `ci` y `engine` corren en todo pull request,
  y `Protect-DEVELOP` y `Protect-MAIN` exigen los ocho.
- **[ADR-0028](docs/00-project/adr/0028-limite-de-peticiones-en-oauth-token.md) (aceptada): límite de peticiones en
  `/oauth/token`**, en el borde y en la aplicación (decisiones de Jeremi del 2026-10-28; RS10, parcial).
  - En `hive-api` (`src/auth/token_limits.py`) hay tres límites: 20 peticiones por minuto por origen, 10 aserciones
    rechazadas en 10 minutos por origen, que lo bloquean, y 30 tokens por hora para cada clave de cliente.
  - Se comprueban antes de descifrar nada. Pasarlos da `429` con `Retry-After` y `temporarily_unavailable`.
  - Los fallos se cuentan por origen, nunca por cliente, para que nadie pueda bloquear a un cliente en su nombre.
  - El origen es `CF-Connecting-IP`, o la dirección del par en las redes privadas. Una IPv6 cuenta por su /64.
  - En Cloudflare, una regla de 10 peticiones por IP cada 10 segundos (`docs/03-implementation/autenticacion.md`).
  - El contrato OpenAPI declara el `429`. DPoP queda para otra tarea.
- `cloudflared` del túnel (`hive-tunel`) pasa de 2026.10.0 a 2026.10.4, fijado por digest: VULN-176088 y
  TUN-10994. Desplegado a mano en midgard el 2026-10-26.

### Añadido
- **Releases continuas** (HIG-35; decisiones de Jeremi del 2026-10-30;
  [releases](docs/05-deployment/releases.md)). Esta es la primera.
  - **Una release por tanda:** cada tanda que se despliega se corta antes como versión SemVer: MENOR si añade algo,
    PARCHE si solo corrige.
  - **Producción es la última release:** se construye y se despliega en midgard desde su etiqueta `vX.Y.Z`, y las
    imágenes llevan la versión.
  - **Las versiones no esperan a los gates:** la 0.4.0 sale con el Gate 3 abierto. Su documento
    (`.ai-dlc/gates/gate-3-testing.md`) dice con cifras qué está y qué falta.
  - **`.ai-dlc/scripts/cortar_release.py`:** pasa `[Unreleased]` a la versión con su fecha y pone al día los enlaces.
    No escribe nada si la versión ya existe, si no es mayor que la última o si no hay nada que publicar. Con
    `--notas` da las notas de la release de GitHub. Lo prueba `tests/unit/test_release_cut.py`.
- **El estado del bot lo mueve lo que dice Meta** (HIG-74, HIG-75; decisiones de Jeremi del 2026-10-30). Antes, ningún
  código lo cambiaba: los 2 bots de producción seguían en `REG` aunque recibían mensajes.
  - **`REG` → `ACT`:** cuando Meta verifica el webhook del bot, o le envía una notificación firmada. Lo segundo activa
    también los bots que se verificaron hace tiempo.
  - **`REG` o `ACT` → `ERR`:** cuando Meta rechaza el token del bot (0 o 190) o sus permisos (10 o 131005), al enviar
    o al gestionar plantillas.
  - **`ERR` → `ACT`:** al cargar un token nuevo, o cuando Meta acepta un envío o una llamada sobre plantillas.
  - **Lo que no lo mueve:** nada saca un bot de `LOK` o `DIS`, y una verificación nueva no lo saca de `ERR`, porque
    prueba el webhook y no el token. Una firma mala tampoco cuenta: la puede enviar cualquiera.
  - **Cómo se escribe:** cada paso es una actualización condicional (`TenantRepository.move_bot`) y deja un `WARNING`.
    La regla está en `src/controller/bot_state.py`.
  - **El webhook escribe una sola cosa**, el paso a `ACT` al verificar
    ([ADR-0021](docs/00-project/adr/0021-el-webhook-solo-publica.md), enmienda 0.2.0). Si esa escritura falla, Meta
    recibe igual su `challenge`, y la ingesta activa el bot con su primer mensaje.
  - **`GET /chat/{bot_id}/info` y `GET /bots`** dicen el estado por su nombre (`status`) y desde cuándo
    (`statusDate`).
  - **El notificador del motor:** cuando se rinde con un pedido del cliente, el aviso `EnvioFallido` lleva el último
    código de Meta. El estado del envío dice entonces `token_rejected` o `permission`, y la PWA lo explica.
  - **Pruebas:**
    - `tests/unit/test_bot_state.py`, con 30 casos;
    - tres casos nuevos en `tests/unit/test_conversations.py`;
    - el caso del token en `engine/tests/unit/test_worker_salientes.py`.

    Las pruebas del webhook usan ahora un bot en `ACT`, como el que ve Meta.
- **La PWA del cliente** (HIG-87, [ADR-0031](docs/00-project/adr/0031-mensajes-salientes.md),
  [ADR-0027](docs/00-project/adr/0027-el-mapa-de-estilo-de-hive.md); RF29, RF30). Va en `portal/src/static/`,
  sin paso de build: React 18 y htm como UMD, módulos ES y los componentes del sistema de diseño. Todo sale del
  portal, con la CSP en `'self'`.
  - **Las pantallas:** los bots del cliente; las plantillas de cada bot, con su estado y el motivo de un rechazo;
    sus contactos, sin el número; y la conversación de cada uno, con la ventana de 24 horas y el consentimiento.
  - **Las plantillas:** el `cliente-admin` las crea, edita y borra. El editor tiene vista previa y comprueba antes
    de enviar a Meta el nombre, los valores sin huecos, un ejemplo por valor y los botones.
  - **El envío:** texto con la ventana abierta, solo el `cliente-admin`. Una plantilla aprobada, cualquiera de los
    dos roles. Cada envío lleva una `Idempotency-Key` nueva. La PWA sigue el pedido hasta que Meta lo acepta o lo
    rechaza, y el rechazo sale en palabras, con el código de Meta.
  - **El rol** sale de la sesión: un `cliente-analista` no ve los botones que le darían `403`.
  - **Cada pantalla** lleva el cURL de su llamada a `hive-api`.
  - **Lo que no es nuestro**, con sus licencias: React, ReactDOM y htm, del registro de npm con su `integrity`
    comprobado; las fuentes Geist y JetBrains Mono, bajo la OFL; y la copia del sistema de diseño.
  - **El portal** declara `text/javascript` para `.js` y `font/woff2` para `.woff2`. Python 3.12 no conoce el
    segundo, y con `nosniff` un módulo que llegara como `text/plain` no se ejecutaría.
  - **Pruebas:** todo lo que piden la página, sus módulos y sus estilos sale del portal con su tipo. La copia del
    sistema de diseño es la de `assets/design-system/`.
  - **Usa la primera parte (#130) y la segunda (#131)**: `GET /bots`, la conversación y el estado de un envío.
- **Lo que la PWA lee de un cliente** (HIG-87, primera parte).
  - **`GET /bots`:** los bots del cliente y qué credenciales de Meta tiene cargadas cada uno, nunca su valor.
  - **`GET /chat/{bot_id}/contacts/{contact_id}/conversation`:** los últimos 50 mensajes del contacto, cada uno con su
    origen (el motor, o el cliente y quién), y la ventana de 24 horas de Meta: si está abierta y hasta cuándo. No
    lleva el número del contacto. Cuando la lee una persona, queda un `WARNING` de auditoría (RF28).
  - **`GET /chat/{bot_id}/contacts/{contact_id}/messages/{request_id}`:** qué pasó con un pedido de envío durante sus
    24 horas:
    - `accepted`: Bifröst lo tiene;
    - `sent`: Meta lo aceptó, y lleva su id de mensaje;
    - `failed`: con el código de Meta y un motivo que la PWA explica (`window_closed`, `template_paused`,
      `template_disabled`, `template_parameters`, `template_missing`, `marketing_limit`, `not_deliverable`,
      `no_token`, `gave_up`).
  - **El estado del pedido:** `OutboundRequest` gana `status`, `messageId`, `sentAt` y el fallo. La ingesta lo marca
    `sent` cuando llega su `RespuestaEnviada`, y `failed` con el evento nuevo `EnvioFallido`. Un pedido ya enviado no
    pasa a fallido, ni al revés.
  - **`EnvioFallido`:** evento nuevo en `engine.reply.failed.{botId}`, en el stream `RESPUESTAS`. Queda en
    `asyncapi-v2.yaml` y en `shared.contract.FailedSend`. Lo publicará el notificador en la segunda parte, junto con el
    cambio de Bifröst.
  - Las tres rutas son también del `cliente-analista`. La matriz pasa a la 1.6.0.
  - **Pruebas:** `tests/unit/test_conversations.py`, con 26 pruebas: la ventana, los 50 mensajes, sin número, la
    auditoría, cada motivo de fallo, el orden entre enviado y fallido, el aislamiento y el contrato del evento.
- **`hive-portal`, el BFF de la PWA** (HIG-93, [ADR-0029](docs/00-project/adr/0029-autenticacion-de-personas.md),
  [ADR-0034](docs/00-project/adr/0034-sesiones-del-portal-en-bifrost.md); RS22, AB25). Es un proyecto nuevo en
  `portal/`, con su imagen, `ghcr.io/jeremialcala/hive-portal`, sus pruebas y su CI (`portal · test`,
  `portal · image`).
  - **El inicio de sesión con Zitadel:** OIDC Authorization Code con PKCE (S256), `state` y `nonce`. El portal es un
    cliente confidencial (`client_secret_basic`). Un inicio de sesión en curso dura 10 minutos, vale una vez y va
    atado al navegador que lo empezó (cookie `__Host-hive-login`).
  - **El ID token:** el portal lo comprueba con el código de `shared/auth/idp.py`, que pasa del gateway a `shared/`
    con el `nonce` de ese inicio de sesión. Después lo cambia por un token de Hive en `hive-api` (RFC 8693), con una
    prueba DPoP de su propia clave, que solo vive en su memoria.
  - **La sesión, en el servidor:** en el bucket `sesiones` de Bifröst, sellada con AES-256-GCM y guardada bajo el hash
    de su identificador (ADR-0034). Termina a los 30 minutos sin uso, a las 12 horas en total, o cuando Hive deja de
    dar token a la persona.
  - **En el navegador, solo una cookie:** `__Host-hive-session`, con `HttpOnly`, `Secure` y `SameSite=Lax`. Toda
    petición que cambia algo lleva `X-Hive-Portal: 1` y el origen del portal. Si no, `403`, y no se reenvía nada.
  - **`/api/<ruta>`** reenvía la petición de la PWA a `hive-api` como la persona:
    - pasan solo las cabeceras necesarias, sin la cookie;
    - el token se renueva antes de caducar, y una vez más si `hive-api` responde `401`, como tras un reinicio;
    - `/api/oauth/…` no se alcanza.
  - **Las páginas:** CSP sin nada de fuera, HSTS, `nosniff` y `no-store`. La PWA de HIG-87 ocupará `/`; hasta
    entonces hay una página que dice si hay sesión.
  - **El despliegue:** `portal/deploy/docker-compose.yml`, en las redes `hive-gateway_publico` y `bifrost`. La guía de
    autenticación (0.6.0) dice cómo configurar la app en Zitadel, la clave de las sesiones, Bifröst y el nombre
    público.
  - **Bifröst:** el bucket `sesiones` y el usuario `portal` (higerotech/bifrost#7).
  - **CI:** el análisis de dependencias (`Seguridad`) audita también los locks del portal, y un cambio solo del portal
    no redespliega el gateway.
  - **Pruebas:** 51 pruebas unitarias contra un Zitadel y un `hive-api` falsos, y 2 de integración contra un NATS con
    los permisos de Bifröst. Cubren lo que pedía HIG-93: una cookie robada sin la cabecera no cambia nada, una sesión
    caducada lleva otra vez al inicio de sesión y el navegador nunca recibe un token. Cobertura del 85 %, y pylint
    10/10.
- **El `cliente-admin` invita y gestiona las personas de su cliente** (HIG-94,
  [ADR-0029](docs/00-project/adr/0029-autenticacion-de-personas.md); RF16, AB24).
  - **Rutas nuevas:** `POST` y `GET /people`, y `PUT` y `DELETE /people/{person_id}`. Las usa el `cliente-admin` o la
    credencial de máquina; el `cliente-analista` recibe `403`.
  - **Invitar por correo**, con un rol. Hive busca a la persona en Zitadel con su cuenta de servicio:
    - si no existe, la crea con el correo sin verificar y su código devuelto, para que Zitadel solo envíe la
      invitación (`invite_code`);
    - si existe, la enlaza, porque una persona puede ser de más de un cliente.

    Después la registra como `pending`, y pasa a `active` cuando entra por primera vez. La misma persona dos veces en
    un cliente da `409`, sin preguntar a Zitadel.
  - **Cambiar el rol, bloquear y dejar entrar otra vez:** `PUT /people/{person_id}` con `role`, `status` o los dos.
    Se nota en el siguiente intercambio, en 10 minutos como mucho.
  - **Dar de baja:** `DELETE /people/{person_id}`. La persona no consigue más tokens y su correo se borra; queda solo
    quién era, para la auditoría. Su usuario de Zitadel se queda, porque puede ser de otro cliente. Invitarla otra vez
    la trae de vuelta.
  - **El último `cliente-admin` activo** no se degrada, no se bloquea y no se da de baja: `409 LAST_ADMIN`. Un
    administrador que todavía está invitado no cuenta.
  - **La auditoría:** cada invitación, cambio y baja deja un `WARNING`, el nivel que guarda producción, con quién lo
    hizo, a quién y qué. Nunca el correo. Es la auditoría de las personas hasta la del cliente (RF18, HIG-95).
  - **Cuando Zitadel no responde:**
    - si no se alcanza, está saturado o rechaza la cuenta de servicio, `503`, con el motivo en el log;
    - si dice que no, `422 IDP_REFUSED` con su estado HTTP, nunca su mensaje.
  - **Configuración:** `IDP_ORGANIZATION_ID`, `IDP_TOKEN_FILE` y, si hace falta, `IDP_API_URL`. El compose monta
    `keys/idp/` en `/run/idp`, solo lectura, y el token se lee en cada invitación. La guía de autenticación (0.5.0)
    explica cómo crear la cuenta de servicio con `ORG_USER_MANAGER` y cómo guardar su token sin que pase por la
    pantalla.
  - **Pruebas:** `tests/unit/test_invitations.py`, con 40 pruebas contra una API de Zitadel falsa. Cubren el
    aislamiento entre clientes, que una invitación no da nada de otro cliente, el último administrador, el bloqueo, la
    baja con el correo borrado, la reinvitación y los fallos de Zitadel. El IdP falso de las pruebas pasa a
    `tests/fake_idp.py`, compartido con `test_people.py`.
  - **Documentación:** el README, la guía de autenticación, el PRD (0.10.0: RF16 construido), la matriz (1.5.0),
    `config.env.example` y el contrato OpenAPI.
- **Las personas de un cliente entran por Zitadel, y Hive decide qué pueden hacer** (HIG-92,
  [ADR-0029](docs/00-project/adr/0029-autenticacion-de-personas.md); RF17, RS22, RS23).
  - **`ClientUser`**, en la colección `client_users`, guarda de cada persona:
    - su `iss` y su `sub` en Zitadel;
    - el correo, cifrado con la clave del cliente y con índice ciego;
    - el rol (`cliente-admin` o `cliente-analista`), el estado y quién la invitó.

    Una persona puede ser de más de un cliente, y no puede estar dos veces en el mismo: lo impide la clave primaria.
    Se borra con su cliente.
  - **El intercambio** (`token-exchange`, RFC 8693) en `POST /oauth/token`. El BFF presenta el ID token de la persona
    con una prueba DPoP de su propia clave. Hive comprueba del ID token:
    - la firma, con el JWKS de Zitadel, que guarda una hora y relee como mucho una vez por minuto;
    - el `iss`, el `aud` y el `azp`;
    - la caducidad;
    - el `mfa` del `amr`.

    Después exige que la persona esté invitada o activa y que su cliente esté activo, y emite el token con la persona y
    su rol (`hive_user`, `hive_user_role`), atado a la clave del BFF. Una persona de más de un cliente elige con
    `hive_client`.
  - **Los errores del intercambio:**
    - lo que no vale da siempre el mismo `invalid_grant`;
    - una persona de más de un cliente sin `hive_client`, `invalid_target`;
    - sin IdP configurado, `unsupported_grant_type`;
    - sin las claves de Zitadel, `503`.

    Los tokens se cuentan por persona con el límite de la ADR-0028.
  - **Configuración:** `IDP_ISSUER`, `IDP_AUDIENCE` y, si hace falta, `IDP_JWKS_URL`. Sin los dos primeros solo entran
    las credenciales de máquina.
  - **El `Principal`** gana la persona y su rol. El envío a un contacto guarda `sentBy: person <uuid>`. La credencial
    de máquina conserva los permisos de `cliente-admin` (D3).
  - **La autorización por rol:**
    - el `cliente-analista` solo alcanza las rutas de `ANALYST_ROUTES` y recibe `403` en el resto (AB23);
    - en el envío, solo plantillas;
    - la ficha del bot le llega sin el `verifyToken` (D6);
    - una persona del operador alcanza el registro de clientes solo si es `cliente-admin`.
  - **La matriz contra el código:** la matriz (1.4.0) gana la sección «Rutas y roles», ruta por ruta, y
    `tests/unit/test_role_matrix.py` la compara con las dependencias de cada ruta, `ANALYST_ROUTES` y
    `WORKER_ROUTES`.
  - **Pruebas:** `tests/unit/test_people.py`, con 81 pruebas contra un IdP falso con su propio JWKS. Cubren AB23,
    AB24, AB26 y AB27, la firma falsificada, el `kid` inventado, el `amr` sin `mfa`, la persona de dos clientes, el
    límite y que la aserción de una máquina sigue igual.
  - **Documentación:**
    - la guía de autenticación (0.4.0) explica el intercambio paso a paso y la configuración del IdP, y corrige que
      el token ya está atado a una clave;
    - el README, el PRD (0.9.0) y el contrato OpenAPI.
  - **Pendiente:** que la persona exista en producción. Las invitaciones son HIG-94, y el BFF, HIG-93.
- **El webhook recibe los cambios de estado de las plantillas** (HIG-86,
  [ADR-0031](docs/00-project/adr/0031-mensajes-salientes.md); RF30). Meta avisa con `message_template_status_update`
  cuando una plantilla queda aprobada, rechazada, en pausa o desactivada.
  - **El webhook** lee los avisos firmados y los publica como un evento `CambioDePlantilla`, en
    `webhook.meta.templates` del stream `WEBHOOK`. Bifröst no cambia: el webhook ya publica en `webhook.meta.>` y la
    ingesta lee todo el stream. Un aviso sin cuenta, sin plantilla o sin estado no se publica, y Meta recibe `200`.
  - **La ingesta** aplica cada aviso a la copia, con su motivo (`statusReason`, que la lista devuelve como
    `reason`). Ignora:
    - los avisos de una cuenta que no es el `wabaId` del bot;
    - los de una plantilla que Hive no tiene;
    - los anteriores a lo que la copia ya sabe (`statusAt`).
  - **El orden:** la escritura es condicional, así que un aviso reentregado tarde no deshace uno posterior. Lo que Hive
    hace en Meta (crear, editar, sincronizar) deja la copia al día menos 60 s, porque Meta puede aprobar antes de
    responder.
  - **`REINSTATED`** deja la plantilla `APPROVED`. **`FLAGGED`** no cambia su estado: Meta la sigue enviando hasta que
    la pausa o la desactiva, cada vez con su aviso.
  - **Contrato:** `asyncapi-v2.yaml` gana el canal `webhookPlantillas` y el esquema `CambioDePlantilla`.
  - **En el alta de un bot** de WhatsApp, la app de Meta se suscribe también a `message_template_status_update`
    (README).
  - **Pruebas:** `tests/unit/test_template_status.py`, con 29 pruebas. Cubren cada estado, el aviso sin firma (`403`),
    el de otra cuenta, el de otro cliente, el de una plantilla desconocida, los malformados, el orden, el evento del
    contrato y su `Nats-Msg-Id`.
  - **Documentación:** el README, el PRD (0.8.2: RF29 y RF30, construido) y el contrato AsyncAPI.
- **Las plantillas de WhatsApp de un bot** (HIG-85, [ADR-0031](docs/00-project/adr/0031-mensajes-salientes.md); RF30).
  Hive las crea, edita, borra y lista en Meta con el `whatsappToken` del bot, y guarda una copia de cada una con su
  estado. Solo se envía una plantilla cuya copia diga `APPROVED`.
  - **Rutas nuevas:** `POST` y `GET /chat/{bot_id}/templates`, y `PUT` y `DELETE
    /chat/{bot_id}/templates/{template_id}`, con el id de Meta. `GET` con `?sync=true` trae la lista de Meta sobre la
    copia y quita lo que Meta ya no tiene, solo si la lista llegó entera.
  - **La cuenta:** `PUT /chat/{bot_id}/credentials` acepta `wabaId`, el id de la cuenta de WhatsApp Business. No es
    secreto y se guarda en claro. Un solo token para enviar y para gestionar plantillas (decisión de Jeremi del
    2026-10-29): necesita el permiso `whatsapp_business_management`.
  - **Antes de ir a Meta** se comprueba la plantilla, para no gastar los envíos que Meta permite por hora:
    - el nombre, en minúsculas, dígitos y `_`;
    - la categoría, `MARKETING` o `UTILITY`;
    - un solo cuerpo, con sus valores `{{1}}`, `{{2}}`… desde 1, sin huecos y con un ejemplo cada uno;
    - la cabecera de texto y el pie, sin valores;
    - hasta 10 botones, y un enlace solo a `https`.
  - **Editar** solo vale para una plantilla `APPROVED`, `REJECTED` o `PAUSED`. Si no, `409`. La copia vuelve a
    `PENDING` y no se envía hasta que Meta la apruebe otra vez. Una `DISABLED` no se borra: Meta no lo permite (`409`).
  - **Errores:**
    - sin `wabaId`, `422 NO_WABA`; sin token, `422 NO_TOKEN`;
    - un token que Meta no acepta, `502` con el permiso que falta;
    - un no de Meta, `422 META_REFUSED` con su código y nunca su mensaje;
    - Meta caído o saturado, `503`.
  - **El log** dice el bot y el id de Meta, nunca el texto de la plantilla ni el token.
  - **Configuración:** `META_GRAPH_URL` y `META_GRAPH_VERSION`, por defecto `https://graph.facebook.com` y `v26.0`.
  - **Pruebas:** `tests/unit/test_templates.py`, con 69 pruebas contra un Graph API local. Las de aislamiento, de
    autenticación, de la credencial del motor y del log cubren las cuatro rutas.
  - **Documentación:** el README, el PRD (0.8.1: RF29, RF30 y AB40), la matriz (1.3.1) y el contrato OpenAPI.
- **El notificador envía lo que el cliente escribe a sus contactos** (HIG-84,
  [ADR-0031](docs/00-project/adr/0031-mensajes-salientes.md)).
  - **El consumidor:** lee cada `PedidoDeEnvio` de `SALIENTES` con su consumidor `notifier` y envía por la Cloud API.
    Un texto va con `type: text`; una plantilla, con su nombre, su idioma y sus valores como parámetros del cuerpo.
  - **Sin duplicados:** apunta cada pedido en `enviados`, así que una reentrega no sale dos veces.
  - **Al historial:** publica lo enviado en `RESPUESTAS` con `origin: client`, quién lo pidió (`sentBy`) y el pedido
    (`requestId`), sin `inReplyTo`. La ingesta lo guarda con su origen y su autor, y la exportación lo muestra.
  - **Lo que Meta rechaza para siempre** se descarta, con su código en el log: la ventana cerrada (131047), una
    plantilla en pausa (132015), desactivada (132016) o con otros valores (132000), y el límite de marketing por
    persona (131049). Lo que puede ir más tarde (límite de tasa, gateway caído) se reintenta.
  - **El pedido lleva el número del contacto** y el `phone_number_id` desde el que se envía, como un mensaje de
    `MOTOR` (decisión de Jeremi del 2026-10-29). El envío sale del número del bot al que el contacto escribió por
    última vez, que la ingesta guarda cifrado en el contacto (`lastPhoneId`, RS05). Para un contacto de antes, del
    primer número del bot; sin ninguno, la ruta responde `422 NO_WHATSAPP`.
  - **Contrato:** `asyncapi-v2.yaml` y `shared/contract.py` ganan el `PedidoDeEnvio` como modelo, con `to` y
    `phoneId`. `RespuestaEnviada` gana `origin`, `sentBy` y `requestId`, y `inReplyTo` deja de ser obligatorio.
  - **La ADR-0031** queda corregida en lo del número y en desde qué número se envía.
- **Escribir a un contacto por WhatsApp** (HIG-83, [ADR-0031](docs/00-project/adr/0031-mensajes-salientes.md); RF29,
  RF31, RS27). Nueva ruta, `POST /chat/{bot_id}/contacts/{contact_id}/messages`, para un texto o una plantilla. Antes
  de publicar comprueba, en este orden:
  - que el contacto es del cliente; si no, `404`;
  - la ventana de 24 horas, con el último mensaje del contacto; un texto fuera de ella da `422 OUTSIDE_WINDOW`;
  - que la plantilla esté aprobada, sea de `MARKETING` o `UTILITY` y lleve sus valores;
  - fuera de la ventana, el consentimiento del contacto para su categoría (`NO_CONSENT`);
  - los límites: 60 por minuto por bot y 1 000 al día por cliente, con `429` y `Retry-After`.

  Después publica un `PedidoDeEnvio` en `gateway.outbound.{partición}.{bot}.{contacto}` (stream `SALIENTES`) y responde
  `202`. Más detalles:
  - **Un solo envío por clave:** la misma `Idempotency-Key` en 24 horas devuelve el mismo pedido. Con otro cuerpo da
    `IDEMPOTENCY_KEY_REUSED`. Si el bus no toma el pedido, la clave queda libre.
  - **El log** dice quién, a qué bot y contacto y qué plantilla, nunca el texto, los valores ni el número.
  - **El consentimiento:** `PUT` y `DELETE /chat/{bot_id}/contacts/{contact_id}/consent`. Retirarlo lo conserva con su
    fecha, y va en la exportación del contacto.
  - **Colecciones nuevas:** `templates`, la copia de las plantillas, que llenará HIG-85, y `outbound`, los pedidos, que
    caducan a las 24 horas. Se borran con el bot, el cliente y el contacto.
  - **Los contadores** viven en el bucket `limites` de Bifröst (higerotech/bifrost#6), con escrituras condicionadas por
    revisión y serializadas en el proceso. Sin bus, en desarrollo, viven en memoria.
  - **El contrato `asyncapi-v2.yaml`** gana el `PedidoDeEnvio`, y el OpenAPI, las tres rutas.
  - **Pruebas:** `tests/unit/test_outbound.py`, con 40 pruebas, y `tests/integration/test_send_limits_bus.py`, contra un
    NATS real.
  - **Corrección de la ADR-0031:** el orden entre un envío del cliente y una respuesta del bot no está garantizado,
    porque van en streams distintos.
- **[ADR-0033](docs/00-project/adr/0033-dpop-en-los-tokens-de-acceso.md) (aceptada el 2026-10-29): los tokens de acceso van atados
  por DPoP a la clave que los pidió** (HIG-89).
  - **La clave:** una credencial de máquina, de un cliente o de un worker del motor, firma cada prueba con su clave
    registrada. El BFF usa un par suyo, que vive solo en memoria.
  - **Cada petición** lleva `Authorization: DPoP` y una prueba de un solo uso. La prueba dice el método, la URL y la
    hora (60 segundos, y posterior al arranque de `hive-api`), y lleva el hash del token.
  - **El `jti`** se guarda en memoria, no en Atlas. Sin `DPoP-Nonce` en la v1.
  - **El motor también,** y el cambio es de golpe: decisiones de Jeremi del 2026-10-29.
  - **Medido en midgard,** con media CPU: firmar una prueba cuesta 0,9 ms y verificarla, 1,7 ms.
  - **En el PRD** (0.8.0): RS30 y AB50–AB51, y RS02 ampliado. La ADR-0025 da DPoP por resuelto.
- **[ADR-0032](docs/00-project/adr/0032-integraciones-de-terceros-con-mcp.md) (aceptada el 2026-10-29): los servicios de terceros
  entran por un catálogo MCP del operador, y solo el enlace los llama** (HIG-79).
  - **El catálogo:** servidores remotos por Streamable HTTP. Cada herramienta está aprobada por la huella de su
    descripción y su esquema, y es de lectura o de escritura según decide el operador, no las anotaciones. Una
    herramienta nueva o cambiada no se ofrece.
  - **Las APIs del cliente** siguen siendo las llamadas de los intents.
  - **Un almacén de credenciales por cliente,** cifrado con su clave de datos. El `authRef` apunta a él, y solo el
    enlace lo lee.
  - **OAuth en la v1,** con PKCE, permisos fijados por el catálogo, renovación y revocación.
  - **Lo que tiene efectos** lo confirma el usuario final con un botón.
  - **Lo que responde una herramienta** es dato no confiable y no sale del contexto de la conversación.
  - **En el PRD** (0.7.0): RF32–RF35, RS28–RS29 y AB43–AB49. En la matriz (1.3.0), la sección 8 apunta a sus
    requisitos.
- **[ADR-0031](docs/00-project/adr/0031-mensajes-salientes.md) (aceptada el 2026-10-29): el cliente escribe a sus contactos por
  WhatsApp, y solo el notificador envía** (HIG-82).
  - **El camino:** `hive-api` valida y publica un `PedidoDeEnvio` en Bifröst, el notificador envía y lo enviado vuelve
    al historial por `RESPUESTAS`, con su autor.
  - **La ventana de 24 horas** se calcula con el historial.
  - **El consentimiento de cada contacto** lo guarda Hive. Es lo que Meta exige para escribir fuera de la ventana.
  - **Los límites:** 60 envíos por minuto por bot y 1 000 al día por cliente.
  - **La idempotencia,** con `Idempotency-Key`.
  - **Las plantillas** las gestiona `hive-api` en Meta con el token del bot, que guarda su `WABA_ID` y una copia de
    cada plantilla.
  - **Las decisiones de Jeremi del 2026-10-29:** el consentimiento lo guarda Hive, los límites, y un solo token de
    WhatsApp por bot, con el permiso de gestión de plantillas además del de mensajería.
  - **En el PRD** (0.6.0): RF29–RF31, RS27, AB36–AB42 y RS06 matizado. En la matriz (1.2.0), la sección 7 apunta a
    sus requisitos y gana las filas del consentimiento.
- **Los requisitos de las capacidades de la D9 en el PRD** (`docs/01-requirements/mvp-baas-gateway.md` 0.5.0,
  HIG-80).
  - **Del operador:** suspender un cliente (RF19), la auditoría de la plataforma (RF20), las trazas y los eventos del
    bus (RF21), y el reproceso y el mantenimiento (RF22).
  - **Del `cliente-admin`:** editar la persona (RF23), editar o borrar reglas (RF24), rotar el `verifyToken` (RF25),
    las versiones (RF26) y los guardrails (RF27).
  - **De los dos roles del cliente:** calificar respuestas y ver las métricas (RF28).
  - **Seguridad y abuso:** tres requisitos (RS24–RS26) y ocho escenarios de abuso (AB28–AB35), con su traza en el
    diagrama de requisitos.
  - **Las cuatro decisiones de Jeremi del 2026-10-29:**
    - un cliente suspendido sigue recibiendo, y el bus retiene sus mensajes 7 días sin procesarlos;
    - lo destructivo del operador pide MFA de los últimos 5 minutos y un motivo;
    - quien califica lee la conversación completa, sin el número y dejando rastro;
    - la base de guardrails de la plataforma no se desactiva.
  - **La matriz rol × acción** pasa a la 1.1.0: cada capacidad apunta a su requisito.
- **[ADR-0030](docs/00-project/adr/0030-alimentacion-de-midgard-y-nidavellir.md) (propuesta): un SAI para midgard y
  Nidavellir, con apagado ordenado.**
  - El 2026-10-28 hubo cuatro cortes de luz, y con cada uno Hive cayó entero: unas 8 h 13 min.
  - Tras el último, otros 77 min sin bus, porque el SSD de Bifröst llegó tarde al arranque.
  - Propone un SAI interactivo de línea con AVR y USB, unos 20 min de autonomía y NUT: midgard es el primario,
    Nidavellir el secundario, y al final midgard ordena al SAI que corte su salida.
  - Quedan abiertos el presupuesto, qué equipos entran, los minutos en batería, el consumo medido y el modelo.
- **[Zitadel en Nidavellir](deploy/idp/README.md): el despliegue del IdP de las personas** (HIG-91, HIG-96; ADR-0029,
  enmienda). Se despliega a mano desde `develop`, como el gateway en midgard.
  - Cinco contenedores en `deploy/idp/docker-compose.yml`: PostgreSQL 17.11, la API y el login de Zitadel v4.19.4,
    Caddy delante (sin el socket de Docker) y un túnel propio en `hive-auth.higerotech.com`. Imágenes por digest, sin
    capacidades, sin puertos publicados y la base sola en una red interna.
  - Los secretos los escribe `generar-secretos.sh` en la máquina, con root y 0600, sin imprimirlos.
  - El vigía de midgard reinicia los contenedores `unhealthy` y avisa por ntfy.
  - Respaldo nocturno en el NAS, cifrado con `age` antes de salir de la máquina, guardado 30 días sin borrar nunca el
    último bueno, con métricas para Thor y aviso si falla. Una restauración de prueba en un PostgreSQL sin red.
  - Pruebas: `tests/unit/test_idp_compose.py` (lo que el compose no puede perder) y
    `tests/unit/test_hive_idp_respaldo.py` (el respaldo con un docker y un age de mentira).
- **[ADR-0029](docs/00-project/adr/0029-autenticacion-de-personas.md) (aceptada): las personas de un cliente** (decisiones
  de Jeremi del 2026-10-28).
  - Entran con Zitadel, alojado en midgard con su propio PostgreSQL, siempre con MFA (passkey o TOTP).
  - Hive guarda a cada persona (`ClientUser`), con su cliente, su rol y el correo cifrado, y decide qué alcanza.
  - El token llega a `hive-api` por un intercambio en `/oauth/token` (RFC 8693): Hive emite su propio token de
    10 minutos con la persona y el rol.
  - La PWA guarda la sesión en un BFF, `hive-portal`, y el navegador solo tiene una cookie `HttpOnly`.
  - El PRD pasa a la v0.4.0, con RF16–RF18, RS22–RS23 y AB23–AB27. La clasificación de datos suma el correo, las
    credenciales y la sesión de las personas.
- **[La matriz rol × acción](docs/02-design/matriz-rol-accion.md) (aceptada)**, a partir del borrador de Jeremi del
  2026-10-26: qué puede cada rol sobre cada recurso de `hive-api`.
  - Los roles son `operador`, `cliente-admin`, `cliente-analista` y el `motor`, worker por worker.
  - Separa lo que el código ya autoriza de lo que se propone. Lo construido sale de `require_operator`,
    `require_service`, `WORKER_ROUTES` y el repositorio del cliente.
  - Lo nuevo del borrador entra como propuesto, sin requisito en el PRD: usuarios humanos del cliente, sesión con un
    IdP, guardrails, versiones y procesos MCP.
  - Recoge las decisiones de Jeremi del mismo día. D1–D6: Hive tendrá personas con sesión dentro de cada cliente, con
    un catálogo fijo de dos roles, y la credencial de máquina conserva los permisos de `cliente-admin`. D7: MCP entra,
    para integrar servicios de terceros. D8: el operador configura los servidores MCP por defecto, y `cliente-admin`
    carga las credenciales de su cuenta y configura integraciones con sus propias APIs. D9: las capacidades del
    borrador sin requisito entran en la v1. Queda en la v1.0.0, aceptada; las llamadas a APIs y a servidores MCP las
    hace el enlace, y el evaluador comprueba que cada respuesta cumpla los criterios del bot.
    D10, del 2026-10-27: el cliente escribe a sus contactos por WhatsApp, con plantillas de Meta fuera de la
    ventana de 24 horas; el `cliente-analista` solo envía plantillas aprobadas.
- **[ADR-0027](docs/00-project/adr/0027-el-mapa-de-estilo-de-hive.md) (aceptada): el mapa de estilo de Hive**
  (decisiones de Jeremi del 2026-10-26). Concepto "Pragmatismo Técnico", oscuro por defecto, paleta Slate Black /
  Dark Ash / Hive Amber con Meta Blue y WhatsApp Green solo para canales, Geist + JetBrains Mono servidas desde el
  propio origen, cuatro patrones obligatorios (code-first, confirmación escribiendo el nombre o el UUID, cargas con
  micro-copy técnico, slide-overs) y el onboarding de un bot en cuatro pasos.
  - El design system vive en el artifact "Hive" de Claude; el repo guarda su exportación versionada en
    `assets/design-system/` (tokens, `tokens.css`, `bundle.css`, `bundle.js`, tipos, brand book y superficies) y el
    registro con el historial y el procedimiento de actualización en `docs/02-design/design-system.md`.
  - El logotipo, en `assets/logos/`: isotipo hexagonal isométrico, wordmark geométrico, lockups horizontal y
    vertical, monocromos, favicon e iconos de la PWA, con su guía de uso.
- **[ADR-0026](docs/00-project/adr/0026-documentacion-publica-de-la-api.md) (aceptada): la documentación de la API de
  gestión, en `https://hive.higerotech.com/docs`** (decisiones de Jeremi del 2026-10-26).
  - Una página de Redoc generada con el contrato versionado (`deploy/docs/`), en un contenedor propio,
    `hive-docs`: nginx sin privilegios, de solo lectura y solo en la red del túnel. La API sigue sin servir su
    esquema.
  - Nada de fuera: Redoc se sirve desde la página, comprobado contra el hash que fija Redocly CLI, sin fuentes de
    Google. La CSP solo deja pasar el script en línea por su hash.
  - Dependencias de npm exactas y resueltas con más de dos semanas, con `npm audit` limpio.
  - El CI construye la imagen y comprueba la página, su CSP y que no sirve otra cosa. `hive-vigia` también la
    vigila.
  - En Cloudflare, el túnel necesita el nombre público `hive.higerotech.com` hacia `http://hive-docs:8080`.
- **El vigía de Hive en midgard** (`deploy/vigia/`; decisión de Jeremi del 2026-10-21). Docker marca `unhealthy` un
  contenedor cuyo healthcheck falla, pero no lo reinicia: el 2026-10-21, `bots-gateway` pasó seis minutos colgado sin
  que nada lo levantara.
  - `hive-vigia.timer` mira cada minuto el gateway, el webhook, la ingesta y los cuatro workers del motor, reinicia el
    que esté `unhealthy` y avisa por ntfy, por el canal de los otros vigías de midgard.
  - Tras 3 reinicios en una hora deja de reiniciarlo y avisa con urgencia, una vez por hora.
  - Corre en el host, no en un contenedor con el socket de Docker, que sería root en midgard.
  - Si no puede avisar, reinicia igual: queda en `journalctl -u hive-vigia`.
- **El túnel propio de Hive**, `hive-tunel` (E7; ADR-0024): publica `hive-webhook.higerotech.com` hacia el webhook y
  `hive-api.higerotech.com` hacia la API de gestión. Llega a ellos por una red, `publico`, que solo los une a ellos
  dos. Solo arranca con `COMPOSE_PROFILES=tunel`, y su token es `TUNNEL_TOKEN` en el `.env`.
- **ADR-0024, versión 0.2.0: un subdominio por superficie** (decisión de Jeremi del 2026-10-20). La API de gestión
  pasa a `https://hive-api.higerotech.com`, que es la audiencia de los tokens, y el webhook de Meta va en
  `https://hive-webhook.higerotech.com`. `hive.higerotech.com` queda reservado para el producto.
- **[ADR-0024](docs/00-project/adr/0024-el-producto-se-llama-hive.md) (aceptada): el producto se llama Hive**, con
  su API de gestión en `https://hive.higerotech.com`, que es también la audiencia de los tokens (decisión de Jeremi
  del 2026-10-20). Los componentes conservan sus nombres, y no se renombran el repositorio, las imágenes ni los
  contenedores. La URL del webhook de Meta se decide en el E7. El README, el charter, el glosario y la guía de
  autenticación lo dicen.
- **Para el clasificador del motor** (E3a del plan de los workers):
  - `shared.contract.what_was_said`: lo que dice un mensaje identificado, su texto y sus adjuntos. Era de la
    ingesta, que lo usa igual para el historial; así el motor lee el mismo texto que el gateway guarda.
  - `EngineKeyStore.api_key`: la clave de la API de Anthropic de un worker, en `motor/data/anthropic/<worker>` de
    Andvari, probada contra OpenBao con la política de cada worker.
- **`shared/`, lo que usan el gateway y el motor** (ADR-0023; E1 del plan de los workers del motor):
  - **Qué pasa:** el bus, las trazas, los tokens de la API (`shared.auth`), el almacén de claves de Andvari, la regla
    de destinos y los nombres de los workers. Se mueven con su historia, y el gateway los importa de ahí sin cambiar
    de comportamiento.
  - **`shared.contract`:** el contrato v2 en modelos (`IdentifiedMessage`, `SentReply`), probados contra
    `asyncapi-v2.yaml` desde el gateway.
  - **Las imágenes:** las dos copian `shared/`. La del motor se construye desde la raíz, con un `.dockerignore`
    propio.
- **El CI por rutas y la imagen del motor** (ADR-0023):
  - `ci` y `build` del gateway dejan fuera `engine/`;
  - el motor tiene su propio `engine` (pylint y pruebas con cobertura mínima del 60 %) y su `build-engine`, que
    publica `ghcr.io/jeremialcala/chatbot-engine`. No se llama `build`, porque ese nombre es el que despliega el
    gateway;
  - `Seguridad` audita también los locks del motor;
  - el umbral de CodeQL trata `engine/tests/` como pruebas.
- **El motor de respuestas, en `engine/`**, con sus 13 commits de historia: el repositorio pasa a ser un monorepo
  (ADR-0023). gitleaks no encontró nada en esa historia. El motor conserva sus dependencias, sus pruebas, su
  Dockerfile, sus docs y su CHANGELOG.
- [ADR-0023](docs/00-project/adr/0023-monorepo-del-gateway-y-el-motor.md) (aceptada): el gateway y el motor de
  respuestas pasan a un solo repositorio, este. El motor entra en `engine/` con su historia, el gateway se queda
  en la raíz y el código común irá a `shared/`. Cada componente tiene sus dependencias, su imagen, su CHANGELOG y
  sus etiquetas. El CI es uno solo, filtrado por rutas.
- I7: adónde puede llamar una acción de un intent (R6, RS16, AB09, T11;
  [guía](docs/03-implementation/destinos-de-los-intents.md)).
  - **La regla** (`classes.destinations`): `https`, sin usuario ni contraseña y con el host escrito, sin
    marcadores. El host tiene que ser una IP pública o un nombre de al menos dos etiquetas que no cuelgue de un
    dominio de red local. Así no se alcanzan `andvari`, `bots-gateway`, `192.168.10.1`, el servicio de metadatos de
    la nube, ni las formas numéricas de 127.0.0.1.
  - **Se comprueba al guardar o cambiar un intent** (`422` sin repetir la URL) **y al entregar las herramientas al
    motor**: una acción guardada antes, con un destino prohibido, no sale en `GET /chat/{bot_id}/tools`.
  - **Lo que queda para el worker de enlace:** resolver el nombre en cada llamada y comprobar cada dirección.
- I6b: el comando que da de alta las claves del motor para un cliente, `python -m ops.engine_keys <client_id>`
  ([ADR-0022](docs/00-project/adr/0022-una-credencial-por-worker.md)).
  - **Una clave por worker:** las crea, guarda la parte privada en Andvari y registra la pública por la API como
    el operador.
  - **Dónde se guardan:** en un motor KV de Andvari, en `motor/data/clientes/<client_id>/<worker>`. Cada worker lee
    la suya con su propio rol.
  - **Se puede repetir:** no rehace lo hecho, termina un alta que quedó a medias y nunca escribe encima de una clave
    guardada. Nunca imprime ni escribe en disco una clave privada.
  - **En midgard:** el servicio `engine-keys` del compose de despliegue, en el perfil `operations`, que solo arranca
    bajo demanda.
  - **El cliente del KV,** `crypto.engine_key_store`, con sus pruebas de integración contra OpenBao, usa las mismas
    políticas que el repositorio `andvari`.
  - **`config.env.example`:** `OPERATOR_CLIENT_ID` y `ENGINE_KEYS_ROLE_ID`.
- I6a: una credencial por worker del motor ([ADR-0022](docs/00-project/adr/0022-una-credencial-por-worker.md), R5,
  RS21, AB21, T29).
  - **Cada clave del motor dice de qué worker es:** `classifier`, `coordinator`, `link`, `responder`, `evaluator`
    o `notifier`. Lo dice el registro, no el token.
  - **Cada worker alcanza solo sus rutas** (`WORKER_ROUTES`):
    - el notificador, los tokens de Meta;
    - el coordinador, las reglas, las herramientas y las vueltas;
    - el clasificador, el de respuesta y el evaluador, las reglas;
    - el de enlace, las herramientas;
    - todos, las claves públicas del gateway.
    Una ruta nueva nace cerrada para todos.
  - **Dos claves como máximo por worker y cliente**, para rotar sin corte. El límite se comprueba en la propia
    actualización.
  - **El log dice el worker** en cada entrega de tokens y de vueltas, y en cada ruta denegada.
- Las trazas van a **Saga**, el Grafana Tempo de Yggdrasil (ADR-0008 de Yggdrasil).
  - **Cómo llegan:** los tres servicios entran en la red externa `yggdrasil_saga` y envían a `http://saga:4318`.
  - **Qué alcanzan:** esa red solo lleva a Saga, ni a Mimir ni a Odín.
  - **Requisito de arranque:** Yggdrasil tiene que estar levantado antes que el gateway.
- I5: trazas con OpenTelemetry y la regla de los logs (R4, RNF09, RS19, T28).
  - **Una traza por mensaje,** del webhook al motor:
    - un span por petición en el webhook y en la gestión;
    - el evento de `WEBHOOK` lleva el contexto de la petición;
    - la ingesta lo retoma en `ingest.notification` y en un `ingest.message` por mensaje;
    - cada mensaje identificado sale con el contexto de su span;
    - las respuestas y los avisos de supresión se trazan igual.
  - **La regla de los logs:**
    - un span solo admite una lista cerrada de atributos, cada uno con la forma de su valor: identificadores del
      servicio, rutas declaradas, sujetos, recuentos y palabras fijas;
    - no se instrumenta ninguna biblioteca por su cuenta, porque guardarían la URL con su query string y la IP de
      quien llama;
    - ninguna excepción se guarda en un span.
  - **Exportación:** por OTLP/HTTP solo si hay `OTEL_EXPORTER_OTLP_ENDPOINT`; sin él, las trazas no salen del
    proceso. El compose nombra cada servicio con `OTEL_SERVICE_NAME`.
  - **Dependencias:** OpenTelemetry SDK 1.45.0, porque la 1.45.1 aún no tenía dos semanas.
  - **Probado** en midgard: el evento y su mensaje identificado comparten traza.
  - **Guía:** `docs/03-implementation/trazas.md`.
- D6c: avisos de supresión al motor (ADR-0017, RF15).
  - **Qué se publica:** después de cada borrado, la API de gestión publica en `SUPRESIONES` un `Supresion` del
    contrato v2 por cada cosa borrada, solo con identificadores. En un contacto, va un aviso por cada copia de esa
    persona en el bot.
  - **Para qué:** el coordinador borra con ellos el contexto que guarda.
  - **Si el bus no lo acepta:** el borrado no se deshace ni falla. Queda en el log, y el contexto caduca solo en 24
    horas.
  - **Despliegue:** el compose le da a la gestión su usuario `gestion` de Bifröst, la red `bifrost` y la CA.
  - **Probado** contra NATS en el CI y contra el Bifröst real en midgard.
- D6b: `GET /chat/{bot_id}/contacts/{contact_id}/turns`, las 10 últimas vueltas de una conversación (RF15,
  ADR-0017), para que el coordinador del motor rehaga su contexto cuando no lo tiene.
  - **Qué devuelve:** desde el décimo mensaje del contacto contando por el final, todo lo que vino después, en los
    dos sentidos y en orden, con cada texto descifrado.
  - **Quién la alcanza:** solo la clave del motor para ese cliente. El cliente tiene la exportación para eso.
  - **Cómo se registra:** cada entrega deja una línea en el log, sin el texto, y la respuesta lleva `no-store`.
  - **Pendiente:** I6 la dejará solo para la clave del coordinador (ADR-0022).
- D6a: las respuestas del motor, en el historial (ADR-0017, RF14).
  - **Qué lee:** el worker de ingesta lee también `RESPUESTAS`, con el consumidor `historial` de Bifröst, en un
    bucle propio.
  - **Qué guarda:** cada `RespuestaEnviada` (contrato v2), como mensaje de salida del contacto. Lleva en `inReplyTo`
    el mensaje al que responde y el texto cifrado; de cada adjunto, solo el tipo. La misma respuesta publicada dos
    veces se guarda una vez.
  - **Cuándo la descarta:** si su contacto ya no existe.
  - **Cuándo espera:** si no responden la base o Andvari.
  - **Probado** contra NATS en el CI y contra el Bifröst real en midgard.
- I4b: el worker de ingesta, y el webhook que solo publica (ADR-0021).
  - **El worker de ingesta** es `ingest.py`, el servicio `ingest` del compose. Lee `WEBHOOK` con el consumidor
    `ingesta` de Bifröst y trata las notificaciones una a una. Con cada mensaje:
    1. lo salta si ya está en el historial;
    2. identifica al contacto;
    3. lo publica como `MensajeIdentificado` en la partición de su conversación, en `MENSAJES`, y envía el sobre v1
       a Hermod;
    4. lo guarda en el historial.
  - **Publicar va antes que guardar**, al revés que en la ADR. Un corte entre los dos pasos repite una publicación,
    nunca la pierde.
  - **Qué hace con cada evento:** lo confirma al terminar; lo descarta si no se puede ingerir nunca; y lo deja para
    la reentrega de Bifröst si falta la base, Andvari, Bifröst o Hermod.
  - **Con bus, el webhook solo publica**, y con el bot en memoria no hace ningún viaje a la base. Sin bus, que solo
    pasa en desarrollo, ejecuta la misma ingesta; fuera de desarrollo, no arranca sin `NATS_URL`.
  - **Medido en midgard:**
    - el webhook baja de 185 ms (contacto conocido) y 415 ms (contacto nuevo) a **8,3 ms de mediana** en los dos
      casos, y a 50–52 ms con 4 a la vez;
    - la ingesta trata 2,1 notificaciones por segundo, limitada por los viajes a Atlas.
  - **Pruebas:**
    - de la ingesta y del worker;
    - de integración del worker contra NATS, con los usuarios y los permisos de Bifröst;
    - las de viajes a la base, rehechas para el webhook que solo publica.
  - **Dos correcciones:**
    - `replay_erasures` cuenta ahora también el historial de los contactos que vuelve a borrar. Antes, un historial
      que volviera sin su contacto pasaba desapercibido.
    - `pika` deja de escribir en el log a nivel INFO: eran 18 líneas por cada mensaje enviado a Hermod.
- I4a, el historial de cada conversación (ADR-0017, RF14), en la colección `messages`:
  - **Contenido de cada mensaje:** el contacto por su `uuid`, nunca por el número; el texto cifrado con la clave del
    cliente; de cada adjunto, solo el tipo; y caducidad a los 12 meses de guardarse.
  - **Un solo registro por mensaje.** La clave primaria es el bot, el sentido y el identificador de Meta, así que un
    reintento de Meta no se guarda dos veces, y esa garantía no depende de un índice.
  - **Exportación y borrado.** `GET /chat/{bot_id}/contacts/{contact_id}` exporta la conversación con el contacto.
    Se borra con el contacto, el bot y el cliente, y también al repetir los borrados del registro de supresiones.
    Las respuestas de borrado cuentan ahora también los `messages`.
  - Todavía no lo escribe nadie: lo hará el worker de ingesta (I4b).
- I3 hecho el 2026-10-19: el webhook publica en Bifröst cada notificación firmada que trae mensajes de personas como
  un solo `EventoWebhook` del contrato v2, en `webhook.meta.whatsapp` o `webhook.meta.messenger` (ADR-0021).
  - `Nats-Msg-Id` es el bot más el SHA-256 de los identificadores de Meta, y cada evento lleva su `traceparent`.
  - Si Bifröst no acusa el evento, Meta recibe `503` y no se manda nada a Hermod.
  - Hasta I4 los sobres v1 siguen yendo a Hermod; sin `NATS_URL` el webhook funciona como antes.
  - El compose mete al webhook en la red `bifrost` con su fichero de credenciales.
  - El evento que construye el código se valida contra `asyncapi-v2.yaml`.
  - Medido en midgard: le añade unos 4 ms al webhook.
- I2 hecho el 2026-10-19: el puerto `EventPublisher` y su adaptador `NatsPublisher` sobre Bifröst (`src/bus`, `nats-py`
  2.16.0).
  - Una conexión por proceso, `Nats-Msg-Id` en cada evento y espera del acuse del stream.
  - Cualquier fallo es `BusUnavailable`.
  - Medido contra Bifröst con el usuario del webhook: 2,1 ms de mediana por evento de 1 KB, frente a los 8,3 ms de
    Hermod con una conexión por mensaje.
  - Probado contra un NATS 2.14.8 real configurado como Bifröst, también en el CI.
  - El webhook sigue publicando en Hermod: el evento que publicará en Bifröst llega con I3.
  - Guía: `docs/03-implementation/bus-de-eventos.md`.
- I1 hecho el 2026-10-19: medido el webhook actual en midgard, contra Atlas y Hermod reales, con notificaciones de
  WhatsApp sintéticas y firmadas. Con un contacto conocido responde en 185 ms de mediana y cumple RNF01; con uno
  nuevo tarda 415 ms, y con 4 notificaciones a la vez, de 357 a 472 ms. Ninguno de los dos cumple. Publicar en Hermod
  cuesta 8 ms: el resto es Atlas. Es la línea base de I2 e I4. El script es `tests/performance/measure_webhook.py` y
  el resultado está en `docs/04-testing/medicion-webhook-i1.md`.
- El almacén de Bifröst está en el SSD de midgard (2026-10-19): el acuse de un mensaje de 1 KB baja de 44,5 ms a
  2,2 ms de mediana, y el de una notificación de 3 MB de 196 ms a 69 ms. Sin el SSD, Bifröst no arranca, y el disco
  queda vigilado. El resto de los servicios se moverá tras unos días de vigilancia (ADR-0020).
- El webhook publicará cada notificación como un solo evento, con sus mensajes, y la ingesta los separa (decisión
  del 2026-10-19, ADR-0021 y contrato v2). En el disco de midgard cada escritura cuesta un `fsync` de unos 45 ms; con
  un evento por notificación, hasta los 3 MB que envía Meta caben en 196 ms de mediana. Bifröst admite ya 4 MB por
  mensaje. ADR-0019 y ADR-0020 quedan aceptadas sin condición.
- I0 hecho el 2026-10-19: Bifröst (NATS 2.14.8) desplegado en midgard con sus cuatro streams, dos consumidores, el
  bucket de contexto y un usuario por servicio (repositorio local `Projects/bifrost`). Medido en su disco mecánico: el
  acuse con `fsync` en cada escritura tarda 44,5 ms de mediana por mensaje, frente a 0,42 ms con la configuración por
  defecto, y publicar a la vez no lo agrupa. La caducidad del bucket se renueva con cada escritura: ADR-0017 queda
  aceptada sin condición.
- Gate 1 del refactor aprobado el 2026-10-18: arquitectura y threat model 0.3.0 y el contrato del bus 2.0.0 en
  `approved`; ADR-0021 y ADR-0022 aceptadas; ADR-0017, ADR-0019 y ADR-0020 aceptadas con la condición de que I0
  confirme en el NATS de midgard la caducidad del bucket de contexto y el coste del `fsync`. ADR-0006 queda sustituida
  por ADR-0021.
- Diseño del refactor (Gate 1, en revisión): `architecture.md` 0.3.0 con el objetivo (C4 Container con webhook,
  ingesta, gestión, Bifröst y el bucket de contexto; secuencia de la notificación con sus fallos; ciclo de vida de un
  evento en `WEBHOOK`; qué tiene cada servicio; brecha por incrementos) y `threat-model.md` 0.3.0 con el DFD del
  objetivo y las amenazas T22–T31.
- Lo que llega de Meta espera en Bifröst 7 días como máximo, lo mismo que reintenta Meta (decisión del 2026-10-18,
  ADR-0021).
- ADR-0021 y ADR-0022, propuestas para el Gate 1 del refactor. ADR-0021 (D2): el webhook solo comprueba la firma y
  publica un evento por mensaje, y responde `200` con el acuse de Bifröst; un worker de ingesta identifica al
  contacto, guarda el historial, deduplica los reintentos de Meta y publica en la partición de la conversación.
  Propone guardar 24 horas lo que llega de Meta. ADR-0022 (D3): una clave de servicio por cliente y por worker, con
  sus rutas: solo el notificador alcanza los tokens de Meta.
- Contrato del bus, versión 2.0.0 (`asyncapi-v2.yaml`, propuesto): los streams `WEBHOOK`, `MENSAJES`, `RESPUESTAS`
  y `SUPRESIONES`, con `Nats-Msg-Id`, `traceparent` y 16 particiones por conversación. Una prueba de contrato
  comprueba que cada referencia existe y que cada esquema es un JSON Schema válido.
- ADR-0019 y ADR-0020, propuestas para el Gate 1 del refactor: NATS JetStream 2.14 como bus de eventos (D1), con
  `sync_interval: always` porque es un solo nodo sin UPS, un usuario por servicio con permisos por sujeto, TLS,
  almacén cifrado en disco y el orden por conversación con 16 particiones; y su despliegue en midgard como
  **Bifröst** (`/srv/infra/bifrost`, contenedor `bifrost-nats`, red Docker `bifrost`), igual que Hermod y Andvari.
- Requisitos del refactor, aprobados el 2026-10-18 (Gate 0 del refactor): PRD 0.3.0 con Instagram (RF13), historial exportable (RF14),
  contexto del coordinador (RF15), orden por conversación (RNF08), traza de cada mensaje (RNF09), trazas y bus sin
  datos personales de más (RS19), un usuario por servicio en el bus (RS20), una credencial por worker (RS21) y los
  escenarios de abuso AB17–AB22; DFD del objetivo. Charter, glosario y clasificación de datos al día con el refactor.
  Decidido al aprobarlo: el historial se guarda 12 meses por mensaje como máximo, de un adjunto solo su tipo, e
  Instagram se conecta con Facebook Login a través de la página del cliente.

### Cambiado
- **El 131058 de Meta tiene su motivo: `sample_template`** (HIG-87). Meta no lo documenta. Con el log del motor
  (#133) se vio que, el 2026-10-30, Meta respondía con él a `hello_world`, su plantilla de muestra, que solo sale
  desde sus números públicos de prueba.
  - `GET /chat/{bot_id}/contacts/{contact_id}/messages/{request_id}` devuelve el motivo `sample_template`.
  - La PWA lo explica en palabras y propone enviar una plantilla propia aprobada.
  - Prueba: un caso más en `tests/unit/test_conversations.py`.
- **El motor deja en el log lo que Meta explica de un error, no solo su código**
  (`engine/src/infrastructure/meta/whatsapp.py`). El 131058 del 2026-10-30, que Meta no documenta, solo se pudo
  interpretar a ojo.
  - **Qué se añade:** detrás de `Meta <código>` van el título o el `message`, los `details` y el `fbtrace_id`, que es
    lo que pide el soporte de Meta. Así sale en los `WARNING` del notificador, por ejemplo «Meta no acepta el pedido
    de envío…».
  - **Qué se tapa,** porque Meta puede repetir el número: las cifras de cinco o más, los `wamid` (que llevan el número
    dentro), los correos y las cadenas largas que podrían ser un token.
  - **Formato:** una sola línea, de 300 caracteres como mucho. El notificador sigue leyendo el código igual.
  - **Pruebas:** `tests/unit/test_whatsapp_cloud.py`, con el número, un `wamid`, un correo, un token, saltos de
    línea, un texto largo y tipos que no son texto.
- **Runbook de Zitadel (`deploy/idp/README.md`) 0.3.0: el proyecto `Hive` y la aplicación `hive-portal`, tal como se
  crearon el 2026-10-29** (HIG-91, HIG-93). Es un cliente confidencial con secreto, solo Authorization Code y sin
  refresh token. No lleva roles ni datos del usuario en los tokens, y sus rutas están en `hive-app.higerotech.com`.
- **Runbook de Zitadel (`deploy/idp/README.md`): la configuración en la consola, con las trampas del 2026-10-28.**
  - La MFA obligatoria va después de registrar un TOTP en cada administrador. Activarla antes deja la consola
    rechazando todas las sesiones (`AUTHZ-Kl3p0`), porque el login no obliga a registrar el segundo factor. Se
    explica también cómo salir por `loginname` y `mfa/set`.
  - El nombre de usuario completo del administrador, y su correo, que es de un dominio que no existe.
  - Solo `IAM_OWNER` para las personas.
  - El SMTP de Resend en el puerto 2465 y cómo reconocer un problema de MTU por el túnel PIA.
- **ADR-0029, enmendada:** Zitadel va en Nidavellir y no en midgard (decisión de Jeremi del 2026-10-28), con dos
  condiciones. Una, 14 días sin cuelgues antes de que entren personas de verdad. Otra, vigilancia y respaldos
  cifrados en el NAS. Los nombres públicos son `hive-auth.higerotech.com` y `hive-app.higerotech.com`, y los
  correos salen por Resend, que entra como subencargado en la clasificación de datos.
- **El README ya no lleva el correo ni la web personal de Jeremi** en la sección Licencia (decisión de Jeremi del
  2026-10-26): el mantenedor se nombra con su perfil de GitHub, como en el README del motor.
- **El README, al día con lo que hay en `develop`.** Contaba el gateway de antes de Bifröst y de la ADR-0025. Ahora
  cuenta:
  - el camino de un mensaje por `WEBHOOK`, `hive-ingest`, `MENSAJES` y `RESPUESTAS`, y la cola `llm` como heredada;
  - `POST /oauth/token`, las rutas de `Clients`, la de las vueltas y los pasos para dar de alta un bot;
  - los paquetes y entidades nuevos (`Client`, `ConversationMessage`, `SeenToken`, `clientId`);
  - la configuración de Bifröst, de las trazas y de `ops.engine_keys`;
  - `hive-docs`, el túnel y el vigía en el despliegue, y que hoy se despliega a mano desde `develop`;
  - en Seguridad, el token de acceso de 10 minutos, la API publicada y la documentación aparte.

  También dice que Instagram está en el contrato pero el webhook todavía no lo lee, y que `GET /chat/{bot_id}/info`
  devuelve el `verifyToken`. Se corrigen al paso los comentarios desfasados de `config.env.example`, del compose y
  de `webhook.py` e `ingestion.py`.
- **El contrato de la API de gestión, puesto al día para publicarlo** (ADR-0026).
  - Se llama «Hive management API», y su descripción explica cómo se pide el token (ADR-0025). Ya no decía lo de
    antes: que cada petición llevaba un JWT firmado por quien llama.
  - Corregidas la descripción de `GET /chat/{bot_id}/rules`, que devuelve la persona y las reglas como datos, y la
    de la exportación de un contacto, que incluye su historial.
  - Sin contacto ni términos de servicio (decisión de Jeremi del 2026-10-26).
  - El generador añade a cada contrato dónde atiende (`servers`) y pone en `info.version` la versión del
    CHANGELOG.
- **Incompatible para todo cliente de la API: OAuth 2.0 `client_credentials`** (ADR-0025; decisiones de Jeremi del
  2026-10-26).
  - **`POST /oauth/token`** recibe una aserción (el JWT firmado por el cliente dentro de un JWE de antes, ahora con
    `sub` igual a `iss`, RFC 7523) y devuelve un token de acceso: un JWT que firma el gateway (`typ: at+jwt`, `ES256`),
    que vale 10 minutos y para todas las peticiones. Errores de RFC 6749: `invalid_request`, `unsupported_grant_type`
    e `invalid_client`.
  - **Las rutas solo aceptan tokens de acceso.** El token de una petición de antes ya no entra: el cambio es de golpe.
  - **Una petición deja de ir a la base para autenticarse**: el token dice quién es el cliente. Solo la petición de
    un token escribe su `jti`.
  - **Revocar una clave o borrar un cliente** impide al momento pedir un token; el que ya tenía vale hasta que
    caduca. Para cortarlos todos, se rota la clave de firma del gateway.
  - **El gateway tiene una clave de firma propia** (P-256, `use: sig`) en su fichero de claves: no arranca sin ella
    (`python -m ops.gateway_key generate-signing`). No se entrega en `GET /gateway/keys`.
  - **La prueba de una clave nueva** (`POST /keys`) se ata al `jti` del token de acceso y vale una vez.
  - `ops.client` pasa a `access-token`, `assertion` y `add-key` con el token; `ops.engine_keys` pide un token y lo
    reutiliza. El contrato OpenAPI declara el esquema `oauth2` con el flujo `clientCredentials`.
- **Todo pasa a llamarse Hive** (ADR-0024, versión 0.3.0; decisión de Jeremi del 2026-10-22): «la plataforma para la
  gestión automatizada de tus bots y agentes».
  - El repositorio es `jeremialcala/hive`; GitHub redirige `wh-python-fastapi-messenger`.
  - Las imágenes, `ghcr.io/jeremialcala/hive-gateway` y `hive-engine`. `build` ya no toma el nombre del repositorio.
  - **Incompatible para el despliegue:** el proyecto compose del gateway es `hive-gateway`, en
    `/srv/apps/hive-gateway`, y sus contenedores `hive-webhook`, `hive-api` y `hive-ingest`. Los nombres públicos del
    túnel apuntan a `http://hive-webhook:5002` y `http://hive-api:5002`. Los alias de red con los nombres viejos, que
    cubrieron el traslado, se quitaron el mismo día.
  - El vigía, el runbook del motor y la documentación de operación usan los nombres nuevos.
- **`GET /chat/{bot_id}/rules` devuelve datos, no un prompt** (E4 del plan de los workers del motor; decisión de
  Jeremi del 2026-10-20). Antes armaba un prompt para Llama 2 (`<s> <<SYS>> Tu nombre es… <</SYS>>`), pensado para el
  motor de antes, con Ollama. Ahora `data` es `{"name", "role", "tone", "description", "rules": [...]}`, y el motor
  escribe con ellos el `system` de Claude. **Es un cambio incompatible** para quien leyera la cadena: el único que lo
  hacía era ese motor, que ya no existe, y el contrato OpenAPI no documentaba la respuesta.
- **Incompatible para la API de gestión (I7):** en `apiCall`, `method` solo admite `GET`, `POST`, `PUT`, `PATCH` o
  `DELETE` en mayúsculas; `authType`, `none`, `bearer`, `api_key` o `basic`; `timeout`, de 1 a 30 segundos; y
  `headers` no puede fijar `Host`. Antes se guardaba cualquier valor.
- **Incompatible para la API de gestión (I6a):**
  - **`POST /clients/{client_id}/service-keys` exige el campo `worker`.** Sin él, o con uno que no existe, responde
    `422`.
  - **`serviceKeys`, en `GET /keys` y `GET /clients`, pasa a ser una lista de `{"kid", "worker"}`**, en lugar de una
    lista de `kid`.
  - **Las claves del motor registradas antes no tienen worker** (`"worker": null`). Siguen alcanzando las cinco rutas
    del motor, vueltas incluidas, hasta que el motor se parta en workers. Cada uso deja un aviso en el log.
- RNF01 tiene cifra: 250 ms de mediana y menos de un 1 % por encima de 1 s. Es un objetivo propio: la documentación
  actual de Meta no publica ninguna; la cifra viene de la guía de 360dialog.
- **Incompatible para el despliegue:** el webhook y la API de gestión son dos servicios, de la misma imagen y en dos
  contenedores (ADR-0018, primer incremento del refactor). `uvicorn webhook:app` sirve solo `GET` y `POST
  /chat/{bot_id}` y `/health`, sin el par de claves del gateway ni el registro de supresiones; `uvicorn app:app`,
  el resto. Así una petición de gestión lenta ya no para el webhook (RD-05), y el túnel publica el webhook sin
  filtrar por ruta (RS09). El compose de despliegue los levanta en los puertos 5003 y 5002 de loopback.
- Dos contratos OpenAPI: `openapi.yaml` para la gestión y `openapi-webhook.yaml` para el webhook.
- `GATEWAY_KEYS_FILE` y `GATEWAY_AUDIENCE` solo los exige la API de gestión.

### Eliminado
- **`EngineKeyStore.api_key`** (`shared/engine_key_store.py`), que leía de Andvari la clave de la API de Anthropic de
  un worker. El motor ya no usa Claude (ADR-0010 del motor), y Andvari borró esas claves
  (`motor/retirar-anthropic.sh` de andvari).

### Seguridad
- **OpenTelemetry sube a 1.45.1** (y sus paquetes `0.66b0` a `0.66b1`) en los locks del gateway: CVE-2026-114603 de
  `opentelemetry-api`, que pip-audit marcó en el CI el 2026-10-22. La 1.45.1 es del 2026-10-06 y ya tenía las dos
  semanas que se esperan antes de tomar una versión.
- **`certifi` sube a 2026.10.19** en los locks del gateway (GHSA-6q48-6c7w-9gw2, crítica, publicada el 2026-10-20).
  Las versiones anteriores confían en las raíces de emSign, de las que dependen ocho intermedias cuyas claves privadas
  se comprometieron: quien tenga una y esté en el camino puede suplantar cualquier sitio HTTPS ante `requests` y `pip`.
  Se toma con un día, contra la regla de las dos semanas, porque ninguna versión anterior trae el arreglo.
  `requirements.txt` la fija como mínima. Los locks del motor ya la tenían. La imagen del gateway se reconstruye al
  fusionar.

### Corregido
- **Un despliegue de la PWA llega al navegador enseguida** (HIG-87). El portal no decía nada del caché de sus ficheros,
  así que Cloudflare y el navegador los guardaban 4 horas (`max-age=14400`). El 2026-10-30, el texto de
  `sample_template` (#134) no llegaba, y tras un despliegue podía correr un `app.js` nuevo con un `api.js` viejo.
  - Ahora todo lo que no lleva ya `Cache-Control` sale con `no-cache`: la página, los módulos, el sistema de diseño
    y las fuentes. Se revalida por `etag`, y lo que no cambió vuelve como `304`. Lo que lleva sesión sigue con
    `no-store`.
  - Prueba: `test_the_pwa_s_files_are_asked_again_after_a_deploy`.
- **El portal ya no escribe cada petición en su log** (HIG-93). `uvicorn` dejaba la línea de cada
  petición, y la de `/auth/callback` llevaba en la consulta el código del inicio de sesión. Ahora arranca con
  `--no-access-log`, como el gateway. Se vio en el primer arranque en midgard, antes de que nadie iniciara sesión.
- **A la cuenta de servicio de Zitadel sin su rol se la reconoce como tal** (HIG-94). En la primera invitación real
  (2026-10-30), Zitadel respondió `404 membership not found (AUTHZ-cdgFk)`: el rol `ORG_USER_MANAGER` se había creado
  como rol del proyecto, no en *Administrator Roles*. Hive lo daba como `422 IDP_REFUSED (404)`, como si Zitadel
  rechazara a la persona.
  - **El id del error:** Hive guarda el id que Zitadel pone al final del mensaje (`AUTHZ-…`, `COMMAND-…`). Nunca
    guarda el mensaje, que puede llevar el correo.
  - **Los errores de autorización** (`AUTHZ-`, `AUTH-`) son de la cuenta de servicio: dan `503`, y el log dice qué
    rol le falta.
  - **Un no de Zitadel** a la persona da `422 IDP_REFUSED (<estado>, <id>)`.
  - **La guía de autenticación** (0.5.2) dice dónde va el rol.
- **La guía guardaba el token de Zitadel donde `hive-api` no lo podía leer** (HIG-94). Lo dejaba como fichero de
  root, pero el contenedor corre como el usuario 10001, como los otros secretos de `keys/`. Ahora la guía (0.5.1)
  lo deja de ese usuario, en modo `400`. Se vio al desplegar, antes de crear el token.
- **Cada aviso de plantilla deja su rastro en el log de producción** (HIG-86). Producción solo guarda desde
  `WARNING`, y el cambio aplicado iba en `INFO`, así que no quedaba nada. El primer aviso real (2026-10-29) se
  descartó «de otra cuenta» sin decir cuál. Ahora cada aviso deja un `WARNING` con su plantilla y su estado:
  - aplicado;
  - de otra cuenta, con la cuenta del aviso y la del bot;
  - de una plantilla que Hive no tiene;
  - anterior a lo que la copia sabe;
  - o sin su forma.
  Solo se escriben ids de Meta. Lo que no tiene forma de id sale como `?`.
- **Un no de Meta a las plantillas dice también su subcódigo** (HIG-85). Con solo el código, el `100` del primer
  sync en producción (2026-10-29) no distinguía una cuenta equivocada de un token sin permiso. Ahora la respuesta
  y el log llevan `código/subcódigo`, solo si son números. El `100/33` explica qué revisar: el `wabaId` y el
  permiso `whatsapp_business_management`. El mensaje de Meta sigue sin guardarse.
- **El vigía ya no arranca lo que alguien paró** (`deploy/vigia/hive-vigia.sh`, HIG-96). Docker marca `unhealthy`
  también un contenedor parado, y el vigía le hacía `docker restart`, que lo arranca. El 2026-10-28 deshizo en 23 s el
  `docker stop hive-idp-proxy` de la prueba de aceptación de HIG-96, y lo mismo habría hecho en medio de una
  restauración o un mantenimiento. Ahora solo reinicia lo que está en marcha y unhealthy (`running unhealthy`). Vale
  para midgard y para Nidavellir, que usan el mismo script.
- **La raíz de `hive-auth.higerotech.com` lleva a la consola de Zitadel** (HIG-91). Llevaba a la página de login, que
  abierta sola no vuelve a ninguna aplicación y terminaba en la cuenta del usuario. Ahora `/` redirige con un 302 a
  `/ui/console`, que arranca su propio flujo OIDC.
- **El proxy de Zitadel arranca** (HIG-91). La imagen de Caddy trae `/usr/bin/caddy` con la capacidad de fichero
  `cap_net_bind_service`, y con `cap_drop: ALL` el kernel se negaba a ejecutarlo («operation not permitted»).
  `hive-idp-proxy` recupera solo `NET_BIND_SERVICE`. El runbook de `deploy/idp/` dice ahora dónde generar la clave de
  `age` (fuera del repositorio y de OneDrive), cómo llamar al compose sin `cd` y que `config` va siempre con `-q`.
- **Las fechas de los documentos se guardan en UTC y lo dicen** (H10, HIG-23).
  - Las entidades y el campo `timestamp` de las respuestas usaban `datetime.now`, una hora local sin zona, que
    MongoDB toma por UTC: en una máquina de Venezuela quedaban cuatro horas antes.
  - Ahora usan `utc_now` (`src/classes/tool_clock.py`), igual que el historial y el sobre de la cola.
  - Las fechas leídas de la base, que pymongo entrega sin zona, se marcan como UTC con `as_utc` antes de
    compararlas o de devolverlas. La exportación de un contacto y la ficha de un número las dan con `+00:00`.
  - En midgard la imagen ya corría en UTC. Comprobado en Atlas: los 918 documentos con fecha tienen el
    `createdAt` igual a la hora UTC de su `_id`, así que no hay datos que migrar.
- **El webhook y la ingesta ya no esperan a MongoDB, a Andvari ni a la API Graph dentro del bucle de eventos**
  (H14, HIG-31).
  - En el webhook, la búsqueda del bot y la verificación son funciones, que FastAPI corre en su pool de hilos, y
    los secretos del bot se abren en un hilo. Un fallo de la caché ya no deja sin respuesta las demás
    notificaciones ni `/health`.
  - En la ingesta, la búsqueda del bot, la identificación del contacto, que en Messenger pide el perfil a la API
    Graph, y la lectura y escritura del historial van en `asyncio.to_thread`. El otro consumidor del worker y los
    latidos de NATS siguen mientras tanto.
  - La identificación del contacto pierde el registro de eventos, que nunca se llamaba con un `eventId`.
  - `tests/unit/test_event_loop.py` lo comprueba con esperas de 1,5 s. Sus 12 pruebas fallan con el código
    anterior.
- **La API de gestión tampoco espera dentro del bucle de eventos** (H14, HIG-31).
  - Corre un solo worker, y unas 24 rutas eran corrutinas que llamaban a controladores que esperan a MongoDB y a
    Andvari: una lectura lenta retenía todas las peticiones, `/health` incluido.
  - Las rutas y sus controladores pasan a ser funciones. Los tres borrados siguen siendo corrutinas, porque
    esperan el aviso al motor por el bus, pero borran en un hilo.
  - `tests/unit/test_management_event_loop.py` fija qué rutas pueden ser corrutinas y comprueba con esperas de
    1,5 s que `/health` responde mientras tanto. Sus 4 pruebas fallan con el código anterior.
- **La API de gestión se quedaba sin responder, health check incluido, cuando una consulta a Atlas no volvía.** En
  midgard (2026-10-21), `bots-gateway` estuvo seis minutos sin contestar y cuatro mensajes de Hive esperaron entre 6 y
  7 minutos su respuesta. Antes, el token de Meta tardó una vez 70 s.
  - **Causa:** la conexión a MongoDB no tenía ningún timeout, así que pymongo esperaba para siempre en una conexión
    TCP que había muerto sin cerrarse: tenía datos sin confirmar en la cola de envío. Y como las rutas son
    corrutinas que llaman a código bloqueante, esa espera retenía el event loop y, con él, todas las peticiones.
    Atlas en sí respondía bien: un ping, en 60–750 ms; las vueltas, en unos 80 ms.
  - **Las esperas tienen fin:** los 14 módulos de `classes/` se conectan con `connect_database`, con
    `socketTimeoutMS`, `connectTimeoutMS` y `serverSelectionTimeoutMS` de 5 s (`DB_TIMEOUT_MS`), por debajo de los
    10 s que el coordinador del motor espera por llamada. Una conexión inactiva más de 60 s (`DB_MAX_IDLE_MS`) se
    cierra en vez de reutilizarse.
  - **Fuera del event loop:** la autenticación (`get_principal`), `get_repository` y las cuatro rutas del motor
    (reglas, herramientas, vueltas y tokens) son funciones, no corrutinas: FastAPI las corre en su pool de hilos y una
    lectura lenta solo retiene su propia petición. El resto de rutas siguen siendo corrutinas: con el timeout, una
    lectura colgada las retiene 5 s como mucho.
- **El worker de ingesta se reiniciaba cada pocos minutos.** En midgard, `bots-ingest` llegó a 13 reinicios en unas
  horas (2026-10-20).
  - **Causa:** nats-py avisa de que no hay nada que leer con su `nats.errors.TimeoutError`, pero a veces lo hace con
    el `asyncio.TimeoutError` de siempre: cuando el tiempo se agota entre sus dos peticiones a Bifröst. `consume()`
    solo capturaba el suyo, así que ese error tumbaba el proceso.
  - **Arreglo:** ahora captura los dos.
  - **No se perdían mensajes:** JetStream reentrega lo que no se confirmó. El `ConnectionClosedError` del log era
    la consecuencia: al caerse un bucle, el otro se quedaba sin conexión.
- Las alertas de CodeQL abiertas en `main` (2026-10-19).
  - **`controller/intents.py`:** las seis funciones respondían desde un `finally` con `return`, que se tragaba
    cualquier excepción salida de los propios `except` y también una cancelación (`asyncio.CancelledError`). La
    respuesta se arma ahora después del `try`; lo que antes contestaban no cambia.
  - **`classes/entity_jwk.py`:** la clave privada de cada evento se escribía sin cerrar el fichero si fallaba la
    escritura, y con los permisos por defecto. Ahora se crea con permisos solo para su dueño (600) y se cierra
    siempre.
  - **39 imports sin usar** quitados, en `classes/`, `utils/` y `controller/chatbot.py`.
  - **`__all__` en los diez módulos que se importan con `*`,** en `constants/`, `controller/` y
    `utils/key_management.py`. Se borra `constants/locations.py`, que estaba vacío.
  - **Las cinco alertas de `app.py`** (cuatro imports y una variable sin usar) ya no existen en `develop`, y se
    cierran al llegar a `main`.

## [0.3.0] - 2026-10-18

Cierre del Gate 2 (implementación).

### Añadido
- ADR-0017, propuesta: el historial de cada conversación se guarda en el gateway (Atlas, cifrado con la clave del
  cliente, exportable y con supresión) y el contexto del coordinador, las últimas 10 vueltas más un resumen, vive
  24 horas en un bucket KV de NATS. Decidido el 2026-10-18 que el historial se exporta, que el contexto caduca a las
  24 horas y su tamaño; quedan abiertas la retención del historial y los adjuntos.
- La parte de la IA de la revisión dual del Gate 2 (`docs/03-implementation/revision-dual-gate-2.md`): cuatro
  revisores sobre todo lo que entró desde la 0.2.0, sin hallazgos críticos, uno alto, siete medios y trece bajos.
  Anotados en el threat model H14 (llamadas que bloquean el proceso) y H15 (gates que no son obligatorios).
- Plan para encajar el gateway en la topología de ocho workers que propone el análisis «Rust vs. Python»
  (`docs/00-project/plan-topologia-de-workers.md`): revisión del análisis, por qué el webhook actual no cumple los
  250 ms de mediana que pide Meta para WhatsApp, ocho cambios y el recorrido por los gates. Decidido el
  2026-10-18: NATS JetStream en lugar de RabbitMQ, la identificación del contacto fuera del webhook, una credencial
  por worker, Instagram como tercer canal y cerrar antes el Gate 2.
- ADR-0013, aceptado: OpenBao como gestor de secretos y controlador central de claves, desplegado en el servidor
  con el nombre de Andvari. La clave maestra de ADR-0010 vive en su motor Transit, derivada por cliente.
- Workflow `Seguridad`, con tres gates que rompen el build: fuga de secretos (gitleaks sobre la historia
  completa), dependencias vulnerables (`pip-audit` sobre el lock) y análisis estático (CodeQL sobre todo el código,
  con umbral en los hallazgos de seguridad altos y críticos).
- `requirements.lock` y `requirements-dev.lock`: dependencias fijadas con hashes. La imagen y el CI instalan
  desde ellos.
- Suelo de cobertura por ramas en el CI: 80 %, lo que pide el Gate 2.
- Aislamiento entre clientes en la capa de datos (ADR-0012): entidad cliente, `clientId` obligatorio en
  chatbots, números, reglas, intents y contactos, y capa de repositorio por la que pasa todo acceso a esos
  datos. Lo ajeno responde `404`, igual que lo inexistente.
- Autenticación por cliente (ADR-0005, ADR-0009): un JWT firmado por el cliente (`ES256`) dentro de un JWE cifrado
  para el gateway (`RSA-OAEP-256`, `A256CBC-HS512`), de 60 segundos como máximo y un solo uso.
- Registro de clientes: `POST` y `GET /clients` para el operador, con las claves de un cliente en
  `/clients/{client_id}/keys`; y `/keys` para que cada cliente rote la suya. `GET /gateway/keys` devuelve la
  audiencia y las claves públicas del gateway.
- Comandos `python -m ops.gateway_key`, `ops.operator_key` y `ops.client`, y la guía
  `docs/03-implementation/autenticacion.md`.
- Cifrado de los datos de cada cliente (ADR-0010, ADR-0013): los números de contactos y bots, los tokens de Meta,
  el App Secret y el token de verificación se guardan cifrados (AES-256-GCM) con una clave por cliente, envuelta
  por la clave maestra de Andvari. El contacto se busca por un índice ciego.
- `PUT /chat/{bot_id}/credentials`: el cliente carga y rota las credenciales de Meta de su bot.
- `python -m migrations.encrypt_client_data`: cifra lo que estaba guardado en claro. Sin `--apply` solo informa.
- Pruebas de integración contra un OpenBao real, también en el pipeline, y la guía
  `docs/03-implementation/cifrado-de-datos.md`.
- `python -m migrations.assign_operator_client`: asigna al cliente del operador los documentos anteriores a la
  existencia de clientes. Sin `--apply` solo informa.
- Sobre versión 1 de la cola `llm` (ADR-0011): cada sobre lleva `schemaVersion` y, en `messageId`, el
  identificador que Meta dio al mensaje, que es la clave con la que el motor de respuestas deduplica.
- Caducidad de 24 horas en cada mensaje publicado: el broker descarta el que nadie ha consumido.
- Pruebas de integración de la publicación contra un RabbitMQ real, también en el pipeline.
- Contactos de un bot (RF08): `GET /chat/{bot_id}/contacts` los lista por páginas, y `POST /contacts/search` encuentra
  a una persona, por número o por identificador de Messenger, en todos los bots del cliente.
- Exportar y borrar un contacto (RF09; GDPR, arts. 15, 17 y 20): `GET` y
  `DELETE /chat/{bot_id}/contacts/{contact_id}`.
- Baja de un bot y de un cliente (RF10): `DELETE /bot/{bot_id}` borra un bot con sus contactos, números, reglas e
  intents; `DELETE /clients/{client_id}`, solo para el operador, borra a un cliente con todo lo suyo, su clave de
  datos envuelta incluida. Los respaldos anteriores a una baja siguen siendo legibles.
- Guía `docs/03-implementation/exportar-y-borrar.md`.
- Registro de supresiones (ADR-0014): cada borrado de un contacto, de un bot o de un cliente se apunta, antes de
  hacerse, en ficheros fuera de la base de datos (`ERASURES_DIR`), con identificadores y fechas y sin datos
  personales. `python -m migrations.replay_erasures` repite los borrados después de restaurar un respaldo; sin
  `--apply` solo informa.
- ADR-0016, aceptado: el motor de respuestas tiene una credencial por cliente.
- Credencial del motor de respuestas (RS06): el operador registra bajo cada cliente la clave pública con la que el
  motor actúa por él, en `POST` y `DELETE /clients/{client_id}/service-keys`. Con ella el motor alcanza las reglas,
  las herramientas y los tokens de los bots de ese cliente, y ninguna otra ruta.
- `GET /chat/{bot_id}/tokens`: los tokens de Meta de un bot, solo para el motor. Cada entrega deja una línea en el
  log.
- El sobre de la cola lleva `clientId`, el cliente dueño del bot. El contrato AsyncAPI pasa a la versión 1.1.0;
  `schemaVersion` sigue en 1.
- `GET /keys` y `GET /clients` listan en `serviceKeys` las claves del motor que alcanzan los bots de cada cliente.
- ADR-0015, aceptado: a Meta solo se le pide reintento, con un `503`, cuando falta algo que el servicio necesita.
- Límite de tamaño: ninguna petición puede pasar de 3 MiB, que es lo más que envía Meta. Por encima responde `413`,
  antes de comprobar firma o token.
- ADR-0014, aceptado: en los respaldos la supresión se cumple por caducidad, y los respaldos de la base se
  conservan 365 días como máximo. Corrige el apartado de borrado de ADR-0010, que contaba con que destruir la
  clave de datos de un cliente alcanzara a los respaldos.

### Cambiado
- El webhook va menos veces a la base: el chatbot que encuentra se guarda 30 segundos en memoria, y al buscar el
  contacto ya no se pide dos veces. Un mensaje de un contacto conocido pasa de 4 viajes a Atlas a 2 en WhatsApp y de
  3 a 2 en Messenger; uno de un contacto nuevo, de 7 a 5 en WhatsApp y de 6 a 5 en Messenger. Con 60 ms por viaje,
  medidos el 2026-10-18 desde la red de midgard, son unos 120 ms frente a los 240 de antes y los 250 de mediana que pide Meta. Borrar el chatbot o su
  cliente, o cambiar sus credenciales, lo saca de la memoria del proceso que lo hace; en los demás procesos dura
  como mucho esos 30 segundos. Lo que no se encuentra no se guarda. Es un arreglo intermedio: la solución es el
  worker de ingesta del plan (R1), y no resuelve que esas llamadas bloqueen el proceso (RD-05).
- **Incompatible para el consumidor de la cola:** el gateway publica un sobre por cada mensaje de la notificación,
  y en WhatsApp el contenido lleva solo ese mensaje y a quien lo envió. Los acuses de entrega y de lectura, los ecos
  de lo que envía la página y las pulsaciones de botones de Messenger se aceptan con `200` y no se publican.
- Un mensaje de WhatsApp sin el número al que iba dirigido (`metadata.phone_number_id`) no se publica: el motor no
  tendría desde dónde responder. Antes salía con un número de ejemplo.
- El procesamiento de la notificación ya no escribe en el log el contenido de los mensajes. La identificación del
  contacto sigue registrando su número o su identificador.
- Los chatbots guardados antes de este cambio responden `404`, también en el webhook, hasta que se ejecute la
  migración que les asigna cliente.
- El gate de análisis estático bloquea por hallazgos altos o críticos fuera del código de pruebas. Los de
  `tests/` se informan como aviso: una prueba maneja secretos de mentira por necesidad.
- **Incompatible:** el servicio no arranca sin los ajustes del servicio de claves (`ANDVARI_ADDR`,
  `ANDVARI_ROLE_ID` y `ANDVARI_SECRET_ID_FILE`), y fuera de desarrollo la dirección tiene que ser `https`.
- **Incompatible:** `GET /chat/{bot_id}/info` ya no devuelve los tokens de Meta, solo cuáles están cargados; y
  `GET /chat/{bot_id}/phones` necesita el servicio de claves para devolver los números.
- Un bot con secretos en claro responde `404` en el webhook hasta que se ejecute la migración de cifrado.
- Sin el servicio de claves, el webhook y lo que toca datos cifrados responden `503`.
- La respuesta `422` a una petición mal formada dice dónde está el error y por qué (`type`, `loc` y `msg`), y ya no
  trae `input` ni `ctx`.
- **Incompatible para el despliegue:** borrar un contacto, un bot o un cliente responde `503` mientras el registro de
  supresiones no se pueda escribir. En el servidor hay que crear el directorio `erasures/`, con dueño `10001`, e
  incluirlo en los respaldos.
- Cada petición deja una sola línea en el log: el método, la ruta tal como está declarada, los identificadores bien
  formados de su dirección, el código y el tiempo. Ya no se escribe la dirección tal como llegó ni la de quien llama.
- De un fallo se escribe en el log su tipo y la línea de la que salió, no su texto.
- **Cambia lo que se responde a Meta** (ADR-0015). Una notificación firmada que no es JSON recibe `200` y un aviso en
  el log; antes recibía `422`. Una sin tipo de objeto o sin entradas se descarta con un aviso; antes se aceptaba
  rellenando valores de ejemplo. Sin firma válida la respuesta es `403` aunque el cuerpo no sea JSON; antes era `422`.
- Si no responde la base de datos, el webhook responde `503`, igual que cuando falta el broker o el servicio de
  claves.
- Un mensaje de Messenger se publica sin el nombre cuando la Graph API no da el perfil, y su contacto se guarda sin
  él.
- Los mensajes de una notificación que se dejan fuera por incompletos, y las notificaciones de un tipo que no se
  atiende, dejan un aviso en el log.
- La imagen arranca Uvicorn sin su log de accesos.
- **Incompatible:** la cabecera `X-API-Key` y el ajuste `ADMIN_API_KEY` desaparecen. Todo lo que no es el webhook
  ni `/health` pide el token nuevo, y el servicio no arranca sin `GATEWAY_KEYS_FILE` y `GATEWAY_AUDIENCE`.
- El operador es un cliente más en las rutas de los chatbots: solo alcanza los suyos.
- Una clave de `config.env` que ya no corresponde a ningún ajuste se ignora, en vez de impedir el arranque.
- `GET` y `POST /chat/{bot_id}/phones` responden `404` para un chatbot que no existe. Antes respondían `200`
  con el texto `HTTP_404_NOT_FOUND` en el cuerpo.
- La imagen instala solo las dependencias de ejecución; las de pruebas pasan a `requirements-dev.txt`.
- Se retiran once dependencias que el código no importaba: `fastapi-sessions`, `asgi-correlation-id`,
  `python-multipart`, `pytz`, `pyjwt`, `pycryptodome`, `keyboard`, `segno`, `pillow`, `phonenumbers` y
  `pydantic-extra-types`.

### Eliminado
- El envío de mensajes por WhatsApp desde el gateway, que no se usaba: responder es cosa del motor (ADR-0002).
- La mención al endpoint `/answers` en la descripción de la API. Nunca existió, y entregar la respuesta es cosa del
  motor (ADR-0002).

### Corregido
- Hallazgos de la revisión dual del Gate 2, cada uno con su prueba:
  - Añadir una clave mientras se revocaba otra devolvía la revocada, y podía recrear un cliente borrado: el alta y la
    baja son ahora una sola actualización condicionada (RD-02).
  - Lo que la Graph API no devolvía de un perfil de Messenger se guardaba con el nombre, la foto y el identificador
    de una persona real; el identificador del contacto sale ahora del remitente del mensaje (RD-03).
  - La migración de cifrado devolvía a la base un documento borrado mientras corría (RD-08).
  - Repetir las supresiones desde un directorio que no existe decía que no había nada que hacer (RD-10).
  - Un intent borrado se podía leer y modificar (RD-11).
  - Una respuesta malformada del servicio de claves acababa en `500` y no en `503` (RD-13).
  - Las pruebas de logs del motor solo veían los avisos, y una de autenticación dependía de lo que tardara la
    batería (RD-07, RD-18).
- `metadata` de WhatsApp salía hacia la cola como una lista de pares y no como un objeto, y el motor no podía leer
  el número desde el que responder.
- De una notificación con varios mensajes no salían todos: con varias entradas, o con varios eventos de Messenger,
  solo se publicaba lo último, y los demás se perdían con un `200`.
- El filtro que debía descartar el eco de lo que envía la propia página en Messenger no descartaba nada: el eco
  seguía el camino de un mensaje más.
- La fecha del sobre dependía de la zona horaria de la máquina: fuera de UTC salía desplazada.
- Con la base de datos caída, el webhook respondía `200` y el mensaje se perdía. Ahora responde `503` y Meta lo
  reenvía.
- Si la Graph API no devolvía el perfil de quien escribía por Messenger, el mensaje se perdía con un `200`.
- Un mensaje que fallaba al procesarse se llevaba consigo a los que venían detrás en la misma notificación.
- A una notificación sin tipo de objeto o sin entradas se le ponían valores de ejemplo en vez de rechazarla.
- Un fallo al guardar un contacto se tragaba dentro de un `finally` con `return`, y el mensaje se perdía con un
  `200`. Ahora la caída del servicio de claves llega hasta Meta como `503`.
- Un intent se podía leer, cambiar o desactivar a través de cualquier chatbot: la ruta no comprobaba que fuera
  suyo. Y se podían añadir reglas a un chatbot que no existe.
- Los valores por defecto de las entidades se calculaban una sola vez, al importar. Todos los documentos
  guardaban como fecha de creación la hora de arranque del proceso, todos los mensajes de la cola compartían
  `_uuid`, y todas las credenciales compartían la clave con la que se firma el token de verificación.
- La marca de tiempo de las respuestas de la API era también la hora de arranque.
- Las respuestas de los endpoints de gestión llegaban rotas al cliente desde la actualización de Starlette: los
  controladores enviaban el cuerpo como texto, y la versión nueva ya no lo convierte. Ahora lo envían en bytes, y
  `Content-Length` cuenta bytes y no caracteres, que no coincidían en cuanto había una tilde.

### Seguridad
- **Incompatible para los clientes:** `POST /keys` exige, además del token, la prueba de que quien añade la clave
  tiene su parte privada: un JWT que firma la clave nueva (`typ: key-proof+jwt`) para el `jti` del token de la misma
  petición. Sin ella responde `422` `INVALID_KEY_PROOF`. El cliente de referencia la genera con
  `python -m ops.client add-key`. El operador sigue añadiendo claves sin prueba: es quien deja volver a un cliente que
  perdió la suya. No frena a quien robó una clave, que puede probar que tiene una propia (RD-04).
- Un POST al webhook sin nada con forma de firma recibe `403` sin abrir los secretos del bot (RD-09).
- Un token solo se acepta con el JWT en forma compacta, y el emisor firmado tiene que ser el del token (RD-12).
- Cada alta de una clave de cliente deja un aviso en el log con su `kid`, y cada baja otro; el procedimiento de
  revocación pide quitar todas las claves que el cliente no reconozca (RD-04).
- `pip-audit` audita también el lock que instala la imagen, y el `.dockerignore` excluye los secretos en cualquier
  directorio (RD-06, RD-17).
- FastAPI pasa de 0.110.0 a 0.143.0 y Starlette de 0.36.3 a 1.7.0. La versión anterior de Starlette tenía 14
  vulnerabilidades conocidas; con las dependencias nuevas, `pip-audit` no encuentra ninguna.
- Los valores de ejemplo de los modelos del webhook llevaban números de teléfono reales, uno de ellos también
  codificado dentro de un identificador de mensaje de WhatsApp. Se sustituyen por números del rango reservado
  para ficción. Siguen en la historia de git y en las imágenes ya publicadas.
- La respuesta a una petición mal formada repetía lo enviado: una credencial de Meta rechazada por su formato volvía
  en el `422`. Ya no devuelve el valor.
- El cuerpo de una notificación se interpretaba antes de comprobar su firma: quien no tenía el secreto de la
  aplicación podía hacer que el servicio interpretara lo que enviara. Ahora no se lee nada sin firma válida.
- El log de accesos de Uvicorn escribía cada petición con su query string, también en `production`: el token de
  verificación que Meta envía al comprobar el webhook quedaba en el log del contenedor. Está apagado.
- Los logs ya no llevan números, nombres, identificadores de Messenger, contenido de mensajes, reglas ni credenciales,
  en ningún nivel, ni el texto de las excepciones, que los repetía. Tampoco lo que escribirían el driver de la
  base y el cliente HTTP.
- Dos respuestas de error devolvían el texto de la excepción. Un fallo inesperado responde ahora `500` sin él.
- `/docs`, `/redoc` y `/openapi.json` solo se sirven en `development`. `ENVIRONMENT` solo admite `development`,
  `staging` o `production`: con cualquier otro valor el servicio no arranca, en vez de arrancar con otro nivel de log
  y sin exigir HTTPS a Andvari. El compose de despliegue lo fija en `production` (RD-16).

## [0.2.0] - 2026-10-12

Cierre del Gate 1 (diseño).

### Añadido
- Documentos del Gate 1, aprobados: arquitectura (C4, flujos, ciclo de vida del mensaje, modelo de datos) y
  threat model (21 amenazas con STRIDE y DREAD, y seis hallazgos del diseño).
- Contratos: OpenAPI generado de la aplicación y AsyncAPI de la cola `llm`, con pruebas de contrato que los
  verifican contra el código (`tests/contract`).
- ADR-0006 a ADR-0008, que registran decisiones ya construidas o tomadas: publicación confirmada, App Secret por
  bot y despliegue en el servidor propio.
- ADR-0009 a ADR-0012, aceptados: parámetros del token, clave de cifrado de datos por cliente, contrato de la
  cola y aislamiento por cliente en la capa de datos.

## [0.1.0] - 2026-10-12

Cierre del Gate 0 (requisitos). Primera versión con la estructura AI-DLC.

### Añadido
- Documentos del Gate 0, aprobados: charter, glosario, clasificación de datos (GDPR, con el cliente como
  responsable y la plataforma como encargada) y el PRD BAAS-001 del gateway como servicio BaaS, con el estado
  construido, parcial o pendiente de cada requisito.
- El Gate 0 identifica el motor de respuestas, `ms-python-metamsghandler`, y registra lo que falta entre los dos
  servicios: el motor rechaza los mensajes de Messenger que publica el gateway, descarta el bot de destino y
  nadie entrega todavía la respuesta al usuario (RF07, RF12).
- ADR-0002: el motor de respuestas, y no el gateway, envía la respuesta al usuario final.
- ADR-0003: el `bot_id` identifica cada mensaje y es la llave con la que el motor pide al gateway la configuración
  del bot; el identificador no autoriza por sí solo (RS18).
- ADR-0004: el webhook es lo único que se autentica solo con la firma de Meta; el resto de endpoints, con JWE y
  material de clave propio de cada cliente registrado. La clave `ADMIN_API_KEY` queda como mecanismo provisional.
- ADR-0005: el token de autenticación es un JWT firmado por el cliente y metido dentro de un JWE cifrado para el
  gateway, que solo registra la clave pública de cada cliente.
- Estructura AI-DLC: `.ai-dlc/` con los checklists de los gates 0–5, las plantillas y el mapeo OWASP; `docs/` por
  fase; `SECURITY.md`, `CONTRIBUTING.md`, `CODEOWNERS` y este changelog (ADR-0001).
- Guardia de GitFlow: los pull requests hacia `main` solo pueden venir de `develop`, `release/*` o `hotfix/*`.
- Imagen de contenedor sobre `python:3.12-slim` fijada por digest, con usuario sin privilegios y `HEALTHCHECK`.
- Workflows `ci` (pruebas y build en cada pull request) y `build` (imagen `sha-<7>` en GHCR al llegar a `main`).
- `deploy/docker-compose.yml` para el servidor y `GET /health` como sonda de vida.
- Suite de pruebas que corre sin MongoDB, RabbitMQ ni `config.env`.
- `config.env.example` como plantilla de configuración.
- Gestión de intents por chatbot (`/chat/{bot_id}/intents`) y su esquema de herramientas (`/chat/{bot_id}/tools`).

### Cambiado
- El código pasa a `src/` y las pruebas a `tests/unit/`; `pytest` se ejecuta desde la raíz sin argumentos.
- El README pasa a español, documenta los endpoints de intents y `/health`, y deja de afirmar que el mensaje se
  cifra como JWE y que cada petición se registra como evento: ambas cosas están implementadas pero desactivadas.
- La configuración de logging se localiza junto al código, no en el directorio de trabajo.
- El webhook espera a que RabbitMQ confirme el mensaje antes de responder `200`, y responde `503` si el broker no
  está disponible. El consumidor de la cola `llm` debe tolerar duplicados.
- La verificación del webhook devuelve el challenge como texto plano y un `403` real ante un token equivocado.
- Los logs de producción van a la consola en vez de a `application.log`.

### Seguridad
- Los endpoints de gestión exigen la cabecera `X-API-Key` (`ADMIN_API_KEY`, 32 caracteres como mínimo).
- `POST /chat/{bot_id}` verifica `X-Hub-Signature-256` sobre el cuerpo crudo, con `appSecret` por chatbot o
  `FACEBOOK_APP_SECRET`.
- Los logs dejan de incluir query strings, cabeceras y la clave que firma el `verifyToken`.
- `.dockerignore` impide que `config.env` y `certs/` entren en la imagen.

## [0.0.1] - 2024-04-26

Primera versión etiquetada. Los cambios entre esta versión y la adopción de este changelog (2026-10-12) no se
registraron aquí; están en el historial de git.

[Unreleased]: https://github.com/jeremialcala/hive/compare/v0.9.0...HEAD
[0.9.0]: https://github.com/jeremialcala/hive/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/jeremialcala/hive/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/jeremialcala/hive/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/jeremialcala/hive/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/jeremialcala/hive/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/jeremialcala/hive/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/jeremialcala/wh-python-fastapi-messenger/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/jeremialcala/wh-python-fastapi-messenger/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/jeremialcala/wh-python-fastapi-messenger/compare/v0.0.1...v0.1.0
[0.0.1]: https://github.com/jeremialcala/wh-python-fastapi-messenger/releases/tag/v0.0.1
