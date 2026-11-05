# Gate 3 — Pruebas (cierre de Fase 04)

**Aprobado el 2026-11-04 por Jeremi Alcala** (HIG-125). Estuvo abierto desde el 2026-10-30: desde ese día las releases
no esperan a los gates ([releases](../../docs/05-deployment/releases.md)), y la 0.4.0 salió con él abierto. Al cerrar
decidió dos cosas:
- **Los huecos de la matriz OWASP** pasan a los gates 4 y 5, donde ya tienen su tarea: el pipeline (HIG-20) y las
  alertas y el runbook (HIG-140).
- **El mutation testing sigue informando** sin bloquear los PR, sobre los módulos de seguridad.

- [x] Pirámide completa pasando (unit → integration → contract → e2e → security)
  - **Está:** en el CI (`ci`, `engine`, `portal`), con NATS de verdad para la integración. Contadas el 2026-11-04:

    | | unit | integration | contract | e2e | security |
    |---|---|---|---|---|---|
    | gateway | 2 666 | 51 | 100 | 15 (`tests/e2e`, del gateway, el motor y el portal juntos) | 932 (2 520 con `-m security`) |
    | motor | 396 | 15 | 71 | — | 142 (422 con `-m security`) |
    | portal | 69 | 4 | — | — | — |
  - **Seguridad** (HIG-134): `tests/security/` prueba lo hostil en cada ruta. `pytest -m security` la corre junto a
    las pruebas unitarias de la matriz OWASP, que `tests/conftest.py` marca.
  - **e2e** (HIG-135): [del webhook a la respuesta](../../docs/04-testing/e2e.md), con las imágenes de producción,
    Bifröst tal como está configurado, OpenBao con TLS, y Meta y el LLM falsos. Corre en los PR a `main`.
  - **PWA** (HIG-141): [su recorrido](../../docs/04-testing/e2e.md#el-recorrido-de-la-pwa) en Chromium, del login con
    un Zitadel falso al envío de una plantilla, con la CSP sin violaciones y el `cliente-analista` sin botones de
    admin.
  - **Seguridad del motor** (HIG-154): `engine/tests/security/` ataca lo que va al modelo y lo que vuelve de él, con un
    corpus de mensajes hostiles. Encontró que llama.cpp lee las marcas de turno de Gemma 4 dentro de un mensaje como
    tokens especiales, ya corregido en el cliente del LLM y en el gateway.
- [x] Matriz OWASP Top 10 ejecutada
  - **Está** (2026-10-30, HIG-133): [`owasp-mapping.md`](../owasp-mapping.md) traza A01–A10 a los controles reales, con
    la prueba que lo verifica y dónde se decidió. `tests/unit/test_owasp_mapping.py` falla si una prueba, un fichero o
    una ADR citados dejan de existir.
  - **Sus huecos pasan a los gates 4 y 5** (decisión de Jeremi del 2026-11-04), cada uno con su tarea:
    - el pipeline del Gate 4 (HIG-20): escaneo de imagen y de compose, SBOM, Actions por SHA y firma de imágenes;
    - las alertas, el tablero y el runbook del Gate 5 (HIG-140).
- [x] DAST limpio
  - **Está** (2026-10-30, HIG-90): [OWASP ZAP](../../docs/04-testing/dast.md) contra el stack de la prueba de punta a
    punta, en el job `dast · zap` de los PR a `main` y a mano. Escanea `hive-api` con su contrato OpenAPI, el webhook
    con el suyo y el portal entero, y falla con un High que `tests/e2e/zap/rules.tsv` no acepte.
  - **El primer resultado:** 0 High, 0 Medium y 0 Low en los tres; 5 informativas. Las 45 operaciones con token
    responden `401` sin él.
  - **Lo que no alcanza:** las rutas con token, porque ZAP no firma DPoP. Las cubren `tests/security/` y la matriz
    OWASP.
- [x] Rendimiento dentro de SLOs
  - **Está:** el webhook, 8,3 ms de mediana frente a los 250 ms de RNF01
    ([medición](../../docs/04-testing/medicion-webhook-i1.md)).
  - **Está:** el objetivo de punta a punta, RNF10: mediana de 10 s o menos y p95 de 20 s o menos, decidido por Jeremi el
    2026-10-30. Se mide pasivamente con `tests/performance/measure_end_to_end.py`, según
    [medicion-punta-a-punta.md](../../docs/04-testing/medicion-punta-a-punta.md).
  - **Está** (2026-11-04): la medición que cuenta, con 23 respuestas: **mediana 4,8 s y p95 5,5 s**, en nidavellir.
    Una tanda anterior, ese mismo día, dio un p95 de 31 s porque el LLM arrancó lento en el PC donde corre. Está
    apuntada en la medición, y el arranque lento es HIG-156.
- [x] Mutation testing ≥ 60% (objetivo)
  - **Está** (2026-10-30, HIG-137): mutmut sobre los módulos de seguridad (`src/auth/`, la firma del webhook, el estado
    de un chatbot y el cifrado por cliente), en el workflow `mutation`, que informa y no bloquea.
    [La puntuación por módulo](../../docs/04-testing/mutation-testing.md): del 84,8 % al 98,8 % sobre 977
    mutantes, y todos los módulos por encima del 97 %. Sobreviven 12, cada uno con su motivo.
  - **`request_limits.py`** (2026-11-04, HIG-155), que llegó mientras se medía: del 93,5 % de la pasada semanal al
    97,6 %, con tres pruebas nuevas. Sobreviven 4, equivalentes. Con él, 1 154 mutantes y un 98,0 % en el CI.
  - **El alcance son los módulos de seguridad,** y el workflow sigue sin bloquear (decisión de Jeremi del
    2026-11-04). El resto del gateway y el motor no se mutan.
