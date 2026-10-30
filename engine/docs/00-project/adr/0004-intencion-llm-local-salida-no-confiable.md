# ADR-0004: Detección de intención con un LLM autoalojado y salida tratada como no confiable

* **Estado:** superseded
* **Fecha:** 2026-10-12
* **Decisores:** Jeremi Alcalá
* **Fase AI-DLC:** 02-design
* **Versión:** 1.0.0
* **ID:** ADR-0004
* **Supersede / Superseded-by:** superseded by ADR-0009 (2026-10-20)
* **Controles OWASP afectados:** A05, A06, A04 · LLM01, LLM05, LLM10

Decisión retroactiva: implementada en el commit a66e3e9 (junio de 2026). Dónde se ejecuta el
modelo es una decisión aparte: ADR-0007.

## Contexto

Hay que clasificar texto libre, en español y sin un catálogo cerrado de intenciones, en una de
tres estrategias de respuesta (RF-06). El texto es de usuarios anónimos y contiene datos
personales (ver `data-classification.md`), con usuarios en la UE y en Latinoamérica.

## Decisión

- Usar un LLM generalista (`llama3.1`) servido por **Ollama** en infraestructura propia, a
  través del puerto `IntentEngine`.
- Pedir salida JSON (`format="json"`) con un prompt de sistema que fija el contrato y ordena no
  seguir instrucciones contenidas en el mensaje. El texto del usuario va delimitado en el turno
  de usuario, nunca en el prompt de sistema.
- **No confiar en la salida**: se valida con un modelo pydantic (`_RespuestaLLM`);
  `tipo_respuesta` se normaliza a un enum cerrado, la confianza se acota a [0, 1] y por debajo
  de 0.3 se descarta.
- Ante JSON inválido, contrato roto, error de red o timeout (15 s): **fallback seguro**
  (`desconocida`, `texto`, confianza 0). El pipeline no se detiene.
- Los mensajes con adjuntos se clasifican como `adjunto` sin invocar al modelo.

## Alternativas consideradas

| Opción | Pros | Contras | Riesgo de seguridad |
|---|---|---|---|
| LLM autoalojado con salida validada (elegida) | Los datos no salen a terceros; sin coste por token; sin catálogo de intenciones | Hay que operar el serving; latencia y coste fijo (ADR-0007) | Medio: prompt injection (T1), mitigada con rail y validación |
| API de un proveedor de LLM | Sin operación; modelos más capaces | Transferencia de datos personales a un tercero, posiblemente fuera de la UE; coste variable | Medio-alto: nueva frontera de datos y dependencia externa |
| Clasificador clásico entrenado (embeddings más regresión, o un modelo pequeño ajustado) | Rápido, barato, inmune a instrucciones en el texto | Necesita un conjunto etiquetado y reentrenar al cambiar intenciones | Bajo |
| Reglas y palabras clave | Determinista y auditable | No generaliza; mantenimiento por cliente | Bajo |

## Consecuencias

- Positivas: ningún dato personal sale de la infraestructura propia; el fallo del modelo
  degrada a `texto` en lugar de romper el servicio.
- Negativas / deuda asumida:
  - `etiqueta`, `entidades` y `razonamiento` son texto libre del modelo y se publican tal cual:
    el servicio de respuesta recibe contenido influido por el usuario (T2).
  - El modelo se obtiene por tag (`llama3.1`), sin fijar digest (T8).
  - No hay conjunto de evaluación: la calidad de la clasificación no está medida.
  - El fallback convierte un fallo del modelo en una respuesta de tipo `texto`; quien consuma
    el resultado debe mirar `confianza` y `razonamiento` para distinguirlo.
- Impacto en threat model: T1, T2, T7 y T8.
