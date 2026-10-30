# ADR-0028: El límite de peticiones de /oauth/token, en el borde y en la aplicación

* **Estado:** accepted
* **Fecha:** 2026-10-28
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 02-design
* **Versión:** 0.2.0
* **ID:** ADR-0028
* **Supersede:** —
* **Controles OWASP afectados:** A04 (diseño: un endpoint caro sin límite), A07 (fallos de autenticación: la fuerza
  bruta y el consumo); cubre en parte RS10, y entero con la enmienda del 2026-10-30 (A06)

Decisiones de Jeremi del 2026-10-28: el límite va en los dos sitios, en Cloudflare y en `hive-api`, y DPoP se
separa en su propia tarea.

## Contexto

`POST /oauth/token` es la puerta de la API de gestión (ADR-0025), y desde la ADR-0024 está publicada en internet.
Cada petición descifra un JWE con RSA. Las que traen una aserción buena escriben además su `jti` en Atlas
(`SeenToken`). Una ráfaga de basura no consigue ningún token, pero gasta CPU de midgard. Una ráfaga de aserciones
buenas, por ejemplo con una clave robada, escribe en Atlas tantas veces como peticiones haga.

La ADR-0025 lo dejó pendiente para cuando llegaran clientes externos (RS10), y el primer cliente real lo es. Hoy los
intentos fallidos se registran, pero nada los frena.

Hay dos hechos que condicionan el diseño:

- **El plan Free de Cloudflare** da una sola regla de límite, que cuenta solo por IP y por ruta, en ventanas de 10
  segundos y con un bloqueo de 10 segundos. No distingue el método, ni el cliente, ni si la petición falló.
- **El motor llama a `hive-api` por la red interna** (`http://hive-api:5002`), no por el túnel, así que el límite del
  borde no le afecta. Cada worker pide un token por cliente unas seis veces por hora.

## Decisión

1. **Una regla en el borde.** Cloudflare corta las ráfagas antes de que lleguen a midgard: en la ruta
   `/oauth/token`, 10 peticiones por IP cada 10 segundos, y después un bloqueo de 10 segundos. Se configura en el
   panel; la regla está en [autenticacion.md](../../03-implementation/autenticacion.md).
2. **Tres límites en `hive-api`** (`src/auth/token_limits.py`), con ventanas deslizantes:

   | Límite | Cuánto | Qué cuenta | Para qué |
   |---|---|---|---|
   | Por origen | 20 peticiones por minuto | Toda petición | Que una sola dirección no acapare el endpoint |
   | Fallos por origen | 10 en 10 minutos | Cada respuesta `400` o `401` | Frenar la fuerza bruta y la basura |
   | Tokens por clave | 30 por hora | Cada token emitido para un `(cliente, kid)` | Acotar lo que hace una clave robada; un cliente pide unos 6 |

3. **Los fallos se cuentan por origen, nunca por cliente.** Quien conozca el identificador de un cliente podría
   bloquearlo enviando aserciones malas en su nombre. El identificador de un cliente no es secreto, pero sí su clave.
4. **El origen es la IP que vio Cloudflare** (`CF-Connecting-IP`), y si no la hay, la del par de la conexión. La
   cabecera es de fiar porque `hive-api` solo se alcanza por el túnel o desde las redes privadas de midgard: su puerto
   se publica en loopback. Una IPv6 cuenta por su /64, lo que suele tener un abonado: si no, una sola máquina parecería
   muchas. Un worker del motor cuenta por la dirección de su contenedor.
5. **Se comprueba antes de leer la petición.** Un origen que se pasa no llega a descifrar nada. El límite por clave se
   comprueba después de validar la aserción: esa aserción queda gastada y el token no se entrega.
6. **La respuesta es `429`, con `Retry-After`** (RFC 6585), `Cache-Control: no-store` y
   `{"error": "temporarily_unavailable"}`. RFC 6749 no define un error para esto en el endpoint de tokens, así que se
   usa el de «temporalmente no disponible». El contrato OpenAPI lo declara.
7. **En memoria, en el proceso.** La API corre un solo worker (`WEB_CONCURRENCY=1`), y reiniciarla pone los contadores
   a cero, lo que se acepta. Para que una ráfaga desde muchas direcciones no crezca sin fin, se guardan como mucho
   10 000 claves: primero se tiran las caducadas y después las más viejas.
8. **El log no escribe la IP**, que es un dato personal (RS07). Dice qué límite saltó y, en el de tokens, el cliente y
   el `kid`.

## Alternativas consideradas

| Opción | Pros | Contras |
|---|---|---|
| **Borde + aplicación** (elegida) | La regla del borde ahorra el viaje a midgard de las ráfagas; la aplicación cuenta lo que el borde no puede: clientes, claves, fallos y ventanas largas | Dos sitios que configurar |
| Solo en Cloudflare | Sin código | Solo IP y 10 segundos: no frena una clave robada ni la fuerza bruta lenta |
| Solo en la aplicación | Un solo sitio | Las ráfagas llegan igual hasta midgard, aunque se rechacen pronto |
| Contadores en Bifröst (KV) o en MongoDB | Sobreviven al reinicio y valen con varios workers | Un viaje por petición, justo en el camino que se quiere abaratar; con un worker no hace falta |
| Bloquear al cliente tras varios fallos | Más simple de explicar | Cualquiera que sepa su identificador puede dejarlo fuera |

## Consecuencias

- **Un cliente que pide un token por cada petición** se queda sin tokens a la trigésima de la hora. La descripción
  del contrato dice que se reutiliza durante sus 10 minutos, como hace `ops.client` y como hace el motor.
- **Varios clientes detrás de una misma IP** (una NAT de empresa) comparten las 20 peticiones por minuto. Para el uso
  previsto, de unas pocas peticiones por hora, sobra.
- **Si `hive-api` pasa a más de un worker,** cada uno lleva sus contadores y el límite efectivo se multiplica. Entonces
  hay que mover los contadores a Bifröst.
- **El resto de rutas sigue sin límite propio** (hasta la enmienda del 2026-10-30). RS10 queda parcial: con un token de 10 minutos, el abuso de las demás
  rutas está acotado por quién puede tener un token. El webhook sigue en el borde y con el tope del contenedor (T9).
- **DPoP**, atar el token a la clave del cliente, sigue pendiente en su propia tarea.

## Enmienda del 2026-10-30: el resto de rutas y el webhook (HIG-139)

Decisiones de Jeremi del 2026-10-30. El mecanismo es el mismo: ventanas deslizantes en memoria, en el proceso, con el
mismo tope de 10 000 claves por ventana. Está en `src/auth/request_limits.py`. `/oauth/token` conserva sus tres
límites y no cuenta en estos. `/health` no tiene límite en ninguno de los dos servicios.

1. **`hive-api`, por credencial: 300 peticiones por minuto.** Cuenta por `(cliente, clave)` o `(cliente, persona)`,
   lo mismo que `limited_as` de los tokens, y se comprueba en `get_principal`, en cuanto el token es bueno. Una página
   de la PWA pide unas pocas, el motor unas cuantas por mensaje y un cliente que recorre sus contactos los pide de 500
   en 500: solo un bucle que se fue de las manos o un token robado se mantiene por encima.
2. **`hive-api`, fallos por origen: 30 respuestas `401` o `403` en 10 minutos.** Quien se pasa espera hasta que el
   más viejo sale de la ventana. Es el triple que en `/oauth/token`, porque un cliente que aprende la API se equivoca
   de ruta o deja caducar un token de vez en cuando.
3. **Los que llaman desde midgard no cuentan por origen.** Una petición sin `CF-Connecting-IP` llegó por las redes
   privadas: es el portal o el motor. Detrás de la dirección del portal van todas las personas. Si contara por
   origen, una persona que pide a propósito rutas que su rol no alcanza dejaría fuera a todas las demás, de todos los
   clientes. Cada una de esas credenciales tiene ya su límite propio.
4. **El webhook: 20 peticiones por minuto y origen sin firma buena.** Cuenta lo que llega sin la firma de Meta o con
   una mala, el `GET` de verificación con un verify token malo y lo que no va firmado y pide un bot que no existe.
   Pasado el tope, el origen recibe `429` **sin que se lea su cuerpo**: el límite va en un middleware por fuera de
   `BodySizeLimit`, que es quien lo lee.
5. **Lo que trae una firma con la forma de la de Meta pasa siempre.** Meta reintenta lo que no recibe `200`, y
   cortarlo perdería mensajes. Saber si esa firma es buena obliga a leer el cuerpo, así que se lee y se comprueba
   también desde un origen que se pasó. Si resulta mala, cuenta como otro fallo.
6. **La respuesta es la de `/oauth/token`:** `429`, `Retry-After`, `Cache-Control: no-store` y
   `{"error": "temporarily_unavailable"}`. El log dice qué límite saltó y, en el de credencial, el cliente y la clave o
   la persona, nunca la IP.
7. **El webhook corre dos workers** (`WEB_CONCURRENCY=2`) y cada uno lleva sus contadores. El núcleo reparte las
   conexiones de un origen entre los dos, así que el corte real cae entre 20 y 40 fallos por minuto. Se acepta: el
   tope está para cortar barridos y ráfagas, no para contar exacto. `hive-api` sigue con un solo worker y sus números
   son exactos.

**Consecuencias de la enmienda:**

- **Un origen que se pasa en el webhook no corta a Meta,** que firma todo lo que manda. Lo que sí queda fuera durante
  ese minuto es la verificación de un webhook desde ese origen.
- **Una firma falsa con la forma correcta sigue costando una lectura del cuerpo** y la apertura de los secretos del
  bot. La acotan el límite del borde, el tope de 3 MiB y el contenedor (T9).
- **RS10 pasa a «Construido sin desplegar».**
- **La API documenta el límite por credencial y el de fallos** en su descripción del contrato OpenAPI.

## Condiciones de revisión

- `hive-api` corre con más de un worker o en más de una máquina.
- El webhook corre con más de dos workers, o un barrido se cuela entre los dos: sus contadores pasan a Bifröst.
- Un cliente legítimo choca con un límite: se ajusta el número, no se quita.
- El plan de Cloudflare cambia: con más reglas, se puede limitar en el borde también por ruta y por método.
