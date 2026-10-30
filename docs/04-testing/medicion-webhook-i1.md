# I1: cuánto tarda hoy el webhook en responder a Meta

* **Estado:** review
* **Fecha:** 2026-10-19
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 04-testing
* **Versión:** 0.2.0
* **Requisito:** RNF01
* **Incremento:** I1 del [plan de la topología de workers](../00-project/plan-topologia-de-workers.md)

Esta es la línea base del camino actual. El webhook identifica al contacto en Atlas y publica en Hermod antes de
responder. Con esta cifra se compara todo lo que viene después: I2 (publicar en Bifröst), I4 (el webhook que solo
publica) y la medición del Gate 3.

## Después de I4: el webhook solo publica (2026-10-19)

Con el mismo script, el mismo despliegue y la imagen `i4b-c10190f`, el webhook comprueba la firma, publica la
notificación en Bifröst y responde. Tres pasadas:

| Escenario | I1 | I4 | RNF01 |
|---|---|---|---|
| Contacto conocido, uno tras otro | 185 ms | **8,3–8,4 ms**; p99 15–62 ms | cumple |
| Contacto nuevo, uno tras otro | 415 ms | **8,3–8,4 ms**; p99 12–14 ms | cumple |
| Contacto conocido, 4 a la vez | 357–472 ms | **50–52 ms**; p99 97–187 ms | cumple |

- **Con el bot en memoria, el webhook no pregunta nada a Atlas**. Lo fija
  `tests/unit/test_webhook_round_trips.py`. El contacto nuevo ya no cuesta más que el conocido.
- **La primera pasada tras desplegar tuvo 3 respuestas de 1,3 s con 4 a la vez** (1,5 %). En las dos siguientes no
  hubo ninguna por encima de 200 ms. Coincidió con el arranque en frío: las primeras conexiones a Atlas, a Andvari y
  a Bifröst de cada proceso. Se vigila en el Gate 3.
- **Cada pasada dejó sus 610 eventos** en `WEBHOOK`, 610 mensajes identificados en `MENSAJES`, 610 sobres en `llm` y
  610 mensajes en el historial. El consumidor `ingesta` terminó sin pendientes ni reentregas.

### Lo que cuesta ahora: el worker de ingesta

El trabajo que salió del webhook lo hace el worker, una notificación detrás de otra. Ritmo medido vaciando la cola
que dejaron las pasadas: **2,1 notificaciones de un mensaje por segundo**, con un 6 % de CPU. Lo limita la red y no
el procesador: cada mensaje pregunta al historial, busca el contacto (y antes, otra vez el bot), lo publica, abre una
conexión con Hermod y guarda el mensaje, todo a 60 ms de Atlas.

Para el webhook no cambia nada, porque Bifröst guarda lo que llega hasta 7 días. Lo que cambia es cuánto tarda el
motor en recibir un mensaje cuando llegan más de dos por segundo durante un rato. Hay tres maneras de subirlo, que
quedan pendientes:

- no volver a buscar el bot por cada contacto;
- una conexión a Hermod por proceso;
- varios workers, uno por partición.

## Resultado de I1

Se midió el 2026-10-19 en midgard con notificaciones de WhatsApp sintéticas, firmadas como las firma Meta. Hubo dos
pasadas, a las 17:01 y a las 17:05 UTC. Todas las respuestas fueron `200`.

| Escenario | n | Mediana | p95 | p99 | Máx. | > 1 s |
|---|---|---|---|---|---|---|
| Contacto conocido, uno tras otro | 300 + 300 | **185 ms** | 190–192 ms | 257–258 ms | 266 ms | 0 % |
| Contacto nuevo, uno tras otro | 100 + 100 | **415 ms** | 426–430 ms | 487–517 ms | 517 ms | 0 % |
| Contacto conocido, 4 a la vez | 200 + 200 | **357–472 ms** | 432–482 ms | 540–629 ms | 1667 ms | 0–0,5 % |

Frente a RNF01 (mediana de 250 ms o menos y menos de un 1 % por encima de 1 s):

- **Con un contacto conocido y sin concurrencia, cumple**: 185 ms de mediana.
- **Con un contacto nuevo, no cumple**: 415 ms. Ocurre en el primer mensaje de cada persona.
- **Con 4 notificaciones a la vez, no cumple la mediana**: de 357 a 472 ms. El webhook corre con dos procesos, y
  pymongo, que es síncrono, bloquea cada uno mientras espera a Atlas (H14). Las peticiones hacen cola.

## De dónde sale el tiempo

| Parte | Medido | Cómo |
|---|---|---|
| Publicar en Hermod, con una conexión nueva por mensaje (como hace el código) | 8,3 ms de mediana, p95 13,8 ms | 50 publicaciones con confirmación desde la red `hermod` |
| Ida y vuelta a Atlas | 60 ms de mediana | `hello` desde la red de midgard, medido el 2026-10-18 ([PRD](../01-requirements/mvp-baas-gateway.md), RNF01) |

Con un contacto conocido quedan unos 177 ms después de publicar. Equivalen a unos tres viajes a Atlas en serie. Con
uno nuevo, unos 400 ms, siete viajes. Es lo que anticipaba el plan: el plazo del webhook lo pone Atlas, no el bus.
Los viajes se infieren de los tiempos; no se contaron en el código ni en la red durante la prueba.

Lo que implica para los siguientes incrementos:

- **I2** solo cambia la publicación. Bifröst acusa un mensaje de 1 KB en 2,2 ms en el SSD, frente a los 8,3 ms de
  Hermod. Sin I4, la mediana seguirá en unos 180 ms.
- **I4** saca a Atlas del plazo: lo que queda es la firma, Andvari con su caché y la publicación. Según
  [ADR-0021](../00-project/adr/0021-el-webhook-solo-publica.md), la mediana tiene que bajar de 250 ms incluso con un
  contacto nuevo. Se mide con este mismo script.
- **H14** se ve aquí con cifra: con 4 a la vez, la mediana se multiplica por 2 o por 2,5.

## Cómo se midió

El despliegue de medición vive en midgard, en `/srv/apps/bots-gateway`, con el compose de
[`deploy/docker-compose.yml`](../../deploy/docker-compose.yml) sin cambios:

- **Imagen** `ghcr.io/jeremialcala/wh-python-fastapi-messenger:i1-ff40500`, construida en midgard desde `develop`
  (`ff40500`). La de GHCR sale de `main`, que es la 0.3.0 y aún no tiene el webhook aparte (ADR-0018).
- **Base de datos** `bots_i1`, en el mismo clúster de Atlas que la real. La latencia es la de producción, sin tocar
  los datos reales.
- **Hermod:** usuario `gateway-temporal`, que se borra cuando el webhook publique en Bifröst.
- **Andvari:** rol AppRole `gateway`, con su credencial en `keys/andvari-secret-id`.
- **Sin túnel ni CD:** Meta no llama a esta instancia. Las notificaciones salen del propio servidor, por
  `127.0.0.1:5003`.

El script es [`tests/performance/measure_webhook.py`](../../tests/performance/measure_webhook.py). Se ejecuta dentro
de un contenedor de la misma imagen, en la red del host. Crea un operador y un bot sintético con un App Secret
aleatorio, y envía tres escenarios con 200 ms entre mensajes:

- 300 mensajes de un mismo contacto;
- 100 de contactos distintos;
- 200 repartidos en 4 hilos.

Cada petición abre su sesión HTTP con el webhook. Antes van 10 mensajes de calentamiento que no se cuentan. Los
mensajes llegan a la cola `llm` de Hermod: 610 por pasada, todos acusados. La cola se vació después, porque no hay
consumidor.

```bash
DIR=/srv/apps/bots-gateway; M=$DIR/medicion; IMG=ghcr.io/jeremialcala/wh-python-fastapi-messenger:i1-ff40500
run() { docker run --rm --network host -u 0 -v "$M:/m" -v "$DIR/keys/gateway-keys.json:/k/gateway-keys.json:ro" \
          -e AUDIENCE=https://bots-i1.midgard -e IMAGE=i1-ff40500 --entrypoint python "$IMG" /m/measure_webhook.py "$@"; }
run key
IMAGE_TAG=i1-ff40500 docker compose run --rm -T --no-deps -v "$M/operator-public.json:/tmp/operator.json:ro" \
    management python -m ops.operator_key /tmp/operator.json > "$M/operator-client.json"
run prepare
run measure
docker exec hermod-rabbitmq rabbitmqctl -q purge_queue llm
```

Límites de la medición:

- Las notificaciones no pasan por Cloudflare ni por internet. Lo que Meta ve suma el túnel.
- El bot es de WhatsApp. En Messenger, un contacto nuevo añade además la llamada a la Graph API.
- Es la carga de un solo bot, sin otra actividad en la base.
