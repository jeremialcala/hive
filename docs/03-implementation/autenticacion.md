# Autenticación: cómo se llama al servicio y cómo se opera

* **Estado:** review
* **Fecha:** 2026-10-30
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 03-implementation
* **Versión:** 0.6.0

Todo endpoint que no sea el webhook de Meta, `/health` ni `/oauth/token` pide un **token de acceso** en la cabecera
`Authorization: DPoP <token>`, y a su lado una **prueba DPoP** en la cabecera `DPoP`. Es OAuth 2.0 con la concesión
`client_credentials`: el cliente lo pide a `POST /oauth/token` con una **aserción**, un JWT que firma con su clave,
metido dentro de un JWE cifrado para el gateway, y lo usa durante 10 minutos en todas sus peticiones. El token va
**atado a esa clave** (DPoP, RFC 9449): en cada petición, la del token incluida, el cliente firma con ella una prueba
nueva para ese método y esa URL. Un token robado no sirve sin la clave.

Las decisiones están en [ADR-0025](../00-project/adr/0025-oauth2-client-credentials-con-private-key-jwt.md), que
supera en parte [ADR-0005](../00-project/adr/0005-jwt-firmado-por-el-cliente-dentro-del-jwe.md) y
[ADR-0009](../00-project/adr/0009-parametros-del-token-de-autenticacion.md), y en
[ADR-0033](../00-project/adr/0033-dpop-en-los-tokens-de-acceso.md), la de DPoP. Esto es cómo se usan.

## Las claves que intervienen

| Clave | De quién | Dónde vive la parte privada | Para qué |
|---|---|---|---|
| Par P-256 de firma | De cada cliente, operador incluido | Solo en el cliente | Firma la aserción, que demuestra quién pide el token, y cada prueba DPoP, que demuestra que quien usa el token tiene la clave. El gateway guarda la parte pública |
| Par RSA de cifrado | Del gateway | En un fichero del servidor, fuera del repositorio y de la imagen | Que solo el gateway lea la aserción |
| Par P-256 de firma | Del gateway | En el mismo fichero | Firma los tokens de acceso. No se entrega a nadie: solo el gateway los comprueba |

El gateway no guarda ningún secreto de un cliente: si alguien se lleva la base, no se lleva con qué hacerse pasar
por nadie.

## Para un cliente

### La aserción

1. **Firmar** un JWT con la clave privada, algoritmo `ES256`. La cabecera lleva el `kid` que devolvió el registro.
2. **Cifrar** ese JWT como JWE compacto para una clave pública del gateway: `RSA-OAEP-256` con `A256CBC-HS512`. La
   cabecera lleva `cty: JWT` y el `kid` de la clave del gateway.

| Campo del JWT | Valor |
|---|---|
| `iss` y `sub` | El identificador del cliente (`uuid`): el cliente se autentica a sí mismo (RFC 7523) |
| `aud` | La audiencia del gateway, la que devolvió el registro |
| `iat`, `exp` | `exp` como máximo 60 segundos después de `iat` |
| `jti` | Un identificador nuevo en cada aserción |

Una aserción vale **una vez y 60 segundos**. Cualquier otro algoritmo se rechaza.

### La prueba DPoP

Cada petición lleva una, nueva, en la cabecera `DPoP`: un JWT que firma **la misma clave que firma la aserción**.

| Parte | Valor |
|---|---|
| Cabecera | `typ: dpop+jwt`, `alg: ES256` y `jwk`, la parte **pública** de la clave (`kty`, `crv`, `x`, `y`) |
| `jti` | Un identificador nuevo en cada prueba |
| `htm` | El método de la petición: `GET`, `POST`… |
| `htu` | La URL de la petición, sin consulta ni fragmento: `https://<gateway>/keys` |
| `iat` | Cuándo se firmó. Vale 60 segundos, con 5 de margen para un reloj adelantado |
| `ath` | Junto a un token de acceso, su hash: SHA-256 del token, en base64url sin relleno. En `/oauth/token` no va |

Una prueba vale **una vez**: otra petición, aunque sea igual, lleva otra.

### El token de acceso

```
POST /oauth/token
Content-Type: application/x-www-form-urlencoded
DPoP: <la prueba, para POST y https://<gateway>/oauth/token, sin ath>

grant_type=client_credentials
&client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer
&client_assertion=<la aserción>
```

La respuesta es `{"access_token": "…", "token_type": "DPoP", "expires_in": 600}`. El token vale **10 minutos y
para todas las peticiones** que se hagan en ellos: se pide otro un poco antes de que caduque. Es un JWT que firma el
gateway (`typ: at+jwt`), y dice quién es el cliente y la huella de su clave (`cnf.jkt`); el cliente no tiene que
leerlo. Se usa así:

```
GET /keys
Authorization: DPoP <el token>
DPoP: <una prueba nueva, para GET y https://<gateway>/keys, con el ath del token>
```

Los errores son los de OAuth: `400` con `invalid_request` o `unsupported_grant_type` si la petición no es como se
acordó, `400` con `invalid_dpop_proof` si falta la prueba, no vale o no la firma la clave de la aserción, y `401` con
`invalid_client` para cualquier aserción que no valga, sin decir el motivo; el motivo queda en el log del gateway. Un
token o una prueba rechazados en una ruta reciben siempre el mismo `401`, con `WWW-Authenticate: DPoP algs="ES256"`
y `error="invalid_token"` o `error="invalid_dpop_proof"`.

Hay un límite de peticiones ([ADR-0028](../00-project/adr/0028-limite-de-peticiones-en-oauth-token.md)). Pasarlo da
`429` con `{"error": "temporarily_unavailable"}` y una cabecera `Retry-After` con los segundos que hay que esperar.
Se pasa de tres maneras:

- con más de 20 peticiones por minuto desde una misma dirección;
- con 10 aserciones rechazadas en 10 minutos desde una dirección, que queda bloqueada hasta que el primer rechazo
  cumple los 10 minutos;
- con más de 30 tokens en una hora para una misma clave.

Un cliente que reutiliza su token durante sus 10 minutos pide unos 6 por hora y no llega a ninguno de los tres.

### Con el cliente de referencia

`src/ops/client.py` hace esos pasos y no necesita nada del servicio. Sirve tal cual o como ejemplo para hacerlo con
otra librería JOSE y OAuth.

```bash
# Una vez: crea la clave. Escribe la privada en el fichero e imprime la pública, que es la que se registra.
python -m ops.client new-key mi-clave.json

# Cada 10 minutos: un token de acceso, para todas las peticiones de esos 10 minutos.
TOKEN=$(python -m ops.client access-token mi-clave.json <uuid del cliente> <audiencia> gateway-publicas.json https://<gateway>)
# En cada petición: una prueba nueva para su método y su URL.
PRUEBA=$(python -m ops.client proof mi-clave.json GET https://<gateway>/keys "$TOKEN")
curl -H "Authorization: DPoP $TOKEN" -H "DPoP: $PRUEBA" https://<gateway>/keys
```

`gateway-publicas.json` es lo que el registro devolvió en `gateway.keys`, guardado como `{"keys": [...]}`. Los
comandos se ejecutan desde `src/` de este repositorio con `jwcrypto` instalado, o con la imagen del servicio.

### Rotar la clave propia

Un cliente puede tener dos claves a la vez, para cambiar sin corte:

1. `POST /keys` con la clave pública nueva y la **prueba de que tiene su parte privada**: un JWT que firma la clave
   nueva (`typ: key-proof+jwt`, `ES256`), con el `iss` y el `aud` de la aserción y el `jti` del token de acceso con el
   que va esa petición (el campo `jti` del token, que el cliente lee sin comprobarlo). Vale una vez y 60 segundos.
   Sin ella, o si no casa, o si ya se usó una prueba con ese token, la respuesta es `422` `INVALID_KEY_PROOF` y la
   clave no se añade.
2. Empezar a firmar las aserciones con la nueva.
3. `DELETE /keys/{kid}` con el `kid` de la antigua.

Con el cliente de referencia, el paso 1 es:

```bash
python -m ops.client new-key clave-nueva.json
TOKEN=$(python -m ops.client access-token mi-clave.json <uuid del cliente> <audiencia> gateway-publicas.json https://<gateway>)
PRUEBA=$(python -m ops.client proof mi-clave.json POST https://<gateway>/keys "$TOKEN")
CUERPO=$(python -m ops.client add-key clave-nueva.json <uuid del cliente> <audiencia> "$TOKEN")
curl -H "Authorization: DPoP $TOKEN" -H "DPoP: $PRUEBA" -H "Content-Type: application/json" -d "$CUERPO" https://<gateway>/keys
```

La prueba impide registrar una clave con la que nadie puede firmar, pero **no frena a quien robó una clave**: se
hace una propia y prueba que la tiene. Contra eso está la revocación de la tabla de abajo.

No se puede borrar la última clave: el cliente se quedaría fuera. Si pierde su clave, es el operador quien le
registra otra.

## Para el BFF: el token de una persona

Una persona de un cliente no firma aserciones: entra por Zitadel, con MFA, y el BFF de la PWA (`hive-portal`) cambia
su ID token por un token de Hive ([ADR-0029](../00-project/adr/0029-autenticacion-de-personas.md), RFC 8693). Zitadel
dice quién es la persona. Hive dice de qué cliente es, con qué rol, y si todavía puede entrar.

1. **El BFF tiene su propio par P-256**, que genera al arrancar y guarda solo en memoria
   ([ADR-0033](../00-project/adr/0033-dpop-en-los-tokens-de-acceso.md), punto 2). No es una clave registrada en Hive.
   Firma con él la prueba DPoP de cada petición, también la del intercambio.
2. **El intercambio:**

   ```
   POST /oauth/token
   Content-Type: application/x-www-form-urlencoded
   DPoP: <una prueba de la clave del BFF, para POST y https://<gateway>/oauth/token, sin ath>

   grant_type=urn:ietf:params:oauth:grant-type:token-exchange
   &subject_token=<el ID token que Zitadel dio al BFF para esa persona>
   &subject_token_type=urn:ietf:params:oauth:token-type:id_token
   &hive_client=<el uuid del cliente: solo si la persona es de más de uno>
   ```

   Puede llevar `requested_token_type=urn:ietf:params:oauth:token-type:access_token`, que es lo único que se emite.
   Cualquier otro parámetro da `invalid_request`.
3. **Lo que Hive comprueba del ID token:**
   - la firma, con una clave del JWKS de Zitadel, `RS256` o `ES256`;
   - el `iss`, que es `IDP_ISSUER`;
   - el `aud`, que incluye `IDP_AUDIENCE`, el client id del BFF en Zitadel. Si trae más de una audiencia, el `azp`
     tiene que ser ese mismo;
   - el `exp` y el `iat`, con 30 segundos de margen;
   - que el `amr` sea una lista con `mfa`. Sin segundo factor no hay token, aunque la contraseña sea buena.
4. **Lo que Hive comprueba de la persona:** la busca por `iss` y `sub`. Tiene que estar invitada o activa, y su
   cliente activo. La primera vez que consigue un token pasa de invitada a activa.
5. **La respuesta:**

   ```json
   {"access_token": "…", "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
    "token_type": "DPoP", "expires_in": 600}
   ```

   El token es como el de una máquina, pero dice también quién es la persona y su rol (`hive_user`,
   `hive_user_role`), y no lleva clave del cliente. Está atado a la clave del BFF: cada petición con él lleva una
   prueba nueva firmada por esa clave, como en [La prueba DPoP](#la-prueba-dpop).
6. **Los errores:**

   | Respuesta | Cuándo |
   |---|---|
   | `400 invalid_request` | La petición no es la del punto 2 |
   | `400 unsupported_grant_type` | Este `hive-api` no tiene un IdP configurado |
   | `400 invalid_dpop_proof` | Falta la prueba, o no vale |
   | `400 invalid_grant` | El ID token no vale, no tiene `mfa`, o su persona no está en Hive, está bloqueada o de baja, o su cliente no está activo. Siempre la misma respuesta, sin el motivo, que queda en el log |
   | `400 invalid_target` | La persona es de más de un cliente y `hive_client` no dice cuál |
   | `429 temporarily_unavailable` | Un límite de la [ADR-0028](../00-project/adr/0028-limite-de-peticiones-en-oauth-token.md). Los tokens se cuentan por persona, 30 por hora, como los de una clave |
   | `503 temporarily_unavailable` | No se pudieron leer las claves de Zitadel. Se reintenta más tarde |

7. **Cada 10 minutos, otra vez:** el BFF repite el intercambio con el ID token de la sesión antes de que caduque el
   token de Hive. Si falla, cierra la sesión. Así una persona bloqueada o dada de baja pierde el acceso en
   10 minutos como mucho (AB26).

**Lo que alcanza cada rol** está en la [matriz rol × acción](../02-design/matriz-rol-accion.md#rutas-y-roles):
- **`cliente-admin`:** todo lo de su cliente, como la credencial de máquina. Si su cliente es el del operador, también
  el registro de clientes.
- **`cliente-analista`:** solo lee y envía plantillas aprobadas. Cualquier otra ruta le responde `403`, también un
  envío de texto libre. La ficha del bot le llega sin el `verifyToken`.

## Para el operador

Los comandos se ejecutan con la imagen del servicio, en el servidor.

### Puesta en marcha

```bash
# 1. Los dos pares de claves del gateway: el de cifrado y el de firma de los tokens de acceso (ADR-0025). Las
#    privadas se escriben en el fichero, solo para su dueño, y no se imprimen.
docker run --rm -v /srv/apps/hive-gateway/keys:/keys <imagen> python -m ops.gateway_key generate /keys/gateway-keys.json
docker run --rm -v /srv/apps/hive-gateway/keys:/keys <imagen> python -m ops.gateway_key generate-signing /keys/gateway-keys.json

# 2. En el .env: la ruta del fichero dentro del contenedor y la audiencia que pondrán los clientes en sus tokens.
#    GATEWAY_KEYS_FILE=/run/gateway/gateway-keys.json
#    GATEWAY_AUDIENCE=https://hive-api.higerotech.com     (ADR-0024)

# 3. La primera clave del operador. Se crea en la máquina del operador, y al servidor solo va la parte pública.
python -m ops.client new-key operador.json > operador-publica.json
docker compose run --rm -v $PWD/operador-publica.json:/tmp/operador.json:ro gateway python -m ops.operator_key /tmp/operador.json
```

Sin el fichero de claves, o con uno sin una clave de cada tipo, **el servicio no arranca**. El contenedor corre con el
usuario `10001`, que tiene que poder leer el fichero: `chown 10001` y `chmod 400`.

El paso 3 existe porque todos los endpoints piden un token y nadie puede firmar uno antes de tener una clave
registrada. Devuelve el `uuid` del cliente del operador y el `kid` de su clave.

### El IdP de las personas

Para que el intercambio funcione, `hive-api` necesita en su `.env`:

```
IDP_ISSUER=https://hive-auth.higerotech.com      # el iss de los ID tokens de Zitadel
IDP_AUDIENCE=<el client id del BFF en Zitadel>   # el aud de esos ID tokens
# IDP_JWKS_URL=https://hive-auth.higerotech.com/oauth/v2/keys   (es el valor por defecto)
```

Sin `IDP_ISSUER` o sin `IDP_AUDIENCE`, el intercambio responde `unsupported_grant_type` y solo entran las
credenciales de máquina. `hive-api` lee las claves de Zitadel por su nombre público, con TLS, y las guarda una hora.
Si llega un ID token firmado por una clave que no tiene, las vuelve a leer, como mucho una vez por minuto.

### La cuenta de servicio que invita a las personas

Hive invita a las personas con una cuenta de servicio propia en Zitadel (HIG-94, ADR-0029, punto 7). Se crea una vez,
en la consola de Zitadel, `https://hive-auth.higerotech.com/ui/console`:

1. **El usuario de servicio.** En la organización de las personas de Hive: *Users → Service Users → New*. Nombre
   `hive-api`, tipo de token *Bearer*.
2. **Su permiso.** En el usuario `hive-api`, *Administrator Roles → New*, a nivel **Organization**, con el rol
   `ORG_USER_MANAGER`. También se llega desde *Organization*, en la lista de administradores. Le basta para buscar,
   crear e invitar usuarios de esa organización, y nada más.

   **No es un rol de proyecto.** Un rol `ORG_USER_MANAGER` creado en el proyecto y asignado en *Role Assignments*
   no da ningún permiso en Zitadel. Así pasó el 2026-10-30: la búsqueda seguía funcionando, porque solo devuelve lo
   que la cuenta ve, pero crear daba `membership not found (AUTHZ-cdgFk)`. Ahora `POST /people` responde `503` y el
   log de `hive-api` dice `the service account was turned away: 404 AUTHZ-cdgFk; it needs ORG_USER_MANAGER in its
   Administrator Roles`.
3. **Su token.** En el usuario, *Personal Access Tokens → New*. Zitadel lo enseña una sola vez: va directo al fichero
   de midgard, sin pasar por el chat ni por un comando con argumentos.

   ```sh
   # En midgard. Se pega el token, Enter y Ctrl-D: no queda en el historial ni en ps.
   # El fichero es del usuario del contenedor (10001) y solo él lo lee, como los otros secretos de keys/.
   sudo install -d -m 700 -o 10001 -g 10001 /srv/apps/hive-gateway/keys/idp
   sudo sh -c 'umask 077; cat > /srv/apps/hive-gateway/keys/idp/zitadel-token'
   sudo chown 10001:10001 /srv/apps/hive-gateway/keys/idp/zitadel-token
   sudo chmod 400 /srv/apps/hive-gateway/keys/idp/zitadel-token
   # Se comprueba sin enseñarlo: el largo, y que el último byte sea un salto de línea o no importa (se recorta).
   sudo awk '{print length}' /srv/apps/hive-gateway/keys/idp/zitadel-token
   ```

   El directorio ya existe desde el despliegue de HIG-94, con ese dueño.

   El contenedor lo lee de `/run/idp/zitadel-token` en cada invitación, así que rotarlo no pide reiniciar nada.
4. **En el `.env` de `hive-api`:** `IDP_ORGANIZATION_ID=<el id de esa organización>`, junto a `IDP_ISSUER`. Si
   Zitadel no se alcanza por el emisor, `IDP_API_URL`.

Sin la organización o sin el token, `POST /people` responde `503` y el log dice qué falta. Si Zitadel rechaza la
cuenta de servicio (`401`, `403`), también `503`, con el código en el log: es para el operador, no para el cliente.

Nadie consigue un token de persona en producción hasta que haya un IdP, una cuenta de servicio y una persona
invitada. Además, las personas de verdad esperan a que Nidavellir cumpla 14 días seguidos sin colgarse (ADR-0029,
enmienda).

### Alta de un cliente

El cliente crea su clave y envía la parte pública. El operador la registra:

```
POST /clients
{"name": "acme", "signingKey": {"kty": "EC", "crv": "P-256", "x": "...", "y": "..."}}
```

La respuesta lleva lo que el cliente necesita: su `uuid`, el `kid` de su clave, la audiencia y las claves públicas
del gateway. Solo se acepta una clave pública P-256; una privada se rechaza y no se guarda.

### Revocar y recuperar

| Situación | Qué hacer |
|---|---|
| Una clave de un cliente está comprometida | Quitar **todas** las claves que el cliente no reconozca, no solo esa: `GET /clients` las lista en `keys` de cada cliente, y cada una se quita con `DELETE /clients/{client_id}/keys/{kid}`. Desde ese momento no consigue ningún token de acceso nuevo, pero el que ya tenía vale hasta que caduca, 10 minutos como mucho (ADR-0025). Para cortarlos todos al instante, se rota la clave de firma del gateway (abajo). Quien tenía la clave pudo añadir otra suya con ella, con su prueba, y quitar la legítima; cada alta de una clave deja una línea de aviso en el log con su `kid`, y cada baja otra. Si el cliente se queda sin ninguna, se le registra la nueva como en la fila siguiente |
| Un cliente perdió su clave | `POST /clients/{client_id}/keys` con la pública nueva |
| El operador perdió la suya | Repetir el paso 3 de la puesta en marcha |

El operador registra clientes, pero no entra en sus datos: en las rutas de los chatbots es un cliente más y solo
alcanza los suyos.

### Dar paso al motor de respuestas

El motor no tiene una credencial para todo. Cada worker del motor tiene una clave por cliente, y con ella solo
alcanza los bots de ese cliente ([ADR-0016](../00-project/adr/0016-credencial-del-motor-por-cliente.md)) y las
rutas de ese worker ([ADR-0022](../00-project/adr/0022-una-credencial-por-worker.md)). Para cada cliente al que
vaya a servir:

1. El operador da de alta las seis claves de ese cliente con un comando (abajo). Por cada worker, el comando crea
   una clave, guarda la parte privada en Andvari y registra la pública bajo el cliente, diciendo de qué worker es:

   ```
   POST /clients/{client_id}/service-keys
   {"signingKey": {"kty": "EC", "crv": "P-256", "x": "...", "y": "..."}, "worker": "notifier"}
   ```

2. Cada worker lee su clave privada de Andvari, en `motor/data/clientes/<client_id>/<worker>` (campo `jwk`), con
   su propio rol de AppRole, `motor-<worker>`. Solo alcanza la suya, la de cualquier cliente.
3. El worker firma sus aserciones con esa clave, con el identificador del cliente como emisor, y pide su token de
   acceso a `/oauth/token`, como cualquier cliente. Lo usa hasta un minuto antes de que caduque. Qué cliente es lo
   dice cada mensaje que atiende, en `clientId`.

#### El comando que da de alta las claves de un cliente

`python -m ops.engine_keys <client_id>`, en midgard, desde la imagen del gateway:

```bash
cd /srv/apps/hive-gateway
# La clave privada del operador solo se monta para esta ejecución.
IMAGE_TAG=<la etiqueta que corre> docker compose --profile operations run --rm \
    -v /ruta/operador.json:/run/operator/key.json:ro engine-keys <client_id>
```

- **Lo que hace con cada worker:**
  - si ya tiene la clave guardada y registrada, la deja como está;
  - si la tiene guardada pero no registrada, la registra;
  - solo si no tiene ninguna, crea una nueva.

  Por eso se puede volver a ejecutar cuando se para a medias. Una clave guardada no se escribe nunca encima: lo
  impide el check-and-set del KV y lo impide la política `motor-claves`, que solo crea y lee.
- **Guarda antes de registrar.** Así, una clave registrada siempre tiene su parte privada en Andvari.
- **Nunca imprime ni escribe en disco una clave privada.** Imprime, por worker, el `kid` y qué hizo: `kept` y
  `registered`, cada uno `new` o `already`. Si un worker falla, lo dice en `error` y el comando acaba con 1.
- **Configuración** (en el `.env` o en el compose):
  - `GATEWAY_URL`, `GATEWAY_AUDIENCE` y `GATEWAY_PUBLIC_KEYS_FILE` (la salida de `python -m ops.gateway_key show`);
  - `OPERATOR_KEY_FILE` y `OPERATOR_CLIENT_ID`;
  - `ANDVARI_ADDR` y `ANDVARI_CA_FILE`;
  - `ENGINE_KEYS_ROLE_ID` y `ENGINE_KEYS_SECRET_ID_FILE`.

  El rol y su `secret_id` los crea `motor/instalar.sh` del repositorio `andvari`.

Qué worker firma lo dice el registro, no el token: el `kid` dice qué clave es, y la clave, de qué worker. Cada
worker alcanza sus rutas, y cualquier otra le responde `403`:

| Ruta | Qué obtiene | `classifier` | `coordinator` | `link` | `responder` | `evaluator` | `notifier` |
|---|---|:-:|:-:|:-:|:-:|:-:|:-:|
| `GET /chat/{bot_id}/rules` | La persona del bot y sus reglas | ✓ | ✓ | | ✓ | ✓ | |
| `GET /chat/{bot_id}/tools` | Sus herramientas | | ✓ | ✓ | | | |
| `GET /chat/{bot_id}/tokens` | Los tokens de Meta con los que envía la respuesta | | | | | | ✓ |
| `GET /chat/{bot_id}/contacts/{contact_id}/turns` | Las 10 últimas vueltas de una conversación, para que el coordinador rehaga su contexto cuando no lo tiene (RF15) | | ✓ | | | | |
| `GET /gateway/keys` | Las claves públicas del gateway, para seguir su rotación | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

La tabla está en `WORKER_ROUTES` (`src/app.py`). Una ruta nueva nace cerrada para todos los workers hasta que se
apunta ahí.

- **Los tokens solo los recibe el notificador.** El cliente no los recupera con su propia clave, ni el operador
  con la suya, ni otro worker del motor con la de él. No incluyen el App Secret ni el token de verificación.
- **Cada entrega de tokens deja una línea en el log**, también en `production`: el bot, el cliente, la clave y el
  worker.
- **Las vueltas, igual.** Las recibe solo el coordinador; el cliente tiene para eso la exportación del contacto.
  Cada entrega deja en el log el contacto, el bot, el cliente, la clave, el worker y cuántos mensajes fueron,
  nunca su texto. Una vuelta empieza en un mensaje del contacto, y llegan desde el décimo por el final hasta el
  último, en los dos sentidos y en orden.
- **Admite dos claves por worker y cliente**, para rotar sin corte: se registra la nueva, el worker pasa a usarla y
  se borra la antigua con `DELETE /clients/{client_id}/service-keys/{kid}`. Borrarla le quita el acceso en el acto.
- **El cliente ve qué claves del motor alcanzan sus bots, y de qué worker es cada una**: `GET /keys` las lista en
  `serviceKeys`, como `{"kid": ..., "worker": ...}`.
- **Una clave es del cliente o del motor, nunca de los dos**, y es de un solo worker. Registrar la misma dos veces
  responde `409`.

#### Las claves de antes de I6

Las claves que se registraron antes de I6 no tienen worker, y `GET /keys` las muestra con `"worker": null`. Siguen
alcanzando las cinco rutas del motor, vueltas incluidas, hasta que el motor pase a una clave por worker. Cada uso
deja un aviso en el log, también en `production`:

```
The response engine used key <kid> of client <client_id>, which has no worker: register one per worker (ADR-0022)
```

Esa línea es la lista de lo que falta por retirar. Ya no se pueden registrar claves nuevas sin worker: la API
responde `422`. Al terminar el refactor se borran con `DELETE /clients/{client_id}/service-keys/{kid}`.

### Rotar el par de cifrado del gateway

1. `python -m ops.gateway_key generate <fichero>` añade un par nuevo; el anterior sigue valiendo.
2. Reiniciar el servicio, que lee el fichero al arrancar.
3. Los clientes piden las claves con `GET /gateway/keys` y pasan a cifrar con la nueva, la última de la lista.
4. Cuando ya nadie cifra con la antigua: `python -m ops.gateway_key drop <fichero> <kid>` y otro reinicio.

### Rotar el par de firma del gateway

1. `python -m ops.gateway_key generate-signing <fichero>` y reiniciar: la nueva firma, y la antigua sigue
   comprobando los tokens que firmó.
2. A los 10 minutos ya no queda ninguno: `python -m ops.gateway_key drop <fichero> <kid>` y otro reinicio.

Quitar la antigua sin esperar **corta al instante todos los tokens de acceso** que firmó: es la forma de dejar fuera a
quien tenía uno de una clave robada. Los clientes, el motor incluido, piden otro con su aserción.

### El límite de `/oauth/token` en Cloudflare

Es la primera barrera de la ADR-0028. Va por delante de los límites de `hive-api` y frena las ráfagas antes de que
lleguen a midgard. Se da de alta una vez, en el panel de Cloudflare, en la zona `higerotech.com`: **Security → WAF
→ Rate limiting rules → Create rule**.

| Campo | Valor |
|---|---|
| Nombre | `hive-oauth-token` |
| Si las peticiones cumplen | URI Path `equals` `/oauth/token` |
| Con las mismas características | IP |
| Cuando pasan de | 10 peticiones en 10 segundos |
| Entonces | Block, durante 10 segundos |

Es la única regla de límite del plan Free, que solo cuenta por IP y en ventanas de 10 segundos. El motor no pasa por
ella, porque llama a `hive-api` por la red interna. Se comprueba desde fuera: once peticiones seguidas desde la misma
máquina, y la undécima recibe la respuesta de bloqueo de Cloudflare, no la de `hive-api`.

## El portal (`hive-portal`)

El BFF de la PWA (HIG-93, [portal/README.md](../../portal/README.md)) corre en midgard, en `/srv/apps/hive-portal`,
junto a `hive-api`, y se publica en `https://hive-app.higerotech.com`. Ponerlo en marcha lleva estos pasos, una vez:

1. **La app `hive-portal` en Zitadel** (*Projects → Hive → hive-portal*):
   - *Redirect Settings*:
     - Redirect URI: `https://hive-app.higerotech.com/auth/callback`;
     - Post Logout URI: `https://hive-app.higerotech.com/`.
   - *Authentication Method*: **Basic**, que ya está.
   - *Actions → Regenerate client secret*. Zitadel lo enseña una sola vez y va directo al fichero:

     ```sh
     # En midgard. Se pega el secreto, Enter y Ctrl-D.
     sudo install -d -m 700 -o 10001 -g 10001 /srv/apps/hive-portal/keys
     sudo sh -c 'umask 077; cat > /srv/apps/hive-portal/keys/oidc-client-secret'
     sudo chown 10001:10001 /srv/apps/hive-portal/keys/oidc-client-secret
     sudo chmod 400 /srv/apps/hive-portal/keys/oidc-client-secret
     ```
2. **La caducidad de los ID tokens**, en *Default settings → OIDC Token lifetimes*: **12 horas** o más. El portal
   renueva el token de Hive con el ID token de la sesión, así que con menos la sesión termina antes de tiempo
   ([ADR-0034](../00-project/adr/0034-sesiones-del-portal-en-bifrost.md)).
3. **La clave de las sesiones**, que nace en midgard y no sale de allí:

   ```sh
   sudo sh -c 'umask 077; head -c 32 /dev/urandom | base64 > /srv/apps/hive-portal/keys/session-key'
   sudo chown 10001:10001 /srv/apps/hive-portal/keys/session-key
   sudo chmod 400 /srv/apps/hive-portal/keys/session-key
   ```

   Rotarla cierra todas las sesiones, y no rompe nada más.
4. **Bifröst:** el bucket `sesiones` y el usuario `portal` (higerotech/bifrost#7). Se instala con `instalar.sh`,
   después se reinicia Bifröst y por último se ejecuta `jetstream/aprovisionar.sh`. Las credenciales quedan en
   `/srv/infra/bifrost/credenciales/portal.env`.
5. **El `.env` de `/srv/apps/hive-portal`:** `OIDC_CLIENT_ID=<el Client Id de hive-portal>`. El resto ya lo pone el
   compose.
6. **El nombre público**, en el túnel de midgard. En Cloudflare Zero Trust: *Networks → Tunnels → el túnel de
   midgard → Public Hostname → Add*. Ahí, `hive-app.higerotech.com` → `http://hive-portal:8080`. El túnel y el portal
   comparten la red `hive-gateway_publico`.
7. **La imagen y el arranque**, desde una copia de `develop` en `~/hive-build`:

   ```sh
   sudo docker build -f portal/Dockerfile -t ghcr.io/jeremialcala/hive-portal:hive-<sha7> .
   sudo install -d /srv/apps/hive-portal
   sudo install -m 644 portal/deploy/docker-compose.yml /srv/apps/hive-portal/docker-compose.yml
   cd /srv/apps/hive-portal && sudo env PORTAL_IMAGE_TAG=hive-<sha7> docker compose up -d
   ```

Para comprobarlo:
- `curl -s http://127.0.0.1:5004/health`, en midgard;
- `https://hive-app.higerotech.com/auth/login`, que tiene que llevar a Zitadel.

Mientras Nidavellir no cumpla 14 días seguidos sin colgarse, solo entran personas de prueba (ADR-0029, enmienda).

## Lo que no hace todavía

- **El comando no rota las claves**: da de alta las que faltan. Rotar la de un worker pide escribir una versión
  nueva en Andvari, y la política `motor-claves` no lo permite a propósito. Está pendiente.
- **Solo `/oauth/token` tiene límite de peticiones** (ADR-0028). El resto de rutas no lo tiene: cada una pide un
  token, y un token solo se consigue con la clave de un cliente. Los contadores están en memoria: con más de un
  worker de `hive-api`, cada uno lleva los suyos.
- **Un cliente no se puede desactivar por la API.** Revocar sus claves lo deja sin tokens nuevos; el estado del
  cliente solo se cambia en la base.
- **Un token de persona sigue valiendo hasta que caduca**, aunque la persona se bloquee entretanto: como mucho
  10 minutos (ADR-0029, punto 8).
- **La clave privada del gateway es un fichero.** Pasará a Andvari cuando el gateway se integre con él para el
  cifrado de datos (ADR-0013).
