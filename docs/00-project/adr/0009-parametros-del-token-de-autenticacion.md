# ADR-0009: Parámetros del token de autenticación

* **Estado:** accepted; superseded en parte por [ADR-0025](0025-oauth2-client-credentials-con-private-key-jwt.md): sus parámetros son los de la aserción de `POST /oauth/token`, que además lleva `sub`
* **Fecha:** 2026-10-12
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 02-design
* **Versión:** 0.1.0
* **ID:** ADR-0009
* **Supersede:** —
* **Controles OWASP afectados:** A07 (autenticación), A04 (algoritmos y claves), A01 (de quién es cada petición)

Aceptada el 2026-10-12. Concreta lo que [ADR-0005](0005-jwt-firmado-por-el-cliente-dentro-del-jwe.md)
dejó abierto.

## Contexto

Está decidido que el token es un JWT firmado por el cliente y metido dentro de un JWE cifrado para el gateway.
Faltan los parámetros: qué algoritmos se aceptan, qué debe decir el JWT, cuánto dura y cómo se gestionan las
claves. De ellos depende que el token no se pueda fabricar ni reenviar (AB16, RS02).

## Decisión

**Transporte.** El token viaja en la cabecera `Authorization: Bearer <JWE compacto>`. Un token por petición.

**Algoritmos, en lista cerrada.** Uno por capa; cualquier otro se rechaza, incluido `none`.

| Capa | Algoritmo | Clave |
|---|---|---|
| Firma del JWT | `ES256` | Par P-256 del cliente. El gateway registra la parte pública |
| Cifrado de la clave del JWE | `RSA-OAEP-256` | Par RSA del gateway. El cliente recibe la parte pública |
| Cifrado del contenido del JWE | `A256CBC-HS512` | Clave de un solo uso generada por el cliente |

Los dos algoritmos del JWE son los que el código ya usa.

**Contenido obligatorio del JWT.**

| Campo | Valor | Para qué |
|---|---|---|
| `iss` | Identificador del cliente | Elegir la clave pública con la que verificar |
| `aud` | Identificador del gateway | Que un token emitido para otro servicio no valga aquí |
| `iat`, `exp` | `exp` como máximo 60 segundos después de `iat` | Caducidad corta |
| `jti` | Identificador único del token | Un solo uso |

Las cabeceras llevan `kid`: el de la clave del cliente en el JWT y el de la clave del gateway en el JWE. El JWE
lleva además `cty: JWT`.

**Validación en el gateway, en este orden.** Descifrar el JWE; leer `iss` y `kid`; buscar la clave pública de ese
cliente; verificar la firma; comprobar `aud`, `exp` e `iat` con una tolerancia de reloj de 30 segundos; rechazar
un `jti` ya visto. Los `jti` vistos se guardan hasta su caducidad en una colección con borrado automático.

**Autorización.** Lo que un cliente puede hacer no sale del token, sino de su registro: el token solo dice quién
es. El motor de respuestas es un cliente de servicio con alcance de solo lectura sobre la configuración de los
bots.

> **Precisado el 2026-10-13 por [ADR-0016](0016-credencial-del-motor-por-cliente.md):** el motor no tiene una
> credencial para todos los bots, sino una clave por cliente, que solo alcanza los bots de ese cliente.

**Claves.** Un cliente puede tener dos claves públicas activas a la vez, para rotar sin corte. Revocar una es
borrarla del registro. El gateway rota su par con el mismo solapamiento, y los clientes lo distinguen por `kid`.

## Alternativas consideradas

| Opción | Pros | Contras | Riesgo de seguridad |
|---|---|---|---|
| **Un token por petición, de 60 segundos y un solo uso** | Un token capturado no sirve; no hay sesión que robar | Una firma por petición; hay que guardar los `jti` vistos | Bajo |
| Token de sesión de varias horas | Menos trabajo criptográfico | Un token robado vale durante horas | Medio |
| TLS mutuo | Autentica en el transporte, sin tokens | El túnel de Cloudflare termina el TLS: el gateway no vería el certificado del cliente | No encaja con el despliegue |
| Firma `RS256` o `PS256` | Mismo tipo de clave que el JWE | Claves y tokens más grandes | Equivalente |

## Consecuencias

- Positivas: el gateway no guarda secretos de los clientes para autenticar, y cada petición es atribuible a un
  cliente.
- Negativas / deuda asumida: hay que construir el registro de clientes, la verificación y el almacén de `jti`. El
  cliente necesita una librería JOSE y un reloj razonablemente en hora.
- Impacto en threat model: cubre la fabricación y el reenvío de tokens (T8). La clave privada RSA del gateway pasa
  a ser un secreto Restringido (T15).
