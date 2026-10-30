#!/usr/bin/env bash
# generar-secretos.sh: escribe el .env de Zitadel en Nidavellir, con root y una sola vez (ADR-0029).
#
#   sudo ./generar-secretos.sh < token-del-tunel
#
# Los secretos nacen en la máquina, con openssl, y no se imprimen nunca. El único que viene de fuera es el token del
# túnel de Cloudflare, que llega por la entrada estándar y no como argumento, para que no aparezca en `ps`. Si el
# .env ya existe no se toca nada: rotar un secreto es otra operación, que se hace a mano y con cuidado, porque la clave
# maestra de Zitadel cifra lo que guarda y perderla es perder sus datos.
#
# Al final dice qué claves escribió y su longitud, nada más.
set -euo pipefail
umask 077

DIR=${IDP_DIR:-/srv/apps/hive-idp}
ENV_FILE="$DIR/.env"

if [ -e "$ENV_FILE" ]; then
    echo "generar-secretos: $ENV_FILE ya existe; no lo toco" >&2
    exit 1
fi

aleatorio() {   # aleatorio <longitud>: letras y números, sin caracteres que haya que escapar en un DSN
    openssl rand -base64 96 | tr -dc 'A-Za-z0-9' | head -c "$1"
}

TUNNEL_TOKEN=$(tr -d '\r\n' || true)
if [ "${#TUNNEL_TOKEN}" -lt 100 ]; then
    echo "generar-secretos: el token del túnel tiene que llegar por la entrada estándar" >&2
    exit 1
fi

MASTERKEY=$(aleatorio 32)   # Zitadel la exige de 32 caracteres exactos
[ "${#MASTERKEY}" -eq 32 ] || { echo "generar-secretos: la clave maestra no salió de 32" >&2; exit 1; }

tmp=$(mktemp "$DIR/.env.XXXXXX")
trap 'rm -f "$tmp"' EXIT
{
    echo "ZITADEL_MASTERKEY=$MASTERKEY"
    echo "POSTGRES_ADMIN_PASSWORD=$(aleatorio 40)"
    echo "POSTGRES_ZITADEL_PASSWORD=$(aleatorio 40)"
    echo "LOGIN_SESSION_COOKIE_SECRET=$(aleatorio 48)"
    echo "ZITADEL_ADMIN_USERNAME=zitadel-admin"
    echo "ZITADEL_ADMIN_PASSWORD=$(aleatorio 24)Aa1!"
    echo "TUNNEL_TOKEN=$TUNNEL_TOKEN"
} >"$tmp"
chmod 600 "$tmp"
chown root:root "$tmp"
mv "$tmp" "$ENV_FILE"
trap - EXIT

echo "generar-secretos: escrito $ENV_FILE (root, 0600):"
awk -F= '{ print "  " $1 " (" length(substr($0, index($0, "=") + 1)) " caracteres)" }' "$ENV_FILE"
echo "La contraseña inicial de zitadel-admin está en el .env; Zitadel obliga a cambiarla al entrar."
