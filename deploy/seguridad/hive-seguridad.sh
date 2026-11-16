#!/usr/bin/env bash
# hive-seguridad.sh: cuenta en los logs de los contenedores de Hive los eventos de seguridad, y deja los contadores
# en un fichero que Thor sirve a Prometheus (HIG-140, Gate 5). Lo lanza hive-seguridad.timer cada minuto, en el
# segundo 5 (el vigía de Hive corre en el 30, Thor en el 40 y el de morocota en el 50).
#
# Por qué de los logs y no con métricas en la aplicación: los eventos ya se escriben, sin datos personales, y así no
# hay que abrir un puerto por contenedor hacia midgard ni contar en los dos procesos del webhook (decisión de Jeremi
# del 2026-11-16). El precio es que el contador depende del texto de los logs. Por eso
# tests/unit/test_hive_seguridad.py fija cada expresión contra el código: si cambia el texto de un log, la prueba
# falla antes de que el contador deje de verlo.
#
# Cada pasada lee de cada contenedor lo que escribió desde el final de la anterior hasta ahora (`docker logs --since
# --until`), suma lo que casa con EVENTOS a los contadores de ESTADO y reescribe METRICAS de una vez. Los contadores
# solo crecen. Si se pierde el estado, vuelven a cero, y Prometheus lo trata como un reinicio del contador. Si un
# contenedor no se puede leer, hive_seguridad_contenedor_leido queda en 0 y lo que escribió en esa ventana no se
# cuenta.
#
# No copia el texto de ninguna línea, ni al fichero ni al journal: solo cuántas casan.
set -euo pipefail

METRICAS=${SEGURIDAD_METRICAS:-/var/lib/yggdrasil-thor/hive_seguridad.prom}
ESTADO=${SEGURIDAD_ESTADO:-/var/lib/hive-seguridad}
DOCKER=${SEGURIDAD_DOCKER:-docker}

GATEWAY="hive-api hive-webhook hive-ingest"
MOTOR="hive-classifier hive-coordinator hive-responder hive-evaluator hive-notifier"

# evento;contenedores;expresión de grep -E. Un evento puede tener varias filas, con contenedores distintos.
EVENTOS="token_rechazado;hive-api;Rejected (a request|an exchange) for an access token
token_frenado;hive-api;Turned away (a request|an exchange) for an access token
ruta_denegada;hive-api;was denied (a route|an operator endpoint|sending a text)
firma_webhook;hive-webhook;Rejected webhook for chatbot
verificacion_webhook;hive-webhook;Rejected webhook verification for chatbot
bot_err;$GATEWAY;Chatbot [^ ]+ of client [^ ]+ is now ERR
andvari;$GATEWAY;Key service unavailable
bifrost;$GATEWAY;Event bus: |The bus did not take
bifrost;$MOTOR;Esperando a Bifröst|no se pudo publicar
mongodb;$GATEWAY;The database is unavailable"

main() {
    local ahora hasta desde
    ahora=${SEGURIDAD_AHORA:-$(date +%s)}
    # Dos segundos de margen: Docker fecha cada línea al recibirla, y una escrita justo ahora puede llegar tarde.
    hasta=$((ahora - 2))

    declare -A cuenta=() leido=()
    mkdir -p "$ESTADO"
    if [ -r "$ESTADO/contadores" ]; then
        local clave valor
        while read -r clave valor; do
            [ -n "$clave" ] && cuenta[$clave]=$valor
        done <"$ESTADO/contadores"
    fi
    # La primera pasada mira el último minuto. Si el reloj volvió atrás, la ventana queda vacía.
    desde=${cuenta[@hasta]:-$((hasta - 60))}
    [ "$desde" -gt "$hasta" ] && desde=$hasta

    # Global: el trap corre cuando main ya ha vuelto.
    TMP=$(mktemp -d)
    trap 'rm -rf "$TMP"' EXIT

    local claves=() contenedor evento grupo patron n
    for contenedor in $GATEWAY $MOTOR; do
        if "$DOCKER" logs --since "$desde" --until "$hasta" "$contenedor" >"$TMP/log" 2>&1 </dev/null; then
            leido[$contenedor]=1
        else
            leido[$contenedor]=0
        fi
        while IFS=';' read -r evento grupo patron; do
            [[ " $grupo " == *" $contenedor "* ]] || continue
            n=0
            if [ "${leido[$contenedor]}" = 1 ]; then
                n=$(grep -cE -- "$patron" "$TMP/log" || true)
            fi
            [[ " ${claves[*]} " == *" $evento/$contenedor "* ]] || claves+=("$evento/$contenedor")
            cuenta[$evento/$contenedor]=$((${cuenta[$evento/$contenedor]:-0} + n))
        done <<<"$EVENTOS"
    done

    # El estado, y después las métricas: si la pasada muere entre los dos, la siguiente no vuelve a contar lo mismo.
    {
        echo "@hasta $hasta"
        for clave in "${claves[@]}"; do
            echo "$clave ${cuenta[$clave]}"
        done
    } >"$TMP/contadores"
    mv "$TMP/contadores" "$ESTADO/contadores"

    mkdir -p "$(dirname "$METRICAS")"
    local salida
    salida=$(mktemp "$METRICAS.XXXXXX")
    {
        echo "# HELP hive_seguridad_eventos_total Líneas de log de un evento de seguridad de Hive, por contenedor."
        echo "# TYPE hive_seguridad_eventos_total counter"
        for clave in "${claves[@]}"; do
            echo "hive_seguridad_eventos_total{evento=\"${clave%%/*}\",contenedor=\"${clave#*/}\"} ${cuenta[$clave]}"
        done
        echo "# HELP hive_seguridad_contenedor_leido 1 si la última pasada pudo leer el log del contenedor, 0 si no."
        echo "# TYPE hive_seguridad_contenedor_leido gauge"
        for contenedor in $GATEWAY $MOTOR; do
            echo "hive_seguridad_contenedor_leido{contenedor=\"$contenedor\"} ${leido[$contenedor]}"
        done
        echo "# HELP hive_seguridad_ultima_pasada_segundos Hora Unix hasta la que contó la última pasada."
        echo "# TYPE hive_seguridad_ultima_pasada_segundos gauge"
        echo "hive_seguridad_ultima_pasada_segundos $hasta"
    } >"$salida"
    chmod 644 "$salida"
    mv "$salida" "$METRICAS"
}

main "$@"
