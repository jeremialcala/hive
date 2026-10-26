# Diseño del Sistema — ms-python-metamsghandler

* **Estado:** review
* **Fecha:** 2026-10-12
* **Decisores:** Jeremi Alcalá
* **Fase AI-DLC:** 02-design
* **Versión:** 0.2.0
* **Gate:** 1
* **Estilo arquitectónico:** Clean Architecture / hexagonal (puertos y adaptadores), worker dirigido por eventos
* **ADRs relacionadas:** ADR-0001, ADR-0002, ADR-0003, ADR-0004, ADR-0005, ADR-0006, ADR-0007

> **Describe el servicio de antes.** Desde el E3b (2026-10-20) el código de RabbitMQ, NeMo Guardrails y Ollama ya no
> existe: el motor son workers sobre Bifröst ([ADR-0008](adr/ADR-0008-seis-workers-sobre-bifrost.md)) que usan un
> LLM propio con llama.cpp ([ADR-0010](adr/ADR-0010-llm-propio-con-llama-cpp.md), que sustituye a la ADR-0009 de
> Claude). Este documento se rehace con ellas antes de cerrar el Gate 1 del
> motor; mientras tanto, lo construido está en la sección «Construido» de cada ADR.

Documento de diseño reconstruido a partir del código de `develop` (4c7bb9e). Describe lo que
existe; lo que falta está marcado como deuda y enlazado a su amenaza.

## Contextos acotados (DDD)

| Bounded Context | Responsabilidad | Entidades núcleo | Código |
|---|---|---|---|
| Ingesta y normalización | Identificar el origen, sanear y traducir el webhook al modelo común | `MensajeEntrante`, `Adjunto`, `Origen` | `domain/ports/webhook_parser.py`, `infrastructure/adapters/` |
| Seguridad de IA | Decidir si un texto puede llegar al clasificador | `ResultadoGuardrail` | `domain/ports/guardrails_policy.py`, `infrastructure/guardrails/` |
| Clasificación de intención | Determinar qué quiere el usuario y qué estrategia de respuesta aplica | `Intencion`, `TipoRespuesta` | `domain/ports/intent_engine.py`, `infrastructure/llm/` |
| Publicación | Entregar el resultado aguas abajo y confirmar el mensaje de entrada | `ResultadoClasificado` | `domain/ports/message_publisher.py`, `infrastructure/amqp/` |

`infrastructure/security/` (JWE/JWK) e `infrastructure/persistence/` (modelos dinámicos sobre
MongoDB) vienen del template de origen. Ningún módulo del pipeline los importa: son código
heredado sin cablear (ver T11 del threat model).

## Vista C4 — Container

```mermaid
C4Container
    title Contenedores: ms-python-metamsghandler

    System_Ext(meta, "Plataformas de Meta", "Origen de los webhooks")
    Container_Ext(receptor, "Receptor de webhooks", "Python, FastAPI", "Recibe el webhook HTTP y lo encola")

    System_Boundary(sys, "metamsghandler") {
        Container(worker, "Worker de clasificación", "Python 3.12, pika", "Consume, normaliza, aplica guardrails, clasifica y publica")
    }

    ContainerQueue_Ext(qin, "Cola de entrada", "RabbitMQ, durable", "Webhooks crudos de Meta")
    ContainerQueue_Ext(qout, "Cola de salida", "RabbitMQ, durable", "ResultadoClasificado en JSON")
    Container_Ext(ollama, "Ollama", "llama3.1", "Sirve el modelo de guardrails e intención")
    Container_Ext(respuesta, "Servicio de respuesta", "Por definir", "Entrega la respuesta al usuario")

    Rel(meta, receptor, "Notifica eventos", "HTTPS")
    Rel(receptor, qin, "Publica el webhook", "AMQP 0-9-1")
    Rel(worker, qin, "Consume con ACK manual", "AMQP 0-9-1, sin TLS")
    Rel(worker, ollama, "Evalúa la entrada y clasifica", "HTTP: /v1 y /api/chat")
    Rel(worker, qout, "Publica el resultado", "AMQP 0-9-1, sin TLS")
    Rel(respuesta, qout, "Consume resultados", "AMQP 0-9-1")

    UpdateElementStyle(worker, $bgColor="#1168bd", $fontColor="#ffffff", $borderColor="#0b4884")
    UpdateElementStyle(ollama, $borderColor="#b30000")
    UpdateRelStyle(worker, qin, $textColor="#b30000", $lineColor="#b30000")
    UpdateRelStyle(worker, ollama, $textColor="#b30000", $lineColor="#b30000")
    UpdateLayoutConfig($c4ShapeInRow="3", $c4BoundaryInRow="1")
```

*Caption — eje estructura, fase 02-design: un único contenedor propio y sus dependencias. En
rojo, los enlaces que hoy van sin cifrar (`owasp-a04`) y el modelo de IA (`ai-sec`).*

El sistema es un solo proceso sin estado. Todo lo que necesita persistir vive en el broker.

## Vista C4 — Component (worker de clasificación)

```mermaid
C4Component
    title Componentes: worker de clasificación

    ContainerQueue_Ext(qin, "Cola de entrada", "RabbitMQ", "Webhooks crudos")
    ContainerQueue_Ext(qout, "Cola de salida", "RabbitMQ", "Resultados")
    Container_Ext(ollama, "Ollama", "llama3.1", "LLM")

    Container_Boundary(worker, "Worker de clasificación") {
        Component(bootstrap, "Bootstrap", "presentation", "Compone las dependencias y arranca el consumidor")
        Component(consumer, "AmqpConsumer", "pika, hilos", "Lanza un hilo por mensaje y confirma de forma threadsafe")
        Component(usecase, "ProcesarMensajeUseCase", "application", "Orquesta el pipeline y audita")
        Component(resolver, "WebhookResolver y parsers", "adapters", "Identifica el origen, normaliza y sanea")
        Component(guard, "NeMoGuardrailsPolicy", "nemoguardrails 0.22", "Rail de entrada, fail-closed")
        Component(intent, "OllamaIntentEngine", "ollama 0.3, pydantic", "Clasifica y valida la salida del modelo")
        Component(publisher, "AmqpPublisher", "pika", "Serializa y publica el resultado")
    }

    Rel(bootstrap, consumer, "Arranca")
    Rel(consumer, qin, "Consume", "AMQP")
    Rel(consumer, usecase, "ejecutar(body)")
    Rel(usecase, resolver, "parsear()", "MessageNormalizer")
    Rel(usecase, guard, "evaluar()", "GuardrailsPolicy")
    Rel(usecase, intent, "detectar()", "IntentEngine")
    Rel(usecase, publisher, "publicar()", "MessagePublisher")
    Rel(guard, ollama, "self check input", "HTTP /v1")
    Rel(intent, ollama, "chat con format=json", "HTTP /api/chat")
    Rel(publisher, qout, "basic_publish", "AMQP")

    UpdateElementStyle(usecase, $bgColor="#1168bd", $fontColor="#ffffff")
    UpdateElementStyle(guard, $borderColor="#b30000")
    UpdateElementStyle(intent, $borderColor="#b30000")
    UpdateElementStyle(resolver, $borderColor="#b30000")
    UpdateLayoutConfig($c4ShapeInRow="3", $c4BoundaryInRow="1")
```

*Caption — eje estructura, fase 02-design: el caso de uso solo conoce puertos. En rojo, los
tres componentes que tocan entrada no confiable o el modelo (`owasp-a05`, `ai-sec`).*

## Flujos críticos (comportamiento)

```mermaid
sequenceDiagram
    autonumber
    participant Q as Cola de entrada
    participant C as AmqpConsumer
    participant U as ProcesarMensajeUseCase
    participant R as WebhookResolver
    participant G as NeMoGuardrailsPolicy
    participant I as OllamaIntentEngine
    participant L as Ollama
    participant P as AmqpPublisher
    participant O as Cola de salida

    Q->>C: entrega un mensaje (prefetch 1)
    C->>U: ejecutar(body) en un hilo
    U->>R: parsear(body)
    alt origen no soportado o payload inválido
        R-->>U: OrigenNoSoportado o ValueError
        U-->>C: None
        C->>Q: ACK, mensaje descartado
    else mensaje normalizado
        R-->>U: MensajeEntrante
        U->>G: evaluar(texto)
        G->>L: self check input
        L-->>G: respuesta del modelo
        alt rail rechaza o falla
            G-->>U: permitido = false
            U->>P: publicar(resultado bloqueado)
        else rail aprueba
            G-->>U: permitido = true
            U->>I: detectar(mensaje)
            opt texto sin adjuntos
                I->>L: chat con format=json, timeout 15 s
                L-->>I: JSON de intención
            end
            I-->>U: Intencion o fallback seguro
            U->>P: publicar(resultado)
        end
        P->>O: basic_publish
        U-->>C: ResultadoClasificado
        C->>Q: ACK
    end
    Note over Q,C: Si el pipeline lanza una excepción, NACK sin reencolar
```

*Caption — eje comportamiento, fase 02-design: recorrido de un mensaje. Hay hasta dos llamadas
al modelo por mensaje y la confirmación a la cola de entrada llega después de publicar.*

Tres propiedades de este flujo que condicionan el threat model:

1. `prefetch_count=1` hace que el consumo sea serie aunque cada mensaje corra en su hilo:
   el broker no entrega el siguiente hasta que el actual se confirma.
2. El motor de intención tiene timeout (15 s); la llamada del rail no lo fija en este código.
3. El publicador abre una conexión nueva por mensaje y no usa confirmaciones del broker.

## Ciclo de vida de la entidad núcleo

```mermaid
stateDiagram-v2
    [*] --> Recibido: entrega AMQP
    Recibido --> Descartado: origen no soportado o payload inválido
    Recibido --> Normalizado: un parser lo soporta
    Normalizado --> Bloqueado: el rail rechaza o falla
    Normalizado --> Permitido: el rail aprueba o no hay texto
    Permitido --> Clasificado: trae adjuntos o el modelo responde con confianza suficiente
    Permitido --> Fallback: el modelo falla, rompe el contrato o da confianza baja
    Bloqueado --> Publicado: permitido = false
    Clasificado --> Publicado
    Fallback --> Publicado: tipo texto, confianza 0
    Publicado --> Confirmado: ACK
    Descartado --> Confirmado: ACK
    Recibido --> Rechazado: excepción no controlada
    Clasificado --> Rechazado: fallo al publicar
    Rechazado --> [*]: NACK sin reencolar, sin DLQ
    Confirmado --> [*]
```

*Caption — eje comportamiento, fase 02-design: estados de un mensaje dentro del pipeline. El
estado `Rechazado` es terminal y hoy equivale a perder el mensaje.*

## Modelo de datos y dominio

El servicio no tiene almacén propio. El diagrama es el modelo lógico de lo que viaja por memoria
y por la cola de salida.

```mermaid
erDiagram
    MENSAJE_ENTRANTE ||--o{ ADJUNTO : contiene
    MENSAJE_ENTRANTE ||--|| RESULTADO_GUARDRAIL : "se evalua con"
    MENSAJE_ENTRANTE ||--|| RESULTADO_CLASIFICADO : produce
    RESULTADO_CLASIFICADO ||--o| INTENCION : incluye
    MENSAJE_ENTRANTE {
        string mensaje_id PK
        string origen
        string remitente
        string texto
        datetime timestamp
        datetime recibido_en
    }
    ADJUNTO {
        string tipo
        string url_o_id
        string mime_type
    }
    RESULTADO_GUARDRAIL {
        boolean permitido
        string motivo
    }
    INTENCION {
        string etiqueta
        string tipo_respuesta
        float confianza
        json entidades
        string razonamiento
    }
    RESULTADO_CLASIFICADO {
        string mensaje_id FK
        string origen
        string remitente
        string tipo_respuesta
        boolean permitido
        string motivo_bloqueo
        json payload
        datetime procesado_en
    }
```

*Caption — eje estructura, fase 02-design: modelo lógico. `RESULTADO_CLASIFICADO` es lo único
que sale del servicio; `INTENCION` falta cuando la entrada se bloquea.*

```mermaid
classDiagram
    direction LR
    class ProcesarMensajeUseCase {
      +ejecutar(raw_payload) ResultadoClasificado
    }
    class MessageNormalizer {
      <<interface>>
      +parsear(raw) MensajeEntrante
    }
    class WebhookParser {
      <<interface>>
      +soporta(payload) bool
      +parsear(payload) MensajeEntrante
    }
    class GuardrailsPolicy {
      <<interface>>
      +evaluar(texto) ResultadoGuardrail
    }
    class IntentEngine {
      <<interface>>
      +detectar(mensaje) Intencion
    }
    class MessagePublisher {
      <<interface>>
      +publicar(resultado)
    }
    class WebhookResolver
    class MessagingStyleParser
    class MessengerParser
    class InstagramParser
    class WhatsAppParser
    class NeMoGuardrailsPolicy
    class OllamaIntentEngine
    class AmqpPublisher

    ProcesarMensajeUseCase --> MessageNormalizer
    ProcesarMensajeUseCase --> GuardrailsPolicy
    ProcesarMensajeUseCase --> IntentEngine
    ProcesarMensajeUseCase --> MessagePublisher
    WebhookResolver ..|> MessageNormalizer
    WebhookResolver o--> WebhookParser
    MessagingStyleParser ..|> WebhookParser
    WhatsAppParser ..|> WebhookParser
    MessengerParser --|> MessagingStyleParser
    InstagramParser --|> MessagingStyleParser
    NeMoGuardrailsPolicy ..|> GuardrailsPolicy
    OllamaIntentEngine ..|> IntentEngine
    AmqpPublisher ..|> MessagePublisher
```

*Caption — eje estructura, fase 02-design: puertos del dominio y adaptadores que los
implementan. Las dependencias apuntan hacia el dominio.*

### Deuda de diseño observada

| Observación | Dónde | Efecto |
|---|---|---|
| La capa de aplicación importa de infraestructura (`STARTING_AT`, `enmascarar`, constantes de auditoría) | `application/procesar_mensaje.py` | Rompe la regla de dependencia que el propio módulo declara |
| `Settings()` y `logging.config.dictConfig` se ejecutan al importar | `amqp/consumer.py`, `amqp/publisher.py`, `presentation/bootstrap.py`, `main.py` | Importar un módulo exige tener la configuración completa; `logging_config.yaml` se abre con ruta relativa al directorio de trabajo |
| `Settings` exige variables que el pipeline no usa (`db_*`, `key_size`, `entity_*`, `dto_*`, `national_id_url`) | `config/settings.py` | Configuración y secretos innecesarios en cada despliegue |
| Código heredado sin cablear | `infrastructure/security/`, `infrastructure/persistence/` | Superficie y dependencias (mongoengine, pymongo, pillow, jwcrypto) sin uso; 0 % de cobertura |

## Contratos de API

El servicio no expone HTTP. Su interfaz son dos canales AMQP, especificados en
[`contracts/asyncapi.yaml`](contracts/asyncapi.yaml) (AsyncAPI 3.0). Los esquemas de salida se
generan desde los modelos pydantic del dominio, no se escriben a mano.

| Canal | Dirección | Mensaje | AuthN/Z | Garantía hoy |
|---|---|---|---|---|
| Cola de entrada (`queue_name`) | Recibe | Webhook de Meta (`object` = `page`, `whatsapp_business_account` o `instagram`) o sobre custom `jwe_body` | Usuario y contraseña del broker; sin permisos por cola definidos | Al menos una vez desde el broker; ACK tras publicar |
| Cola de salida (`output_queue_name`, por defecto `meta-classified`) | Envía | `ResultadoClasificado` en JSON | El mismo usuario del broker | A lo sumo una vez: sin confirmación del broker ni mensajes persistentes |

## Patrones de seguridad seleccionados (por amenaza DREAD priorizada)

Ordenados por puntuación. «Existe» significa implementado en el código; «Pendiente» es el
control que se adopta y aún no está.

| Amenaza | Patrón / Control | OWASP | Estado |
|---|---|---|---|
| T7 Consumo no acotado | Timeout en toda llamada externa, recorte de entrada, `prefetch_count=1`, límite de tamaño de mensaje en el broker | A10 · LLM10 | Existe: recorte, prefetch y timeout de intención. Pendiente: timeout del rail y límite de tamaño |
| T1 Prompt injection | Defensa en profundidad: sanitización, rail de entrada fail-closed, prompt que separa instrucciones de datos, salida validada | A06 · LLM01 | Existe. Pendiente: medir la eficacia con un corpus |
| T3 PII en logs | Enmascarado en origen y prohibición de registrar cuerpos de mensaje; logs estructurados | A09 | Existe en la auditoría. Pendiente: quitar el cuerpo del log del publicador |
| T6 Pérdida de mensajes | Dead-letter queue, confirmaciones del publicador, mensajes persistentes, `mandatory` | A10 · A08 | Pendiente |
| T5 Sin TLS | TLS en AMQP (`amqps`) y en el enlace con Ollama, o red privada con cifrado | A04 | Pendiente |
| T4 Mensajes falsos | Firma de Meta verificada aguas arriba, un usuario del broker por servicio y permisos por cola | A07 · A08 | Pendiente: depende del receptor |
| T2 Salida del LLM | Contrato cerrado para `etiqueta` (lista permitida) y esquema para `entidades` | A05 · LLM05 | Parcial: `tipo_respuesta` y `confianza` ya están acotados |
| T12 Despliegue | Manifest con límites, `securityContext`, secretos montados, imagen por digest | A02 | Pendiente: ADR-0006 |
| T8 Cadena de suministro | SCA, SBOM, imagen base por digest, build multi-etapa, modelo fijado por digest | A03 | Parcial: versiones fijadas |
| T10 URL de adjunto | Validar esquema y host, o marcar la URL como no confiable en el contrato | A05 | Pendiente |
| T9 Duplicados | Idempotencia por `mensaje_id` | A08 | Pendiente |
| T11 Código heredado | Eliminar o aislar lo que no se usa | A02 | Pendiente: decisión abierta |

El detalle de cada amenaza está en [`threat-model.md`](threat-model.md).
