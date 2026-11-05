# Tests — pirámide AI-DLC

| Nivel | Qué prueba | Estado |
|---|---|---|
| `unit/` | El clasificador con Claude (el SDK de verdad, con un transporte HTTP falso), el worker con cada mensaje y el bucle común | Con pruebas |
| `integration/` | Contra un NATS con los permisos de Bifröst (`nats-test.conf`): leer, publicar, confirmar, reentregar, y que cada usuario solo publica su paso | Con pruebas |
| `contract/` | Que el sobre que se publica cumple `docs/02-design/contracts/asyncapi-motor.yaml` | Con pruebas |
| `e2e/` | Un mensaje de WhatsApp de punta a punta, con Claude de verdad (E7) | Vacío |
| `security/` | Lo que va al modelo y lo que vuelve (T1, T2, T7, T10; LLM01, LLM05, LLM10): un corpus de mensajes hostiles, las marcas de turno, las salidas que no cumplen su esquema, los límites y los destinos. `pytest -m security` la corre con las pruebas unitarias de los controles | Con pruebas (HIG-154) |

```bash
pytest                                  # toda la pirámide
pytest tests/unit --cov=src --cov-branch
```

`pyproject.toml` añade `engine/` y la raíz del monorepo al `sys.path`, así que `from src...` y `from shared...`
resuelven desde cualquier nivel. Las de integración se saltan sin `NATS_TEST_ADDR`, y fallan con
`NATS_TEST_REQUIRED`, como en el CI.
