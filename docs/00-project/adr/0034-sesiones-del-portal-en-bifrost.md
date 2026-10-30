# ADR-0034: Las sesiones del portal viven en Bifröst, selladas por el portal

* **Estado:** accepted
* **Fecha:** 2026-10-30
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 02-design
* **Versión:** 1.0.0
* **ID:** ADR-0034
* **Supersede:** — (decide lo que la [ADR-0029](0029-autenticacion-de-personas.md) dejó abierto en su punto 5)
* **Controles OWASP afectados:** A02 (los tokens de una persona en reposo), A07 (la sesión y su fin), A01 (una
  sesión no se mueve a otra persona)
* **Requisitos:** RS22, AB25 del [PRD](../../01-requirements/mvp-baas-gateway.md)

## Contexto

El portal, `hive-portal`, es el BFF de la PWA (ADR-0029, punto 5; HIG-93). Guarda dos cosas por cada persona con
sesión:

- el **ID token** de Zitadel, que dura 12 horas y con el que pide a `hive-api` un token nuevo cada 10 minutos;
- el **token de Hive** del momento.

El navegador solo tiene una cookie con un identificador al azar. HIG-93 dejó tres opciones para guardar la sesión:
en memoria, en un KV de Bifröst o en otro almacén.

Pesan tres cosas:

- **El reinicio.** La [ADR-0033](0033-dpop-en-los-tokens-de-acceso.md) dice que, si el portal se reinicia, vuelve
  a hacer el intercambio con el ID token de cada sesión. Para eso la sesión tiene que sobrevivir al reinicio. En
  memoria, cada despliegue cerraría la sesión de todas las personas.
- **Lo que hay dentro.** Un ID token vale para conseguir un token de Hive durante 12 horas, sin segundo factor.
  Quien lea el almacén no debe poder usarlo.
- **Lo que ya existe.** Bifröst es el NATS de midgard. Ya guarda en KV los contadores de envíos (`limites`) y el
  contexto del motor. Atlas añadiría un viaje fuera de midgard en cada petición de la PWA, y pondría los tokens en
  otra nube.

## Decisión

Jeremi lo decidió el 2026-10-30.

1. **Un bucket KV de Bifröst, `sesiones`:** 12 horas de vida, una revisión y 16 KB por valor (higerotech/bifrost#7).
   El portal tiene su propio usuario de NATS, `portal`, que solo alcanza ese bucket.
2. **Sellado por el portal antes de salir de él:**
   - **Cifrado:** AES-256-GCM, con una clave de 32 bytes que nace en midgard y se monta como fichero
     (`keys/session-key`).
   - **Atado a su nombre:** cada valor va ligado, como dato asociado, al nombre con el que se guarda. Así una sesión
     no se puede mover al sitio de otra.
3. **Guardada bajo el hash de su identificador** (SHA-256), no bajo el identificador. Desde el bucket no se puede
   rehacer la cookie.
4. **El fin:**
   - **Por inactividad:** a los 30 minutos sin uso.
   - **Por duración:** a las 12 horas desde el inicio de sesión, pase lo que pase.
   - **Por el intercambio:** cuando falla, porque la persona fue bloqueada o dada de baja o su cliente dejó de estar
     activo.
   - **Por el bucket:** olvida todo a las 12 horas aunque el portal no lo borre.
   - **Escrituras:** la hora del último uso se escribe como mucho una vez por minuto, porque cada escritura es un
     fsync en el disco de Bifröst.
5. **Los inicios de sesión en curso** (state, nonce, verificador PKCE) van al mismo bucket, sellados del mismo modo.
   - **Duración:** 10 minutos.
   - **Un solo uso.**
   - **Mismo navegador:** van atados a una segunda cookie, `__Host-hive-login`, del navegador que los empezó. Una
     respuesta de Zitadel que llegue a otro navegador no sirve.

## Alternativas consideradas

| Opción | Pros | Contras |
|---|---|---|
| **KV de Bifröst, sellado** (elegida) | Sobrevive al reinicio. No sale de midgard. Sin componentes nuevos | Hay que provisionar el bucket y el usuario, y la clave de sellado es un secreto más |
| En memoria | Nada sensible en reposo; lo más simple | Cada despliegue o caída cierra todas las sesiones, y contradice lo que la ADR-0033 supuso para el reinicio |
| MongoDB (Atlas) | Persistente y ya conectado al gateway | Los tokens en otra nube, y un viaje a Atlas en cada petición de la PWA |
| Sesión dentro de la cookie, cifrada | Sin almacén | El navegador tendría los tokens, aunque cifrados, y una sesión no se puede cerrar desde el servidor |

## Consecuencias

- **Un secreto nuevo en midgard:** `keys/session-key`, del portal. Si se pierde o se rota, todas las sesiones dejan
  de abrirse y las personas vuelven a iniciar sesión. Rotarla es eso, y no rompe nada más.
- **Bifröst pasa a ser necesario para la PWA:** si no responde, nadie entra al área del cliente. Las credenciales de
  máquina y el motor no dependen de esto.
- **El portal corre con un solo worker:** su clave DPoP vive en memoria (ADR-0033). Si un día son varios, cada uno
  tendrá la suya y las sesiones seguirán valiendo, porque cada worker renueva el token con el ID token de la sesión.
- **La caducidad de los ID tokens de Zitadel** tiene que ser de 12 horas al menos. Con menos, la sesión termina
  cuando caduca el ID token, antes de su hora.

## Condiciones de revisión

- Bifröst deja de ser fiable para algo que las personas usan a diario.
- La PWA necesita sesiones de más de 12 horas, o *refresh tokens* de Zitadel.
- El portal pasa a correr con más de un worker o en otra máquina.
