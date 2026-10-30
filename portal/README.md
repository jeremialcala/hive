# hive-portal

* **Estado:** review
* **Fecha:** 2026-10-30
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 03-implementation
* **Versión:** 0.2.0

El BFF de la PWA de Hive (HIG-93; [ADR-0029](../docs/00-project/adr/0029-autenticacion-de-personas.md), punto 5) en
`https://hive-app.higerotech.com`.

- **El inicio de sesión:** las personas de un cliente entran con Zitadel. El portal hace el flujo OIDC como cliente
  confidencial: Authorization Code con PKCE, `state` y `nonce`.
- **El token de Hive:** el portal cambia el ID token de la persona por un token de Hive en `hive-api` (RFC 8693) y
  reenvía con él las peticiones de la PWA.
- **El navegador nunca recibe un token:** solo tiene una cookie.

## Rutas

| Método | Ruta | Qué hace |
|---|---|---|
| `GET` | `/auth/login` | Empieza el inicio de sesión. `?next=/ruta` dice adónde volver; solo vale una ruta del propio portal. `?client=<uuid>`, para una persona de más de un cliente. |
| `GET` | `/auth/callback` | La respuesta de Zitadel: comprueba el ID token, lo cambia por un token de Hive y abre la sesión. |
| `GET` | `/auth/session` | Si hay sesión, de qué cliente y con qué rol. Nunca un token. |
| `POST` | `/auth/logout` | Cierra la sesión y dice adónde ir para cerrar también la de Zitadel. |
| cualquiera | `/api/<ruta>` | La petición de la PWA a `hive-api` como la persona: `GET /api/people` es `GET /people`. `/api/oauth/…` no se alcanza. |
| `GET` | `/` | La PWA (HIG-87): sus pantallas, más abajo. |
| `GET` | `/health` | Si el proceso está vivo. |

## La PWA

El área del cliente (HIG-87), en `src/static/`. No hay paso de build: React 18 y [htm](https://github.com/developit/htm)
van como ficheros UMD y la aplicación son módulos ES. Todo sale del propio portal, así que la CSP sigue siendo
`'self'` ([ADR-0027](../docs/00-project/adr/0027-el-mapa-de-estilo-de-hive.md)).

Todas las pantallas comparten el esqueleto del diseño (HIG-126, `src/static/shell.js`):

- **La barra lateral, de 240 px,** con ocho secciones: Visión general, Bots, Contactos, Plantillas, Personas,
  Credenciales, Integraciones y Auditoría. Abajo va la tarjeta del cliente, con su nombre (`GET /client`), su
  `client_id`, el rol, «Salir» y el tema.
- **En un teléfono** (48 rem o menos), la barra lateral deja paso a una barra arriba y a la `TabBar` del sistema de
  diseño abajo: Inicio, Bots, Contactos y Más. «Más» (`#/mas`) tiene el resto de secciones, el tema y la salida.
- **La cabecera de cada página** lleva la ruta (breadcrumb), el cliente como eyebrow, el título y como mucho una acción
  primaria en ámbar. Al pie va el cURL de su llamada.
- **El tema** es oscuro por defecto. El claro se elige en la tarjeta del cliente, va en `data-theme` de `<html>` y este
  navegador lo recuerda (`localStorage`, `hive-theme`).
- **Las secciones que darían `403`** al `cliente-analista` (Personas, Credenciales y Auditoría) no se le muestran, y su
  dirección escrita a mano le lleva a Bots. Quien decide sigue siendo `hive-api`.
- **Las secciones que la PWA aún no tiene** lo dicen, con la llamada a la API que las sustituye mientras tanto, si existe.

| Pantalla | Ruta | Qué hace |
|---|---|---|
| Bots | `#/`, `#/bots` | Los bots del cliente (`GET /bots`). |
| Más | `#/mas` | En el teléfono: las secciones que no caben en la `TabBar`. |
| Ficha del bot · Identidad | `#/bots/<bot>/identidad` | La cabecera del bot (estado REG/ACT/ERR, `bot_id`, versión) y su persona. El `cliente-admin` la cambia (`PUT /chat/{bot_id}/persona`); guardar crea la versión siguiente. Los errores de RS11 salen en su campo. |
| Credenciales de Meta | `#/bots/<bot>/credenciales` | Qué tokens están cargados, nunca su valor: solo se cargan o se reemplazan (`PUT /chat/{bot_id}/credentials`). El `verifyToken` y la URL del webhook, para el `cliente-admin`. |
| Cerebro e intents | `#/bots/<bot>/cerebro` | Las reglas (añadir, editar y borrar) y los intents con el destino de cada acción, en un `SlideOver` con los destinos permitidos a la vista (RS16). |
| Versiones | `#/bots/<bot>/versiones` | Las versiones, con la actual en ámbar. Al elegir una, lo que cambiaría al volver a ella y, para el `cliente-admin`, «Volver a vN». Un `422` dice qué ya no cumple las reglas de hoy. |
| Plantillas | `#/bots/<bot>/plantillas` | Las plantillas del bot, con su estado de Meta y el motivo de un rechazo. «Sincronizar con Meta» trae la lista de Meta (`?sync=true`). El `cliente-admin` crea, edita y borra. El editor tiene vista previa con los ejemplos y comprueba lo que Meta comprobaría. |
| Contactos | `#/bots/<bot>/contactos` | Los contactos del bot, sin su número. |
| Contacto | `#/bots/<bot>/contactos/<contacto>` | La conversación, la ventana de 24 horas con lo que le queda y el consentimiento. Desde aquí se envía. |

- **Enviar:** el `cliente-admin` manda texto con la ventana abierta. Cualquiera de los dos roles manda una plantilla
  aprobada, con sus valores y su vista previa. Cada envío lleva su `Idempotency-Key`.
- **Qué salió:** la PWA consulta el pedido cada 2 segundos, hasta 90
  (`GET /chat/{bot_id}/contacts/{contact_id}/messages/{request_id}`). Dice si Meta lo aceptó o, si lo rechazó, el
  motivo en palabras y el código de Meta. Pasados los 90 segundos, avisa de que sigue en curso.
- **El rol:** sale de la sesión. Un `cliente-analista` no ve ningún botón que solo le daría un `403`. Quien decide es
  `hive-api`, no la PWA.
- **Cada pantalla** lleva el cURL de la llamada que hace, contra `https://hive-api.higerotech.com` y con DPoP.

| Directorio | Qué es | De dónde |
|---|---|---|
| `ds/` | `tokens.css`, `bundle.css` y `bundle.js` del sistema de diseño (`window.Hive`) | Copia de `assets/design-system/`. Un test comprueba que son iguales byte a byte. |
| `vendor/` | React y ReactDOM 18.3.1 (`*.production.min.js`), htm 3.1.1 (`htm.umd.js`), con sus licencias | El registro de npm, con el `integrity` sha512 comprobado. |
| `fonts/` | Geist y JetBrains Mono (400, 500 y 700, latín), con sus licencias OFL | `@fontsource` y `geist` del registro de npm. |

Para verla sin Zitadel ni `hive-api`, basta con un servidor que entregue `src/static/` con la CSP del portal y
responda `/auth/session` y `/api/*` con datos de prueba.

## Seguridad

- **La cookie:** `__Host-hive-session`, con `HttpOnly`, `Secure`, `SameSite=Lax` y `Path=/`, sin `Domain`. Lleva un
  identificador al azar. La sesión, con el ID token y el token de Hive, está en el bucket `sesiones` de Bifröst,
  sellada con AES-256-GCM ([ADR-0034](../docs/00-project/adr/0034-sesiones-del-portal-en-bifrost.md)).
- **Contra CSRF:** toda petición que cambia algo (`POST`, `PUT`, `PATCH`, `DELETE`) lleva la cabecera `X-Hive-Portal: 1`,
  y si el navegador manda `Origin`, tiene que ser el del portal. Sin eso, `403`, y no se reenvía nada: otro sitio no
  puede hacer que el navegador mande esa cabecera sin una preconsulta CORS que el portal no acepta.
- **El fin de la sesión:** a los 30 minutos sin uso, a las 12 horas en total, o cuando Hive deja de dar token a la
  persona. Entonces la ruta responde `401 {"authenticated": false, "login": "/auth/login"}`.
- **DPoP:** el portal firma una prueba por petición con un par P-256 que solo vive en su memoria
  ([ADR-0033](../docs/00-project/adr/0033-dpop-en-los-tokens-de-acceso.md)). Tras un reinicio, cada sesión pide un
  token nuevo con su ID token, la primera vez que hace falta.
- **Las cabeceras:** CSP sin nada de fuera (`default-src 'self'`, `frame-ancestors 'none'`), HSTS, `nosniff`,
  `Referrer-Policy: no-referrer` y `Cache-Control: no-store` en todo lo que no es la PWA.
- **Los ficheros de la PWA llevan `Cache-Control: no-cache`.** Cloudflare y el navegador los guardan, pero los vuelven
  a pedir cada vez con su `etag`. Si no cambiaron, la respuesta es un `304` sin cuerpo; tras un despliegue, llega el
  nuevo enseguida. Sin esa cabecera, Cloudflare los guardaba 4 horas, y podía mezclar módulos de dos versiones.
- **Lo que pasa a `hive-api`:** solo `Accept`, `Content-Type` e `Idempotency-Key` de la petición. Ni la cookie, ni un
  `Authorization` del navegador. De la respuesta vuelven el cuerpo, `Content-Type` y `Retry-After`, nunca un
  `Set-Cookie`.

## Configuración

| Variable | Qué es |
|---|---|
| `PORTAL_URL` | `https://hive-app.higerotech.com`: el origen del portal y la base de sus redirect URI. |
| `IDP_ISSUER` | `https://hive-auth.higerotech.com`. |
| `OIDC_CLIENT_ID` | El Client Id de la app `hive-portal` en Zitadel. |
| `OIDC_CLIENT_SECRET_FILE` | El fichero con su secreto. |
| `HIVE_API_URL` | `http://hive-api:5002`, dentro de midgard. Tiene que estar en `GATEWAY_URLS` de `hive-api`. |
| `SESSION_KEY_FILE` | 32 bytes al azar, en base64, con los que se sellan las sesiones. |
| `NATS_URL`, `NATS_USER`, `NATS_PASSWORD`, `NATS_CA`, `NATS_INBOX_PREFIX` | Bifröst, tal como lo escribió en `credenciales/portal.env`. Sin `NATS_URL`, solo en desarrollo, las sesiones viven en memoria. |

La puesta en marcha en midgard está en la
[guía de autenticación](../docs/03-implementation/autenticacion.md#el-portal-hive-portal).

## Desarrollo

```sh
cd portal
uv venv --python 3.12 && uv pip sync --require-hashes requirements-dev.lock
pylint --rcfile=pyproject.toml src
python -m pytest
# Con un NATS como el de Bifröst, para tests/integration:
docker run --rm -p 127.0.0.1:24222:4222 -v "$PWD/tests/integration/nats-test.conf:/etc/nats/test.conf:ro" \
    nats:2.14.8-alpine -c /etc/nats/test.conf
NATS_TEST_ADDR=127.0.0.1:24222 python -m pytest tests/integration
```

Las pruebas corren contra un Zitadel y un `hive-api` falsos, que son servidores HTTP locales (`tests/conftest.py`).
