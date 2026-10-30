# ADR-0009: Claude, por la API de Anthropic, como LLM del motor

* **Estado:** superseded por [ADR-0010](ADR-0010-llm-propio-con-llama-cpp.md) (2026-10-21), en el proveedor, los
  modelos y sus claves. Sus reglas siguen: una llamada que clasifica con salida estructurada y fail-closed, el texto de
  la persona en turnos de usuario y la salida que no es de confianza
* **Fecha:** 2026-10-20
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 02-design
* **Versión:** 0.1.0
* **ID:** ADR-0009
* **Supersede:** ADR-0004 (intención con un LLM autoalojado); ADR-0005 (rail de entrada con NeMo Guardrails), salvo su
  política fail-closed, que se mantiene; ADR-0007 (placement del serving del LLM)
* **OWASP:** A02 (la clave de la API), A04 (TLS hasta la API) · LLM01 (inyección de prompt), LLM02 (datos personales
  hacia un tercero), LLM05 (salida no confiable), LLM10 (consumo sin límite)

Aceptada el 2026-10-20 al fusionar la PR #65, con lo que proponía: el clasificador sustituye a NeMo y Ollama, y el de
respuesta lleva los *fallbacks* del servidor.

> **Superada el 2026-10-21.** En el E7, la API de Anthropic rechazó todas las llamadas desde midgard: 403 «Request
> not allowed», porque su IP sale de Venezuela, que no es una región soportada. La política de Anthropic alcanza
> también a las personas y entidades de allí, así que no se rodea con un proxy. Jeremi decidió servir un modelo propio
> con llama.cpp en la LAN ([ADR-0010](ADR-0010-llm-propio-con-llama-cpp.md)).

## Contexto

El motor necesita un LLM en tres workers: el clasificador (seguridad de la entrada e intención), el de respuesta y el
evaluador (ADR-0008). Hasta ahora era Ollama con `llama3.1` (ADR-0004), que hacía de juez de NeMo y de clasificador a
la vez (ADR-0005). Pero midgard no puede servir un modelo: es un i3-3240 sin AVX2 ni GPU. La alternativa de ADR-0007
era contratar una GPU en Hetzner.

Lo comprobado el 2026-10-20 en la documentación de Anthropic:

- **Retención:** la API borra de su backend las entradas y salidas en 30 días. Lo que marquen sus sistemas de
  seguridad se guarda hasta 2 años. Lo retenido no se usa para entrenar sin permiso expreso.
- **Retención cero (ZDR):** la API de mensajes es elegible, pero se pide a Anthropic, que la aprueba por
  organización.
- **Residencia:** en la API de Anthropic, `inference_geo` solo admite `us` o `global`. Para inferencia en la UE hace
  falta Vertex AI con región `eu`, y entonces el encargado del tratamiento es Google.

## Decisión

Decisiones de Jeremi del 2026-10-20: la API de Anthropic, sin pedir ZDR por ahora, Haiku 5.5 para clasificar y
Opus 5.5 para responder y evaluar.

1. **La API de Anthropic** (`api.anthropic.com`), con el SDK oficial de Python (`anthropic`). No hay serving propio
   ni GPU que mantener.
2. **Un modelo por worker, en su configuración:**

   | Worker | Modelo | Esfuerzo | Por qué |
   |---|---|---|---|
   | Clasificador | `claude-haiku-5-5` | `low` | Una tarea corta, en todos los mensajes |
   | Respuesta | `claude-opus-5-5` | `medium` (el suyo por defecto) | La calidad de lo que lee la persona |
   | Evaluador | `claude-opus-5-5` | `low` | Un veredicto corto sobre una respuesta ya escrita |

   Cambiar de modelo o de esfuerzo es cambiar la configuración, sin tocar código.
3. **El clasificador hace, en una sola llamada, lo que hacían NeMo y Ollama en dos:** decide si el mensaje se
   atiende y cuál es su intención.
   - La respuesta tiene un esquema fijo (`output_config.format`): `permitido`, `motivo`, `etiqueta`, `confianza`,
     `entidades`. Ningún texto libre llega más allá.
   - Se mantiene la política **fail-closed** de ADR-0005. Si falla la llamada, la respuesta no valida o Claude
     rechaza el mensaje (`stop_reason: "refusal"`), el mensaje queda `permitido=false`, con el motivo distinguido
     (error, rechazo o bloqueo).
   - **NeMo, Ollama y `annoy` salen del motor.** Con ellos se van la compilación de C++ en la imagen y la obligación
     de desarrollar en Linux.
4. **El de respuesta** recibe en el `system` la persona del bot y sus reglas, y en `messages` las últimas vueltas y
   el mensaje nuevo.
   - **El texto de la persona va siempre en un turno de usuario, nunca en el `system`.** Es la defensa básica contra
     la inyección de prompt (LLM01).
   - **El `system` es estable por bot y se cachea** (prompt caching).
   - **Lleva los *fallbacks* del lado del servidor** (`fallbacks: "default"`): si el modelo rechaza, la API reintenta
     con otro modelo dentro de la misma llamada. Si el rechazo persiste, el motor responde un texto fijo del bot y
     lo deja en el log.
5. **La salida de Claude no es de confianza (LLM05).**
   - El de respuesta no tiene herramientas: las acciones son del enlace, con los destinos validados (I7).
   - El evaluador revisa la respuesta antes de enviarla.
   - El notificador solo envía texto: nada de enlaces construidos por el modelo, ni plantillas, ni botones.
6. **Una clave de la API por worker que la usa**, guardada en Andvari, en `motor/data/anthropic/<worker>`. Cada rol
   `motor-<worker>` lee solo la suya.
   - Se revoca y se mide el gasto por worker.
   - La clave nunca está en un `.env`, en la imagen ni en el log.
7. **Límites, para que el gasto no se dispare (LLM10):**
   - un `max_tokens` por worker (256 para el clasificador y el evaluador, 1024 para el de respuesta);
   - un timeout del SDK por worker, con 2 reintentos;
   - el límite de gasto mensual de la organización en la consola de Anthropic.

   El coste estimado es de unos 3 céntimos de dólar por mensaje respondido: unos 0,02 céntimos el clasificador,
   1,8 el de respuesta y 1 el evaluador, con 1 500, 3 000 y 2 000 tokens de entrada respectivamente, a los precios
   del 2026-10-06. No cuenta el pensamiento de Opus 5.5, que no se puede desactivar y se cobra como salida: el
   esfuerzo bajo lo acota. Se mide en el esqueleto.
8. **Los datos que salen a Anthropic** son el texto del mensaje, las últimas vueltas de la conversación y las reglas
   del bot.
   - **No salen:** el número de teléfono, el nombre del contacto, el `clientId` ni los tokens de Meta. Los workers
     solo pasan a Claude el texto.
   - La clasificación de datos y el registro de tratamientos anotan a Anthropic como encargado del tratamiento,
     con su retención de 30 días y una transferencia internacional (EE. UU.).
   - **Pedir ZDR es condición antes del primer cliente real**, junto con el DPA de Anthropic.

## Alternativas consideradas

| Opción | Pros | Contras | Riesgo de seguridad |
|---|---|---|---|
| **API de Anthropic, Haiku 5.5 para clasificar y Opus 5.5 para responder** | Sin GPU ni serving propio. Calidad y salida estructurada. Mantenido por el proveedor | Coste por mensaje. Dependencia de un tercero. Inferencia fuera de la UE | Los mensajes salen a un encargado del tratamiento en EE. UU., con 30 días de retención mientras no haya ZDR |
| Claude por Vertex AI en la UE | Inferencia en la UE | Un proyecto de GCP y sus credenciales. Algunas funciones llegan más tarde | El encargado es Google; residencia en la UE |
| Ollama en una GPU de Hetzner (ADR-0007) | Datos en una máquina propia, en la UE | Una GPU que contratar, servir, actualizar y vigilar. Más calidad pide modelos más grandes | La superficie del servidor de inferencia y su red |
| NeMo Guardrails delante de Claude | Rails declarativos | Arrastra `annoy` y C++, otra llamada por mensaje y otra configuración que mantener | La misma: el juez es otro LLM |

## Consecuencias

- **Sale del motor:**
  - `nemoguardrails`, `ollama` y, con ellos, `annoy` y la compilación de C++;
  - los adaptadores `NeMoGuardrailsPolicy` y `OllamaIntentEngine`, sustituidos por adaptadores de Claude detrás
    de los mismos puertos (`GuardrailsPolicy` e `IntentEngine`).
- **ADR-0006 se revisa:** sin un modelo propio, el worker ya no tiene que vivir junto a él. Se despliega en midgard,
  junto a Bifröst y Andvari.
- **Las pruebas no llaman a la API:** usan un cliente falso con el contrato del SDK. Una batería de evaluación
  aparte, con mensajes reales anonimizados y con coste medido, mide la calidad del clasificador y del de respuesta
  antes de cerrar el Gate 3 del motor.
- **El threat model del motor se rehace:**
  - **T1** (inyección): con un modelo externo y la salida estructurada;
  - **T5** (TLS): la API es HTTPS;
  - **T7** (timeouts): fijados;
  - **T12** (secretos): la clave en Andvari.

## Construido (E3a, el clasificador, 2026-10-20)

- **El clasificador:** `ClaudeClasificador` (`src/infrastructure/claude/`), con el SDK `anthropic` 1.11.0 y el cliente
  asíncrono.
  - Hace una llamada por mensaje con texto, a `claude-haiku-5-5` con esfuerzo `low`.
  - Solo envía el texto, en el turno de usuario.
  - La salida va atada a un esquema con `output_config.format`, y se vuelve a validar al leerla, porque la API no
    admite límites de longitud ni de rango en el esquema.
- **Un puerto, no dos:** `ClasificadorDeEntrada` sustituye a `GuardrailsPolicy` e `IntentEngine`. Las consecuencias
  de arriba decían «detrás de los mismos puertos», pero la decisión 3 es una sola llamada que decide las dos cosas,
  y dos puertos obligaban a dos llamadas o a compartir el resultado por debajo.
- **`max_tokens` es 1024, no 256.** Haiku 5.5 piensa por defecto (pensamiento adaptativo), y lo que piensa cuenta en
  `max_tokens`: con 256, una respuesta podía cortarse antes del JSON. La guía de Anthropic recomienda bajar el
  esfuerzo y dejar sitio, no apagar el pensamiento. Lo que piensa a esfuerzo `low` se mide en el esqueleto (E7). Una
  respuesta cortada no se interpreta: es un fallo.
- **Ningún texto libre del modelo sale del clasificador:**
  - el motivo es uno de `ninguno`, `inyeccion`, `abuso` u `otro`;
  - la intención es una de seis genéricas (`saludo`, `pregunta`, `pedido`, `queja`, `despedida`, `otro`), mientras
    el clasificador no lea los intents del bot;
  - las entidades son pares `nombre`/`valor` con un formato fijo, y una mal formada se descarta sola.

  Fuera de taxonomía, el clasificador no decide si un mensaje está fuera de tema: sin las reglas del bot no lo puede
  saber.
- **Fail-closed, con reintentos.** El mensaje sigue solo si Claude lo permite **y** no da motivo para pararlo. Una
  respuesta incoherente no sigue.
  - Un rechazo (`stop_reason: "refusal"`) sale a la primera con el motivo `rechazo`. Haiku 5.5 no tiene *fallback*
    del servidor.
  - Un fallo de la API, una respuesta que no valida o una cortada no se confirman. Bifröst entrega el mensaje otra
    vez (30 s, 1 min, 5 min), y solo en la última entrega (`max_deliver` 4) sale con el motivo `error`. Así una caída
    de minutos de la API no deja sin respuesta a nadie.
  - Un mensaje sin texto (una imagen sin pie) no llega a Claude: sale con el motivo `sin_texto`.
- **La clave**, en Andvari, en `motor/data/anthropic/classifier`, campo `key`. El worker la lee al arrancar con su rol
  `motor-classifier` (`EngineKeyStore.api_key`, en `shared/`). Sin clave no arranca. La política del rol se amplía en
  el repositorio `andvari` (higerotech/andvari#4), y el operador pone la clave con `motor/clave-anthropic.sh`.
- **Los límites:** timeout de 7 s por intento y 2 reintentos del SDK. El peor caso queda por debajo de los 30 s del
  `ack_wait` del consumidor, y la configuración no deja pasar valores que lo rompan.
- **El log** dice los tokens de entrada y de salida de cada llamada, para medir el coste, y nunca el texto, la
  respuesta ni el error de la API.
## Construido (E3b, la retirada, 2026-10-20)

- **Salen del motor** NeMo Guardrails, Ollama y `annoy`, con sus adaptadores, sus prompts y la configuración de los
  rails. Con ellos se van el consumidor y el publicador AMQP (`pika`), el caso de uso que los unía, los parsers de
  webhooks de Meta y el código muerto de MongoDB y JWE (`mongoengine`, `pymongo`, `pillow`). El texto lo lee
  `shared.contract.what_was_said`, no los parsers: es lo que ADR-0008, punto 9, decía reaprovechar, y al final el
  clasificador solo necesitó el saneado.
- **La imagen** pasa a una sola etapa sobre `python:3.12-slim`, e instala solo ruedas (`--only-binary=:all:`): ya no
  hay nada que compilar. Su orden por defecto es el clasificador.
- **La cobertura mínima del CI** sube del 60 % al 80 %, la del gateway (ADR-0023, punto 5). Con el código de antes
  fuera, el motor tiene el 91 %.

## Construido (E5, el de respuesta, 2026-10-20)

- **El de respuesta:** `ClaudeRedactor` (`src/infrastructure/claude/redactor.py`) y el worker
  `python -m src.workers.responder`. Hace una llamada a `claude-opus-5-5` con esfuerzo `medium`, por
  `beta.messages`.
- **El `system` es estable por bot y lleva su punto de caché** (`cache_control` efímero). Contiene las instrucciones
  del motor, la persona del bot y sus reglas, en ese orden, y nada que cambie de un mensaje a otro. El mínimo
  cacheable de Opus 5.5 es de 512 tokens: un bot con pocas reglas puede no llegar, y entonces no se cachea, sin
  error. El log dice los tokens que vienen del caché, para verlo en el E7.
- **La conversación:**
  - las vueltas de la persona van como turnos de usuario y las del bot como turnos del asistente;
  - el último turno es siempre el mensaje que se responde;
  - empieza en la persona, dos seguidos del mismo lado se juntan (la documentación de la API se contradice sobre si
    los admite), y un adjunto se nombra por su tipo.
- **Los fallbacks del servidor:** `fallbacks: "default"` con la beta `server-side-fallback-2026-07-01`, y no el
  modelo fijado: así la API elige el sustituto según el motivo del rechazo. Si cambia de modelo, solo vale lo que
  escribió el que terminó, lo que va tras el último bloque `fallback`. Si rechazan todos, se contesta el texto fijo
  de «no puedo».
- **`max_tokens` es 4096, no 1024.** El pensamiento de Opus 5.5 no se puede apagar y cuenta en `max_tokens`, y la
  guía de Anthropic pide dimensionarlo con él. A 20 dólares el millón de tokens de salida, el peor caso son 8
  céntimos; lo que gasta de verdad se mide en el E7.
- **La salida no es de confianza:** solo se toma el texto, y se recorta a 4096 caracteres, lo que admite WhatsApp.
  Una respuesta cortada por `max_tokens` o vacía no se envía: se reintenta, y en la última entrega se contesta «no
  disponible».
- **Los textos fijos** (`no_puedo`, `solo_texto` y `no_disponible`) están en español, en el worker. Cuando un bot
  pueda tener los suyos, saldrán de su configuración.
- **Los límites:** 50 s por intento y un reintento del SDK. El peor caso queda por debajo de los 120 s del
  `ack_wait` de `MOTOR/responder`.
- **El sobre** que publica, `motor.respuesta.…`, no lleva la persona ni las vueltas: el notificador solo necesita
  el texto y a quién.

## Condiciones de revisión

- Llega el primer cliente real: entonces, ZDR y el DPA firmados, o pasar a Vertex AI en la UE.
- El coste medido por mensaje supera lo que el producto puede pagar.
- La calidad medida del clasificador con Haiku 5.5 no basta para el rail de seguridad.
