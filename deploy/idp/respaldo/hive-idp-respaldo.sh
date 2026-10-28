#!/usr/bin/env bash
# hive-idp-respaldo.sh: el respaldo nocturno del PostgreSQL de Zitadel en el NAS (ADR-0029, enmienda; HIG-96).
#
# El volcado lleva los hashes de las credenciales y los datos cifrados con la clave maestra de Zitadel, y el NAS
# exporta por NFSv3 sin autenticación: se cifra con age ANTES de salir de la máquina, contra una clave pública. La
# privada no vive ni aquí ni en el NAS; la guarda Jeremi, y solo la pide la restauración.
#
# Cada pasada:
#   1. vuelca la base con pg_dump (formato custom, ya comprimido) y la cifra en un fichero .parcial;
#   2. si salió de menos de RESPALDO_MIN_BYTES, lo da por fallido: un volcado vacío no es un respaldo;
#   3. lo renombra al nombre definitivo, solo entonces;
#   4. borra los respaldos de más de RESPALDO_DIAS días, pero nunca el último que salió bien;
#   5. deja sus métricas en el directorio de Thor, que Mimir lee de Nidavellir, y avisa por ntfy si falló.
#
#   hive-idp-respaldo.sh          una pasada
set -euo pipefail
umask 077

DESTINO=${RESPALDO_DESTINO:-/mnt/nas/respaldos/hive-idp}
DESTINATARIO=${RESPALDO_DESTINATARIO:-/etc/hive-idp/respaldo.age.pub}
DIAS=${RESPALDO_DIAS:-30}
MIN_BYTES=${RESPALDO_MIN_BYTES:-4096}
METRICAS=${RESPALDO_METRICAS:-/var/lib/yggdrasil-thor/hive_idp_respaldo.prom}
CONTENEDOR=${RESPALDO_CONTENEDOR:-hive-idp-db}
DOCKER=${RESPALDO_DOCKER:-docker}
AGE=${RESPALDO_AGE:-age}
AVISO_ENV=${AVISO_ENV:-/etc/hive-aviso.env}
AHORA=${RESPALDO_AHORA:-$(date +%s)}
SELLO=$(date -u -d "@$AHORA" +%Y%m%dT%H%M%SZ)
HOST=$(hostname -s)

AVISO_URL=
if [ -r "$AVISO_ENV" ]; then
    # shellcheck disable=SC1090
    . "$AVISO_ENV"
fi

# El último respaldo bueno se recuerda entre pasadas: una que falla no lo borra de las métricas.
ultimo_exito=0
if [ -r "$METRICAS" ]; then
    ultimo_exito=$(awk '$1 == "hive_idp_respaldo_ultimo_exito_segundos" { print $2 }' "$METRICAS")
    ultimo_exito=${ultimo_exito:-0}
fi

metricas() {   # metricas <resultado 1|0> <bytes>
    local tmp ficheros
    # Sin el NAS, find falla: las métricas se escriben igual, con 0 ficheros.
    ficheros=$(find "$DESTINO" -maxdepth 1 -name 'hive-idp-*.dump.age' 2>/dev/null | wc -l || true)
    mkdir -p "$(dirname "$METRICAS")" || return 0
    tmp=$(mktemp "$METRICAS.XXXXXX")
    {
        echo "# HELP hive_idp_respaldo_resultado 1 si la última pasada del respaldo de Zitadel salió bien, 0 si no."
        echo "# TYPE hive_idp_respaldo_resultado gauge"
        echo "hive_idp_respaldo_resultado $1"
        echo "# HELP hive_idp_respaldo_ultimo_exito_segundos Hora Unix del último respaldo de Zitadel que salió bien."
        echo "# TYPE hive_idp_respaldo_ultimo_exito_segundos gauge"
        echo "hive_idp_respaldo_ultimo_exito_segundos $ultimo_exito"
        echo "# HELP hive_idp_respaldo_bytes Tamaño del último respaldo de Zitadel que salió bien."
        echo "# TYPE hive_idp_respaldo_bytes gauge"
        echo "hive_idp_respaldo_bytes $2"
        echo "# HELP hive_idp_respaldo_ficheros Respaldos de Zitadel que hay en el NAS."
        echo "# TYPE hive_idp_respaldo_ficheros gauge"
        echo "hive_idp_respaldo_ficheros $ficheros"
    } >"$tmp"
    chmod 644 "$tmp"
    mv "$tmp" "$METRICAS"
}

avisar() {   # avisar <mensaje>
    [ -n "${AVISO_URL:-}" ] || return 0
    curl -fsS -m 20 --retry 2 --retry-delay 5 \
        -H "Title: $HOST - respaldo de Zitadel" -H "Priority: high" -H "Tags: floppy_disk,warning" \
        -d "$1" "$AVISO_URL" >/dev/null || echo "respaldo: el aviso no salió"
}

parcial=
fallo() {   # fallo <motivo>
    # Sin la trampa: un error escribiendo las métricas o el aviso no vuelve a entrar aquí.
    trap - ERR
    set +e
    [ -n "$parcial" ] && rm -f "$parcial"
    echo "respaldo: FALLÓ: $1" >&2
    metricas 0 0
    avisar "El respaldo de Zitadel falló: $1"
    exit 1
}
trap 'fallo "terminó con un error en la línea $LINENO"' ERR

[ -r "$DESTINATARIO" ] || fallo "no puedo leer la clave pública $DESTINATARIO"
mkdir -p "$DESTINO" || fallo "no puedo crear $DESTINO: ¿está montado el NAS?"

final="$DESTINO/hive-idp-$SELLO.dump.age"
parcial="$final.parcial"
if ! "$DOCKER" exec "$CONTENEDOR" pg_dump -U postgres --format=custom zitadel | "$AGE" -R "$DESTINATARIO" >"$parcial"; then
    fallo "pg_dump o age no terminaron bien"
fi
bytes=$(wc -c <"$parcial" | tr -d ' ')
[ "$bytes" -ge "$MIN_BYTES" ] || fallo "el respaldo salió de $bytes bytes, menos de $MIN_BYTES"
mv "$parcial" "$final"
parcial=
ultimo_exito=$AHORA

# Lo de más de DIAS días se borra, salvo el respaldo que se acaba de hacer. Y los .parcial que quedaran de una pasada
# que se cortó.
find "$DESTINO" -maxdepth 1 -name 'hive-idp-*.dump.age' ! -path "$final" -mmin +$((DIAS * 1440)) -delete
find "$DESTINO" -maxdepth 1 -name 'hive-idp-*.parcial' -mmin +1440 -delete

metricas 1 "$bytes"
echo "respaldo: $final ($bytes bytes)"
