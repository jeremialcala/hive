# ADR-0002: Arquitectura limpia con puertos y adaptadores

* **Estado:** accepted
* **Fecha:** 2026-10-12
* **Decisores:** Jeremi Alcalá
* **Fase AI-DLC:** 02-design
* **Versión:** 1.0.0
* **ID:** ADR-0002
* **Supersede / Superseded-by:** —
* **Controles OWASP afectados:** A06, A05

Decisión retroactiva: se tomó e implementó en los commits a66e3e9 y 6b2aceb (junio de 2026).
Esta ADR la deja escrita; la fecha es la de su documentación.

## Contexto

El servicio depende de tres tecnologías que pueden cambiar por separado: el broker (RabbitMQ),
el motor de IA (Ollama) y la capa de seguridad (NeMo Guardrails). Además, cada plataforma de
Meta tiene su propio formato de webhook. Origina la decisión el requisito RF-02 y RF-03 (un
modelo común) y la necesidad de probar el pipeline sin servicios externos.

## Decisión

Organizar el código en cuatro capas (`domain`, `application`, `infrastructure`, `presentation`)
con la regla de dependencia hacia el dominio:

- El dominio define modelos pydantic y cinco puertos: `WebhookParser`, `MessageNormalizer`,
  `GuardrailsPolicy`, `IntentEngine`, `MessagePublisher`.
- `ProcesarMensajeUseCase` recibe los puertos por constructor y no conoce ninguna tecnología.
- La infraestructura implementa los puertos; `presentation/bootstrap.py` es el único punto de composición.
- Añadir una plataforma es añadir un `WebhookParser` y registrarlo en `WebhookResolver`.

## Alternativas consideradas

| Opción | Pros | Contras | Riesgo de seguridad |
|---|---|---|---|
| Puertos y adaptadores (elegida) | Tests sin servicios externos; cambiar de LLM o de broker toca un adaptador | Más ficheros e indirección para un servicio pequeño | Bajo: los controles (sanitización, validación de salida) quedan en un sitio identificable |
| Estructura del template de origen (`controllers`, `utils`, `classes`) | Menos código | Lógica de negocio mezclada con pika y con el cliente del modelo | Medio: los controles se dispersan y son difíciles de probar |
| Framework de agentes o de cadenas de LLM | Menos código propio para invocar al modelo | Dependencia grande y cambiante; menos control sobre qué llega al modelo | Medio-alto: superficie de terceros en el camino de la entrada no confiable |

## Consecuencias

- Positivas: los 29 tests unitarios mockean Ollama, NeMo y pika; el caso de uso tiene el flujo
  completo en un solo fichero legible.
- Negativas / deuda asumida:
  - `application/procesar_mensaje.py` importa constantes y `enmascarar` de `infrastructure.shared`:
    la regla de dependencia tiene hoy una excepción.
  - Los módulos de infraestructura instancian `Settings()` y configuran el logging al importarse.
  - `infrastructure/security` e `infrastructure/persistence` quedaron del template sin uso.
- Impacto en threat model: facilita T1 y T2 (los controles viven en adaptadores concretos);
  la deuda del código heredado es T11.
