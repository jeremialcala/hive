# Zitadel en Nidavellir

* **Estado:** draft
* **Fecha:** 2026-10-28
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 05-deployment
* **Versión:** 0.2.0
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
   público `hive-auth.higerotech.com` → `http://hive-idp-proxy:8080`. Su token va a un fichero en
   `%LOCALAPPDATA%\hive-idp-clave\tunel.token`, sin pegarlo en ningún sitio.
2. **La clave de los respaldos,** en tu máquina, no en Nidavellir, y **fuera del repositorio y de OneDrive**: la
   carpeta del proyecto se sincroniza con la nube. `winget install --id FiloSottile.age` no deja `age` en el `PATH`
   de Git Bash; con la ruta completa:
   ```
   A="$LOCALAPPDATA/Microsoft/WinGet/Packages/FiloSottile.age_Microsoft.Winget.Source_8wekyb3d8bbwe/age"
   mkdir -p "$LOCALAPPDATA/hive-idp-clave"
   "$A/age-keygen.exe" -o "$LOCALAPPDATA/hive-idp-clave/hive-idp-respaldo.key"
   "$A/age-keygen.exe" -y "$LOCALAPPDATA/hive-idp-clave/hive-idp-respaldo.key" | ssh -i ~/.ssh/id_ed25519_hgtech001 jalcala@192.168.10.2 'sudo install -d -m 755 /etc/hive-idp && sudo install -m 644 /dev/stdin /etc/hive-idp/respaldo.age.pub'
   ```
   Sin `-pq`: el `age` de Ubuntu (1.2.1) no entiende las claves postcuánticas. El aviso de `world-readable` que da
   `age-keygen` en Windows no aplica: `icacls` muestra que solo la leen SYSTEM, los administradores y tu usuario.
   La privada va a tu gestor de contraseñas: es la única forma de abrir los respaldos. No borres el fichero hasta la
   primera restauración de prueba.
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
2. **Los secretos,** desde tu máquina. El token del túnel entra por la entrada estándar:
   ```
   ssh -i ~/.ssh/id_ed25519_hgtech001 jalcala@192.168.10.2 'sudo /srv/apps/hive-idp/generar-secretos.sh' < "$LOCALAPPDATA/hive-idp-clave/tunel.token"
   ```
   Escribe la clave maestra (32 caracteres), las contraseñas de PostgreSQL, el secreto de la cookie del login, la
   contraseña inicial de `zitadel-admin` y el token. **La clave maestra no se puede perder:** cifra lo que Zitadel
   guarda.
3. **Arrancar.** El directorio es de root con 0750, así que `cd` no entra sin root: el compose se nombra entero.
   ```
   C="sudo docker compose --project-directory /srv/apps/hive-idp -f /srv/apps/hive-idp/docker-compose.yml"
   $C config -q && $C up -d
   ```
   `config` siempre con `-q`: sin él imprime el compose con los secretos ya sustituidos. Los cinco contenedores tienen
   que quedar `healthy`, y `https://hive-auth.higerotech.com/debug/healthz` tiene que responder 200 desde fuera.
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

- **El nombre de usuario lleva el dominio de la organización:** `zitadel-admin@higerotech.hive-auth.higerotech.com`.
  Con `zitadel-admin` solo, el login responde «User not found in the system». Su correo es de ese dominio, que no
  existe en DNS: hay que cambiarlo por un buzón real, o la cuenta no se puede recuperar.
- **Correo:** SMTP de Resend.
  - Servidor `smtp.resend.com:2465`, con TLS activado (465 también sirve).
  - Usuario `resend`; la contraseña es una clave de Resend propia de Zitadel, con permiso solo de envío y solo para
    `higerotech.com`.
  - Remitente `no-reply@higerotech.com`, con el nombre «Hive».

  Después de guardarlo hay que **activarlo** y mandar un correo de prueba. Nidavellir sale a internet por el túnel
  PIA de midgard. Si la prueba se queda colgada y la consola dice `RpcError(UNKNOWN): Unknown Content-type`, mira la
  MTU antes que la configuración: Cloudflare cortó la petición porque el TLS no terminaba. Se arregló recortando el
  MSS en `pia0` (higerotech/yggdrasil#111).
- **Administradores:** solo `IAM_OWNER`. `IAM_LOGIN_CLIENT` es de la cuenta de máquina `login-client`, la de la
  pantalla de login, y permite crear sesiones de cualquier usuario: nunca va en una persona. Los roles
  `*_IMPERSONATOR` tampoco hacen falta.
- **Política de inicio de sesión, en este orden.** Al activar «MFA obligatoria», la API rechaza cualquier token de
  una sesión sin segundo factor (`mfa required (AUTHZ-Kl3p0)`), también el de la consola que está abierta. Además,
  el login no obliga a registrar el segundo factor: da la sesión por buena y vuelve a la consola, que la rechaza. Si
  ninguna cuenta con permisos tiene MFA, nadie entra (le pasó a Jeremi el 2026-10-28).
  1. Registrar un TOTP en cada cuenta administradora, `zitadel-admin` incluida. Es la cuenta de emergencia, así que
     lleva TOTP y no una passkey atada a un navegador.
  2. Comprobarlo: salir, volver a entrar y ver que pide el código.
  3. Solo entonces, aplicar la política:
     - MFA obligatoria: TOTP o llave/passkey, ni SMS ni correo;
     - sin registro libre ni proveedores externos;
     - «Ignorar usuarios desconocidos» activado, para que el login no diga qué nombres existen.

  **Si ya te quedaste fuera:**
  1. Abre `https://hive-auth.higerotech.com/ui/v2/login/loginname` sin pasar por la consola y entra con la
     contraseña.
  2. En la misma pestaña, abre `https://hive-auth.higerotech.com/ui/v2/login/mfa/set?loginName=<nombre completo>` y
     registra el TOTP.
  3. Vuelve a `/`.

  Si abres `mfa/set` sin una sesión en el navegador, sale el error genérico «An error occurred in the Server
  Components render», y el log de `hive-idp-login` dice `No session found`.
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
`$C up -d`, con el `$C` de «Arrancar». **Antes de cada actualización de Zitadel, un respaldo a mano**
(`sudo systemctl start hive-idp-respaldo.service`), porque las migraciones de Zitadel no tienen vuelta atrás.
