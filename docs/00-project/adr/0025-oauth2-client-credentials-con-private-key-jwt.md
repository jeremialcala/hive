# ADR-0025: OAuth 2.0 con `client_credentials` y aserción firmada: un token de acceso para varias peticiones

* **Estado:** accepted
* **Fecha:** 2026-10-26
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 02-design
* **Versión:** 0.1.0
* **ID:** ADR-0025
* **Supersede:** en parte [ADR-0005](0005-jwt-firmado-por-el-cliente-dentro-del-jwe.md) y
  [ADR-0009](0009-parametros-del-token-de-autenticacion.md): el JWT firmado por el cliente dentro de un JWE ya no se
  presenta en cada petición, sino una vez, para pedir un token de acceso. Sus algoritmos, sus claves y su uso único
  se mantienen
* **Controles OWASP afectados:** A07 (autenticación), A04 (algoritmos y claves), A01 (de quién es cada petición) ·
  ASVS 5.0, V10 (OAuth y OIDC)

Decisiones de Jeremi del 2026-10-26.

## Contexto

Desde la ADR-0005, cada petición a la API de gestión lleva un token propio: un JWT que el cliente firma con su clave
P-256 (`ES256`), metido en un JWE cifrado para el gateway, que vale una vez y 60 segundos. Funciona, pero:

- **No es un esquema conocido.** Un cliente tiene que firmar y cifrar en cada petición con una librería JOSE; ninguna
  librería de OAuth lo hace por él.
- **Cada petición escribe en la base:** el gateway guarda cada `jti` para que no se use dos veces, y lee la clave y el
  rol del cliente. Desde midgard, cada viaje a Atlas cuesta entre 60 y 750 ms.
- **El motor firma un token por llamada:** dos por mensaje en el coordinador y uno en el notificador.

Jeremi pidió un esquema OAuth 2.0 en el que el servidor del cliente pida un token de portador con una firma JWS que
identifique la clave autorizada.

## Decisión

1. **OAuth 2.0 con la concesión `client_credentials`** (RFC 6749, §4.4) y **autenticación del cliente por aserción
   JWT** (RFC 7521 y RFC 7523, §2.2; en OpenID Connect, `private_key_jwt`). El gateway es a la vez el servidor de
   autorización y el de recursos.

2. **`POST /oauth/token`**, con el cuerpo en `application/x-www-form-urlencoded`:

   | Parámetro | Valor |
   |---|---|
   | `grant_type` | `client_credentials` |
   | `client_assertion_type` | `urn:ietf:params:oauth:client-assertion-type:jwt-bearer` |
   | `client_assertion` | La aserción (punto 3) |

   La respuesta es la de RFC 6749, §5.1, con `Cache-Control: no-store`:
   `{"access_token": "…", "token_type": "Bearer", "expires_in": 600}`. Los errores son los de §5.2:
   `invalid_request` y `unsupported_grant_type` con `400`, e `invalid_client` con `401` para cualquier aserción que no
   valga. La respuesta no dice el motivo; el motivo queda en el log, como hoy.

3. **La aserción es el token de hoy, firmado y cifrado** (decisión de Jeremi: también cifrada, no solo firmada):
   - un JWS `ES256` con el `kid` de una clave que el cliente tiene registrada, dentro de un JWE `RSA-OAEP-256` con
     `A256CBC-HS512` para una clave pública del gateway, con `cty: JWT`. Los algoritmos, en lista cerrada, son los de
     la ADR-0009;
   - `iss` y `sub` son el identificador del cliente (RFC 7523, §3), y `aud`, la audiencia del gateway
     (`https://hive-api.higerotech.com`);
   - dura 60 segundos como mucho y **vale una vez**: su `jti` se guarda, como hoy.

   Así nadie más que el gateway lee la aserción, y una aserción robada no sirve para pedir un segundo token.

4. **El token de acceso es un JWT que firma el gateway** (RFC 9068), no una cadena opaca:
   - cabecera `alg: ES256`, `typ: at+jwt` y el `kid` de una clave de firma del gateway;
   - `iss` y `aud` son la audiencia del gateway; `sub` y `client_id`, el cliente; `iat`, `exp` y un `jti` nuevo;
   - **lleva quién es el cliente:** `hive_role` (el rol de su entrada en el registro), `hive_worker` (para el motor,
     qué worker firma; si no, `null`) y `hive_key` (el `kid` de la clave con la que firmó la aserción). Lo firma el
     gateway, así que se puede creer.

   Cada petición se valida **sin ir a la base:** la firma con la clave del gateway, `typ`, `iss`, `aud`, `exp` y que
   la vida no pase de 10 minutos.

5. **Dura 10 minutos.** Es el tiempo que tarda en notarse revocar una clave o desactivar un cliente: los tokens ya
   emitidos valen hasta que caducan. Revocar una clave impide al momento pedir uno nuevo.

6. **El gateway tiene una clave de firma propia**, un par P-256 (`use: sig`), en el mismo fichero JWK Set que su par
   RSA de cifrado. No se usa la clave de cifrado para firmar.
   - Firma la clave de firma más nueva del fichero; todas verifican, para poder rotarla.
   - No se publica en `GET /gateway/keys`: solo el gateway valida sus tokens, y los clientes cifran con la última
     clave de esa lista.
   - El gateway no arranca sin al menos una clave de cada tipo.

7. **Cambio de golpe** (decisión de Jeremi): desde este despliegue, las rutas de la API solo aceptan tokens de acceso.
   El token de cada petición deja de valer. El motor, el operador (`ops.engine_keys`) y el cliente de referencia
   (`ops.client`) cambian en el mismo PR, y el gateway y el motor se despliegan juntos.

8. **El motor guarda su token por cliente** hasta un minuto antes de que caduque, y si el gateway lo rechaza (`401`),
   pide otro una vez.

9. **La prueba de una clave nueva** (`POST /keys`) sigue atada al `jti` del token de la petición: ahora el del token
   de acceso.

## Alternativas consideradas

**El token de acceso:**

| Opción | Pros | Contras |
|---|---|---|
| **JWT firmado por el gateway** | Cada petición se valida sin ir a la base | Revocar tarda lo que dura el token |
| Opaco, guardado en la base | Se revoca al instante | Cada petición vuelve a consultar Atlas |

**Cuánto dura:** 5 minutos revoca antes, con el doble de peticiones al endpoint; 60, lo habitual en APIs públicas,
deja hasta una hora a una clave robada y ya revocada. **10 minutos.**

**La transición:** que convivieran los dos tokens no rompía nada el día del cambio, pero dejaba dos caminos de entrada
durante semanas. **Cambio de golpe**, con todo desplegado a la vez.

**La aserción:** solo firmada (JWS) es lo que dice RFC 7523 y lo que entiende cualquier librería OAuth. Firmada y
cifrada es como hoy: solo el gateway la lee. **Firmada y cifrada.**

**Atar el token a una clave del cliente** (DPoP, RFC 9449): un token robado no serviría sin la clave, pero el cliente
firma de nuevo en cada petición, que es justo lo que se quita. Queda para cuando haya clientes externos.

## Consecuencias

- **Para un cliente:** pide un token, lo usa durante 10 minutos y pide otro. La aserción la construye con una librería
  JOSE, o con `ops.client`.
- **Cada petición deja de escribir en Atlas:** solo lo hace la petición de un token.
- **Un token de acceso robado vale hasta que caduca**, y en cualquier petición: es un token de portador. Solo viaja por
  TLS, y no queda en el log.
- **Revocar una clave tarda hasta 10 minutos** en cerrar el paso a quien ya tenía un token. Si hace falta cortar al
  instante, se rota la clave de firma del gateway: todos los tokens dejan de valer.
- **No hay límite de tasa** en `/oauth/token`, como en el resto (RS10). *Resuelto el 2026-10-28 por la [ADR-0028](0028-limite-de-peticiones-en-oauth-token.md), en el
  borde y en la aplicación.*
- **El contrato OpenAPI** declara el esquema `oauth2` con el flujo `clientCredentials`.

## Condiciones de revisión

- Llegan clientes externos: DPoP (RFC 9449) y un límite de tasa en `/oauth/token`. El límite llegó con la [ADR-0028](0028-limite-de-peticiones-en-oauth-token.md);
  DPoP, con la [ADR-0033](0033-dpop-en-los-tokens-de-acceso.md).
- Hace falta revocar al instante: tokens opacos o una lista de revocación.
- Un cliente pide un estándar distinto, como `tls_client_auth` (RFC 8705).
