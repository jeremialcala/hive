# 04 — Pruebas (Gate 3)

Las pruebas viven en `tests/`, siguiendo la pirámide `unit → integration → contract → e2e → security`. Aquí van
la estrategia y la evidencia agregada:

- Plan de pruebas y su trazabilidad a los requisitos.
- Matriz OWASP Top 10 ejecutada (referencia a `.ai-dlc/owasp-mapping.md`).
- Resultados de DAST y de mutation testing (objetivo: 60 %).

Cierra con `.ai-dlc/gates/gate-3-testing.md`.

Mediciones:

- [I1: cuánto tarda hoy el webhook en responder a Meta](medicion-webhook-i1.md), la línea base de RNF01.
