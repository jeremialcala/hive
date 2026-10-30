# ADR-0028: El límite de peticiones de /oauth/token, en el borde y en la aplicación

* **Estado:** accepted
* **Fecha:** 2026-10-28
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 02-design
* **Versión:** 0.1.0
* **ID:** ADR-0028
* **Supersede:** —
* **Controles OWASP afectados:** A04 (diseño: un endpoint caro sin límite), A07 (fallos de autenticación: la fuerza
  bruta y el consumo); cubre en parte RS10

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
- **El resto de rutas sigue sin límite propio.** RS10 queda parcial: con un token de 10 minutos, el abuso de las demás
  rutas está acotado por quién puede tener un token. El webhook sigue en el borde y con el tope del contenedor (T9).
- **DPoP**, atar el token a la clave del cliente, sigue pendiente en su propia tarea.

## Condiciones de revisión

- `hive-api` corre con más de un worker o en más de una máquina.
- Un cliente legítimo choca con un límite: se ajusta el número, no se quita.
- El plan de Cloudflare cambia: con más reglas, se puede limitar en el borde también por ruta y por método.
