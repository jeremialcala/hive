# Changelog

Todos los cambios notables de este proyecto se documentan en este archivo.

El formato está basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/),
y este proyecto se adhiere a [Versionado Semántico](https://semver.org/lang/es/).

Las versiones se cortan al cerrar cada gate de AI-DLC: Gate 0 → 0.1.0, Gate 1 → 0.2.0,
Gate 2 → 0.3.0, Gate 3 → 0.4.0, Gate 4 → 0.5.0, Gate 5 → 1.0.0. Ningún gate está cerrado
todavía, así que todo lo documentado sigue en `[Unreleased]`. El historial de commits anterior
a este fichero está en `docs/03-implementation/repo-history.md`.

## [Unreleased]

### Añadido
- **El notificador avisa de los pedidos del cliente que no envía** (HIG-87 del gateway). Hasta ahora se
  descartaban dejando solo una línea en el log. Ahora publica un `EnvioFallido` en
  `engine.reply.failed.{botId}` del stream `RESPUESTAS`, con el pedido, el motivo y, si fue Meta, su código. La API
  lo muestra como fallido, y la PWA explica por qué. Hay tres motivos:
  - `rejected`: Meta lo rechaza para siempre;
  - `no_token`: el bot no tiene token;
  - `gave_up`: se acabaron las entregas.

  El aviso no lleva el texto ni el número. Si Bifröst no lo toma, el pedido espera. En la última entrega, si
  tampoco lo toma, el aviso queda en el log. Necesita higerotech/bifrost#8.
- **El notificador envía lo que el cliente escribe a sus contactos** (HIG-84; ADR-0031 del gateway).
  - **El consumidor:** el tercero del notificador, `SALIENTES/notifier`. Lee cada `PedidoDeEnvio` y envía el texto o
    la plantilla desde el `phoneId` al `to` del pedido, con el token del bot que da el gateway.
  - **Sin duplicados:** lo apunta en `enviados` por pedido.
  - **Al historial:** lo publica en `RESPUESTAS` con `origin: client`, `sentBy` y `requestId`.
  - **`WhatsAppCloud.enviar_plantilla`:** el cuerpo `type: template`, con `language.code` y los valores como
    parámetros de texto del cuerpo; sin valores, sin `components`.
  - **Los rechazos de Meta,** como el resto: los códigos 131047, 132000, 132001, 132015, 132016 y 131049 no se
    reintentan.
  - **Pruebas:** `tests/unit/test_worker_salientes.py`, 17, y 7 más en `test_whatsapp_cloud.py`.
- **El LLM se mantiene caliente** (`src/infrastructure/llm/caliente.py`; decisión de Jeremi del 2026-10-22). El primer
  mensaje tras una noche sin uso tardó 17,8 s, frente a 4 s los siguientes: la clasificación, 5,4 s en vez de 1, y la
  respuesta, 6,7 s en vez de 1,3. Si el clasificador o el de respuesta llevan `LLM_CALENTAR_CADA` segundos (600) sin
  usar el LLM, le mandan una consulta mínima con su propio `system`, de un token de salida. El de respuesta usa el del
  último bot al que contestó, que es el que tiene su ranura. Un candado impide que coincida con una consulta de verdad
  del mismo worker, y un fallo queda en un aviso. El log solo dice «calentado» cuando de verdad se llamó al LLM: el de
  respuesta, antes de su primera respuesta, no tiene prefijo y no manda nada (visto en midgard el 2026-10-23).
- **[ADR-0011](docs/02-design/adr/ADR-0011-leido-y-escribiendo.md) (aceptada): leído y «escribiendo…» en WhatsApp**
  (E11; decisiones de Jeremi del 2026-10-21). El notificador atiende dos consumidores más, a la vez que el suyo
  (`src/workers/presencia.py`):
  - `notifier-leido`, sobre `MENSAJES`, pone en azul los ticks de cada mensaje en cuanto llega;
  - `notifier-escribiendo`, sobre `motor.clasificado`, muestra «escribiendo…» cuando el clasificador deja seguir el
    mensaje, salvo que hayan pasado 20 s o la respuesta ya haya salido.
  - Son un extra: sin reintentos, y si Meta, el gateway o el bucket fallan, el mensaje se confirma igual.
  - `CanalDeMeta.marcar_leido`, en la Cloud API de WhatsApp: `status: read` y, si se pide, `typing_indicator`.
  - `runtime.ejecutar` acepta otros consumidores (`ademas`); si un bucle se cae, se cae el worker.
- **[ADR-0010](docs/02-design/adr/ADR-0010-llm-propio-con-llama-cpp.md) (aceptada): el LLM de Hive, un modelo
  propio.** En el E7, la API de Anthropic rechazó todas las llamadas desde midgard (403 «Request not allowed»), porque
  su IP sale de Venezuela, que no es una región soportada. Jeremi decidió servir Gemma 4 26B A4B (Apache 2.0,
  MXFP4) con llama.cpp en la RTX 3090 de la LAN, en Docker Desktop, un modelo para las dos tareas.
  - Los adaptadores `LlmClasificador` y `LlmRedactor` (`src/infrastructure/llm/`), detrás de los mismos puertos.
    Hablan con la API compatible con la de OpenAI de llama-server, y el clasificador ata la salida a su esquema con
    una gramática.
  - El servidor (`deploy/llm/docker-compose.yml`): build b11434 fijada por digest, dos ranuras, sin pensamiento,
    clave propia y solo en la IP de la LAN.
  - Medido: clasificar, de 0,8 a 2,1 s, con inyección y abuso detectados; responder, 1,2 s a unos 96 tokens/s.
- **El despliegue del esqueleto en midgard** (E7 del [plan](docs/00-project/plan-de-los-workers.md)):
  `deploy/docker-compose.yml` (proyecto `hive-engine`), con los cuatro workers en un contenedor cada uno. Todos son de
  solo lectura, sin capacidades y con su comprobación de salud por la edad de su marca de vida. Su `.env.example` y el
  runbook ([05-deployment](docs/05-deployment/README.md)) dicen qué necesita cada worker y cómo se pone en marcha, se
  comprueba y se vuelve atrás.
- **El notificador, el cuarto worker** (E6 del [plan](docs/00-project/plan-de-los-workers.md)):
  `python -m src.workers.notifier`. Con él el esqueleto está completo de punta a punta, a falta de desplegarlo
  (E7).
  - Lee cada respuesta de `MOTOR`, la envía por la Cloud API de WhatsApp (v26.0, solo texto, sin citar) y publica lo
    enviado en `engine.reply.sent.{botId}`, el `RespuestaEnviada` del contrato v2 del gateway, para el historial.
  - **No envía dos veces la misma respuesta:** lo apunta en el bucket `enviados` de Bifröst y lo mira antes de
    enviar (decisión de Jeremi del 2026-10-20; el bucket, en higerotech/bifrost#2).
  - El token de WhatsApp lo pide al gateway con su clave, lo recuerda 10 minutos y lo vuelve a pedir si Meta lo
    rechaza.
  - Cada error de Meta, por su código: lo que se arregla esperando se reintenta con las entregas de Bifröst; lo que
    no irá nunca (fuera de la ventana de 24 horas, un número que no puede recibir) se descarta.
  - Messenger e Instagram, en el E10.
- **El sobre lleva `cuentaDelBot`**, el `phone_number_id` desde el que se responde, que pone el clasificador. El
  contrato del sobre pasa a la 1.3.0.
- **El de respuesta, el tercer worker** (E5 del [plan](docs/00-project/plan-de-los-workers.md)):
  `python -m src.workers.responder`.
  - Lee cada pedido de `MOTOR` y publica la contestación en `motor.respuesta.…`, y solo entonces confirma.
  - **Con Claude**, Opus 5.5 (esfuerzo `medium`) escribe con la persona del bot y sus reglas en un `system` estable
    por bot y cacheado, y con las vueltas y el mensaje como turnos de la conversación. Lleva los fallbacks del
    servidor (`fallbacks: "default"`): si Opus rechaza, la API lo intenta con otro modelo en la misma llamada.
  - **Si no**, contesta el texto fijo que pidió el coordinador (`no_puedo`, `solo_texto` o `no_disponible`), o el
    de «no puedo» si Claude y sus sustitutos rechazan.
  - Si Claude no contesta, o su respuesta se corta o viene vacía, reintenta con las entregas de Bifröst y, en la
    última, contesta «no disponible».
  - El sobre que publica no lleva la persona ni las vueltas.
  - `max_tokens` es 4096 por el pensamiento de Opus 5.5, que no se puede apagar (ADR-0009, «Construido»).
- **El contrato del sobre pasa a la 1.2.0**, con el canal `respuesta` y su esquema (`Contestacion`).
- **El coordinador, el segundo worker** (E4 del [plan](docs/00-project/plan-de-los-workers.md)):
  `python -m src.workers.coordinator`.
  - Lee lo clasificado de `MOTOR` y publica el pedido en `motor.pedido.…`, y solo entonces confirma.
  - **Si el mensaje sigue**, pide al gateway la persona del bot con sus reglas y las últimas vueltas, con la clave
    del coordinador para el cliente dueño del bot, y pide la respuesta con Claude.
  - **Si no sigue**, pide un texto fijo según el motivo, sin preguntar al gateway: la persona siempre recibe algo.
  - Si el gateway no responde, reintenta con las entregas de Bifröst y, en la última, pide el texto de «no
    disponible». Si el bot o el contacto ya no existen, descarta el mensaje.
  - La API del gateway (`src/infrastructure/gateway/`) se prueba contra un servidor local que abre y verifica cada
    token como el gateway; el worker, contra un NATS con el usuario de Bifröst del coordinador.
- **El contrato del sobre pasa a la 1.1.0**, con el canal `pedido` y sus esquemas (`Persona`, `Vuelta`, `Pedido`).
- **El clasificador, el primer worker** (E3a del [plan](docs/00-project/plan-de-los-workers.md)):
  `python -m src.workers.classifier`.
  - Lee cada mensaje identificado de `MENSAJES` y lo clasifica con Claude Haiku 5.5, en una sola llamada con salida
    estructurada: si se atiende y cuál es su intención. Publica el sobre en `motor.clasificado.…` y solo entonces
    confirma.
  - **Fail-closed:** solo sigue lo que Claude permite sin motivo para pararlo. Un rechazo no sigue. Sin veredicto, el
    mensaje se reintenta con las entregas de Bifröst y, en la última, sale sin seguir con el motivo `error`.
  - A Claude solo le llega el texto. El sobre no lleva el nombre del contacto, y el log no lleva ni el texto ni el
    número.
  - La clave de la API, del worker, la lee de Andvari al arrancar.
  - Lo construido y lo que cambió respecto a las ADR está en
    [ADR-0008](docs/02-design/adr/ADR-0008-seis-workers-sobre-bifrost.md) y
    [ADR-0009](docs/02-design/adr/ADR-0009-claude-como-llm.md): `max_tokens` 1024 por el pensamiento de Haiku 5.5,
    y un puerto en vez de dos.
- **El sobre interno, versión 1**, y su contrato,
  [`asyncapi-motor.yaml`](docs/02-design/contracts/asyncapi-motor.yaml), con pruebas de contrato.
- **El bucle común de los workers** (`src/workers/runtime.py`), probado contra un NATS con los permisos de Bifröst,
  también en el CI.
- Dependencias: `anthropic` 1.11.0, con `httpx2` y `truststore` fijados porque sus versiones más nuevas tienen días;
  `typing_extensions` sube a 4.16.0; y `jsonschema` para las pruebas de contrato.
- **El motor usa `shared/`**, el código que comparte con el gateway (E1 del [plan](docs/00-project/plan-de-los-workers.md)):
  - lo que necesita está en sus `requirements` (`nats-py`, OpenTelemetry, `requests`), con las versiones del gateway,
    y `jwcrypto` sube a 1.6.1, la del gateway;
  - las pruebas lo importan desde la raíz del monorepo;
  - la imagen se construye desde la raíz (`docker build -f engine/Dockerfile .`), con `Dockerfile.dockerignore`.
- ADR-0008 y ADR-0009 aceptadas. Las ADR-0003, 0004, 0005 y 0007 quedan superadas.
- **El diseño del motor en seis workers** (E0 del [plan](docs/00-project/plan-de-los-workers.md)), en propuesta:
  - [ADR-0008](docs/02-design/adr/ADR-0008-seis-workers-sobre-bifrost.md): clasificador, coordinador, enlace,
    respuesta, evaluador y notificador, en una imagen y un contenedor por worker. Se pasan el mensaje por Bifröst en
    un stream propio, `MOTOR`, de 24 h, deduplicado por paso y en orden por conversación. Supera la ADR-0003;
  - [ADR-0009](docs/02-design/adr/ADR-0009-claude-como-llm.md): Claude por la API de Anthropic, con Haiku 5.5 para
    clasificar y Opus 5.5 para responder y evaluar. El clasificador hace en una llamada lo que hacían NeMo y Ollama,
    con salida estructurada y fail-closed. Una clave por worker en Andvari. Supera las ADR-0004, 0005 y 0007;
  - el plan del esqueleto, E0 a E10: primero un mensaje de WhatsApp con respuesta real, después el evaluador, el
    enlace y los demás canales;
  - la clasificación de datos con Anthropic como encargado del tratamiento: qué sale, qué no, dónde y cuánto se
    guarda.
- Estructura AI-DLC en variante polyrepo: `.ai-dlc/` (gates 0 a 5 con su estado, plantillas,
  mapeo OWASP con anexo del servicio) y `docs/00-project` a `docs/06-monitoring` (ADR-0001).
- Fase 00: charter con mapa mental del alcance, glosario del lenguaje ubicuo y clasificación
  de datos (GDPR y leyes LatAm).
- Fase 01: PRD MMH-F01 con C4 Context, journey, 12 escenarios de abuso, 8 requisitos
  funcionales, 14 requisitos de seguridad mapeados a ASVS, trazabilidad y threat assessment inicial.
- Fase 02: `architecture.md` (C4 Container y Component, secuencia, estados, ER, clases),
  `threat-model.md` (STRIDE y DREAD, T1 a T12) y contrato AsyncAPI 3.0 generado desde los
  modelos pydantic.
- ADR-0002 a ADR-0005: decisiones ya implementadas (puertos y adaptadores, consumo AMQP, LLM
  autoalojado con salida no confiable, guardrails fail-closed).
- ADR-0006 y ADR-0007 (`proposed`): placement del worker y del serving del LLM, con matriz de
  performance por dólar y precios consultados el 2026-10-12.
- Fase 03: `repo-history.md`, generado desde `git log`.
- `tests/README.md` y los niveles `integration`, `contract`, `e2e` y `security` de la pirámide, vacíos.
- `SECURITY.md`, `CONTRIBUTING.md`, `.github/CODEOWNERS` y un esqueleto de
  `.github/workflows/security-gates.yml` de ejecución manual.
- `.ai-dlc/scripts/`: generadores del contrato AsyncAPI y del historial del repo.

### Cambiado
- **El enlace y el evaluador, definidos** (decisiones de Jeremi del 2026-10-26, en el plan de los workers). El
  enlace hace las llamadas a las APIs y a los servidores MCP según la intención del mensaje y el contexto de la
  conversación; el evaluador comprueba que cada respuesta cumpla los criterios del bot antes de enviarla.
- **El README, al día.** Decía que el motor no estaba desplegado: corre en midgard desde el E7, con cuatro de sus
  seis workers. Ahora cuenta también las confirmaciones de lectura y el «escribiendo…» del notificador, el
  calentamiento del LLM y que hoy solo responde por WhatsApp. El aviso de `architecture.md` nombraba la ADR-0009
  (Claude), que la ADR-0010 sustituyó.
- **El motor pide tokens de acceso OAuth 2.0 al gateway** (ADR-0025 del gateway, 2026-10-26). Por cada cliente, el
  coordinador y el notificador envían a `/oauth/token` una aserción firmada con su clave para ese cliente y usan el
  token hasta un minuto antes de que caduque, en lugar de firmar un token por llamada. Si el gateway rechaza el token,
  piden otro una vez; si rechaza la aserción, vuelven a leer la clave de Andvari una vez.
- **La imagen del motor es `ghcr.io/jeremialcala/hive-engine`**, no `chatbot-engine`, y el coordinador y el
  notificador llegan a la API de gestión como `hive-api` (ADR-0024 del gateway, versión 0.3.0; decisión de Jeremi del
  2026-10-22).
- **El sobre interno pasa a `schemaVersion` 2** y su contrato a la 2.0.0: donde decía `claude`
  (`pedido.respuesta`, `respuesta.fuente`), dice `modelo`. Todos los workers se despliegan juntos.
- **El clasificador y el de respuesta ya no usan Andvari:** solo Bifröst y la clave del LLM, de `keys/llm-api-key`.
- **El coordinador espera 10 s por llamada al gateway**, no 5 (`COORDINATOR_TIMEOUT`; decisión de Jeremi del
  2026-10-21). En midgard, una lectura de las vueltas tardó 5,8 s: con 5 s, el mensaje esperaba 30 s al reintento y
  la respuesta llegó a los 41 s. Con dos llamadas por mensaje, 20 s como mucho, por debajo del `ack_wait` de 30 s.
- **El coordinador recuerda 5 minutos la persona de cada bot con sus reglas** (decisión de Jeremi del 2026-10-21),
  por cliente y bot. Medido en midgard, el coordinador era el paso más lento de un mensaje después del LLM: 2,7 s de
  mediana, casi todo en sus dos lecturas al gateway. Ahora la de las reglas se hace una vez cada 5 minutos por bot.
  Un cambio de las reglas tarda eso en llegar a las respuestas. Las vueltas se siguen pidiendo en cada mensaje.
- El bucle común pasa a cada worker su conexión con Bifröst al arrancar.
- La lectura del sobre de un paso del motor (`leer_del_motor`) pasa al bucle común: la usan el coordinador y el de
  respuesta.
- **La imagen, en una sola etapa** sobre `python:3.12-slim`, con solo ruedas (`--only-binary=:all:`): sin NeMo ni
  `annoy` no hay nada que compilar. Por defecto arranca el clasificador; cada contenedor dice su worker.
- **La cobertura mínima del CI sube del 60 % al 80 %**, la del gateway. `build-engine` levanta también el NATS, así
  que las pruebas de integración corren en `main` en vez de saltarse.
- El README describe el motor en workers.
- **El motor vive ahora en `engine/` del monorepo `wh-python-fastapi-messenger`**, junto al gateway (ADR-0023 del
  gateway, que supera la ADR-0001 de aquí en lo que decía de un repositorio por servicio). Se importó con toda su
  historia; `ms-python-metamsghandler` queda archivado. Sus ADRs siguen aquí con sus números. El `.github/` propio
  se retira: manda el de la raíz.
- `test/` pasa a `tests/unit/`. `tox.ini`, `pyproject.toml` y el README apuntan a la ruta nueva.
- `pyproject.toml` configura pytest (`pythonpath`, `testpaths`) para que `from src...` resuelva
  también con el ejecutable `pytest`.
- README: mapa de la documentación AI-DLC y estructura actualizada.
- **CI propio dentro del monorepo:** pylint y pruebas con cobertura de ramas mínima del 60 % en cada PR que toca
  `engine/` (workflow `engine`). La imagen `ghcr.io/jeremialcala/chatbot-engine` se publica desde `main`
  (`build-engine`).
- **Dependencias:** las de ejecución en `requirements.txt` y las de desarrollo en `requirements-dev.txt`, cada una
  fijada con hashes en su `.lock`. flake8, tox y pycobertura dejan de instalarse.
- **Dockerfile en dos etapas**, con las bases por digest: las dependencias se compilan desde el lock con hashes y la
  imagen final es `python:3.12-slim`, sin compiladores y solo con el código. Ya no instala rustup.

### Eliminado
- **Claude y el SDK `anthropic`**, con `httpx2` y `truststore`: los adaptadores `ClaudeClasificador` y
  `ClaudeRedactor` pasan a ser los del LLM propio (ADR-0010).
- **El servicio de antes** (E3b del [plan](docs/00-project/plan-de-los-workers.md); ADR-0009, «Construido»):
  - NeMo Guardrails y Ollama, con sus adaptadores, sus prompts y los rails, y con ellos `annoy` y su compilación de
    C++. El motor ya se puede desarrollar fuera de Linux;
  - el consumidor y el publicador AMQP (`pika`), el caso de uso que los unía y `main.py`;
  - los parsers de webhooks de Meta: el clasificador lee el texto con `shared.contract.what_was_said`;
  - el código muerto de MongoDB y JWE (`mongoengine`, `pymongo`, `dnspython`, `pillow`);
  - `config.env.example`, `logging_config.yaml`, `deployment.yaml` (un borrador de otro servicio) y el contrato AMQP
    `asyncapi.yaml` con su generador. El contrato de ahora es `asyncapi-motor.yaml`.

  `jwcrypto` y `cryptography` se quedan: los usa `shared.auth`. `PyYAML` pasa a las dependencias de desarrollo.

### Corregido
- **Las trazas del leído y de «escribiendo…» no decían cómo fue** (visto en midgard el 2026-10-26). El notificador
  ponía en `gateway.outcome` «hecho» o «fallido», que la traza no admite: el atributo se quedaba fuera y el log
  avisaba dos veces por mensaje. Ahora son «sent» y «failed», y `shared/tracing` admite «sent».
- **La primera consulta al LLM tras horas sin uso tardaba hasta 16 s** (ADR-0010, medido el 2026-10-21). La
  generación iba a su velocidad: lo lento era traer de vuelta la memoria de la VM de Docker Desktop. llama-server
  tenía 7 GB propios en la VM de 12, 1,4 GB en swap, casi todo su caché de prompts en RAM (hasta 8 GiB por defecto).
  Y de los 748 MB que el modelo deja fuera de la GPU, solo 5 MB estaban en memoria.
  - `--cache-ram 0`: cada worker tiene su ranura con su `system` en la GPU, y esa caché no le aportaba nada.
  - `--load-mode mmap+mlock`, con `memlock` sin límite: esos 748 MB quedan bloqueados en RAM.
  - **El modelo, en el volumen `hive-llm-modelos`** de la VM, no en `C:\hive-llm`: cargarlo pasa de 3 a 6 minutos a
    unos 40 s. Desde Windows se leía a ~100 MB/s, y con la VM apretada `cp` llegó a fallar con «Out of memory».
  - Resultado: 0,9 GB de memoria propia, 0,1 GB en swap, y la de toda la VM, de 7,3 a 2,4 GB.
- **Un mensaje atendido con retraso tenía por contexto lo que vino después.** El gateway da las últimas vueltas en el
  momento de la consulta. En midgard (2026-10-21), con el gateway colgado, el coordinador atendió cuatro mensajes
  minutos tarde, y la respuesta al primero llevó en sus vueltas los otros tres, que la persona escribió después.
  Ahora las vueltas terminan antes del mensaje que se responde, por su posición en el historial. Si la ingesta aún no
  lo guardó, se quedan las enviadas hasta su `receivedAt`; una hora sin zona cuenta como UTC. Si llegaron muchos
  mensajes después, quedan menos de diez vueltas: el gateway cuenta las diez desde el último.

### Seguridad
- **`pylint` 4.0.9** (era 3.2.3), con `astroid` 4.0.4, por CVE-2026-114608 (GHSA-22qq-2fwx-7p67, moderada,
  publicada el 2026-10-27): `pylint` guardaba su caché de estadísticas como pickle en `PYLINT_HOME` y la cargaba con
  `pickle.load`, así que quien pudiera escribir en ese directorio ejecutaba código. Desde la 4.0.9 la caché es JSON.
  Solo es una herramienta del CI y del desarrollo, no va en la imagen. La 4.0.9 se publicó el 2026-09-23. En el lock
  cambian además `isort` 5.13.2 → 9.0.2 (2026-09-28), con `mypy-extensions` 1.1.0: es lo que resuelve para `pylint`
  4 una compilación desde cero, y el CI compara los paquetes con ella. El código sigue en 10/10.
- **OpenTelemetry sube a 1.45.1** en los locks del motor, como en los del gateway: CVE-2026-114603 de
  `opentelemetry-api` (2026-10-22).
- **Siete dependencias con vulnerabilidades conocidas pasan a versiones corregidas**, todas con más de dos semanas:
  - `cryptography` 42.0.8 → 50.0.2, con `cffi` 1.16.0 → 2.0.0;
  - `pillow` 10.3.0 → 12.3.0;
  - `pymongo` 4.7.3 → 4.18.2, con `dnspython` 2.6.1 → 2.8.0;
  - `jwcrypto` 1.5.6 → 1.5.7;
  - `python-dotenv` 1.0.1 → 1.2.2;
  - `pytest` 8.2.2 → 9.0.3.

  pip-audit no encuentra ninguna en los locks nuevos, y los gates del repositorio los auditan en cada PR. Es el SCA
  que pedía T8; siguen pendientes el SBOM y la firma de la imagen.
- Documentadas, sin corregir, las brechas halladas al reconstruir el threat model: datos
  personales en el log del publicador (T3), AMQP sin TLS (T5), pérdida silenciosa de mensajes
  por falta de DLQ y de confirmaciones (T6), llamada del rail sin timeout (T7) y ausencia de
  SCA, SBOM e imagen por digest (T8).

[Unreleased]: https://github.com/jeremialcala/ms-python-metamsghandler/compare/main...develop
