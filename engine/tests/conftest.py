# -*- coding: utf-8 -*-
"""
    La suite de seguridad del motor (Gate 3, HIG-154): `tests/security/` y las pruebas unitarias que comprueban un
    control del threat model (`docs/02-design/threat-model.md`) o de la matriz OWASP del gateway. `pytest -m security`
    las corre solas, como en el gateway.
"""
import pytest

# Las pruebas unitarias que comprueban un control: lo que va al modelo y lo que se acepta de él (LLM01, LLM05), el
# fallo cerrado de cada worker, los sobres que se descartan, lo que no llega al log y lo que se tapa de Meta.
MODULOS_DE_SEGURIDAD = {
    "test_clasificador_llm", "test_evaluador_llm", "test_redactor_llm", "test_resumen", "test_whatsapp_cloud",
    "test_worker_clasificador", "test_worker_coordinador", "test_worker_evaluador", "test_worker_notificador",
    "test_worker_respuesta", "test_worker_salientes",
}


def pytest_collection_modifyitems(items):
    for item in items:
        if item.path.parent.name == "security" or item.path.stem in MODULOS_DE_SEGURIDAD:
            item.add_marker(pytest.mark.security)
