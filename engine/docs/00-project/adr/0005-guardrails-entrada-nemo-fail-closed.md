# ADR-0005: Rail de entrada con NeMo Guardrails y política fail-closed

* **Estado:** superseded
* **Fecha:** 2026-10-12
* **Decisores:** Jeremi Alcalá
* **Fase AI-DLC:** 02-design
* **Versión:** 1.0.0
* **ID:** ADR-0005
* **Supersede / Superseded-by:** superseded by ADR-0009 (2026-10-20), salvo la política fail-closed, que se mantiene
* **Controles OWASP afectados:** A06, A10 · LLM01, LLM10

Decisión retroactiva: implementada en los commits a66e3e9 y 4c7bb9e (junio de 2026).

## Contexto

El texto de un usuario anónimo va a llegar a un LLM (ADR-0004). Hace falta una capa previa que
rechace los intentos de jailbreak, de manipular las instrucciones o de extraer el prompt, y los
mensajes fuera del alcance de un asistente de atención al cliente. Requisitos de origen: RF-05
y RS-03.

## Decisión

- Un puerto `GuardrailsPolicy` evaluado **antes** de la detección de intención.
- Implementación con **NVIDIA NeMo Guardrails 0.22** y el rail `self check input`: el propio
  LLM juzga si el mensaje debe bloquearse. El flujo Colang emite el sentinel
  `GUARDRAIL_BLOQUEADO` y el adaptador lo detecta.
- **Fail-closed**: cualquier excepción al evaluar se convierte en `permitido=false`.
- Un mensaje bloqueado no se descarta: se publica un `ResultadoClasificado` con
  `permitido=false` y `motivo_bloqueo`, para que el servicio de respuesta decida qué decir.
- El texto vacío se da por permitido sin invocar al modelo.
- NeMo se importa de forma diferida y con la telemetría anónima desactivada. Habla con Ollama
  por su API compatible con OpenAI (`/v1`); el motor de intención usa la API nativa.

## Alternativas consideradas

| Opción | Pros | Contras | Riesgo de seguridad |
|---|---|---|---|
| NeMo `self check input` con el mismo modelo (elegida) | Configuración declarativa; sin otro modelo que servir | Una llamada más al LLM por mensaje; el juez comparte debilidades con el clasificador | Medio: una técnica que engaña al modelo puede evadir las dos capas |
| Modelo de seguridad dedicado (clasificador de jailbreak o un modelo guardián) | Juez independiente y más barato de ejecutar | Otro modelo que servir y evaluar | Bajo-medio |
| Filtro por patrones y listas | Determinista, sin latencia | Fácil de evadir con paráfrasis | Alto si es la única defensa |
| Sin rail, solo salida validada | Menos latencia y coste | La única barrera es la validación del JSON | Alto |
| Fail-open ante error del rail | Más disponibilidad | Un fallo de Ollama desactiva la seguridad | Alto |

## Consecuencias

- Positivas: ningún texto llega al clasificador sin veredicto; la caída del modelo bloquea en
  lugar de abrir.
- Negativas / deuda asumida:
  - Con Ollama caído, **todos** los mensajes con texto salen como bloqueados: el servicio de
    respuesta debe distinguir `motivo_bloqueo = "error en la capa de seguridad"` de un bloqueo real.
  - La llamada del rail no fija timeout en este código (T7).
  - La eficacia no está medida: los tests mockean el motor. El prompt del rail, además, termina
    con una respuesta ya escrita que hay que revisar (threat model, observación 1).
  - `nemoguardrails` arrastra `annoy`, que obliga a compilar en la imagen y a desarrollar en Linux.
  - El motivo del bloqueo es genérico: no queda registrado qué regla saltó.
- Impacto en threat model: T1 y T7.
