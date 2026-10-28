# Zitadel en Nidavellir

* **Estado:** draft
* **Fecha:** 2026-10-28
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 05-deployment
* **Versión:** 0.1.0
* **ADRs:** [ADR-0029](../../docs/00-project/adr/0029-autenticacion-de-personas.md), con su enmienda del 2026-10-28
* **Tareas:** HIG-91 y HIG-96

El IdP de las personas de un cliente. Corre en Nidavellir (`hgtech002`, 192.168.10.2) y se despliega a mano desde
`develop`, como el gateway en midgard. En `https://hive-auth.higerotech.com`.

| Contenedor | Qué es | Red |
|---|---|---|
| `hive-idp-db` | PostgreSQL 17.11. Zitadel entra con su rol `zitadel`, que no es superusuario | `datos` (interna) |
| `hive-idp` | La API de Zitadel v4.19.4: OIDC, consola y APIs de administración | `datos`, `publico` |
| `hive-idp-login` | La interfaz de inicio de sesión de Zitadel v4, en `/ui/v2/login` | `publico` |
| `hive-idp-proxy` | Caddy: reparte entre el login y la API (h2c). Es lo único que alcanza el túnel | `publico` |
| `hive-idp-tunel` | `cloudflared`, con el túnel propio de Zitadel | `publico` |

Nada secreto vive en el repositorio. El `.env` lo escribe `generar-secretos.sh` en la máquina, con root y permisos
0600, y no lo imprime nunca.

## Condiciones de la enmienda

1. **Estabilidad.** Ninguna persona de verdad entra hasta que Nidavellir lleve 14 días seguidos sin colgarse, contados
   desde su último arranque. La cuenta empezó el 2026-10-25 a las 14:40 UTC.
2. **Vigilancia y respaldos.** Son lo que se describe abajo. El componente no se da por bueno sin una restauración de
   prueba que haya salido bien.

## Puesta en marcha

### Lo que hace Jeremi

1. **El túnel.** En Cloudflare Zero Trust → Networks → Tunnels, crear un túnel nuevo, `hive-idp`, con el nombre
   público `hive-auth.higerotech.com` → `http://hive-idp-proxy:8080`. Copiar su token, sin pegarlo en ningún sitio.
2. **La clave de los respaldos,** en tu máquina, no en Nidavellir:
   ```
   age-keygen -o hive-idp-respaldo.key
   ```
   La privada (`hive-idp-respaldo.key`) va a tu gestor de contraseñas: es la única forma de abrir los respaldos. La
   pública, la línea `age1…` que imprime, va a `/etc/hive-idp/respaldo.age.pub` en Nidavellir. Es pública y se puede
   compartir.
3. **El aviso por ntfy.** Copiar `/etc/hive-aviso.env` de midgard a Nidavellir, sin que pase por la pantalla:
   ```
   ssh -i ~/.ssh/id_ed25519_hgtech001 jalcala@192.168.10.1 'sudo cat /etc/hive-aviso.env' | ssh -i ~/.ssh/id_ed25519_hgtech001 jalcala@192.168.10.2 'sudo install -m 600 /dev/stdin /etc/hive-aviso.env'
   ```

### En Nidavellir

1. **El código**, desde tu máquina:
   ```
   git -c core.autocrlf=false archive <sha> deploy/idp deploy/vigia | ssh -i ~/.ssh/id_ed25519_hgtech001 jalcala@192.168.10.2 'rm -rf ~/hive-idp-build && mkdir ~/hive-idp-build && tar -x -C ~/hive-idp-build'
   sudo install -d -m 750 /srv/apps/hive-idp
   sudo cp -r ~/hive-idp-build/deploy/idp/. /srv/apps/hive-idp/
   ```
2. **Los secretos.** El token del túnel entra por la entrada estándar:
   ```
   sudo /srv/apps/hive-idp/generar-secretos.sh < token-del-tunel
   ```
   Escribe la clave maestra (32 caracteres), las contraseñas de PostgreSQL, el secreto de la cookie del login, la
   contraseña inicial de `zitadel-admin` y el token. **La clave maestra no se puede perder:** cifra lo que Zitadel
   guarda.
3. **Arrancar:** `cd /srv/apps/hive-idp && sudo docker compose up -d`. Los cinco contenedores tienen que quedar
   `healthy`, y `https://hive-auth.higerotech.com/debug/healthz` tiene que responder 200 desde fuera.
4. **El vigía:**
   ```
   sudo install -m 755 ~/hive-idp-build/deploy/vigia/hive-vigia.sh /usr/local/sbin/hive-vigia.sh
   sudo install -m 644 /srv/apps/hive-idp/vigia/hive-idp-vigia.{service,timer} /etc/systemd/system/
   sudo systemctl daemon-reload && sudo systemctl enable --now hive-idp-vigia.timer
   sudo VIGIA_CONTENEDORES=hive-idp /usr/local/sbin/hive-vigia.sh prueba
   ```
5. **El respaldo:**
   ```
   sudo apt-get install -y age
   sudo install -m 755 /srv/apps/hive-idp/respaldo/hive-idp-respaldo.sh /usr/local/sbin/
   sudo install -m 755 /srv/apps/hive-idp/respaldo/hive-idp-restauracion-prueba.sh /usr/local/sbin/
   sudo install -m 644 /srv/apps/hive-idp/respaldo/hive-idp-respaldo.{service,timer} /etc/systemd/system/
   sudo systemctl daemon-reload && sudo systemctl enable --now hive-idp-respaldo.timer
   sudo systemctl start hive-idp-respaldo.service && cat /var/lib/yggdrasil-thor/hive_idp_respaldo.prom
   ```
6. **La restauración de prueba,** desde tu máquina y con la clave privada por la entrada estándar:
   ```
   ssh -i ~/.ssh/id_ed25519_hgtech001 jalcala@192.168.10.2 'sudo /usr/local/sbin/hive-idp-restauracion-prueba.sh' < hive-idp-respaldo.key
   ```
   Restaura el último respaldo en un PostgreSQL temporal, sin red, y cuenta sus eventos y usuarios. Se repite cada mes.

### Zitadel, en la consola

Con `zitadel-admin`, que pide cambiar la contraseña al entrar:

- **Correo:** SMTP de Resend. Servidor `smtp.resend.com`, puerto `465` (TLS), usuario `resend`, la contraseña es el
  token de Resend y el remitente `no-reply@higerotech.com`, con el nombre «Hive». Hay que probar un correo.
- **Política de inicio de sesión:** MFA obligatoria (passkey o TOTP), sin registro libre, sin proveedores externos.
- **Proyecto `Hive`,** con la aplicación `hive-portal`: OIDC, Web, confidencial, Authorization Code con PKCE y sus
  URL de retorno en `https://hive-app.higerotech.com`.
- **Una cuenta de servicio** para que `hive-api` cree y enlace personas (HIG-94). La clave se guarda en Andvari.

## Vigilancia

| Qué | Quién | Dónde |
|---|---|---|
| Un contenedor `unhealthy` | `hive-idp-vigia`: lo reinicia y avisa por ntfy | Nidavellir, cada minuto |
| `https://hive-auth.higerotech.com/debug/healthz` no responde | Yggdrasil: blackbox y Gjallarhorn | Pendiente en el repo `yggdrasil` |
| Nidavellir no responde (un cuelgue duro) | Yggdrasil: la máquina deja de exponer métricas | Pendiente en el repo `yggdrasil` |
| El respaldo falló, o lleva más de 26 horas sin hacerse | El propio script avisa por ntfy; Yggdrasil, con `hive_idp_respaldo_ultimo_exito_segundos` | Las métricas, en `/var/lib/yggdrasil-thor`, salen por Thor (:9471) |

Las reglas de Yggdrasil son de su repositorio: van en un PR propio.

## Respaldos

- **Cada noche a las 03:30 UTC,** `pg_dump --format=custom`, cifrado con `age` contra la clave pública **antes de salir
  de la máquina**, en `/mnt/nas/respaldos/hive-idp/hive-idp-<fecha>.dump.age`. El NAS exporta por NFSv3 sin
  autenticación y el volcado lleva los hashes de las credenciales.
- **Se escribe primero como `.parcial`** y solo se renombra si pasa de 4 KiB. Un volcado vacío no es un respaldo.
- **Se guardan 30 días** (decisión de Jeremi del 2026-10-28). Nunca se borra el último que salió bien.
- **Métricas:**
  - `hive_idp_respaldo_resultado`: 1 o 0;
  - `hive_idp_respaldo_ultimo_exito_segundos`;
  - `hive_idp_respaldo_bytes`;
  - `hive_idp_respaldo_ficheros`.

## Actualizar

Cambiar las imágenes en el compose: por digest, con más de dos semanas y leyendo las notas de la versión. Después,
`docker compose up -d`. **Antes de cada actualización de Zitadel, un respaldo a mano**
(`sudo systemctl start hive-idp-respaldo.service`), porque las migraciones de Zitadel no tienen vuelta atrás.
