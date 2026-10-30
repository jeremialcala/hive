# Arranque tras un apagón

* **Estado:** approved
* **Fecha:** 2026-10-30
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 05-deployment
* **Versión:** 1.0.0

midgard no tiene UPS. Después de un corte de luz, Hive vuelve solo, sin que nadie lo toque (RNF06, HIG-116). Si
algo no vuelve, Thor avisa.

## Cómo vuelve

1. **Docker arranca con el sistema** (`docker.service` está habilitado).
2. **Docker levanta cada contenedor** que tiene `restart: unless-stopped`. Son todos los de Hive:
   - **El gateway:** `hive-api`, `hive-webhook`, `hive-ingest`, `hive-docs` y `hive-tunel`.
   - **El motor:** `hive-classifier`, `hive-coordinator`, `hive-responder` y `hive-notifier`.
   - **El portal:** `hive-portal`.

   La infraestructura tiene además su unidad de arranque: `bifrost-arranque`, `andvari-arranque` y `hermod-arranque`.
   `tests/unit/test_restart_after_outage.py` falla si un servicio de los compose de producción pierde su política.
3. **No hace falta un orden:** cada pieza espera a la que necesita.
   - **El motor y la ingesta:** reintentan su primera conexión con Bifröst para siempre (`max_reconnect_attempts=-1`),
     así que no se caen si Bifröst aún no está.
   - **`hive-api`, el webhook y el portal:** se conectan a Bifröst con su primer uso, y responden `503` mientras falte.
   - **Andvari:** se desella solo con su clave de sello (`/srv/infra/andvari/secretos/sello.key`).
4. **Si Andvari quedara sellado,** nada entra en bucle:
   - el motor abre su almacén de claves sin llamarlo y falla mensaje a mensaje, y Bifröst se los vuelve a entregar;
   - la API y el webhook responden `503`, y Meta reenvía.

   Thor avisa con `AndvariSellado`.

## Qué vigila Thor

Thor, de Yggdrasil, publica la salud de cada contenedor y de cada unidad que se le dice en `/etc/default/yggdrasil-thor`,
que es de root y está fuera del repositorio. Desde el 2026-10-30 incluye los de Hive:

```sh
THOR_CONTENEDORES="andvari-openbao bifrost-nats hermod-rabbitmq hive-api hive-webhook hive-ingest hive-docs hive-tunel hive-portal hive-classifier hive-coordinator hive-responder hive-notifier"
```

`THOR_UNIDADES` incluye `hive-vigia.timer`. Un contenedor que no existe, o que no está sano, sale con `0`, y Mimir lanza
`ServicioDePlataformaCaido`. Al añadir un contenedor a Hive, se añade también a esa línea. La prueba de arriba
comprueba que esta página nombra todos.

El vigía de Hive (`hive-vigia.timer`) reinicia lo que queda `unhealthy`, pero no mira durante los 10 minutos que siguen a
un arranque.

## La prueba

El 2026-10-30, con la 0.7.0, se reinició midgard con `sudo systemctl reboot` y no se tocó nada más:

| | |
|---|---|
| Arranque del sistema | 15:45:24 UTC |
| Contenedores levantados por Docker | de 15:47:51 a 15:48:15 |
| Los 13 sanos (10 de Hive, Bifröst, Andvari y Hermod) | 15:53, con 0 reinicios cada uno |
| Andvari | Desellado solo |
| Thor | Los 10 de Hive a `1`, ningún servicio a `0` |
| Bifröst | La ingesta y los cuatro workers conectados |
| `hive-api`, el webhook y el portal, desde fuera | `200` |

Antes hubo cuatro reinicios reales de midgard: el 2026-10-28 a las 18:32 y a las 21:57, y el 2026-10-29 a la 01:07 y
a las 15:04. Después de ellos el vigía no dio ningún aviso, y `hive-tunel` volvió solo.

La prueba con Andvari sellado no se hizo en vivo. Sellarlo pide un token raíz, que está revocado, así que lo de arriba
sale de cómo está escrito el código.
