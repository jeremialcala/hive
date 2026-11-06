#!/usr/bin/env bash
# desplegar.sh: despliega una release de Hive en el servidor con las imágenes que construyó, escaneó y firmó el CI
# (HIG-20, ADR-0036). También sirve para volver atrás: volver es desplegar la versión anterior.
#
# Por qué: hasta la 0.20.0 las imágenes se construían en el servidor, y la que corría no era la que el CI había
# escaneado. Ahora el workflow `release` las publica en ghcr, firmadas con cosign sin claves. Este script solo levanta
# una imagen si su firma dice que la produjo ese workflow, de este repositorio y con esa misma etiqueta.
#
# Se lanza desde el portátil, sin copiar nada al servidor:
#
#   ssh nidavellir 'sudo bash -s -- vX.Y.Z' < deploy/desplegar.sh                  la release, desde ghcr y verificada
#   ssh nidavellir 'sudo bash -s -- vX.Y.Z --solo-verificar' < deploy/desplegar.sh descarga y verifica; no cambia nada
#   ssh nidavellir 'sudo bash -s -- vX.Y.Z --local' < deploy/desplegar.sh          con las imágenes que ya están en el
#                                                                                   servidor, sin firma: las anteriores
#                                                                                   a la 0.20.0
#
# Qué hace, en este orden. Todo lo que puede fallar va antes de cambiar nada:
#   1. Lee qué versión corre ahora (la de hive-api) y dice cómo volver a ella.
#   2. Por cada imagen: descarga su digest de la release, la descarga por ese digest y verifica su firma. Si una
#      falla, para sin haber tocado nada.
#   3. Descarga los tres composes de la etiqueta.
#   4. Etiqueta las imágenes como vX.Y.Z e instala los composes que cambiaron, con copia del anterior en /root.
#   5. Levanta el gateway y el motor seguidos (desde DPoP, un motor viejo frente a un gateway nuevo recibe 401), y
#      después el portal.
#   6. Espera a que los diez contenedores estén healthy, con la imagen verificada. Si no, vuelve solo a la versión
#      que corría, con las copias que acaba de guardar y sus imágenes, que siguen en el servidor.
#
# No toca Bifröst, ni el túnel (`hive-tunel` va en el perfil `tunel`), ni los .env salvo ENGINE_IMAGE_TAG, y no imprime
# ningún .env. El runbook es docs/05-deployment/runbook-despliegue-y-rollback.md.
set -euo pipefail

REPO=jeremialcala/hive
REGISTRO=ghcr.io/jeremialcala
# La firma solo vale si la hizo el workflow release de este repositorio con la etiqueta que se despliega: más
# estricto que la expresión de ADR-0036, que acepta cualquier etiqueta vX.Y.Z.
EMISOR=https://token.actions.githubusercontent.com
IDENTIDAD=https://github.com/$REPO/.github/workflows/release.yml@refs/tags
COSIGN=ghcr.io/sigstore/cosign/cosign:v3.1.4@sha256:0f06154836d2de34f887a5c273506d52ca43edaa1d9e53e6510fa78a48f69c2d
PRIMERA_FIRMADA=v0.20.0
IMAGENES="gateway engine portal docs"
CONTENEDORES="hive-webhook hive-api hive-ingest hive-docs hive-classifier hive-coordinator hive-responder hive-evaluator hive-notifier hive-portal"

DOCKER=${DESPLIEGUE_DOCKER:-docker}
CURL=${DESPLIEGUE_CURL:-curl}
APPS=${DESPLIEGUE_APPS:-/srv/apps}
COPIAS=${DESPLIEGUE_COPIAS:-/root}
CERROJO=${DESPLIEGUE_CERROJO:-/run/lock/hive-desplegar.lock}
ESPERA_S=${DESPLIEGUE_ESPERA_S:-300}
PAUSA_S=${DESPLIEGUE_PAUSA_S:-5}

# Globales, y no locales de main: el trap de salida las lee también cuando main ya ha vuelto.
TMP=""
CAMBIADO=0
ANTERIOR=desconocida
VUELTA=0
PENDIENTES=""

# El script llega por la entrada estándar (`bash -s`): un comando que la leyera se comería el resto del script. Por
# eso todo va en main, que bash ha leído entero antes de ejecutarlo, y docker no recibe la entrada.
dk() { "$DOCKER" "$@" </dev/null; }
bajar() { "$CURL" -fsSL --retry 3 --max-time 60 -o "$1" "$2" </dev/null; }
paso() { echo "· $*"; }
morir() { echo "✗ $*" >&2; exit 1; }

# El compose de cada proyecto: dónde está en el repositorio y dónde en el servidor.
compose_del_repo() {
    case "$1" in
        hive-gateway) echo deploy/docker-compose.yml ;;
        hive-engine) echo engine/deploy/docker-compose.yml ;;
        hive-portal) echo portal/deploy/docker-compose.yml ;;
    esac
}

imagen_de() {
    case "$1" in
        hive-webhook | hive-api | hive-ingest) echo gateway ;;
        hive-docs) echo docs ;;
        hive-portal) echo portal ;;
        *) echo engine ;;
    esac
}

es_version() { [[ "$1" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; }

# Cómo volver a una versión: con --local si es anterior a la primera que el CI firmó.
como_volver() {
    local version=$1 local_=""
    es_version "$version" || { echo "a mano, con el runbook: «$version» no es una versión"; return; }
    if [ "$version" != "$PRIMERA_FIRMADA" ] &&
        [ "$(printf '%s\n' "$version" "$PRIMERA_FIRMADA" | sort -V | head -n1)" = "$version" ]; then
        local_=" --local"
    fi
    echo "ssh nidavellir 'sudo bash -s -- $version$local_' < deploy/desplegar.sh"
}

al_salir() {
    local estado=$?
    [ -n "$TMP" ] && rm -rf "$TMP"
    [ "$estado" = 0 ] && return
    if [ "$VUELTA" = 1 ]; then
        return
    elif [ "$CAMBIADO" = 1 ]; then
        echo "✗ Falló después de empezar a cambiar. Para volver a $ANTERIOR: $(como_volver "$ANTERIOR")" >&2
    else
        echo "✗ No se ha cambiado nada." >&2
    fi
}

main() {
    local version=${1:-} modo=firmada
    shift || true
    for opcion in "$@"; do
        case "$opcion" in
            --local) modo=local ;;
            --solo-verificar) modo=verificar ;;
            *) morir "opción desconocida: $opcion" ;;
        esac
    done
    es_version "$version" || morir "uso: desplegar.sh vX.Y.Z [--local | --solo-verificar]"

    exec 9>"$CERROJO"
    flock -n 9 || morir "ya hay otro despliegue en marcha ($CERROJO)"

    # El trap antes de crear nada temporal.
    trap al_salir EXIT
    TMP=$(mktemp -d)

    # 1. Lo que corre ahora.
    ANTERIOR=$(dk inspect -f '{{.Config.Image}}' hive-api 2>/dev/null || true)
    ANTERIOR=${ANTERIOR##*:}
    ANTERIOR=${ANTERIOR:-desconocida}
    paso "Corre $ANTERIOR. Para volver: $(como_volver "$ANTERIOR")"

    # 2. Las imágenes: por su digest y con la firma comprobada, o las que ya hay en el servidor.
    local img ref linea
    for img in $IMAGENES; do
        if [ "$modo" = local ]; then
            dk image inspect -f '{{.Id}}' "$REGISTRO/hive-$img:$version" >/dev/null 2>&1 ||
                morir "no está en el servidor: $REGISTRO/hive-$img:$version"
            paso "hive-$img $version: la del servidor, sin verificar (--local)"
            continue
        fi
        bajar "$TMP/digest-$img" "https://github.com/$REPO/releases/download/$version/digest-hive-$img.txt" ||
            morir "la release $version no tiene digest-hive-$img.txt"
        # «hive-<imagen> ghcr.io/jeremialcala/hive-<imagen>@sha256:<64 hex>», y nada más.
        linea=$(head -n1 "$TMP/digest-$img")
        ref=${linea#"hive-$img "}
        [ "$ref" != "$linea" ] && [ "${ref%@*}" = "$REGISTRO/hive-$img" ] &&
            [[ "${ref#*@}" =~ ^sha256:[0-9a-f]{64}$ ]] ||
            morir "digest-hive-$img.txt no tiene la forma esperada"
        dk pull -q "$ref" >/dev/null || morir "no se pudo descargar $ref"
        dk run --rm "$COSIGN" verify --certificate-oidc-issuer "$EMISOR" \
            --certificate-identity "$IDENTIDAD/$version" "$ref" >/dev/null 2>"$TMP/cosign-$img" || {
            cat "$TMP/cosign-$img" >&2
            morir "la firma de hive-$img no es la del workflow release en $version"
        }
        echo "$ref" >"$TMP/ref-$img"
        paso "hive-$img ${ref#*@}: firma verificada"
    done
    if [ "$modo" = verificar ]; then
        paso "Las cuatro imágenes de $version están verificadas. No se ha cambiado nada."
        return
    fi

    # 3. Los composes de la etiqueta, también antes de cambiar nada.
    local proyecto
    for proyecto in hive-gateway hive-engine hive-portal; do
        bajar "$TMP/$proyecto.yml" "https://raw.githubusercontent.com/$REPO/$version/$(compose_del_repo "$proyecto")" ||
            morir "no se pudo descargar el compose de $proyecto en $version"
        [ -f "$APPS/$proyecto/docker-compose.yml" ] || morir "no existe $APPS/$proyecto/docker-compose.yml"
    done
    [ "$(grep -c '^ENGINE_IMAGE_TAG=' "$APPS/hive-engine/.env")" = 1 ] ||
        morir "$APPS/hive-engine/.env no tiene una sola línea ENGINE_IMAGE_TAG"

    # Si la sonda falla, se vuelve solo a lo que corría (RF11), con las copias de este despliegue y las imágenes que
    # siguen en el servidor: sin GitHub ni ghcr. No se puede si no se sabe qué corría, si es la misma versión (su
    # etiqueta ya apunta a la nueva) o si faltan sus imágenes.
    local vuelta="" nombre reinicios
    if ! es_version "$ANTERIOR"; then
        vuelta="no se sabe qué versión corría"
    elif [ "$ANTERIOR" = "$version" ]; then
        vuelta="ya corría $version"
    else
        for img in $IMAGENES; do
            dk image inspect -f '{{.Id}}' "$REGISTRO/hive-$img:$ANTERIOR" >/dev/null 2>&1 ||
                vuelta="no está en el servidor $REGISTRO/hive-$img:$ANTERIOR"
        done
    fi
    paso "Si no queda healthy: ${vuelta:-vuelve solo a $ANTERIOR}"

    # 4. Desde aquí se cambia el servidor.
    CAMBIADO=1
    local ts
    ts=$(date +%Y%m%d%H%M%S)
    if [ "$modo" = firmada ]; then
        for img in $IMAGENES; do
            dk tag "$(cat "$TMP/ref-$img")" "$REGISTRO/hive-$img:$version"
        done
    fi
    for proyecto in hive-gateway hive-engine hive-portal; do
        if ! cmp -s "$TMP/$proyecto.yml" "$APPS/$proyecto/docker-compose.yml"; then
            cp -p "$APPS/$proyecto/docker-compose.yml" "$COPIAS/$proyecto-compose.bak-$ts.yml"
            install -m 0644 "$TMP/$proyecto.yml" "$APPS/$proyecto/docker-compose.yml"
            paso "$proyecto: compose nuevo; el anterior en $COPIAS/$proyecto-compose.bak-$ts.yml"
        fi
    done
    cp -p "$APPS/hive-engine/.env" "$COPIAS/hive-engine.env.bak-$ts"
    sed -i "s/^ENGINE_IMAGE_TAG=.*/ENGINE_IMAGE_TAG=$version/" "$APPS/hive-engine/.env"

    # 5 y 6. Levantar y esperar.
    if levantar "$version" && esperar "$version"; then
        for nombre in $CONTENEDORES; do
            reinicios=$(dk inspect -f '{{.RestartCount}}' "$nombre")
            [ "$reinicios" = 0 ] || paso "$nombre lleva $reinicios reinicios: mira su log"
        done
        paso "Hive $version desplegado: los diez contenedores healthy, con su imagen. Antes corría $ANTERIOR."
        return
    fi
    local fallidos=$PENDIENTES
    [ -z "$vuelta" ] || morir "$version no quedó healthy:$fallidos. No vuelve solo: $vuelta"

    paso "$version no quedó healthy:$fallidos. Vuelvo a $ANTERIOR"
    VUELTA=1
    for proyecto in hive-gateway hive-engine hive-portal; do
        if [ -f "$COPIAS/$proyecto-compose.bak-$ts.yml" ]; then
            install -m 0644 "$COPIAS/$proyecto-compose.bak-$ts.yml" "$APPS/$proyecto/docker-compose.yml"
        fi
    done
    cp -p "$COPIAS/hive-engine.env.bak-$ts" "$APPS/hive-engine/.env"
    if levantar "$ANTERIOR" && esperar "$ANTERIOR"; then
        morir "$version no quedó healthy:$fallidos. Ha vuelto a $ANTERIOR, que sí lo está: mira el log de esos contenedores"
    fi
    morir "$version no quedó healthy:$fallidos, y la vuelta a $ANTERIOR tampoco:$PENDIENTES. A mano, con el runbook"
}

# Gateway y motor seguidos; el portal al final. Se llama dentro de un if, donde set -e no actúa: cada paso, con
# su return.
levantar() {
    local version=$1
    env IMAGE_TAG="$version" "$DOCKER" compose --project-directory "$APPS/hive-gateway" up -d </dev/null || return 1
    "$DOCKER" compose --project-directory "$APPS/hive-engine" up -d </dev/null || return 1
    env PORTAL_IMAGE_TAG="$version" "$DOCKER" compose --project-directory "$APPS/hive-portal" up -d </dev/null ||
        return 1
    paso "Levantados el gateway, el motor y el portal en $version"
}

# Los diez healthy y con la imagen de la etiqueta: la verificada, o la del servidor con --local. Lo que falta queda en
# PENDIENTES.
esperar() {
    local version=$1 fin nombre img esperada estado imagen salud id reinicios
    fin=$(($(date +%s) + ESPERA_S))
    while :; do
        PENDIENTES=""
        for nombre in $CONTENEDORES; do
            img=$(imagen_de "$nombre")
            esperada=$(dk image inspect -f '{{.Id}}' "$REGISTRO/hive-$img:$version" 2>/dev/null) || esperada=ninguna
            estado=$(dk inspect -f '{{.Config.Image}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}|{{.Image}}|{{.RestartCount}}' "$nombre" 2>/dev/null) || estado="||"
            IFS='|' read -r imagen salud id reinicios <<<"$estado"
            if [ -z "$salud" ]; then
                PENDIENTES="$PENDIENTES $nombre(no existe)"
            elif [ "$imagen" != "$REGISTRO/hive-$img:$version" ] || [ "$id" != "$esperada" ]; then
                PENDIENTES="$PENDIENTES $nombre(otra imagen)"
            elif [ "$salud" != healthy ]; then
                PENDIENTES="$PENDIENTES $nombre($salud)"
            fi
        done
        [ -z "$PENDIENTES" ] && return 0
        [ "$(date +%s)" -ge "$fin" ] && return 1
        sleep "$PAUSA_S"
    done
}

main "$@"
