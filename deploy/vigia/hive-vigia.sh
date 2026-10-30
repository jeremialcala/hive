#!/usr/bin/env bash
# hive-vigia.sh: reinicia los contenedores de Hive que Docker marca unhealthy, y avisa por ntfy. Lo lanza
# hive-vigia.timer cada minuto, en el segundo 30 (el vigía de Yggdrasil corre en el 20, el de Criterio en el 40 y el
# de morocota en el 50, y todos consultan docker).
#
# Por qué: Docker marca unhealthy un contenedor cuyo healthcheck falla, pero no lo reinicia; `restart:
# unless-stopped` solo actúa cuando el proceso termina. El 2026-10-21, la API de gestión pasó seis minutos colgada y
# unhealthy, sin que nada lo levantara, y los mensajes de Hive esperaron detrás.
#
# Por qué en el host y no en un contenedor con el socket de Docker: ese socket es root en midgard. Aquí ya lo es, y
# no hay nada más que proteger.
#
# Contra los bucles: un contenedor que sigue unhealthy después de VIGIA_MAX_REINICIOS reinicios en VIGIA_VENTANA_S
# segundos no se reinicia más; se avisa una vez por ventana, con prioridad urgente, para que lo mire una persona.
# Nada se toca en los primeros VIGIA_GRACIA_S segundos tras arrancar midgard: el stack todavía está convergiendo.
#
# El aviso es secundario: si no se puede leer AVISO_ENV o el push falla, el contenedor se reinicia igual y queda en
# el journal (`journalctl -u hive-vigia`). El tema de ntfy ES la credencial: se lee de AVISO_ENV (en midgard, un
# enlace a /etc/smartd-aviso.env, clave AVISO_URL).
#
#   hive-vigia.sh          una pasada
#   hive-vigia.sh prueba   manda un aviso de prueba
set -euo pipefail

AVISO_ENV=${AVISO_ENV:-/etc/hive-aviso.env}
ESTADO=${VIGIA_ESTADO:-/var/lib/hive-vigia}
CONTENEDORES=${VIGIA_CONTENEDORES:-hive-webhook hive-api hive-ingest hive-docs hive-classifier hive-coordinator hive-responder hive-notifier}
MAX_REINICIOS=${VIGIA_MAX_REINICIOS:-3}
VENTANA_S=${VIGIA_VENTANA_S:-3600}
GRACIA_S=${VIGIA_GRACIA_S:-600}
UPTIME_S=${VIGIA_UPTIME_S:-$(cut -d. -f1 /proc/uptime)}
AHORA=${VIGIA_AHORA:-$(date +%s)}
DOCKER=${VIGIA_DOCKER:-docker}
HOST=$(hostname -s)

AVISO_URL=
if [ -r "$AVISO_ENV" ]; then
    # shellcheck disable=SC1090
    . "$AVISO_ENV"
else
    echo "vigia: no puedo leer $AVISO_ENV, reinicio sin avisar"
fi

enviar() {   # enviar <título> <prioridad> <etiquetas> <mensaje>
    [ -n "${AVISO_URL:-}" ] || return 0
    curl -fsS -m 20 --retry 2 --retry-delay 5 \
        -H "Title: $HOST - $1" -H "Priority: $2" -H "Tags: $3" \
        -d "$4" "$AVISO_URL" >/dev/null || echo "vigia: el aviso no salió"
}

if [ "${1:-}" = prueba ]; then
    [ -n "${AVISO_URL:-}" ] || { echo "vigia: sin AVISO_URL en $AVISO_ENV"; exit 1; }
    enviar "Prueba de aviso" default "white_check_mark" "Canal de avisos del vigía de Hive operativo."
    echo "vigia: prueba enviada"
    exit 0
fi

if [ "$UPTIME_S" -lt "$GRACIA_S" ]; then
    echo "vigia: arranque reciente (${UPTIME_S} s < ${GRACIA_S} s), no se comprueba"
    exit 0
fi
mkdir -p "$ESTADO"

reiniciados=()
rendidos=()
fallidos=()
desde=$((AHORA - VENTANA_S))
for c in $CONTENEDORES; do
    # Solo lo que está en marcha. Docker marca unhealthy también a un contenedor parado, y `docker restart` lo
    # arranca: el 2026-10-28 el vigía deshizo en 23 s un `docker stop hive-idp-proxy` hecho a propósito. Lo que
    # alguien para, se queda parado.
    estado=$($DOCKER inspect -f '{{.State.Status}}{{if .State.Health}} {{.State.Health.Status}}{{end}}' "$c" \
        2>/dev/null || true)
    [ "$estado" = "running unhealthy" ] || continue

    # Los reinicios de la ventana, uno por línea, con su hora. Los de antes se olvidan.
    registro="$ESTADO/$c.reinicios"
    recientes=$(awk -v desde="$desde" '$1 >= desde' "$registro" 2>/dev/null || true)
    if [ -n "$recientes" ]; then printf '%s\n' "$recientes" >"$registro"; else : >"$registro"; fi
    cuantos=$(printf '%s' "$recientes" | grep -c . || true)

    if [ "$cuantos" -ge "$MAX_REINICIOS" ]; then
        echo "vigia: $c sigue unhealthy tras $cuantos reinicios en ${VENTANA_S} s, no lo reinicio más"
        marca="$ESTADO/$c.rendido"
        avisado=$(cat "$marca" 2>/dev/null || echo 0)
        if [ $((AHORA - avisado)) -ge "$VENTANA_S" ]; then
            rendidos+=("$c")
            echo "$AHORA" >"$marca"
        fi
        continue
    fi

    # -t 10: un proceso colgado no atiende el SIGTERM; a los 10 s, Docker lo mata.
    if $DOCKER restart -t 10 "$c" >/dev/null; then
        echo "$AHORA" >>"$registro"
        reiniciados+=("$c")
        echo "vigia: $c estaba unhealthy, reiniciado ($((cuantos + 1)) en ${VENTANA_S} s)"
    else
        fallidos+=("$c")
        echo "vigia: $c estaba unhealthy y no se pudo reiniciar"
    fi
done

if [ ${#reiniciados[@]} -gt 0 ]; then
    enviar "Hive: reiniciado" high "arrows_counterclockwise" \
        "Estaba unhealthy y se reinició: ${reiniciados[*]}. Si vuelve a pasar, mira su log antes del reinicio."
fi
if [ ${#fallidos[@]} -gt 0 ]; then
    enviar "Hive: no se pudo reiniciar" urgent "rotating_light" \
        "Unhealthy y docker restart falló: ${fallidos[*]}."
fi
if [ ${#rendidos[@]} -gt 0 ]; then
    enviar "Hive: sigue caído" urgent "rotating_light" \
        "Sigue unhealthy tras $MAX_REINICIOS reinicios en $((VENTANA_S / 60)) min y no se reinicia más: ${rendidos[*]}."
fi
