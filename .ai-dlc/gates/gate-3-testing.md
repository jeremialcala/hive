# Gate 3 — Pruebas (cierre de Fase 04)

**Estado a 2026-10-30: abierto.** Desde ese día las releases no esperan a los gates (decisión de Jeremi,
[releases](../../docs/05-deployment/releases.md)): la 0.4.0 sale con este gate abierto, y aquí queda, con evidencia,
lo que está y lo que falta. Lo que falta es HIG-125.

- [ ] Pirámide completa pasando (unit → integration → contract → e2e → security)
  - **Está:** en el CI (`ci`, `engine`, `portal`), con NATS de verdad para la integración.

    | | unit | integration | contract | e2e | security |
    |---|---|---|---|---|---|
    | gateway y motor | 1 879 | 49 | 87 | 2 (`tests/e2e`, juntos) | 654 (1 752 con `-m security`) |
    | motor | 268 | 7 | 57 | 0 | 0 |
    | portal | 60 | 2 | — | — | — |
  - **Seguridad** (HIG-134): `tests/security/` prueba lo hostil en cada ruta. `pytest -m security` la corre junto a
    las pruebas unitarias de la matriz OWASP, que `tests/conftest.py` marca.
  - **e2e** (HIG-135): [del webhook a la respuesta](../../docs/04-testing/e2e.md), con las imágenes de producción,
    Bifröst tal como está configurado, OpenBao con TLS, y Meta y el LLM falsos. Corre en los PR a `main`.
  - **PWA** (HIG-141): [su recorrido](../../docs/04-testing/e2e.md#el-recorrido-de-la-pwa) en Chromium, del login con
    un Zitadel falso al envío de una plantilla, con la CSP sin violaciones y el `cliente-analista` sin botones de
    admin. **Falta:** el motor no tiene suite de seguridad propia.
- [ ] Matriz OWASP Top 10 ejecutada
  - **Está** (2026-10-30, HIG-133): [`owasp-mapping.md`](../owasp-mapping.md) traza A01–A10 a los controles reales, con
    la prueba que lo verifica y dónde se decidió. `tests/unit/test_owasp_mapping.py` falla si una prueba, un fichero o
    una ADR citados dejan de existir.
  - **Falta:** sus huecos, cada uno con su tarea:
    - DAST (HIG-90);
    - el pipeline del Gate 4 (HIG-20);
    - las alertas y el runbook (HIG-140).
- [ ] DAST limpio
  - **Falta:** no hay DAST (HIG-90, OWASP ZAP en los PR hacia `main`).
- [ ] Rendimiento dentro de SLOs
  - **Está:** el webhook, 8,3 ms de mediana frente a los 250 ms de RNF01
    ([medición](../../docs/04-testing/medicion-webhook-i1.md)).
  - **Está:** el objetivo de punta a punta, RNF10: mediana de 10 s o menos y p95 de 20 s o menos, decidido por Jeremi el
    2026-10-30. Se mide pasivamente con `tests/performance/measure_end_to_end.py`, según
    [medicion-punta-a-punta.md](../../docs/04-testing/medicion-punta-a-punta.md).
  - **Falta:** una medición que cuente, con 20 respuestas o más. La del 2026-10-30 cumple (mediana 9,5 s, p95
    10,0 s), pero con 3.
- [ ] Mutation testing ≥ 60% (objetivo)
  - **Falta:** no está configurado.
