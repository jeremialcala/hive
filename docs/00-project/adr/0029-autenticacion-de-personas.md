# ADR-0029: Las personas de un cliente entran con Zitadel, y Hive decide qué pueden hacer

* **Estado:** accepted
* **Fecha:** 2026-10-28
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 02-design
* **Versión:** 0.1.0
* **ID:** ADR-0029
* **Supersede:** —
* **Controles OWASP afectados:** A07 (autenticación de personas, MFA obligatoria), A01 (el rol y el cliente salen de
  Hive, no del IdP), A02 (los tokens de la sesión no llegan al navegador), A05 (un componente nuevo en el borde)

Decisiones de Jeremi del 2026-10-28, sobre las D1, D2, D3 y D5 de la
[matriz rol × acción](../../02-design/matriz-rol-accion.md):

- el IdP es Zitadel, alojado en midgard;
- la identidad llega a `hive-api` por un intercambio en `/oauth/token`;
- la MFA es obligatoria para todas las personas;
- la PWA guarda la sesión en un BFF, con una cookie.

## Contexto

Hasta hoy, cada cliente de Hive es una sola identidad de máquina: una clave que pide tokens por OAuth 2.0
`client_credentials` ([ADR-0025](0025-oauth2-client-credentials-con-private-key-jwt.md)). El área del cliente de la
ADR-0027 necesita personas: un `cliente-admin` que configura y un `cliente-analista` que lee, califica y envía
plantillas. Esos son los dos roles de un catálogo fijo (D1, D2). Los alcances de cada rol están en la matriz rol ×
acción v1.0.0.

Tres condiciones pesan en la decisión:

- **Los datos.** El correo de una persona es un dato personal, y Hive es el encargado del tratamiento (GDPR): mientras
  menos terceros lo vean, mejor.
- **El lugar.** Hive opera desde Venezuela, y la API de Anthropic ya rechazó a midgard por su país. Un IdP SaaS de
  EE. UU. puede hacer lo mismo, y sin IdP no entra nadie.
- **La máquina.** midgard tiene 7,6 GB de RAM y 4 núcleos, compartidos con otros proyectos. El 2026-10-28 quedaban
  unos 4 GB disponibles.

## Decisión

1. **Zitadel, alojado en midgard,** con su propio PostgreSQL. Sus contenedores son `hive-idp` y `hive-idp-db`, en una
   red propia, y el túnel los publica (ADR-0024). Zitadel autentica a la persona: su contraseña, su MFA, sus passkeys,
   la verificación del correo y la recuperación de la cuenta. Ni el rol ni el cliente viven en Zitadel.
2. **Hive guarda a sus personas.** Hay una entidad nueva, `ClientUser`, con estos campos:
   - el cliente al que pertenece;
   - el `iss` y el `sub` de la persona en Zitadel;
   - el correo, cifrado con la clave de datos del cliente y con un índice ciego para buscarlo, como el número de un
     contacto;
   - el rol, `cliente-admin` o `cliente-analista`;
   - el estado: activa, bloqueada o dada de baja;
   - quién la invitó y cuándo.

   Las personas del operador son `ClientUser` del cliente del operador.
3. **El intercambio de tokens** (RFC 8693) en `POST /oauth/token`, con
   `grant_type=urn:ietf:params:oauth:grant-type:token-exchange`:
   1. El BFF presenta el ID token de Zitadel como `subject_token`.
   2. Hive comprueba su firma con las claves de Zitadel, su `iss`, su `aud` (el BFF), su caducidad, y que su `amr`
      diga que hubo MFA.
   3. Hive busca a la persona por `iss` y `sub`, y exige que ella y su cliente estén activos.
   4. Hive emite su propio token de acceso (`at+jwt`, 10 minutos) con el cliente, la persona y el rol.

   `hive-api` sigue viendo un único formato de token, y el `Principal` gana la persona y el rol.
4. **La credencial de máquina conserva los permisos de `cliente-admin`** (D3), para las integraciones. Su token dice
   que es una máquina, no una persona.
5. **Un BFF para la PWA:** `hive-portal`.
   - Sirve la PWA y hace el flujo OIDC con Zitadel (Authorization Code con PKCE) como cliente confidencial.
   - Guarda los tokens de su lado.
   - El navegador solo tiene una cookie `__Host-` con `HttpOnly`, `Secure` y `SameSite=Lax`.
   - Reenvía las peticiones de la PWA a `hive-api` con el token de Hive, que renueva por intercambio cuando caduca.
   - Las peticiones que cambian algo llevan además una cabecera que solo pone la PWA, contra CSRF.
   - La sesión caduca a los 30 minutos sin uso y a las 12 horas en total.
6. **La MFA es obligatoria para todas las personas,** de los tres roles: passkey o TOTP. La exigen la política de
   Zitadel y, además, Hive, que rechaza el intercambio si el `amr` no la refleja.
7. **Las invitaciones las hace Hive.** El `cliente-admin` invita a alguien por su correo. Hive registra a la persona
   como pendiente y, con una cuenta de servicio de Zitadel, crea su usuario para que Zitadel le mande el correo de
   alta y configure la MFA. Si la persona ya existe en Zitadel, se enlaza la misma: una persona puede pertenecer a
   más de un cliente.
8. **Las bajas y los bloqueos se aplican en Hive.** Una persona bloqueada no consigue token en el siguiente
   intercambio, así que pierde el acceso en 10 minutos como mucho, el mismo plazo que una clave revocada en la
   ADR-0025. El BFF cierra su sesión cuando falla el intercambio.
9. **Cada petición de una persona lleva a esa persona en el `Principal`.** Así la auditoría de su cliente, la D5,
   puede atribuir cada acción a quien la hizo. La auditoría tiene su propio requisito.

## Alternativas consideradas

| Opción | Pros | Contras |
|---|---|---|
| **Zitadel en midgard** (elegida) | Escrito en Go; necesita 512 MB–1 GB más su PostgreSQL ([docs](https://zitadel.com/docs/guides/deploy/overview), [requisitos](https://help.zitadel.com/what-are-zitadel-minimum-self-hosted-specs)). OIDC, MFA, passkeys y organizaciones. Los datos no salen de casa, y no depende de que un SaaS atienda desde Venezuela | La operación, las actualizaciones y los respaldos son nuestros. Si cae midgard, cae también el inicio de sesión, igual que el resto de Hive |
| Keycloak en midgard | Es el más maduro y el más conocido | Necesita 1,25–2 GB ([Red Hat, Keycloak 26](https://docs.redhat.com/es/documentation/red_hat_build_of_keycloak/26.0/html/high_availability_guide/concepts-memory-and-cpu-sizing-)); con lo que ocupan los otros proyectos de midgard, va justo |
| Auth0, gestionado | Sin operación propia | El plan gratis llega a 25 000 MAU, pero limita la MFA y los roles; el B2B parte de unos 150 USD al mes. Son cifras de agregadores ([1](https://ssojet.com/blog/auth0-pricing-growth-penalty), [2](https://blog.logto.io/es/auth0-pricing-explain)), sin confirmar en auth0.com. Los correos van a Okta, y hay que comprobar que atiende desde Venezuela |
| Un inicio de sesión propio en `hive-api` | Ningún componente nuevo | Contraseñas, MFA, recuperación y passkeys hechos a mano: el lugar donde más se equivoca uno solo |
| `hive-api` valida el token de Zitadel en cada ruta | No hay intercambio | Dos formatos de token en todas las rutas y una lectura del rol en cada petición. Además, el rol quedaría cerca del IdP |
| La PWA como SPA con PKCE | No hace falta un backend propio | El token queda en el navegador, al alcance de un XSS. La guía de OAuth para aplicaciones de navegador recomienda un BFF cuando hay datos sensibles |
| MFA solo para `cliente-admin` y el operador | Menos fricción | El analista ve contactos y envía plantillas: también toca datos personales |

La matriz de placement completa queda pendiente: los precios de los IdP gestionados no se pudieron confirmar en sus
páginas oficiales el 2026-10-28. La decisión no depende de ellos, sino de la residencia de los datos, de la
disponibilidad desde Venezuela y de la memoria de midgard.

## Consecuencias

- **Tres componentes nuevos en midgard:** `hive-idp`, `hive-idp-db` y `hive-portal`. Calculo entre 1,5 y 2 GB en
  total; hay que medirlo al desplegar. El PostgreSQL de Zitadel entra en los respaldos nocturnos.
- **Dos nombres más en el túnel:** el de Zitadel y el del portal. Se proponen `hive-auth.higerotech.com` y
  `hive-app.higerotech.com`, y los decide Jeremi al desplegar.
- **Zitadel necesita un SMTP** para los correos de alta y de recuperación.
- **`/oauth/token` admite dos grants:** `client_credentials`, para las máquinas, y `token-exchange`, para el BFF. El
  límite de la ADR-0028 cuenta los dos.
- **Si Zitadel cae,** las personas no pueden entrar, pero las credenciales de máquina y el motor siguen funcionando.
- **Las versiones** de Zitadel y de PostgreSQL se fijan por digest, y con más de dos semanas, como el resto.
- **La matriz rol × acción** pasa a construirse sobre esto. La fila «Iniciar y cerrar sesión una persona» deja de
  ser solo una decisión.

## Condiciones de revisión

- midgard se queda sin memoria, o Zitadel exige más de lo previsto.
- Un cliente pide entrar con su propio IdP (SSO empresarial). Zitadel puede federarlo, pero la decisión es otra.
- Se confirman los precios de los IdP gestionados y cambia la cuenta.
- Hive deja de operar desde Venezuela.
