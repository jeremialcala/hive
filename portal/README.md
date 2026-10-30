# hive-portal

* **Estado:** review
* **Fecha:** 2026-10-30
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 03-implementation
* **Versión:** 0.1.0

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
| `GET` | `/` | La PWA. Hasta HIG-87, una página que dice si hay sesión. |
| `GET` | `/health` | Si el proceso está vivo. |

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
