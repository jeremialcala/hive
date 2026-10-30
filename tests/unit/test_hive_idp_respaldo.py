# -*- coding: utf-8 -*-
"""
    El respaldo nocturno de Zitadel (deploy/idp/respaldo/hive-idp-respaldo.sh, HIG-96): cifra el volcado antes de que
    salga de la máquina, no da por bueno uno vacío, guarda 30 días sin borrar nunca el último bueno, y deja métricas
    que dicen cuándo salió bien por última vez aunque la pasada de hoy falle. Corre con un docker y un age de mentira.
"""
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

RESPALDO = Path(__file__).resolve().parents[2] / "deploy" / "idp" / "respaldo" / "hive-idp-respaldo.sh"
AHORA = 1_800_000_000
DIA = 86400

pytestmark = pytest.mark.skipif(shutil.which("bash") is None or os.name == "nt", reason="el respaldo es de bash")

# docker exec <contenedor> pg_dump ...: escribe un volcado del tamaño que diga el fichero `tamano`, o falla.
DOCKER = """#!/bin/sh
[ -f "$FALSO/docker-falla" ] && exit 1
head -c "$(cat "$FALSO/tamano")" /dev/zero
"""
# age -R <clave pública>: marca lo que cifra, para que la prueba vea que pasó por age.
AGE = """#!/bin/sh
[ -f "$FALSO/age-falla" ] && exit 1
printf 'CIFRADO-CON-AGE:'
cat
"""
CURL = """#!/bin/sh
echo "$*" >>"$FALSO/avisos"
"""


@pytest.fixture
def nidavellir(tmp_path):
    falso = tmp_path / "falso"
    falso.mkdir()
    for nombre, guion in (("docker", DOCKER), ("age", AGE), ("curl", CURL)):
        (falso / nombre).write_text(guion, encoding="utf-8")
        (falso / nombre).chmod(0o755)
    (falso / "tamano").write_text("8192", encoding="utf-8")
    clave = tmp_path / "respaldo.age.pub"
    clave.write_text("age1prueba\n", encoding="utf-8")
    aviso = tmp_path / "aviso.env"
    aviso.write_text("AVISO_URL=https://ntfy.example/tema\n", encoding="utf-8")
    return {"falso": falso, "destino": tmp_path / "nas" / "hive-idp", "clave": clave, "aviso": aviso,
            "metricas": tmp_path / "thor" / "hive_idp_respaldo.prom"}


def pasada(nas, ahora=AHORA, check=True):
    env = {**os.environ, "PATH": f"{nas['falso']}:{os.environ['PATH']}", "FALSO": str(nas["falso"]),
           "RESPALDO_DESTINO": str(nas["destino"]), "RESPALDO_DESTINATARIO": str(nas["clave"]),
           "RESPALDO_METRICAS": str(nas["metricas"]), "RESPALDO_DOCKER": str(nas["falso"] / "docker"),
           "RESPALDO_AGE": str(nas["falso"] / "age"), "AVISO_ENV": str(nas["aviso"]),
           "RESPALDO_AHORA": str(ahora)}
    return subprocess.run(["bash", str(RESPALDO)], env=env, capture_output=True, text=True, check=check)


def metricas(nas) -> dict:
    lineas = nas["metricas"].read_text(encoding="utf-8").splitlines()
    return {linea.split()[0]: float(linea.split()[1]) for linea in lineas if not linea.startswith("#")}


def respaldos(nas) -> list:
    return sorted(p.name for p in nas["destino"].glob("hive-idp-*"))


def test_el_volcado_sale_cifrado_y_con_su_fecha(nidavellir):
    pasada(nidavellir)

    (nombre,) = respaldos(nidavellir)
    assert nombre == "hive-idp-20270115T080000Z.dump.age"
    assert (nidavellir["destino"] / nombre).read_bytes().startswith(b"CIFRADO-CON-AGE:")
    assert metricas(nidavellir) == {"hive_idp_respaldo_resultado": 1, "hive_idp_respaldo_ultimo_exito_segundos": AHORA,
                                    "hive_idp_respaldo_bytes": 8192 + len("CIFRADO-CON-AGE:"),
                                    "hive_idp_respaldo_ficheros": 1}


def test_un_volcado_vacio_no_es_un_respaldo_y_avisa(nidavellir):
    (nidavellir["falso"] / "tamano").write_text("10", encoding="utf-8")

    resultado = pasada(nidavellir, check=False)

    assert resultado.returncode != 0
    assert respaldos(nidavellir) == []
    assert metricas(nidavellir)["hive_idp_respaldo_resultado"] == 0
    assert "respaldo de Zitadel" in (nidavellir["falso"] / "avisos").read_text(encoding="utf-8")


@pytest.mark.parametrize("falla", ["docker-falla", "age-falla"])
def test_si_pg_dump_o_age_fallan_no_queda_nada_a_medias(nidavellir, falla):
    (nidavellir["falso"] / falla).touch()

    assert pasada(nidavellir, check=False).returncode != 0
    assert respaldos(nidavellir) == []
    assert metricas(nidavellir)["hive_idp_respaldo_resultado"] == 0


def test_una_pasada_que_falla_recuerda_el_ultimo_respaldo_bueno(nidavellir):
    pasada(nidavellir)
    (nidavellir["falso"] / "docker-falla").touch()

    pasada(nidavellir, ahora=AHORA + DIA, check=False)

    valores = metricas(nidavellir)
    assert valores["hive_idp_respaldo_resultado"] == 0
    assert valores["hive_idp_respaldo_ultimo_exito_segundos"] == AHORA
    assert valores["hive_idp_respaldo_ficheros"] == 1


def test_guarda_treinta_dias_y_nunca_borra_el_ultimo(nidavellir):
    nidavellir["destino"].mkdir(parents=True)
    ahora = time.time()
    for dias, nombre in ((40, "viejo"), (29, "reciente")):
        fichero = nidavellir["destino"] / f"hive-idp-{nombre}.dump.age"
        fichero.write_bytes(b"x" * 8192)
        os.utime(fichero, (ahora - dias * DIA, ahora - dias * DIA))

    pasada(nidavellir)

    nombres = respaldos(nidavellir)
    assert "hive-idp-viejo.dump.age" not in nombres
    assert "hive-idp-reciente.dump.age" in nombres
    assert len(nombres) == 2


def test_sin_la_clave_publica_no_vuelca_nada(nidavellir):
    nidavellir["clave"].unlink()

    assert pasada(nidavellir, check=False).returncode != 0
    assert respaldos(nidavellir) == []
