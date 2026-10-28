# Autenticación: cómo se llama al servicio y cómo se opera

* **Estado:** review
* **Fecha:** 2026-10-26
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 03-implementation
* **Versión:** 0.2.0

Todo endpoint que no sea el webhook de Meta, `/health` ni `/oauth/token` pide un **token de acceso** en la cabecera
`Authorization: Bearer <token>`. Es OAuth 2.0 con la concesión `client_credentials`: el cliente lo pide a
`POST /oauth/token` con una **aserción**, un JWT que firma con su clave, metido dentro de un JWE cifrado para el
gateway, y lo usa durante 10 minutos en todas sus peticiones. Las decisiones están en
[ADR-0025](../00-project/adr/0025-oauth2-client-credentials-con-private-key-jwt.md), que supera en parte
[ADR-0005](../00-project/adr/0005-jwt-firmado-por-el-cliente-dentro-del-jwe.md) y
[ADR-0009](../00-project/adr/0009-parametros-del-token-de-autenticacion.md); esto es cómo se usan.

## Las claves que intervienen

| Clave | De quién | Dónde vive la parte privada | Para qué |
|---|---|---|---|
| Par P-256 de firma | De cada cliente, operador incluido | Solo en el cliente | Firma la aserción: demuestra quién pide el token. El gateway guarda la parte pública |
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

### El token de acceso

```
POST /oauth/token
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials
&client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer
&client_assertion=<la aserción>
```

La respuesta es `{"access_token": "…", "token_type": "Bearer", "expires_in": 600}`. El token vale **10 minutos y
para todas las peticiones** que se hagan en ellos: se pide otro un poco antes de que caduque. Es un JWT que firma el
gateway (`typ: at+jwt`), y dice quién es el cliente; el cliente no tiene que leerlo.

Los errores son los de OAuth: `400` con `invalid_request` o `unsupported_grant_type` si la petición no es como se
acordó, y `401` con `invalid_client` para cualquier aserción que no valga, sin decir el motivo; el motivo queda en el
log del gateway. Un token de acceso rechazado en una ruta recibe siempre el mismo `401`.

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
curl -H "Authorization: Bearer $TOKEN" https://<gateway>/keys
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
curl -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d "$(python -m ops.client add-key clave-nueva.json <uuid del cliente> <audiencia> $TOKEN)" https://<gateway>/keys
```

La prueba impide registrar una clave con la que nadie puede firmar, pero **no frena a quien robó una clave**: se
hace una propia y prueba que la tiene. Contra eso está la revocación de la tabla de abajo.

No se puede borrar la última clave: el cliente se quedaría fuera. Si pierde su clave, es el operador quien le
registra otra.

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

## Lo que no hace todavía

- **El comando no rota las claves**: da de alta las que faltan. Rotar la de un worker pide escribir una versión
  nueva en Andvari, y la política `motor-claves` no lo permite a propósito. Está pendiente.
- **Solo `/oauth/token` tiene límite de peticiones** (ADR-0028). El resto de rutas no lo tiene: cada una pide un
  token, y un token solo se consigue con la clave de un cliente. Los contadores están en memoria: con más de un
  worker de `hive-api`, cada uno lleva los suyos.
- **Un cliente no se puede desactivar por la API.** Revocar sus claves lo deja sin tokens nuevos; el estado del
  cliente solo se cambia en la base.
- **El token de acceso no está atado a la clave del cliente** (DPoP, RFC 9449): quien lo robe lo usa hasta que
  caduque. Queda para cuando haya clientes externos (ADR-0025).
- **La clave privada del gateway es un fichero.** Pasará a Andvari cuando el gateway se integre con él para el
  cifrado de datos (ADR-0013).
