# Alertas y tableros de Hive

* **Estado:** review
* **Fecha:** 2026-11-20
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 06-monitoring
* **Versión:** 1.0.0
* **Requisitos:** A09 de la [matriz OWASP](../../.ai-dlc/owasp-mapping.md); HIG-140 (Gate 5)

Qué avisa de Hive, por dónde llega el aviso y dónde se mira. Las reglas viven en Yggdrasil
(`deploy/prometheus/rules/`), cada una con sus pruebas de promtool en el CI. Este documento es el inventario desde
el lado de Hive. La fuente de verdad son las reglas: si una cambia, se cambia aquí.

## Cómo llega un aviso

- **Mimir** evalúa las reglas cada 15 s y manda lo que dispara a **Gjallarhorn** (Alertmanager).
- **Nornas** (Node-RED) lo entrega una sola vez por ntfy al móvil de Jeremi y lo pinta en Hlidskjalf, la tablet de
  pared, hasta que se resuelve.
- **El vigía de Hive** (`deploy/vigia/hive-vigia.sh`) va aparte: cada minuto reinicia en nidavellir lo que Docker
  marca unhealthy, y avisa por ntfy.

`critical` es para mirarlo en el momento; `warning`, en el día.

## El inventario

**Disponibilidad, desde fuera** (`hive-slo.yml`; ver [SLO](slo.md)):

| Alerta | Severidad | Qué dice |
|---|---|---|
| `HiveSLOQuemaRapida` | critical | `hive-api` o `hive-webhook` no se alcanzan desde fuera |
| `HiveSLOQuemaLenta` | warning | Fallan a ratos |
| `HiveSLOPresupuestoAgotado` | warning | Por debajo del 99,5 % en 30 días: aplica la política del presupuesto |
| `HiveLatenciaFueraDeSLO` | warning | Menos del 99 % de respuestas en menos de 1 s |
| `HiveSLOSinSondas` | warning | No llegan las sondas: el SLO no se está midiendo |

**Seguridad** (`hive-alertas.yml`, grupo `hive-seguridad`; ver [eventos de seguridad](eventos-de-seguridad.md)):

| Alerta | Severidad | Qué dice |
|---|---|---|
| `HiveTokenRechazosRepetidos` | warning | 10 o más rechazos de `/oauth/token` en 10 min: alguien prueba claves o aserciones |
| `HiveTokenFrenado` | warning | El límite de `/oauth/token` frenó a un origen o a una persona |
| `HiveRutasDenegadas` | warning | 5 o más rutas denegadas en 10 min: un rol pide lo que no le toca |
| `HiveWebhookRechazado` | warning | Un aviso al webhook sin la firma de Meta, o una verificación con el `verifyToken` equivocado |
| `HiveBotEnError` | critical | Meta rechazó el token de un bot, que pasa a ERR |
| `HiveDependenciaFallando` | critical | Andvari, Bifröst o MongoDB fallan para Hive durante más de 6 min |
| `HiveSeguridadSinMetricas` | warning | El contador de eventos está parado |
| `HiveSeguridadContenedorSinLeer` | warning | El contador no puede leer el log de un contenedor |

**La plataforma de Hive** (`hive-alertas.yml`, grupo `hive`, y `thor-alertas.yml`):

| Alerta | Severidad | Qué dice |
|---|---|---|
| `BifrostCaido` | critical | Bifröst no está sano: el motor no procesa mensajes |
| `AndvariSellado` | critical | Andvari está sellado: el gateway no abre ninguna clave y responde 503 |
| `ZitadelExternoCaido` | critical | Zitadel no responde desde fuera: las personas no entran |
| `ZitadelRespaldoAtrasado`, `ZitadelRespaldoFallido`, `ZitadelRespaldoSinMetricas` | warning | El respaldo nocturno de Zitadel |
| `ServicioDeHostCaido` | critical | Un contenedor de Hive vigilado por Thor está caído en nidavellir |
| `ContenedorCaido` | warning | Un contenedor murió y no ha vuelto |
| `ThorSinMetricas` | warning | Mimir no recibe nada de nidavellir: está colgado, apagado o sin red |
| `HostReiniciado` | warning | nidavellir arrancó hace menos de 15 min ([arranque tras un apagón](../05-deployment/arranque-tras-apagon.md)) |

**Los pilares** (`loki-alertas.yml` y `saga-alertas.yml`):

| Alerta | Severidad | Qué dice |
|---|---|---|
| `LokiCaido` | warning | Los logs de este rato no se guardan (Gná reintenta) |
| `LokiSinLogs` | warning | 2 h sin una línea de nidavellir: Gná no entrega |
| `LokiDescartaLogs` | warning | Loki descartó líneas: un log de seguridad que no se guardó |
| `SagaCaida` | warning | Las trazas de este rato se pierden |

## Los tableros

En Odín (Grafana de midgard), carpeta **Hive**:

- **«Hive · SLO»:** la disponibilidad y la latencia de 30 días por componente, el presupuesto que queda, las ventanas
  de la quema y las sondas junto a la ruta de midgard.
- **«Hive · seguridad»:** los eventos de seguridad por tipo y contenedor, el estado del contador y las alertas de
  seguridad activas. Es el tablero de seguridad del Gate 5; Jeremi lo revisó en producción el 2026-11-20.

Los dos marcan las alertas en sus gráficas. Para los logs y las trazas, Explore con los datasources Loki y Saga
(consultas útiles en el [runbook](runbook-incidentes.md#buscar-la-evidencia)).
