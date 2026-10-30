# ADR-0005: El token de autenticación es un JWT firmado por el cliente, dentro de un JWE

* **Estado:** accepted; superseded en parte por [ADR-0025](0025-oauth2-client-credentials-con-private-key-jwt.md): el token se presenta una vez, a `POST /oauth/token`, para pedir un token de acceso
* **Fecha:** 2026-10-12
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 00-project
* **Versión:** 0.1.0
* **ID:** ADR-0005
* **Supersede:** —
* **Controles OWASP afectados:** A07 (autenticación), A04 (algoritmos y claves), A01 (el token identifica al cliente)

## Contexto

[ADR-0004](0004-firma-en-el-webhook-jwe-en-el-resto.md) decidió que todo endpoint que no sea el webhook se
autentica con JWE, y dejó abierto cómo demuestra ese JWE quién lo envía. Un JWE cifrado con la clave pública del
gateway lo puede fabricar cualquiera que tenga esa clave: garantiza que solo el gateway lo lee, no que venga de un
cliente concreto.

## Decisión

**El token de autenticación es un JWT firmado por el cliente y metido dentro de un JWE.**

1. El cliente firma un JWT con su clave privada.
2. Ese JWT firmado se cifra como JWE con la clave pública del gateway.
3. El gateway descifra el JWE con su clave privada y verifica la firma del JWT con la clave pública que el cliente
   registró.

La firma demuestra quién envía; el cifrado impide que un tercero lea el token en tránsito. El orden es firmar y
después cifrar.

## Alternativas consideradas

| Opción | Pros | Contras | Riesgo de seguridad |
|---|---|---|---|
| **JWT firmado dentro del JWE** | El gateway solo guarda claves públicas de los clientes: una fuga de su base no permite suplantar a nadie. Cada parte custodia su propia clave privada | Dos operaciones criptográficas por petición; el cliente necesita una librería JOSE y gestionar su par de claves | Bajo si se fijan los algoritmos y se valida caducidad y un solo uso |
| Clave simétrica por cliente | Más simple y más rápido; el cifrado autenticado ya demuestra posesión | El gateway guarda un secreto por cliente: quien lo robe suplanta a ese cliente | Medio: el secreto existe en dos sitios |
| JWE asimétrico sin firma | Lo más parecido a lo que hay en el código | No autentica al emisor | Alto: no sirve como autenticación |

## Consecuencias

- Positivas: para autenticar, el gateway no guarda ningún secreto de los clientes. El registro de un cliente
  contiene su clave pública de firma, y con ella se le identifica en cada petición.
- Negativas / deuda asumida:
  - Hay que construir el registro de clientes con su clave pública, y el par de claves de cifrado del gateway. El
    material de clave que hay hoy en el código es por evento, no por cliente, y está desactivado.
  - El motor de respuestas, como cliente de servicio, también firma su JWT con una clave propia.
  - La clave de firma del cliente autentica, pero no resuelve el cifrado de los números de teléfono: el gateway y
    el motor necesitan leer el número para localizar al contacto y responderle, y eso no se puede hacer con una
    clave cuya parte privada solo tiene el cliente. La custodia de la clave de cifrado de datos sigue abierta.
- Impacto en threat model: la clave privada de cifrado del gateway pasa a ser un secreto Restringido. Quien la
  obtenga lee los tokens en tránsito, aunque no puede fabricarlos.

## Abierto

Parámetros de diseño del Gate 1. Ninguno está decidido aquí:

- Algoritmos de firma y de cifrado permitidos, fijados en una lista cerrada. El código usa hoy `RSA-OAEP-256` con
  `A256CBC-HS512` para el JWE; el algoritmo de firma está por elegir.
- Contenido obligatorio del JWT: quién lo emite, a quién va dirigido, caducidad corta e identificador de un solo
  uso, para que un token capturado no se pueda reenviar (AB16 del [PRD](../../01-requirements/mvp-baas-gateway.md)).
- Cómo registra y rota un cliente su clave pública, y cómo publica y rota el gateway la suya.
