# ADR-0022: Cada worker del motor tiene su propia credencial, con sus rutas

* **Estado:** accepted
* **Fecha:** 2026-10-18
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 02-design
* **Versión:** 0.1.0
* **ID:** ADR-0022
* **Supersede:** — (amplía [ADR-0016](0016-credencial-del-motor-por-cliente.md): la clave sigue siendo por cliente, y
  ahora también por worker)
* **Controles OWASP afectados:** A01 (mínimo privilegio entre servicios), A07 (credenciales de servicio)

Aceptada el 2026-10-18 al aprobar el Gate 1 del refactor. Se construye en I6.

Propuesta como entregable del Gate 1 del refactor ([plan](../plan-topologia-de-workers.md), R5). Decisión D3 del 2026-10-18.

## Contexto

[ADR-0016](0016-credencial-del-motor-por-cliente.md) le dio al motor una clave por cliente, que alcanza cuatro
rutas: las reglas, las herramientas y los tokens de Meta de un bot, y las claves públicas del gateway. Con el motor
partido en workers, esa clave estaría en todos ellos, y la de cualquiera alcanzaría los tokens de Meta, que solo
necesita el notificador. Es el escenario AB21 del PRD.

## Decisión

**Una clave de servicio por cliente y por worker.** El registro dice de qué worker es cada clave, y eso decide qué
rutas alcanza:

| Worker | Rutas |
|---|---|
| Clasificador | Reglas del bot |
| Coordinador | Reglas, herramientas y las 10 últimas vueltas de una conversación (RF15) |
| Enlace | Herramientas |
| Respuesta | Reglas |
| Evaluador | Reglas |
| Notificador | Tokens de Meta del bot |
| Todos | Claves públicas del gateway |

1. **Al registrar la clave**, el operador dice de qué worker es (`POST /clients/{client_id}/service-keys` con el
   campo `worker`). Admite dos por worker y cliente, para rotar sin corte.
2. **Quién firma lo dice el registro, no el token.** Igual que en ADR-0016: el `kid` dice qué clave es, y la clave,
   de qué worker.
3. **Una ruta nueva nace cerrada para todos los workers**, hasta que se apunte en esa tabla.
4. **Las claves de ADR-0016, sin worker**, siguen alcanzando sus cuatro rutas hasta que el motor pase a una por
   worker. Cada uso deja un aviso en el log, y se retiran al terminar el refactor.
5. **En el bus pasa lo mismo:** cada worker tiene su usuario en Bifröst, con sus sujetos (RS20,
   [ADR-0019](0019-nats-jetstream-como-bus.md)).

## Alternativas consideradas

| Opción | Pros | Contras | Riesgo de seguridad |
|---|---|---|---|
| **Una clave por cliente y por worker** | La clave robada de un worker solo alcanza lo que ese worker usa, y de un cliente | Más claves que registrar y rotar: seis por cliente | El menor de los razonables |
| Una clave por cliente para todo el motor (lo de ADR-0016) | Nada que cambiar | La clave de cualquier worker alcanza los tokens de Meta | AB21 tal cual |
| Una clave por worker para todos los clientes | Seis claves en total | Una clave robada alcanza a todos los clientes | Lo que ADR-0016 descartó (T12) |

## Consecuencias

- **El motor custodia seis claves por cliente**, en vez de una. Generarlas y registrarlas tiene que ser un comando,
  no un procedimiento a mano.
- **El registro de clientes cambia:** la clave de servicio lleva su worker, y `GET /keys` lo muestra.
- **Una ruta nueva para el coordinador:** las 10 últimas vueltas de una conversación. Se fija con el historial.
- **El log dice qué worker pidió los tokens**, además del cliente y la clave.

## Construido (I6a, 2026-10-19)

- **Los nombres de los workers en la API** son `classifier`, `coordinator`, `link`, `responder`, `evaluator` y
  `notifier` (`enums.Worker`). La tabla de arriba está en `WORKER_ROUTES` (`src/app.py`), y
  `tests/unit/test_service_credential.py` la escribe otra vez para que no cambie sin querer.
- **El worker es obligatorio al registrar una clave.** Sin él, o con uno que no existe, la API responde `422`. Las
  claves de ADR-0016 ya registradas siguen valiendo, pero no se pueden registrar más.
- **Las claves sin worker alcanzan también las vueltas** (RF15), no solo las cuatro rutas de ADR-0016. La ruta de las
  vueltas llegó con D6b, cuando la única clave del motor no tenía worker. Quitársela ahora le rompería esa ruta
  antes de que el motor pase a una clave por worker. Lo propuse al empezar I6a, y Jeremi dio paso a I6a con ese
  criterio.
- **El límite de dos claves cuenta por worker** dentro de la propia actualización (`$expr` sobre las claves de ese
  worker), así que dos registros a la vez no lo rebasan. Las claves sin worker no cuentan para ninguno.
- **`GET /keys` y `GET /clients`** muestran cada clave del motor como `{"kid", "worker"}`, en lugar de solo su `kid`.
  Una clave de antes sale con `"worker": null`.
- **El log** dice el worker en cada entrega de tokens y de vueltas, y en cada ruta denegada. Cada uso de una clave
  sin worker deja un aviso.
- Pendiente: el usuario de Bifröst por worker (I6c, cuando el motor se parta).

## Construido (I6b, 2026-10-19)

- **El comando:** `python -m ops.engine_keys <client_id>`, y en midgard el servicio `engine-keys` del compose (perfil
  `operations`, solo bajo demanda). Por cada worker crea una clave, guarda la parte privada en Andvari y registra la
  pública por la API, firmando como el operador. Es lo que la consecuencia de arriba pedía: un comando, no un
  procedimiento a mano.
- **Las claves privadas viven en Andvari** (decisión de Jeremi, frente a ficheros con permisos 600), en un motor KV
  versión 2:
  - la ruta es `motor/data/clientes/<client_id>/<worker>`;
  - el KV exige check-and-set;
  - cada worker las lee con su rol `motor-<worker>`, que solo alcanza la suya;
  - el comando entra con el rol `motor-claves`, que crea y lee pero no puede escribir encima, borrar ni listar.

  Lo prepara `motor/instalar.sh` del repositorio `andvari`.
- **El registro pasa por la API** (decisión de Jeremi, frente a escribir en la base como `ops.operator_key`):
  pasa las mismas validaciones y deja en el log el aviso de cada alta.
- **Se puede repetir:**
  - lo que ya está guardado y registrado se deja;
  - lo guardado y no registrado se registra;
  - solo se crea clave para el worker que no tiene ninguna.

  Guarda antes de registrar, así que una clave registrada siempre tiene su parte privada en Andvari.
- **Lo que costó:** sin token raíz vivo, Andvari no se podía administrar, porque OpenBao 2.7.1 cierra por defecto
  los endpoints sin autenticar de `generate-root`. El repositorio `andvari` añade un listener solo dentro del
  contenedor que los abre, y `raiz-temporal.sh`, que genera un token raíz con la clave de recuperación, ejecuta
  una orden y lo revoca.
- Pendiente: rotar la clave de un worker. Pide escribir una versión nueva en el KV, y `motor-claves` no lo permite a
  propósito.

## Condiciones de revisión

- El motor cambia su partición en workers.
- El número de clientes hace inmanejable registrar seis claves por cada uno.
