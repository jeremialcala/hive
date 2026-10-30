# ADR-0016: El motor de respuestas tiene una credencial por cliente

* **Estado:** accepted
* **Fecha:** 2026-10-13
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 02-design
* **Versión:** 0.1.0
* **ID:** ADR-0016
* **Supersede:** — (corrige el alcance del cliente de servicio en [ADR-0009](0009-parametros-del-token-de-autenticacion.md) y [ADR-0012](0012-aislamiento-por-cliente-en-la-capa-de-datos.md))
* **Controles OWASP afectados:** A01 (qué alcanza el motor), A07 (cómo se autentica), A04 (quién recibe los tokens de Meta), A09 (rastro de cada entrega)

Aceptada el 2026-10-13.

> **Ampliada por [ADR-0022](0022-una-credencial-por-worker.md)**, aceptada el 2026-10-18: una clave por cliente y por worker, cada una con sus rutas.

## Contexto

El motor de respuestas necesita del gateway la configuración de cada bot y sus tokens de Meta, y los pide por
`bot_id` ([ADR-0003](0003-el-bot-id-es-la-llave-de-la-configuracion.md)). Se autentica como cualquier cliente, con
un JWT firmado dentro de un JWE ([ADR-0004](0004-firma-en-el-webhook-jwe-en-el-resto.md),
[ADR-0005](0005-jwt-firmado-por-el-cliente-dentro-del-jwe.md)).

ADR-0009 y ADR-0012 preveían un solo cliente de servicio, con lectura de la configuración de cualquier bot. Eso deja
una credencial que alcanza los tokens de Meta de todos los clientes: es la amenaza T12 del
[threat model](../../02-design/threat-model.md) y el escenario AB15 del PRD.

## Decisión

**El motor tiene una clave por cliente, y con ella solo alcanza los bots de ese cliente.**

1. **Una clave por cliente.** El motor genera un par de claves para cada cliente al que sirve. El operador registra
   la parte pública bajo ese cliente, como clave de servicio. Admite dos a la vez, para rotar sin corte.
2. **Se autentica igual que un cliente.** Firma con esa clave un token cuyo emisor es el cliente por el que actúa.
   El formato y los plazos son los de ADR-0009.
3. **Quién firma lo dice la lista en la que está la clave.** Si está entre las claves de firma del cliente, firma
   el cliente; si está entre sus claves de servicio, firma el motor. Una clave no puede estar en las dos.
4. **El motor alcanza cuatro rutas y ninguna más**: las reglas, las herramientas y los tokens de un bot, y las
   claves públicas del gateway. Cualquier otra responde `403`, y una ruta nueva nace cerrada para él hasta que se
   apunte en esa lista.
5. **Los tokens de Meta solo se entregan al motor**, en `GET /chat/{bot_id}/tokens`. El cliente no los recupera
   con su propia clave, ni el operador con la suya. No se entregan el App Secret ni el token de verificación, que
   el motor no usa.
6. **Cada entrega de tokens deja una línea en el log**: el bot, el cliente y la clave con la que se pidieron.
7. **El sobre de la cola dice de qué cliente es el bot.** Lleva `clientId` junto a `botId`, y con él el motor sabe
   cuál de sus claves usar. El contrato pasa a la versión 1.1.0; `schemaVersion` sigue en 1, porque quien no use
   el campo puede ignorarlo.

## Alternativas consideradas

| Opción | Pros | Contras | Riesgo de seguridad |
|---|---|---|---|
| **Una clave del motor por cliente** | Una clave filtrada alcanza a un solo cliente; el acceso se retira cliente a cliente, y la baja de un cliente se lo lleva | El motor custodia tantas claves como clientes; un paso más en cada alta | Acotado a un cliente por clave |
| Una sola credencial para todos los bots (lo previsto) | Una clave que custodiar y nada que hacer en cada alta | Quien la consiga alcanza los tokens de todos | El más alto: es T12 tal cual |
| Una clave por bot | El alcance más corto | Una clave que registrar y rotar por cada bot | El menor; el coste de operarlo crece con cada bot |
| El motor usa la clave del propio cliente | Nada nuevo en el registro | El cliente tendría que entregar su clave privada, y el motor podría hacer todo lo que hace el cliente | Rompe ADR-0005: la clave privada del cliente no sale de él |

## Consecuencias

- Positivas:
  - Una clave del motor filtrada, o un token capturado, alcanza los bots de un cliente y no los de los demás.
  - El acceso del motor a un cliente se revoca borrando su clave, sin tocar a los otros.
  - Lo que el motor puede hacer está escrito en un solo sitio, y es de solo lectura.
  - El cliente ve qué claves del motor alcanzan sus bots.
- Negativas / deuda asumida:
  - **El motor custodia una clave privada por cliente.** Si las guarda todas en el mismo sitio y alguien se lleva
    ese sitio, se las lleva todas: la separación protege frente a la fuga de una clave, no frente a quien
    comprometa el motor entero. Dónde las guarda es decisión de su repositorio.
  - **Un paso más en cada alta.** Hasta que el operador registra la clave del motor para un cliente, el motor no
    puede responder por sus bots.
  - **El contrato de la cola cambia.** Un consumidor que validara de forma estricta contra la versión 1.0.0
    rechazaría el campo nuevo. El motor de hoy lo ignora.
  - **El log crece con cada petición de tokens.** Conviene que el motor los guarde en memoria por poco tiempo.
- Impacto en threat model: T12 y AB15 quedan controlados del lado del gateway. El motor no usa todavía esta
  credencial: es trabajo de su repositorio.

## Condiciones de revisión

- El número de clientes hace inmanejable una clave por cada uno.
- El motor pasa a necesitar algo más que leer: cualquier escritura es otra decisión.

## Abierto

- **Dónde guarda el motor sus claves** y cómo las rota. Andvari es el candidato.
- **Cuánto tiempo guarda el motor en memoria** la configuración y los tokens de un bot.
