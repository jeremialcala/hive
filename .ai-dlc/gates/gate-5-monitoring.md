# Gate 5 — Monitoreo (cierre de Fase 06)

**Abierto** (HIG-140): tres de los cinco ítems están cumplidos.
- **El SLO** está definido y se medirá en cuanto se despliegue la release de Yggdrasil con sus sondas.
- **El proceso de incidentes** está documentado, pero falta probarlo con el simulacro (HIG-140, punto 5).

El gate se cierra con el simulacro.

- [ ] SLIs/SLOs definidos y monitoreados
  - **Definidos** (2026-11-20, decisión de Jeremi): hive-api y hive-webhook disponibles el 99,5 % en 30 días, y el 99 %
    de las respuestas en menos de 1 s, medidos con sondas desde fuera
    ([SLI y SLO](../../docs/06-monitoring/slo.md)). Los números de partida, medidos por el mismo camino: 99,87 % en
    7 días y 99,37 % en 30.
  - **Vigilados por Mimir** (higerotech/yggdrasil#134): reglas, alertas de quema del presupuesto con sus 7 pruebas de
    promtool, y el tablero «Hive · SLO».
  - **Falta:** desplegarlo y comprobar en midgard que llegan las sondas.
- [x] Dashboard de seguridad activo
  - **Está** (Yggdrasil 0.5.27, 2026-11-19): «Hive · seguridad» en Odín, con los eventos que cuenta el contador de los
    logs ([eventos de seguridad](../../docs/06-monitoring/eventos-de-seguridad.md)). Jeremi lo revisó en producción
    el 2026-11-20.
  - **Sus 8 alertas**, cargadas en Mimir y probadas con promtool
    ([alertas y tableros](../../docs/06-monitoring/alertas-y-tableros.md)).
- [x] Tres pilares operando (métricas, logs, traces)
  - **Métricas:** Mimir, con el contador de seguridad, Thor de nidavellir y, con el SLO, las sondas externas.
  - **Logs:** Loki en midgard, desde el 2026-11-20 (Yggdrasil 0.5.28). Gná los lleva desde nidavellir, y en la
    primera lectura llegaron los 16 contenedores de Hive y su plataforma, y ninguno más. Se guardan 90 días, sin
    borrado por API.
  - **Trazas:** Saga, una por mensaje, desde el 2026-10-19 ([trazas](../../docs/03-implementation/trazas.md)).
  - **Cómo encajan:** [06 — Monitoreo](../../docs/06-monitoring/README.md#los-tres-pilares). Los logs no llevan el id
    de traza; queda en el feedback loop.
- [ ] Proceso de respuesta a incidentes documentado y probado
  - **Documentado** ([runbook de incidentes](../../docs/06-monitoring/runbook-incidentes.md)):
    - detectar, clasificar, contener, erradicar, recuperar y postmortem;
    - seis escenarios: clave de un cliente robada, token de Meta filtrado, cliente comprometido, persona
      comprometida, secreto de la plataforma y caída;
    - cómo buscar la evidencia en Loki;
    - el aviso de brechas a los clientes (art. 33.2).
  - **Falta:** el simulacro sobre el cliente de prueba (HIG-140, punto 5).
- [x] Feedback loop a fases previas establecido
  - **Está** ([feedback loop](../../docs/06-monitoring/feedback-loop.md)): cada acción de un postmortem vuelve como un
    cambio en su fase, con una revisión mensual del SLO, la seguridad y las alertas.
  - **Ya funcionaba:** seis casos de producción que cambiaron una fase anterior. El último, al escribir el runbook,
    corrigió la documentación que prometía revocar los tokens al instante.
  - **Los huecos del runbook,** con la fase a la que vuelven, están en sus pendientes.
