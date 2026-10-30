# ADR-0018: El webhook y la API de gestión corren como dos servicios

* **Estado:** accepted
* **Fecha:** 2026-10-18
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 02-design
* **Versión:** 0.1.0
* **ID:** ADR-0018
* **Supersede:** — (amplía [ADR-0008](0008-placement-del-gateway.md): el gateway sigue en `midgard`, ahora en dos
  contenedores)
* **Controles OWASP afectados:** A01 (el servicio de internet no tiene las rutas de gestión), A02 (configuración
  mínima de cada contenedor), A04 (una petición lenta no deja sin servicio al otro)

Aceptada el 2026-10-18, al cerrar el Gate 2 aceptando RD-05 con esta condición.

## Contexto

El gateway era una sola aplicación con 33 rutas en un contenedor con dos workers. Tres problemas venían de ahí:

- **RD-05 / H14.** Las llamadas a MongoDB y a Andvari bloquean el proceso. Dos peticiones de gestión lentas, como
  exportar un contacto, borrar un cliente o esperar a Andvari, ocupaban los dos workers, y Meta se quedaba sin
  respuesta. Reintenta, y Messenger da de baja la página tras una hora de fallos.
- **RS09.** En internet solo se publica `/chat/<uuid>`. Con una sola aplicación, el túnel tenía que filtrar por
  ruta, y `/chat/{bot_id}` comparte prefijo con rutas de gestión como `/chat/{bot_id}/info` o
  `/chat/{bot_id}/tokens`. Un error en esa expresión publicaba la gestión.
- **Mínimo privilegio.** El proceso expuesto a internet sin autenticar tenía las claves privadas del gateway, que
  descifran el token de cualquier cliente, y el registro de supresiones con las operaciones de borrado.

Medido el 2026-10-18 con la imagen, en reposo: un contenedor de un worker ocupa 66 MiB, y uno de dos workers, 156 MiB.

## Decisión

**Dos aplicaciones FastAPI de la misma imagen, en dos contenedores**:

| Servicio | Módulo | Rutas | Lo que monta |
|---|---|---|---|
| Webhook | `webhook.py` (`uvicorn webhook:app`) | `GET` y `POST /chat/{bot_id}`, `/health` | La credencial y la CA de Andvari |
| API de gestión | `app.py` (`uvicorn app:app`, el `CMD` de la imagen) | Las otras 30 y `/health` | Además, el par de claves del gateway y el registro de supresiones |

- **Lo común está en `service.py`**: el límite de tamaño, la respuesta a una petición mal formada, la línea de log
  de cada petición, `/health` y la configuración de Andvari.
- **El webhook arranca sin `GATEWAY_KEYS_FILE`, `GATEWAY_AUDIENCE` ni el registro**. La API de gestión no arranca
  sin las dos primeras.
- **Puertos en loopback:** el webhook en el 5003 y la gestión en el 5002. El túnel publica solo el webhook, en su
  propio hostname, sin filtrar por ruta. La gestión sigue privada hasta que se publique en un hostname suyo
  (RS09).
- **Recursos:** el webhook con dos workers, 384 MB y una CPU; la gestión con uno, 256 MB y media CPU. Antes eran
  512 MB y una CPU para todo.
- **Despliegue:** el webhook arranca cuando la gestión está sana, así que la URL de salud que comprueba el receptor,
  la del webhook, responde por los dos.
- **Dos contratos OpenAPI:** `openapi.yaml` para la gestión y `openapi-webhook.yaml` para el webhook. Los dos se
  generan con `.ai-dlc/scripts/gen_openapi.py`.

## Alternativas consideradas

| Opción | Por qué no |
|---|---|
| Pasar las llamadas bloqueantes a hilos aparte, sin separar | No resuelve la exposición ni el privilegio, y las llamadas a la base son muchas y repartidas. El refactor (R1) saca del webhook las de los contactos de todos modos |
| Una sola aplicación y filtrar por ruta en el túnel | Es lo que había. Un error en el filtro publica la gestión, y una petición de gestión lenta sigue parando el webhook |
| Dos repositorios o dos imágenes | Duplica el pipeline y las dependencias sin ganar nada: los dos usan los mismos modelos, repositorios y cifrado |

## Consecuencias

- **Una petición de gestión lenta ya no para el webhook.** Lo que queda de RD-05 está dentro de cada proceso: en el
  webhook lo resuelve el worker de ingesta (I4 del plan); en la gestión, un incremento propio.
- **La caché del bot del webhook (#29) no se entera de los cambios hechos en la gestión.** Cambiar las credenciales
  de un bot o borrarlo tarda hasta 30 segundos en notarse en el webhook. Ya pasaba entre los dos workers de antes.
  Se puede invalidar con un evento por el bus cuando exista NATS.
- **El `.env` sigue siendo uno para los dos.** El webhook recibe variables que no usa, como las de RabbitMQ, que la
  gestión no necesita, o la audiencia de los tokens. Separar las credenciales es el siguiente paso de mínimo
  privilegio: un usuario de Atlas para el webhook, sin borrar ni tocar clientes, y un AppRole de Andvari solo para
  descifrar.
- **El webhook sigue pudiendo descifrar datos de cualquier cliente**, porque necesita el App Secret de todos los
  bots. La separación protege las claves del gateway y las operaciones destructivas, no la confidencialidad de los
  datos.
- **La memoria no sube:** ~220 MiB entre los dos, en reposo, frente a los 156 MiB del contenedor de antes y los
  6,8 GB libres de midgard.
- **Cambia cómo se ejecuta en local:** dos `uvicorn`, uno por servicio (README).

## Condiciones de revisión

- Se publica la gestión en internet.
- Se separan las credenciales por servicio.
- El webhook deja de leer la base al hacer R1: entonces pierde también la credencial de Atlas.
