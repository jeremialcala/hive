#!/usr/bin/env bash
# hive-idp-restauracion-prueba.sh: comprueba que el último respaldo de Zitadel se puede restaurar (HIG-96).
#
#   ssh -i ~/.ssh/id_ed25519_hgtech001 jalcala@192.168.10.2 'sudo /usr/local/sbin/hive-idp-restauracion-prueba.sh' < hive-idp-respaldo.key
#
# La clave privada de age llega por la entrada estándar, desde la máquina de Jeremi: nunca se escribe en el disco de
# Nidavellir, solo en memoria (/dev/shm, 0600) mientras dura la prueba. El respaldo se restaura en un PostgreSQL
# temporal, con la misma imagen y sin red, y se cuentan los eventos de Zitadel. No toca la base de verdad. Al acabar
# lo borra todo, salga bien o mal.
set -euo pipefail
umask 077

DESTINO=${RESPALDO_DESTINO:-/mnt/nas/respaldos/hive-idp}
IMAGEN=${RESPALDO_IMAGEN:-$(docker inspect -f '{{.Config.Image}}' hive-idp-db)}
PRUEBA=hive-idp-restauracion-prueba

clave=$(mktemp /dev/shm/hive-idp-clave.XXXXXX)
volcado=$(mktemp /dev/shm/hive-idp-volcado.XXXXXX)
limpiar() {
    rm -f "$clave" "$volcado"
    docker rm -f "$PRUEBA" >/dev/null 2>&1 || true
}
trap limpiar EXIT

cat >"$clave"
grep -q '^AGE-SECRET-KEY-' "$clave" || { echo "restauración: no llegó una clave de age por la entrada estándar" >&2; exit 1; }

ultimo=$(find "$DESTINO" -maxdepth 1 -name 'hive-idp-*.dump.age' -printf '%T@ %p\n' | sort -n | tail -1 | cut -d' ' -f2-)
[ -n "$ultimo" ] || { echo "restauración: no hay respaldos en $DESTINO" >&2; exit 1; }
echo "restauración: probando $(basename "$ultimo")"

age -d -i "$clave" "$ultimo" >"$volcado"

docker run -d --name "$PRUEBA" --network none -e POSTGRES_PASSWORD=prueba "$IMAGEN" >/dev/null
for _ in $(seq 1 30); do
    docker exec "$PRUEBA" pg_isready -U postgres >/dev/null 2>&1 && break
    sleep 2
done
docker exec "$PRUEBA" createdb -U postgres zitadel
docker exec "$PRUEBA" psql -U postgres -c 'CREATE ROLE zitadel' >/dev/null
docker exec -i "$PRUEBA" pg_restore -U postgres --dbname zitadel --no-owner --exit-on-error <"$volcado"

eventos=$(docker exec "$PRUEBA" psql -U postgres -d zitadel -tAc 'SELECT count(*) FROM eventstore.events2')
usuarios=$(docker exec "$PRUEBA" psql -U postgres -d zitadel -tAc \
    "SELECT count(DISTINCT aggregate_id) FROM eventstore.events2 WHERE aggregate_type = 'user'")
echo "restauración: bien. $eventos eventos y $usuarios usuarios en el respaldo."
[ "$eventos" -gt 0 ] || { echo "restauración: el respaldo no tiene eventos" >&2; exit 1; }
