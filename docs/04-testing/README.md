# 04 — Pruebas (Gate 3)

Las pruebas viven en `tests/`, siguiendo la pirámide `unit → integration → contract → e2e → security`. Aquí van
la estrategia y la evidencia agregada:

- Plan de pruebas y su trazabilidad a los requisitos.
- Matriz OWASP Top 10 ejecutada (referencia a `.ai-dlc/owasp-mapping.md`).
- Resultados de DAST y de mutation testing (objetivo: 60 %).

Cierra con `.ai-dlc/gates/gate-3-testing.md`.

Pruebas de punta a punta:

- [Del webhook a la respuesta](e2e.md), con las imágenes de producción (HIG-135).

Mutation testing:

- [De los módulos de seguridad](mutation-testing.md): qué se muta, cómo corre y la puntuación por módulo (HIG-137).

Mediciones:

- [I1: cuánto tarda hoy el webhook en responder a Meta](medicion-webhook-i1.md), la línea base de RNF01.
- [Cuánto espera una persona la respuesta](medicion-punta-a-punta.md): el objetivo de punta a punta (RNF10), cómo se
  mide y cada medición.
