# -*- coding: utf-8 -*-
"""
    El contador de eventos de seguridad (deploy/seguridad/hive-seguridad.sh, HIG-140): cuenta en los logs de Hive lo
    que alertan las reglas de Yggdrasil. Depende del texto de los logs, así que aquí se fija cada expresión contra el
    código que escribe la línea: un ejemplo por fila, que tiene que salir de un log del código y casar con su
    expresión. El conteo corre con un docker de mentira.
"""
import ast
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
CONTADOR = RAIZ / "deploy" / "seguridad" / "hive-seguridad.sh"
CODIGO = [RAIZ / "src", RAIZ / "shared", RAIZ / "engine" / "src"]
COMPOSES = [RAIZ / "deploy" / "docker-compose.yml", RAIZ / "engine" / "deploy" / "docker-compose.yml"]
AHORA = 1_800_000_000

# Un mensaje de ejemplo por cada fila del contador, como lo escribe el código.
EJEMPLOS = {
    "token_rechazado": [
        "Rejected a request for an access token: the assertion has expired",
        "Rejected an exchange for an access token: the token of the IdP has expired",
    ],
    "token_frenado": [
        "Turned away a request for an access token: too many failures from its origin",
        "Turned away an exchange for an access token: client c-1, person p-1 got their share of tokens this hour",
    ],
    "ruta_denegada": [
        "A cliente-analista was denied a route: client c-1, person p-1",
        "Client c-1 was denied an operator endpoint",
        "A cliente-analista was denied sending a text: client c-1, person p-1",
    ],
    "firma_webhook": ["Rejected webhook for chatbot b-1: missing or invalid X-Hub-Signature-256"],
    "verificacion_webhook": ["Rejected webhook verification for chatbot b-1"],
    "bot_err": ["Chatbot b-1 of client c-1 is now ERR: Meta turned its token away, 190"],
    "andvari": ["Key service unavailable: ConnectError"],
    "bifrost": [
        "Event bus: TimeoutError",
        (
            "The bus did not take a send of client c-1 (TimeoutError): it was not accepted, and the same key can be "
            "sent again"
        ),
        "Esperando a Bifröst: ConnectionClosedError",
        "El mensaje clasificado del bot b-1 no se pudo publicar: TimeoutError",
    ],
    "mongodb": ["The database is unavailable: ServerSelectionTimeoutError at webhook.py:36"],
}
# Lo que pasa a menudo en producción y no es un evento: no se cuenta.
RUIDO = [
    "Audit: person p-1 read the conversation of contact k-1 of chatbot b-1",
    "Chatbot b-1 of client c-1 is now ACT: Meta took a send",
    "Meta tokens of chatbot b-1 handed to the response engine: client c-1, key k",
    "Client c-1 was not let send to contact k-1 of chatbot b-1: {}",
]


def guion():
    return CONTADOR.read_text(encoding="utf-8")


def eventos():
    """Las filas de EVENTOS, con los grupos de contenedores resueltos: [(evento, [contenedores], expresión)]."""
    texto = guion()
    grupos = dict(re.findall(r'^(GATEWAY|MOTOR)="([^"]+)"$', texto, re.MULTILINE))
    bloque = re.search(r'^EVENTOS="(.*?)"$', texto, re.MULTILINE | re.DOTALL).group(1)
    filas = []
    for linea in bloque.splitlines():
        evento, contenedores, expresion = linea.split(";", 2)
        for nombre, valor in grupos.items():
            contenedores = contenedores.replace(f"${nombre}", valor)
        filas.append((evento, contenedores.split(), expresion))
    return filas


def plantilla(nodo) -> str | None:
    """El mensaje de una llamada al log, como expresión: lo variable de un f-string o un %s casa con cualquier cosa."""
    if isinstance(nodo, ast.JoinedStr):
        return "".join(re.escape(parte.value) if isinstance(parte, ast.Constant) else ".+" for parte in nodo.values)
    if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
        return re.sub(r"%[sdr]", ".+", re.escape(nodo.value))
    return None


def plantillas():
    encontradas = []
    for carpeta in CODIGO:
        for fichero in carpeta.rglob("*.py"):
            for nodo in ast.walk(ast.parse(fichero.read_text(encoding="utf-8"))):
                es_log = (isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Attribute)
                          and nodo.func.attr in ("info", "warning", "error", "critical", "exception")
                          and isinstance(nodo.func.value, ast.Name) and nodo.func.value.id in ("log", "logger"))
                if es_log and nodo.args and (patron := plantilla(nodo.args[0])) is not None:
                    encontradas.append(patron)
    return encontradas


def test_cada_ejemplo_lo_escribe_un_log_del_codigo():
    del_codigo = plantillas()
    sin_log = [ejemplo for lista in EJEMPLOS.values() for ejemplo in lista
               if not any(re.fullmatch(patron, ejemplo, re.DOTALL) for patron in del_codigo)]

    assert sin_log == []


def test_cada_fila_del_contador_casa_con_un_ejemplo_de_su_evento():
    sin_ejemplo = [(evento, expresion) for evento, _, expresion in eventos()
                   if not any(re.search(expresion, ejemplo) for ejemplo in EJEMPLOS.get(evento, []))]

    assert sin_ejemplo == []


def test_cada_ejemplo_lo_cuenta_su_evento_y_ningun_otro():
    filas = eventos()
    for evento, lista in EJEMPLOS.items():
        for ejemplo in lista:
            casan = {otro for otro, _, expresion in filas if re.search(expresion, ejemplo)}
            assert casan == {evento}, ejemplo
    for linea in RUIDO:
        assert not [evento for evento, _, expresion in filas if re.search(expresion, linea)], linea


def test_los_contenedores_son_los_de_los_composes():
    declarados = set()
    for compose in COMPOSES:
        declarados |= set(re.findall(r"container_name: (\S+)", compose.read_text(encoding="utf-8")))
    contados = {nombre for _, contenedores, _ in eventos() for nombre in contenedores}

    assert contados - declarados == set()
    # Cada contenedor del gateway y del motor se lee, aunque hoy no tenga un evento propio.
    assert {"hive-api", "hive-webhook", "hive-ingest", "hive-notifier"} <= contados


# --- El conteo, con un docker de mentira ---------------------------------------------------------------------------

en_bash = pytest.mark.skipif(shutil.which("bash") is None or os.name == "nt", reason="el contador es de bash")

# logs --since <desde> --until <hasta> <contenedor>: el log de $FALSO/logs/<contenedor>, o falla si no existe.
DOCKER = """#!/bin/sh
echo "$*" >>"$FALSO/llamadas"
[ -f "$FALSO/logs/$6" ] || { echo "Error response from daemon: No such container: $6" >&2; exit 1; }
cat "$FALSO/logs/$6"
"""


@pytest.fixture
def nidavellir(tmp_path):
    falso = tmp_path / "falso"
    (falso / "logs").mkdir(parents=True)
    (falso / "docker").write_text(DOCKER, encoding="utf-8", newline="\n")
    (falso / "docker").chmod(0o755)
    return falso, tmp_path / "estado", tmp_path / "thor" / "hive_seguridad.prom"


def pasada(nidavellir, logs: dict, ahora=AHORA):
    falso, estado, metricas = nidavellir
    for fichero in (falso / "logs").iterdir():
        fichero.unlink()
    for contenedor, lineas in logs.items():
        texto = "".join(f"[2026-11-16 03:08:22.307] WARNING [production] funcion   {linea}\n" for linea in lineas)
        (falso / "logs" / contenedor).write_text(texto, encoding="utf-8")
    (falso / "llamadas").unlink(missing_ok=True)
    env = {**os.environ, "FALSO": str(falso), "SEGURIDAD_DOCKER": str(falso / "docker"),
           "SEGURIDAD_ESTADO": str(estado), "SEGURIDAD_METRICAS": str(metricas), "SEGURIDAD_AHORA": str(ahora),
           "LC_ALL": "C"}
    salida = subprocess.run(["bash", str(CONTADOR)], env=env, capture_output=True, text=True, check=True)
    texto = metricas.read_text(encoding="utf-8")
    contadores = {(evento, contenedor): int(valor) for evento, contenedor, valor in
                  re.findall(r'^hive_seguridad_eventos_total\{evento="([^"]+)",contenedor="([^"]+)"\} (\d+)$',
                             texto, re.MULTILINE)}
    leidos = dict(re.findall(r'^hive_seguridad_contenedor_leido\{contenedor="([^"]+)"\} ([01])$', texto, re.MULTILINE))
    llamadas = (falso / "llamadas").read_text(encoding="utf-8").splitlines()
    return contadores, leidos, llamadas, texto, salida.stdout + salida.stderr


TODOS = ["hive-api", "hive-webhook", "hive-ingest", "hive-classifier", "hive-coordinator", "hive-responder",
         "hive-evaluator", "hive-notifier"]


def todos_vacios():
    return {contenedor: [] for contenedor in TODOS}


@en_bash
def test_cuenta_cada_evento_en_su_contenedor(nidavellir):
    logs = todos_vacios()
    logs["hive-api"] = [*EJEMPLOS["token_rechazado"], EJEMPLOS["token_rechazado"][0], *EJEMPLOS["ruta_denegada"],
                        *RUIDO]
    logs["hive-webhook"] = [*EJEMPLOS["firma_webhook"] * 3, *EJEMPLOS["verificacion_webhook"]]
    logs["hive-ingest"] = [*EJEMPLOS["bot_err"], EJEMPLOS["bifrost"][0], *EJEMPLOS["mongodb"]]
    logs["hive-coordinator"] = [EJEMPLOS["bifrost"][2], EJEMPLOS["bifrost"][3]]
    contadores, leidos, _, _, _ = pasada(nidavellir, logs)

    assert contadores[("token_rechazado", "hive-api")] == 3
    assert contadores[("ruta_denegada", "hive-api")] == 3
    assert contadores[("firma_webhook", "hive-webhook")] == 3
    assert contadores[("verificacion_webhook", "hive-webhook")] == 1
    assert contadores[("bot_err", "hive-ingest")] == 1
    assert contadores[("bifrost", "hive-ingest")] == 1
    assert contadores[("mongodb", "hive-ingest")] == 1
    assert contadores[("bifrost", "hive-coordinator")] == 2
    # Lo que no pasó está a cero, para que increase() tenga de dónde partir.
    assert contadores[("andvari", "hive-api")] == 0
    assert contadores[("token_frenado", "hive-api")] == 0
    assert set(leidos.values()) == {"1"}


@en_bash
def test_los_contadores_solo_crecen_y_cada_ventana_empieza_donde_acabo_la_anterior(nidavellir):
    logs = todos_vacios()
    logs["hive-webhook"] = EJEMPLOS["firma_webhook"] * 2
    _, _, llamadas, _, _ = pasada(nidavellir, logs)
    contadores, _, segundas, _, _ = pasada(nidavellir, logs, ahora=AHORA + 60)

    assert contadores[("firma_webhook", "hive-webhook")] == 4
    # La primera mira el último minuto; la segunda, desde donde acabó la primera.
    assert f"logs --since {AHORA - 62} --until {AHORA - 2} hive-webhook" in llamadas
    assert f"logs --since {AHORA - 2} --until {AHORA + 58} hive-webhook" in segundas


@en_bash
def test_un_contenedor_que_no_se_puede_leer_queda_a_cero_y_los_demas_se_cuentan(nidavellir):
    logs = todos_vacios()
    del logs["hive-webhook"]
    logs["hive-api"] = EJEMPLOS["token_rechazado"]
    contadores, leidos, _, _, _ = pasada(nidavellir, logs)

    assert leidos["hive-webhook"] == "0"
    assert leidos["hive-api"] == "1"
    assert contadores[("firma_webhook", "hive-webhook")] == 0
    assert contadores[("token_rechazado", "hive-api")] == 2


@en_bash
def test_si_el_reloj_vuelve_atras_la_ventana_queda_vacia(nidavellir):
    pasada(nidavellir, todos_vacios())
    _, _, llamadas, _, _ = pasada(nidavellir, todos_vacios(), ahora=AHORA - 600)

    assert f"logs --since {AHORA - 602} --until {AHORA - 602} hive-api" in llamadas


@en_bash
def test_no_copia_el_texto_de_ninguna_linea(nidavellir):
    falso, estado, metricas = nidavellir
    logs = todos_vacios()
    logs["hive-api"] = [*EJEMPLOS["token_rechazado"], *EJEMPLOS["bot_err"]]
    _, _, _, texto, salida = pasada(nidavellir, logs)

    guardado = texto + salida + (estado / "contadores").read_text(encoding="utf-8")
    assert "assertion" not in guardado and "c-1" not in guardado and "b-1" not in guardado
    # Thor lo sirve: legible para todos, sin el fichero temporal al lado.
    assert oct(metricas.stat().st_mode & 0o777) == "0o644"
    assert [p.name for p in metricas.parent.iterdir()] == ["hive_seguridad.prom"]
