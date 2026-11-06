# -*- coding: utf-8 -*-
"""
    El despliegue de una release (deploy/desplegar.sh, HIG-20, ADR-0036): solo levanta imágenes cuya firma es la del
    workflow release con esa etiqueta, comprueba todo antes de cambiar nada y dice cómo volver. Corre con un docker y
    un curl de mentira, y un servidor de mentira en un directorio temporal.
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
SCRIPT = RAIZ / "deploy" / "desplegar.sh"
ADR = RAIZ / "docs" / "00-project" / "adr" / "0036-imagenes-firmadas-desde-el-ci.md"
RELEASE = RAIZ / ".github" / "workflows" / "release.yml"

IMAGENES = ("gateway", "engine", "portal", "docs")
CONTENEDORES = ("hive-webhook", "hive-api", "hive-ingest", "hive-docs", "hive-classifier", "hive-coordinator",
                "hive-responder", "hive-evaluator", "hive-notifier", "hive-portal")
COMPOSES = {"hive-gateway": "deploy/docker-compose.yml", "hive-engine": "engine/deploy/docker-compose.yml",
            "hive-portal": "portal/deploy/docker-compose.yml"}
NUEVA = "v0.21.0"
ANTES = "v0.20.0"

en_bash = pytest.mark.skipif(shutil.which("bash") is None or os.name == "nt", reason="el despliegue es de bash")

# Cada llamada queda en «llamadas». El estado del servidor de mentira vive en $FALSO:
#   imagenes/<imagen>-<etiqueta>  las imágenes etiquetadas que hay; version-<proyecto>  lo que levantó su compose;
#   salud/<contenedor>[-<versión>]  si existe, su estado en vez de healthy (con cualquier versión, o con esa);
#   firma-mala, otra-imagen  los fallos que se piden.
DOCKER = r"""#!/bin/sh
echo "$*" >>"$FALSO/llamadas"
proyecto_de() {
    case "$1" in
        hive-portal) echo hive-portal ;;
        hive-webhook|hive-api|hive-ingest|hive-docs) echo hive-gateway ;;
        *) echo hive-engine ;;
    esac
}
imagen_de() {
    case "$1" in
        hive-webhook|hive-api|hive-ingest) echo gateway ;;
        hive-docs) echo docs ;;
        hive-portal) echo portal ;;
        *) echo engine ;;
    esac
}
case "$1" in
    inspect)
        nombre=$4; img=$(imagen_de "$nombre"); v=$(cat "$FALSO/version-$(proyecto_de "$nombre")")
        id="sha256:id-$img-$v"; [ -f "$FALSO/otra-imagen" ] && id="sha256:otra"
        salud=healthy
        [ -f "$FALSO/salud/$nombre" ] && salud=$(cat "$FALSO/salud/$nombre")
        [ -f "$FALSO/salud/$nombre-$v" ] && salud=$(cat "$FALSO/salud/$nombre-$v")
        case "$3" in
            *Health*) echo "ghcr.io/jeremialcala/hive-$img:$v|$salud|$id|0" ;;
            *RestartCount*) echo 0 ;;
            *) echo "ghcr.io/jeremialcala/hive-$img:$v" ;;
        esac ;;
    image)
        ref=$5; img=${ref#ghcr.io/jeremialcala/hive-}; img=${img%%:*}; v=${ref##*:}
        [ -f "$FALSO/imagenes/$img-$v" ] || exit 1
        echo "sha256:id-$img-$v" ;;
    pull) ;;
    run)
        [ -f "$FALSO/firma-mala" ] && { echo "Error: no matching signatures" >&2; exit 1; }
        echo "[]" ;;
    tag)
        img=${3#ghcr.io/jeremialcala/hive-}; img=${img%%:*}; touch "$FALSO/imagenes/$img-${3##*:}" ;;
    compose)
        dir=$3; proyecto=${dir##*/}
        case "$proyecto" in
            hive-gateway) v=$IMAGE_TAG ;;
            hive-portal) v=$PORTAL_IMAGE_TAG ;;
            hive-engine) v=$(sed -n 's/^ENGINE_IMAGE_TAG=//p' "$dir/.env") ;;
        esac
        echo "$v" >"$FALSO/version-$proyecto" ;;
esac
"""
# curl -fsSL --retry 3 --max-time 60 -o <fichero> <url>: sirve $FALSO/web/<ruta de la url sin el host>.
CURL = r"""#!/bin/sh
echo "curl $*" >>"$FALSO/llamadas"
while [ $# -gt 1 ]; do [ "$1" = -o ] && salida=$2; shift; done
ruta=${1#https://*/}
[ -f "$FALSO/web/$ruta" ] || exit 22
cp "$FALSO/web/$ruta" "$salida"
"""


def digest(img):
    return "sha256:" + f"{IMAGENES.index(img) + 1}" * 64


@pytest.fixture
def servidor(tmp_path):
    falso, apps, copias = tmp_path / "falso", tmp_path / "apps", tmp_path / "root"
    for ruta in (falso / "imagenes", falso / "salud", falso / "web", copias):
        ruta.mkdir(parents=True)
    for nombre, guion in (("docker", DOCKER), ("curl", CURL)):
        (falso / nombre).write_text(guion, encoding="utf-8", newline="\n")
        (falso / nombre).chmod(0o755)
    # Lo que corre: la v0.20.0 y sus composes.
    for img in IMAGENES:
        (falso / "imagenes" / f"{img}-{ANTES}").touch()
    for proyecto in COMPOSES:
        (falso / f"version-{proyecto}").write_text(ANTES, encoding="utf-8")
        (apps / proyecto).mkdir(parents=True)
        (apps / proyecto / "docker-compose.yml").write_text(f"# {proyecto} {ANTES}\n", encoding="utf-8")
    (apps / "hive-engine" / ".env").write_text(f"OTRA=1\nENGINE_IMAGE_TAG={ANTES}\n", encoding="utf-8")
    # La release nueva: sus digests y sus composes, con el del gateway cambiado.
    descargas = falso / "web" / "jeremialcala" / "hive" / "releases" / "download" / NUEVA
    descargas.mkdir(parents=True)
    for img in IMAGENES:
        (descargas / f"digest-hive-{img}.txt").write_text(
            f"hive-{img} ghcr.io/jeremialcala/hive-{img}@{digest(img)}\n", encoding="utf-8")
    for proyecto, ruta in COMPOSES.items():
        destino = falso / "web" / "jeremialcala" / "hive" / NUEVA / ruta
        destino.parent.mkdir(parents=True, exist_ok=True)
        cambia = proyecto == "hive-gateway"
        destino.write_text(f"# {proyecto} {NUEVA if cambia else ANTES}\n", encoding="utf-8")
    return falso, apps, copias


def desplegar(servidor, *argumentos, espera=1):
    falso, apps, copias = servidor
    (falso / "llamadas").unlink(missing_ok=True)
    env = {**os.environ, "FALSO": str(falso), "DESPLIEGUE_DOCKER": str(falso / "docker"),
           "DESPLIEGUE_CURL": str(falso / "curl"), "DESPLIEGUE_APPS": str(apps), "DESPLIEGUE_COPIAS": str(copias),
           "DESPLIEGUE_CERROJO": str(falso / "cerrojo"), "DESPLIEGUE_ESPERA_S": str(espera),
           "DESPLIEGUE_PAUSA_S": "0"}
    # Como en producción: el script por la entrada estándar.
    with SCRIPT.open("rb") as guion:
        resultado = subprocess.run(["bash", "-s", "--", *argumentos], stdin=guion, env=env, capture_output=True)
    llamadas = (falso / "llamadas").read_text(encoding="utf-8").splitlines() if (falso / "llamadas").exists() else []
    return resultado.returncode, resultado.stdout.decode() + resultado.stderr.decode(), llamadas


def que_levanta(llamadas):
    return [linea.split()[2].rsplit("/", 1)[1] for linea in llamadas if linea.startswith("compose ")]


@en_bash
def test_despliega_la_release_verificada(servidor):
    falso, apps, copias = servidor
    codigo, salida, llamadas = desplegar(servidor, NUEVA)

    assert codigo == 0, salida
    for img in IMAGENES:
        ref = f"ghcr.io/jeremialcala/hive-{img}@{digest(img)}"
        assert f"pull -q {ref}" in llamadas
        verificacion = [linea for linea in llamadas if linea.startswith("run ") and linea.endswith(ref)]
        assert len(verificacion) == 1
        assert ("--certificate-identity https://github.com/jeremialcala/hive/.github/workflows/release.yml"
                f"@refs/tags/{NUEVA}") in verificacion[0]
        assert "--certificate-oidc-issuer https://token.actions.githubusercontent.com" in verificacion[0]
        assert f"tag {ref} ghcr.io/jeremialcala/hive-{img}:{NUEVA}" in llamadas
    # Gateway y motor seguidos, el portal al final, y todo después de verificar.
    assert que_levanta(llamadas) == ["hive-gateway", "hive-engine", "hive-portal"]
    ultima_verificacion = max(i for i, linea in enumerate(llamadas) if linea.startswith("run "))
    primer_cambio = min(i for i, linea in enumerate(llamadas) if linea.startswith(("tag ", "compose ")))
    assert ultima_verificacion < primer_cambio
    for proyecto in COMPOSES:
        assert (falso / f"version-{proyecto}").read_text(encoding="utf-8").strip() == NUEVA
    assert "Hive v0.21.0 desplegado" in salida
    assert "Corre v0.20.0" in salida


@en_bash
def test_instala_solo_los_composes_que_cambiaron_con_copia(servidor):
    falso, apps, copias = servidor
    codigo, salida, _ = desplegar(servidor, NUEVA)

    assert codigo == 0, salida
    assert (apps / "hive-gateway" / "docker-compose.yml").read_text(encoding="utf-8") == f"# hive-gateway {NUEVA}\n"
    copias_de_compose = sorted(p.name for p in copias.glob("*-compose.bak-*.yml"))
    assert len(copias_de_compose) == 1
    assert copias_de_compose[0].startswith("hive-gateway-compose.bak-")
    assert (copias / copias_de_compose[0]).read_text(encoding="utf-8") == f"# hive-gateway {ANTES}\n"


@en_bash
def test_cambia_solo_la_etiqueta_del_motor_en_su_env(servidor):
    falso, apps, copias = servidor
    codigo, salida, _ = desplegar(servidor, NUEVA)

    assert codigo == 0, salida
    assert (apps / "hive-engine" / ".env").read_text(encoding="utf-8") == f"OTRA=1\nENGINE_IMAGE_TAG={NUEVA}\n"
    copia = list(copias.glob("hive-engine.env.bak-*"))
    assert len(copia) == 1
    assert copia[0].read_text(encoding="utf-8") == f"OTRA=1\nENGINE_IMAGE_TAG={ANTES}\n"
    # El .env no sale nunca en la salida.
    assert "OTRA" not in salida


@en_bash
def test_una_firma_que_no_es_la_del_workflow_no_cambia_nada(servidor):
    falso, apps, copias = servidor
    (falso / "firma-mala").touch()
    codigo, salida, llamadas = desplegar(servidor, NUEVA)

    assert codigo != 0
    assert "la firma de hive-gateway no es la del workflow release" in salida
    assert "No se ha cambiado nada" in salida
    assert not [linea for linea in llamadas if linea.startswith(("tag ", "compose "))]
    assert not list(copias.iterdir())
    assert (apps / "hive-engine" / ".env").read_text(encoding="utf-8").endswith(f"={ANTES}\n")


@en_bash
@pytest.mark.parametrize("contenido", [
    "",
    "hive-gateway ghcr.io/jeremialcala/hive-gateway:v0.21.0\n",
    "hive-gateway ghcr.io/otro/hive-gateway@sha256:" + "1" * 64 + "\n",
    "hive-gateway ghcr.io/jeremialcala/hive-gateway@sha256:" + "1" * 63 + "\n",
    "hive-engine ghcr.io/jeremialcala/hive-gateway@sha256:" + "1" * 64 + "\n",
    "hive-gateway ghcr.io/jeremialcala/hive-gateway@sha256:" + "1" * 64 + "; reboot\n",
])
def test_un_digest_con_otra_forma_no_se_descarga(servidor, contenido):
    falso, _, _ = servidor
    (falso / "web" / "jeremialcala" / "hive" / "releases" / "download" / NUEVA / "digest-hive-gateway.txt"
     ).write_text(contenido, encoding="utf-8")
    codigo, salida, llamadas = desplegar(servidor, NUEVA)

    assert codigo != 0
    assert "no tiene la forma esperada" in salida
    assert not [linea for linea in llamadas if linea.startswith(("pull ", "run ", "tag ", "compose "))]


@en_bash
def test_una_release_sin_digests_no_cambia_nada(servidor):
    codigo, salida, llamadas = desplegar(servidor, "v0.99.0")

    assert codigo != 0
    assert "no tiene digest-hive-gateway.txt" in salida
    assert "No se ha cambiado nada" in salida
    assert not [linea for linea in llamadas if linea.startswith(("tag ", "compose "))]


@en_bash
@pytest.mark.parametrize("argumentos", [(), ("0.21.0",), ("v0.21",), ("v0.21.0;id",), (NUEVA, "--rapido")])
def test_sin_una_version_valida_no_hace_nada(servidor, argumentos):
    codigo, salida, llamadas = desplegar(servidor, *argumentos)

    assert codigo != 0
    assert llamadas == []


@en_bash
def test_solo_verificar_no_cambia_nada(servidor):
    falso, apps, copias = servidor
    codigo, salida, llamadas = desplegar(servidor, NUEVA, "--solo-verificar")

    assert codigo == 0, salida
    assert len([linea for linea in llamadas if linea.startswith("run ")]) == 4
    assert not [linea for linea in llamadas if linea.startswith(("tag ", "compose "))]
    assert not list(copias.iterdir())
    assert "No se ha cambiado nada" in salida


@en_bash
def test_volver_a_una_version_sin_firma_con_local(servidor):
    falso, apps, copias = servidor
    for img in IMAGENES:
        (falso / "imagenes" / f"{img}-v0.19.0").touch()
    composes = falso / "web" / "jeremialcala" / "hive" / "v0.19.0"
    for proyecto, ruta in COMPOSES.items():
        (composes / ruta).parent.mkdir(parents=True, exist_ok=True)
        (composes / ruta).write_text(f"# {proyecto} v0.19.0\n", encoding="utf-8")
    codigo, salida, llamadas = desplegar(servidor, "v0.19.0", "--local")

    assert codigo == 0, salida
    # Sin firma que verificar: ni digest, ni pull, ni cosign; los composes sí, de su etiqueta.
    assert not [linea for linea in llamadas if linea.startswith(("pull ", "run ", "tag "))]
    assert not [linea for linea in llamadas if "releases/download" in linea]
    assert que_levanta(llamadas) == ["hive-gateway", "hive-engine", "hive-portal"]
    assert (apps / "hive-portal" / "docker-compose.yml").read_text(encoding="utf-8") == "# hive-portal v0.19.0\n"
    assert (apps / "hive-engine" / ".env").read_text(encoding="utf-8").endswith("=v0.19.0\n")


@en_bash
def test_local_sin_las_imagenes_en_el_servidor_no_cambia_nada(servidor):
    codigo, salida, llamadas = desplegar(servidor, "v0.18.0", "--local")

    assert codigo != 0
    assert "no está en el servidor: ghcr.io/jeremialcala/hive-gateway:v0.18.0" in salida
    assert not [linea for linea in llamadas if linea.startswith(("tag ", "compose "))]


@en_bash
def test_si_la_sonda_falla_vuelve_solo_a_lo_que_corria(servidor):
    falso, apps, copias = servidor
    (falso / "salud" / f"hive-coordinator-{NUEVA}").write_text("unhealthy", encoding="utf-8")
    codigo, salida, llamadas = desplegar(servidor, NUEVA)

    # Falla, para que se note, pero deja corriendo lo de antes, con sus composes y su .env.
    assert codigo != 0
    assert "hive-coordinator(unhealthy)" in salida
    assert "Ha vuelto a v0.20.0, que sí lo está" in salida
    assert que_levanta(llamadas) == ["hive-gateway", "hive-engine", "hive-portal"] * 2
    for proyecto in COMPOSES:
        assert (falso / f"version-{proyecto}").read_text(encoding="utf-8").strip() == ANTES
        assert (apps / proyecto / "docker-compose.yml").read_text(encoding="utf-8") == f"# {proyecto} {ANTES}\n"
    assert (apps / "hive-engine" / ".env").read_text(encoding="utf-8") == f"OTRA=1\nENGINE_IMAGE_TAG={ANTES}\n"
    # Sin descargar nada para volver.
    vuelta = llamadas[[i for i, linea in enumerate(llamadas) if linea.startswith("compose ")][3]:]
    assert not [linea for linea in vuelta if linea.startswith(("curl ", "pull ", "run ", "tag "))]


@en_bash
def test_si_la_vuelta_tampoco_queda_healthy_lo_dice(servidor):
    falso, _, _ = servidor
    (falso / "salud" / "hive-coordinator").write_text("unhealthy", encoding="utf-8")
    codigo, salida, _ = desplegar(servidor, NUEVA)

    assert codigo != 0
    assert "la vuelta a v0.20.0 tampoco: hive-coordinator(unhealthy). A mano, con el runbook" in salida


@en_bash
def test_un_contenedor_con_otra_imagen_no_cuenta_como_desplegado(servidor):
    falso, _, _ = servidor
    (falso / "otra-imagen").touch()
    codigo, salida, _ = desplegar(servidor, NUEVA)

    assert codigo != 0
    assert "v0.21.0 no quedó healthy: hive-webhook(otra imagen)" in salida


@en_bash
def test_sin_las_imagenes_de_lo_que_corria_no_vuelve_solo_y_dice_como(servidor):
    falso, _, _ = servidor
    for proyecto in COMPOSES:
        (falso / f"version-{proyecto}").write_text("v0.19.0", encoding="utf-8")
    (falso / "salud" / f"hive-api-{NUEVA}").write_text("starting", encoding="utf-8")
    codigo, salida, llamadas = desplegar(servidor, NUEVA)

    assert codigo != 0
    assert "No vuelve solo: no está en el servidor ghcr.io/jeremialcala/hive-" in salida
    assert que_levanta(llamadas) == ["hive-gateway", "hive-engine", "hive-portal"]
    # La anterior a la primera firmada se vuelve a levantar con --local.
    assert "ssh nidavellir 'sudo bash -s -- v0.19.0 --local' < deploy/desplegar.sh" in salida


def test_la_identidad_es_la_del_workflow_release_de_adr_0036():
    guion = SCRIPT.read_text(encoding="utf-8")
    patron = re.search(r"--certificate-identity-regexp '([^']+)'", ADR.read_text(encoding="utf-8")).group(1)
    identidad = re.search(r"^IDENTIDAD=(\S+)$", guion, re.MULTILINE).group(1).replace("$REPO", "jeremialcala/hive")
    emisor = re.search(r"^EMISOR=(\S+)$", guion, re.MULTILINE).group(1)

    assert re.fullmatch(patron, f"{identidad}/v1.2.3")
    assert "--certificate-oidc-issuer https://token.actions.githubusercontent.com" in ADR.read_text(encoding="utf-8")
    assert emisor == "https://token.actions.githubusercontent.com"
    # El workflow verifica su propia firma con la misma identidad.
    assert "/.github/workflows/release.yml@${{ github.ref }}" in RELEASE.read_text(encoding="utf-8")


def test_cosign_va_fijado_por_digest_y_en_la_version_del_workflow():
    guion = SCRIPT.read_text(encoding="utf-8")
    cosign = re.search(r"^COSIGN=(\S+)$", guion, re.MULTILINE).group(1)
    version = re.search(r"cosign-release: (v[0-9.]+)", RELEASE.read_text(encoding="utf-8")).group(1)

    assert re.fullmatch(r"ghcr\.io/sigstore/cosign/cosign:v[0-9.]+@sha256:[0-9a-f]{64}", cosign)
    assert cosign.split("@")[0].endswith(f":{version}")


def test_los_contenedores_que_espera_son_los_que_vigila_thor():
    guion = SCRIPT.read_text(encoding="utf-8")
    esperados = re.search(r'^CONTENEDORES="([^"]+)"$', guion, re.MULTILINE).group(1).split()

    assert sorted(esperados) == sorted(CONTENEDORES)
    runbook = (RAIZ / "docs" / "05-deployment" / "arranque-tras-apagon.md").read_text(encoding="utf-8")
    for nombre in esperados:
        assert f"`{nombre}`" in runbook
