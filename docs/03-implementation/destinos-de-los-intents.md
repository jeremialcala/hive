# Destinos de los intents: adónde puede llamar una acción

* **Estado:** review
* **Fecha:** 2026-10-20
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 03-implementation
* **Versión:** 0.1.0
* **Requisitos:** RS16
* **Amenaza:** T11 (SSRF por intents)
* **Incremento:** I7 del [plan de la topología de workers](../00-project/plan-topologia-de-workers.md) (R6)

Un cliente define, en cada acción de un intent, la llamada a API que el motor hace por él (`apiCall`). La hace el
worker de enlace, desde la red de la plataforma. Si el cliente pudiera apuntarla adonde quisiera, el worker sería un
*confused deputy*: llamaría con sus permisos de red a Andvari, a la API de gestión o al router (T11). Por eso el
gateway solo guarda destinos que no pueden ser la plataforma.

## La regla

La aplica `shared.destinations.check_destination`. Un destino vale si cumple todo esto:

| Condición | Por qué | Rechaza, por ejemplo |
|---|---|---|
| El esquema es `https` | Nada en claro, y nada de `file:`, `gopher:` o `ftp:` | `http://api.example.com/x` |
| Sin usuario ni contraseña | Las credenciales de una llamada son su `authRef`, no la URL | `https://user:pass@api.example.com/` |
| El host y el puerto están escritos, sin `{marcadores}` | Si el modelo eligiera el host, elegiría adónde llamar. Los marcadores solo van en la ruta o en la consulta | `https://{host}/x`, `https://api.example.com:{port}/` |
| Una dirección IP solo si es pública | Ni loopback, ni redes privadas, ni enlace local, ni el espacio compartido de los operadores, ni multicast, ni el servicio de metadatos de la nube; tampoco IPv4 mapeado en IPv6 | `https://127.0.0.1/`, `https://192.168.10.1/`, `https://169.254.169.254/`, `https://[::ffff:127.0.0.1]/` |
| Un nombre, con al menos dos etiquetas y la última no solo de dígitos | Los hosts de las redes de la plataforma son de una etiqueta (`andvari`, `hermod`, `hive-api`). `2130706433` y `0x7f.1` son formas de escribir 127.0.0.1 que algunas bibliotecas resuelven | `https://andvari:8200/`, `https://2130706433/`, `https://0x7f.1/` |
| El nombre no cuelga de un dominio de red local | `localhost`, `local`, `localdomain`, `internal`, `intranet`, `lan`, `home`, `home.arpa`, `corp`, `private` | `https://printer.local/`, `https://api.internal/` |
| Solo ASCII y sin espacios ni saltos de línea | Un nombre internacional va en su forma `xn--`, que no admite caracteres que se confundan con otros. Un salto de línea podría inyectar cabeceras | `https://bücher.example/` |
| 2048 caracteres como mucho | | |

Además, en el resto de `apiCall`:

- **`method`**: `GET`, `POST`, `PUT`, `PATCH` o `DELETE`, en mayúsculas.
- **`authType`**: `none`, `bearer`, `api_key` o `basic`.
- **`timeout`**: de 1 a 30 segundos. Del otro lado hay una persona esperando la respuesta.
- **`headers` no puede fijar `Host`.** Con un `Host` propio, una llamada a una dirección pública podría servirse
  como un nombre de la red de la plataforma.

Un destino rechazado responde `422`, con el campo que falla y por qué, **sin repetir lo que se envió**.

## Dónde se comprueba

1. **Al guardar un intent**, al crearlo o cambiarlo (`POST` y `PUT` de `/chat/{bot_id}/intents`). Un intent que llama
   adonde no puede no se guarda.
2. **Al entregar las herramientas al motor** (`GET /chat/{bot_id}/tools`). Una acción guardada antes de esta regla, o
   cuyo destino haya dejado de valer porque la regla creció, se deja fuera. El modelo no la llega a conocer, así que
   el motor no hace esa llamada. El log dice cuántas quedaron fuera y de qué intent, sin la URL ni el nombre de la
   acción, que son lo que escribió el cliente.

## Lo que tiene que hacer el worker de enlace

Al guardar, el gateway no resuelve el nombre, a propósito. Un nombre público puede resolver a 127.0.0.1 (como
`127.0.0.1.nip.io`), o empezar a hacerlo después de guardarse (*DNS rebinding*). Comprobarlo al guardar daría una
falsa seguridad, y la API dependería de la red para algo que no puede garantizar. **La comprobación que cierra T11 es
la del worker de enlace, en cada llamada:**

1. Aplicar la misma regla a la URL ya con los marcadores sustituidos, con los valores codificados para la URL.
2. Resolver el nombre y comprobar **cada** dirección con la regla de las IP. Si alguna no es pública, no llamar.
3. Conectar a la dirección comprobada y no volver a resolver, con el nombre como SNI y como `Host`.
4. No seguir redirecciones, o comprobar cada una con los pasos 1 a 3.
5. No usar los proxies del entorno (`HTTPS_PROXY`), con el plazo de `timeout` como máximo y un tamaño máximo de
   respuesta.

Mientras el motor no se parta en workers, nada ejecuta todavía estas llamadas (RS16).

## Pruebas

`tests/unit/test_intent_destinations.py`:

- los destinos que valen y los que no, uno por fila de la tabla;
- que un intent con un destino prohibido no se guarda ni se cambia;
- que el resto de `apiCall` se ciñe a lo de arriba;
- que una acción guardada antes de la regla no sale en las herramientas.
