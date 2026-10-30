# El motor de respuestas

> **Dentro del monorepo de los chatbots.** Vive en `engine/` del repositorio `jeremialcala/hive`, junto al
> gateway que le entrega los mensajes, desde el 2026-10-20
> ([ADR-0023](../docs/00-project/adr/0023-monorepo-del-gateway-y-el-motor.md)). El repositorio
> `ms-python-metamsghandler`, donde nació, queda archivado con su historia, que también está aquí. Las órdenes de
> este README se ejecutan desde `engine/`.

Es la mitad de **Hive** que responde ([ADR-0024](../docs/00-project/adr/0024-el-producto-se-llama-hive.md)): contesta
los mensajes que las personas envían a los chatbots. Hoy responde por WhatsApp; Messenger e Instagram llegan en el
E10. Son workers de una misma imagen, uno por contenedor, que se pasan cada mensaje por Bifröst, el NATS JetStream de
midgard ([ADR-0008](docs/02-design/adr/ADR-0008-seis-workers-sobre-bifrost.md)). El diseño tiene seis, y hoy
corren cuatro. El clasificador y el de respuesta usan
un modelo propio, Gemma 4 26B A4B, servido por llama.cpp en una RTX 3090 de la LAN
([ADR-0010](docs/02-design/adr/ADR-0010-llm-propio-con-llama-cpp.md)): el texto no sale de la red de Hive.

## El recorrido de un mensaje

```mermaid
flowchart LR
    GW["gateway<br/>(ingesta)"] -- "gateway.message.…<br/>MENSAJES" --> CL["clasificador"]
    CL -- "motor.clasificado.…" --> CO["coordinador"]
    CO -- "motor.pedido.…" --> RE["respuesta"]
    RE -- "motor.respuesta.…" --> NO["notificador"]
    NO -- "engine.reply.sent.…<br/>RESPUESTAS" --> GW
    NO -- "Graph API" --> META["Meta"]
```

Es el esqueleto: el enlace y el evaluador llegan después. El orden está en el
[plan de los workers](docs/00-project/plan-de-los-workers.md).

| Worker | Estado | Lee | Publica |
|---|---|---|---|
| Clasificador | **Construido** (E3) | `MENSAJES`, consumidor `classifier` | `motor.clasificado.{p}.{bot}.{contacto}` |
| Coordinador | **Construido** (E4) | `MOTOR`, consumidor `coordinator` | `motor.pedido.{p}.{bot}.{contacto}` |
| Respuesta | **Construido** (E5) | `MOTOR`, consumidor `responder` | `motor.respuesta.{p}.{bot}.{contacto}` |
| Notificador | **Construido** (E6) | `MOTOR`, consumidor `notifier` | `engine.reply.sent.{bot}` y Meta |

### El clasificador

`python -m src.workers.classifier`. Por cada mensaje identificado, una llamada al LLM de Hive, con la salida atada a
un esquema, decide si se atiende y cuál es su intención.

- **Fail-closed:** solo sigue lo que el modelo permite sin motivo para pararlo.
- **Solo el texto va al modelo**, que corre en la LAN.
- **Publica el sobre para el coordinador** y solo entonces confirma.

### El coordinador

`python -m src.workers.coordinator`. Si el mensaje sigue, pide al gateway la persona del bot con sus reglas y las
últimas vueltas, con su clave para el cliente dueño del bot, y pide la respuesta al modelo. Si no sigue, pide un
texto fijo según el motivo: la persona siempre recibe algo.

### El de respuesta

`python -m src.workers.responder`. El modelo escribe la contestación con la persona del bot y sus reglas en el
`system`, y las vueltas y el mensaje como turnos de la conversación. Si no, contesta el texto fijo que pidió el
coordinador.

### El notificador

`python -m src.workers.notifier`. Envía el texto por la Cloud API de WhatsApp, con el token del bot que le da el
gateway, y publica lo enviado para el historial del gateway. Antes de enviar mira el bucket `enviados` de Bifröst, para
no enviar dos veces la misma respuesta. Messenger e Instagram llegan en el E10.

También es el que se ocupa de lo que la persona ve mientras espera (`src/workers/presencia.py`), con dos consumidores
más: `notifier-leido`, sobre `MENSAJES`, pone en azul los ticks del mensaje en cuanto llega, y `notifier-escribiendo`,
sobre `motor.clasificado`, muestra «escribiendo…» cuando el mensaje sigue. Las dos cosas son un extra: no se
reintentan y nada espera por ellas.

El sobre que se pasan los workers es el contrato interno del motor,
[`asyncapi-motor.yaml`](docs/02-design/contracts/asyncapi-motor.yaml).

## Estructura

```text
src/
├── domain/
│   ├── models/sobre.py              # el sobre de los workers, versión 1
│   └── ports/                       # ClasificadorDeEntrada, FuenteDelBot, Redactor, y los del envío
├── infrastructure/
│   ├── llm/                         # LlmClasificador y LlmRedactor: llama.cpp por su API compatible con la de OpenAI,
│   │                                #   y el calentamiento del modelo tras un rato sin uso (caliente.py)
│   ├── gateway/                     # ApiDelGateway: la API de gestión, con la clave del worker
│   ├── meta/                        # WhatsAppCloud: la Cloud API de WhatsApp
│   ├── bifrost/                     # EnviadosEnBifrost: el bucket `enviados`
│   └── adapters/sanitize.py         # el saneado del texto antes de salir
└── workers/
    ├── runtime.py                   # el bucle común: atarse al consumidor, leer, publicar, confirmar
    ├── settings.py                  # la configuración de cada worker, del entorno
    ├── classifier.py                # el clasificador
    ├── coordinator.py               # el coordinador
    ├── responder.py                 # el de respuesta
    ├── notifier.py                  # el notificador
    └── presencia.py                 # las confirmaciones de lectura y el «escribiendo…», del notificador
tests/                               # unit, contract e integration (contra un NATS)
docs/                                # documentación AI-DLC por fase (00 a 06)
```

Lo que el motor comparte con el gateway (el bus, las trazas, los contratos v2, el almacén de claves de Andvari) está
en `shared/`, en la raíz del monorepo.

## Configuración

Del entorno del contenedor. Ningún secreto va en ella:

| Variable | Qué |
|---|---|
| `NATS_URL`, `NATS_USER`, `NATS_PASSWORD`, `NATS_CA`, `NATS_INBOX_PREFIX` | Bifröst. Las deja `credenciales/<worker>.env` del repositorio `bifrost` |
| `ANDVARI_ADDR`, `ANDVARI_CA_FILE` | Andvari, para el coordinador y el notificador |
| `ENGINE_ROLE_ID`, `ENGINE_SECRET_ID_FILE` | El rol AppRole `motor-<worker>` y el fichero con su `secret_id` |
| `LLM_URL`, `LLM_API_KEY_FILE`, `LLM_MODEL` | El LLM de Hive, para el clasificador y el de respuesta. Por defecto el modelo es `gemma-4-26b-a4b` |
| `LLM_CALENTAR_CADA` | Segundos sin usar el LLM tras los que el clasificador y el de respuesta lo calientan con su `system`. 600 por defecto; 0, nunca |
| `CLASSIFIER_MAX_TOKENS`, `CLASSIFIER_TIMEOUT` | El clasificador. Por defecto: 256 y 20 s |
| `GATEWAY_URL`, `GATEWAY_AUDIENCE`, `GATEWAY_PUBLIC_KEYS_FILE`, `GATEWAY_CA_FILE`, `COORDINATOR_TIMEOUT` | El coordinador: la API del gateway, como para `ops.engine_keys`. El timeout, 10 s por llamada |
| `RESPONDER_MAX_TOKENS`, `RESPONDER_TIMEOUT` | El de respuesta. Por defecto: 1024 y 90 s |
| `GATEWAY_*` (como el coordinador), `GRAPH_URL`, `GRAPH_VERSION`, `NOTIFIER_TIMEOUT` | El notificador. Por defecto: `https://graph.facebook.com`, `v26.0` y 8 s por llamada |
| `LOG_LEVEL` | `INFO` por defecto. El log va al stdout del contenedor |

El servidor del LLM corre en el equipo de la RTX 3090 (`deploy/llm/docker-compose.yml`). Su clave nace allí, y una
copia va a `keys/llm-api-key` del motor en midgard.

## Desarrollo y pruebas

Python 3.12. Las dependencias se instalan desde los locks, con hashes (`requirements.lock` y `requirements-dev.lock`;
cómo regenerarlos, en el `CONTRIBUTING.md` de la raíz). Las pruebas no llaman al LLM: el clasificador y el de
respuesta se prueban contra un servidor HTTP local que hace de llama-server.

```bash
# Desde engine/. Las de integración necesitan un NATS como Bifröst; sin NATS_TEST_ADDR se saltan.
docker run -d --rm --name nats-motor -p 127.0.0.1:24222:4222 \
  -v "$PWD/tests/integration/nats-test.conf:/etc/nats/test.conf:ro" nats:2.14.8-alpine -c /etc/nats/test.conf
pip install --require-hashes -r requirements-dev.lock
pylint --rcfile=pyproject.toml src
NATS_TEST_ADDR=127.0.0.1:24222 python -m pytest --cov=src --cov-branch --cov-fail-under=80

# La imagen, desde la raíz del monorepo, que es donde está shared/
docker build -f Dockerfile -t hive-engine ..
```

El CI (workflows `engine` y `build-engine`) hace lo mismo, con el NATS y con las pruebas de integración obligatorias.

## Despliegue

Corre en midgard desde el E7, un contenedor por worker (`hive-classifier`, `hive-coordinator`, `hive-responder` y
`hive-notifier`), junto a Bifröst y Andvari, con [`deploy/docker-compose.yml`](deploy/docker-compose.yml). Es la
instancia de prueba: la imagen se construye en midgard desde `develop`, sin pasar por `main`. El procedimiento está en
[docs/05-deployment](docs/05-deployment/README.md); el Gate 4 queda para cuando el motor llegue a `main`.

## Metodología AI-DLC

El motor sigue el estándar AI-DLC ([ADR-0001](docs/00-project/adr/0001-adopcion-estructura-ai-dlc.md)).

| Carpeta | Contenido |
|---|---|
| [`docs/`](docs/README.md) | Charter, requisitos, arquitectura, threat model, ADRs y contratos, por fase |
| [`.ai-dlc/`](.ai-dlc/) | Checklists de los gates 0 a 5 con su estado, plantillas y mapeo OWASP |
| [`tests/`](tests/README.md) | Pirámide de pruebas |
| [`CHANGELOG.md`](CHANGELOG.md) | Cambios notables |
| [`SECURITY.md`](SECURITY.md) · [`CONTRIBUTING.md`](CONTRIBUTING.md) | Política de seguridad y flujo de trabajo |

Ningún gate está cerrado todavía. La arquitectura y el threat model describen aún el servicio de antes, el de
RabbitMQ, NeMo y Ollama: se rehacen con ADR-0008 y ADR-0010 antes de cerrar el Gate 1 del motor.

## Autor

**Jeremi Alcalá** — [github.com/jeremialcala](https://github.com/jeremialcala)
