# ADR-0006: Una notificación recibe `200` solo cuando el broker confirma el mensaje

* **Estado:** superseded (por ADR-0021, 2026-10-18)
* **Fecha:** 2026-10-12
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 02-design
* **Versión:** 0.1.0
* **ID:** ADR-0006
* **Supersede:** —
* **Controles OWASP afectados:** A10 (condiciones excepcionales), A08 (integridad de los datos)

Decisión ya construida (PR #3, versión 0.1.0). Se registra aquí para que conste con sus alternativas.

> **Sustituida por [ADR-0021](0021-el-webhook-solo-publica.md)**, aceptada el 2026-10-18. Lo construido la sigue hasta I4: el webhook responderá `200` con el acuse de Bifröst, y la identificación del contacto pasa a un worker de ingesta.

> **Ampliada el 2026-10-13 por [ADR-0015](0015-que-se-responde-a-meta.md)**, que fija qué se responde en los demás
> casos: `503` también cuando no responden la base de datos o el servicio de claves, y `200` para lo firmado que
> no se entiende o no se puede procesar.

## Contexto

El gateway respondía `200` a Meta nada más recibir la notificación y publicaba en RabbitMQ en segundo plano. Si el
broker no estaba disponible, la excepción se perdía y el mensaje también: Meta lo daba por entregado y nadie se
enteraba.

## Decisión

El webhook espera a que RabbitMQ confirme el mensaje (confirmaciones del publicador, mensaje persistente y marca
`mandatory`) antes de responder `200`. Si no puede publicar, responde `503` para que Meta reintente.

## Alternativas consideradas

| Opción | Pros | Contras | Riesgo de seguridad |
|---|---|---|---|
| **Esperar la confirmación y responder `503` si falla** | Ningún mensaje aceptado se pierde; el reintento lo hace Meta | El webhook tarda lo que tarden la base y el broker; puede haber entregas duplicadas | Bajo |
| Responder `200` al instante y publicar después | Respuesta inmediata a Meta | Pérdida silenciosa si el broker falla | Medio: pérdida de datos sin rastro |
| Guardar primero en la base y publicar con un proceso aparte (outbox) | Sin pérdida y con respuesta rápida | Otro componente que operar; el contenido de los mensajes pasaría a guardarse en la base | Aumenta los datos personales en reposo |

## Consecuencias

- Positivas: se cumple RNF02; el fallo del broker es visible para Meta y para los logs.
- Negativas / deuda asumida: la entrega es "al menos una vez". El consumidor debe deduplicar por el identificador
  del mensaje de Meta, y hoy no lo hace nadie (RNF04). El tiempo de respuesta del webhook depende de MongoDB y de
  RabbitMQ (RNF01, sin medir).
- Impacto en threat model: cierra la pérdida silenciosa (T16) y abre el reenvío de duplicados (T7).
