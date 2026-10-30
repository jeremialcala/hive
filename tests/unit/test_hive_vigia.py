# -*- coding: utf-8 -*-
"""
    El vigía de Hive en midgard (deploy/vigia/hive-vigia.sh): reinicia lo que Docker marca unhealthy, porque Docker
    no lo hace, y deja de hacerlo si no sirve. Corre con un docker y un curl de mentira.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

VIGIA = Path(__file__).resolve().parents[2] / "deploy" / "vigia" / "hive-vigia.sh"
AHORA = 1_800_000_000

pytestmark = pytest.mark.skipif(shutil.which("bash") is None or os.name == "nt", reason="el vigía es de bash")

DOCKER = """#!/bin/sh
# inspect -f <formato> <nombre> | restart -t 10 <nombre>
case "$1" in
    inspect) [ -f "$FALSO/salud/$4" ] || exit 1; cat "$FALSO/salud/$4" ;;
    restart) [ -f "$FALSO/no-reinicia" ] && exit 1; echo "$4" >>"$FALSO/reiniciados" ;;
esac
"""
CURL = """#!/bin/sh
echo "$*" >>"$FALSO/avisos"
"""


@pytest.fixture
def midgard(tmp_path):
    falso = tmp_path / "falso"
    (falso / "salud").mkdir(parents=True)
    for nombre, guion in (("docker", DOCKER), ("curl", CURL)):
        (falso / nombre).write_text(guion, encoding="utf-8")
        (falso / nombre).chmod(0o755)
    aviso = tmp_path / "aviso.env"
    aviso.write_text("AVISO_URL=https://ntfy.example/tema\n", encoding="utf-8")
    return falso, aviso, tmp_path / "estado"


def pasada(midgard, salud: dict, ahora=AHORA, uptime=86400, aviso=True, **entorno):
    falso, fichero, estado = midgard
    for nombre in os.listdir(falso / "salud"):
        os.remove(falso / "salud" / nombre)
    for nombre, valor in salud.items():
        # «unhealthy» a secas es un contenedor en marcha; «exited unhealthy», uno parado.
        (falso / "salud" / nombre).write_text(valor if " " in valor else f"running {valor}".rstrip(), encoding="utf-8")
    for registro in ("reiniciados", "avisos"):
        (falso / registro).unlink(missing_ok=True)
    env = {**os.environ, "PATH": f"{falso}:{os.environ['PATH']}", "FALSO": str(falso),
           "VIGIA_DOCKER": str(falso / "docker"), "VIGIA_ESTADO": str(estado),
           "AVISO_ENV": str(fichero) if aviso else str(falso / "no-existe"),
           "VIGIA_CONTENEDORES": "hive-api hive-webhook hive-coordinator",
           "VIGIA_AHORA": str(ahora), "VIGIA_UPTIME_S": str(uptime), **entorno}
    salida = subprocess.run(["bash", str(VIGIA)], env=env, capture_output=True, text=True, check=True).stdout

    def leer(registro):
        ruta = falso / registro
        return ruta.read_text(encoding="utf-8").splitlines() if ruta.exists() else []

    return leer("reiniciados"), leer("avisos"), salida


def test_reinicia_lo_unhealthy_y_avisa(midgard):
    reiniciados, avisos, _ = pasada(midgard, {"hive-api": "unhealthy", "hive-webhook": "healthy",
                                              "hive-coordinator": "starting"})

    assert reiniciados == ["hive-api"]
    (aviso,) = avisos
    assert "Priority: high" in aviso and "hive-api" in aviso and "https://ntfy.example/tema" in aviso


def test_sin_nada_unhealthy_no_toca_nada_ni_avisa(midgard):
    # Un contenedor sin healthcheck, o que no existe, no es unhealthy.
    assert pasada(midgard, {"hive-api": "healthy", "hive-webhook": ""})[:2] == ([], [])


def test_lo_que_alguien_paro_se_queda_parado(midgard):
    """Docker marca unhealthy un contenedor parado, y `docker restart` lo arrancaría (2026-10-28)."""
    salud = {"hive-api": "exited unhealthy", "hive-webhook": "restarting unhealthy",
             "hive-coordinator": "paused unhealthy"}

    assert pasada(midgard, salud)[:2] == ([], [])


def test_recien_arrancado_midgard_no_reinicia_nada(midgard):
    reiniciados, avisos, salida = pasada(midgard, {"hive-api": "unhealthy"}, uptime=120)

    assert (reiniciados, avisos) == ([], []) and "arranque reciente" in salida


def test_tras_tres_reinicios_en_una_hora_no_reinicia_mas_y_avisa_una_vez(midgard):
    for minuto in range(3):
        assert pasada(midgard, {"hive-api": "unhealthy"}, ahora=AHORA + 60 * minuto)[0] == ["hive-api"]

    reiniciados, avisos, _ = pasada(midgard, {"hive-api": "unhealthy"}, ahora=AHORA + 180)
    assert reiniciados == [] and len(avisos) == 1 and "Priority: urgent" in avisos[0]

    # En la misma hora no se repite el aviso.
    assert pasada(midgard, {"hive-api": "unhealthy"}, ahora=AHORA + 240)[:2] == ([], [])


def test_pasada_la_hora_vuelve_a_intentarlo(midgard):
    for minuto in range(3):
        pasada(midgard, {"hive-api": "unhealthy"}, ahora=AHORA + 60 * minuto)

    assert pasada(midgard, {"hive-api": "unhealthy"}, ahora=AHORA + 3600 + 61)[0] == ["hive-api"]


def test_sin_el_tema_de_ntfy_reinicia_igual(midgard):
    reiniciados, avisos, salida = pasada(midgard, {"hive-api": "unhealthy"}, aviso=False)

    assert reiniciados == ["hive-api"] and avisos == [] and "sin avisar" in salida


def test_si_docker_no_puede_reiniciar_avisa_con_urgencia(midgard):
    (midgard[0] / "no-reinicia").write_text("", encoding="utf-8")

    reiniciados, avisos, _ = pasada(midgard, {"hive-coordinator": "unhealthy"})

    assert reiniciados == [] and len(avisos) == 1 and "Priority: urgent" in avisos[0]
