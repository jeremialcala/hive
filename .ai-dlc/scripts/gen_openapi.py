"""Regenera los contratos OpenAPI de los dos servicios a partir de la aplicación.

`docs/02-design/contracts/openapi.yaml` es el de la API de gestión (`app.py`) y `openapi-webhook.yaml` el del
webhook que llama Meta (`webhook.py`). No se escriben a mano: se derivan de `openapi()`. Ejecutar desde la raíz del
repositorio:

    python .ai-dlc/scripts/gen_openapi.py

No necesita `config.env` ni servicios: usa los ajustes de prueba de `tests/conftest.py`.
`tests/contract/test_openapi_contract.py` falla si el fichero versionado deja de coincidir con la aplicación.
"""
import importlib
import pathlib
import re
import runpy
import sys

import yaml

RAIZ = pathlib.Path(__file__).resolve().parents[2]
CONTRATOS = RAIZ / "docs" / "02-design" / "contracts"
# El módulo de cada servicio y el fichero de su contrato.
SERVICIOS = {"app": CONTRATOS / "openapi.yaml", "webhook": CONTRATOS / "openapi-webhook.yaml"}
# Dónde atiende cada servicio (ADR-0024). Va en el contrato, que es lo que se publica en hive.higerotech.com/docs
# (ADR-0026), y no en la aplicación: allí haría que la documentación de desarrollo llamara a producción.
PUBLICO = {"app": "https://hive-api.higerotech.com", "webhook": "https://hive-webhook.higerotech.com"}

# Los DTO generan sus valores por defecto y sus ejemplos con Faker y random al importarse, así que cambian en cada
# arranque. Se quitan del contrato para que este sea estable y comparable.
VOLATILES = {"default", "examples", "example"}

CABECERA = """\
# Contrato OpenAPI de un servicio del gateway (fase 02-design). Refleja la API tal como está construida.
# Generado con .ai-dlc/scripts/gen_openapi.py: regenerar, no editar a mano.
# Sin valores por defecto ni ejemplos: en el código son aleatorios y cambian en cada arranque.
# Declara la autenticación (OAuth2, client_credentials en /oauth/token, ADR-0025). La firma X-Hub-Signature-256
# del webhook no aparece: se comprueba en una dependencia que OpenAPI no ve.
"""


def normalizar(nodo, en_propiedades=False):
    """Devuelve el nodo sin las claves volátiles. Los nombres de propiedad de un esquema nunca se quitan."""
    if isinstance(nodo, dict):
        return {
            clave: normalizar(valor, en_propiedades=(clave == "properties"))
            for clave, valor in nodo.items()
            if en_propiedades or clave not in VOLATILES
        }
    if isinstance(nodo, list):
        return [normalizar(valor) for valor in nodo]
    return nodo


def version() -> str:
    """
        La versión del gateway según el CHANGELOG: la última publicada y, si `[Unreleased]` ya tiene cambios,
        `+develop` (metadatos de compilación de SemVer). La de la aplicación es la de los ajustes, que en las
        pruebas no dice nada.
    """
    texto = (RAIZ / "CHANGELOG.md").read_text(encoding="utf-8")
    pendiente, publicada = re.search(r"## \[Unreleased\](.*?)## \[(\d+\.\d+\.\d+)\]", texto, re.S).groups()
    return publicada + ("+develop" if re.search(r"^- ", pendiente, re.M) else "")


def volcar(spec, publico: str | None = None) -> str:
    spec = {**spec, "info": {**spec["info"], "version": version()}}
    if publico:
        spec = {**spec, "servers": [{"url": publico}]}
    return CABECERA + yaml.safe_dump(normalizar(spec), sort_keys=True, allow_unicode=True, width=110)


def generar() -> dict:
    """El contrato de cada servicio, por fichero."""
    # Antes que el conftest: este genera la clave del gateway de las pruebas con código de `src`.
    sys.path.insert(0, str(RAIZ / "src"))
    # Y la raíz, de donde se importa `shared` (ADR-0023).
    sys.path.insert(1, str(RAIZ))
    runpy.run_path(str(RAIZ / "tests" / "conftest.py"))
    return {destino: volcar(importlib.import_module(modulo).app.openapi(), PUBLICO[modulo])
            for modulo, destino in SERVICIOS.items()}


if __name__ == "__main__":
    CONTRATOS.mkdir(parents=True, exist_ok=True)
    for destino, contrato in generar().items():
        destino.write_text(contrato, encoding="utf-8", newline="\n")
        print("escrito", destino.relative_to(RAIZ))
