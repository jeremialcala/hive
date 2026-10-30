# ADR-0003: Consumo AMQP con un hilo por mensaje y confirmación manual

* **Estado:** superseded
* **Fecha:** 2026-10-12
* **Decisores:** Jeremi Alcalá
* **Fase AI-DLC:** 02-design
* **Versión:** 1.0.0
* **ID:** ADR-0003
* **Supersede / Superseded-by:** superseded by ADR-0008 (2026-10-20)
* **Controles OWASP afectados:** A10, A08

Decisión retroactiva: implementada en los commits 824fdcd, c8a2de3 y 4c7bb9e (junio de 2026).

## Contexto

Procesar un mensaje puede tardar segundos: hay hasta dos llamadas al modelo. `pika` con
`BlockingConnection` no es thread-safe y, si el callback bloquea el bucle de E/S, se pierden los
heartbeats y el broker cierra la conexión. Requisitos de origen: RF-01 y RS-07.

## Decisión

- `BlockingConnection` con `basic_qos(prefetch_count=1)` y confirmación manual.
- Cada entrega lanza un hilo que ejecuta el caso de uso; el ACK o el NACK se devuelven al hilo
  de la conexión con `add_callback_threadsafe`.
- ACK solo si el caso de uso termina sin excepción, es decir, después de publicar el resultado.
- Ante excepción, `basic_nack(requeue=False)`: el mensaje no vuelve a la cola.
- Los payloads no soportados o inválidos se registran y se confirman: no son un error del servicio.
- Colas declaradas como durables. Al cerrar, cada hilo tiene 5 segundos para terminar.
- El publicador abre y cierra una conexión por mensaje.

## Alternativas consideradas

| Opción | Pros | Contras | Riesgo de seguridad |
|---|---|---|---|
| Hilo por mensaje con ACK threadsafe y `prefetch_count=1` (elegida) | Los heartbeats siguen vivos durante llamadas largas; contrapresión natural | Procesamiento en serie: el caudal lo fija la latencia del modelo | Bajo: un mensaje lento no tumba la conexión |
| Procesar en el callback, sin hilos | Código más simple | Una llamada lenta al modelo corta la conexión y provoca reentregas | Medio: reentregas en bucle |
| `prefetch_count` mayor con un pool de hilos | Más caudal | Varias llamadas simultáneas al modelo; orden no garantizado por remitente | Medio: amplifica el consumo no acotado (T7) |
| Cliente asíncrono (`aio-pika`) | Concurrencia sin hilos | Reescritura; NeMo y el cliente de Ollama usados aquí son síncronos | Bajo |
| `requeue=True` ante error | No se pierde el mensaje | Un mensaje venenoso se reprocesa sin fin | Alto: DoS por un solo mensaje |

## Consecuencias

- Positivas: conexión estable y contrapresión; un mensaje malformado no entra en bucle.
- Negativas / deuda asumida:
  - **No hay dead-letter queue**: `queue_declare` no fija `x-dead-letter-exchange`, así que un
    NACK equivale a descartar el mensaje salvo que una política del broker diga otra cosa.
  - El publicador no usa confirmaciones (`confirm_delivery`), ni `delivery_mode=2`, ni
    `mandatory`, ni enlaza la cola de salida: un resultado puede perderse sin error.
  - No hay idempotencia: una reentrega de Meta produce dos resultados.
  - Una conexión nueva por publicación añade latencia y carga al broker.
- Impacto en threat model: T6 (pérdida de mensajes), T7 (consumo) y T9 (duplicados).
