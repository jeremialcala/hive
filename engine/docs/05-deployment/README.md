# 05 — Despliegue (Gate 4)

* **Estado:** draft
* **Fecha:** 2026-10-20
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 05-deployment
* **Versión:** 0.1.0

El esqueleto de Hive en midgard (E7 del [plan](../00-project/plan-de-los-workers.md)). Es la instancia de prueba: las
imágenes se construyen en midgard desde `develop`, sin pasar por `main` ni por el despliegue continuo (decisión de
Jeremi del 2026-10-20). El Gate 4, con el pipeline, sus gates y el SBOM, queda para cuando el motor llegue a `main`.

## Qué corre

| Proyecto compose | Dónde | Contenedores |
|---|---|---|
| `hive-gateway` | `/srv/apps/hive-gateway` (`deploy/docker-compose.yml` de la raíz) | `hive-webhook`, `hive-api`, `hive-ingest`, `hive-docs` y, con el perfil `tunel`, `hive-tunel` |
| `hive-engine` | `/srv/apps/hive-engine` (`engine/deploy/docker-compose.yml`) | `hive-classifier`, `hive-coordinator`, `hive-responder` y `hive-notifier` |

`hive-tunel` publica, desde Cloudflare (ADR-0024 del gateway):

- `https://hive-webhook.higerotech.com` hacia `http://hive-webhook:5002`;
- `https://hive-api.higerotech.com` hacia `http://hive-api:5002`;
- `https://hive.higerotech.com` hacia `http://hive-docs:8080`: la documentación de la API, en `/docs` (ADR-0026 del
  gateway).

Bifröst, Andvari y Saga los ponen sus repositorios. Los workers llegan a ellos y a la API del gateway por las redes
externas `bifrost`, `andvari` y `yggdrasil_saga`.

El LLM corre fuera de midgard, en el equipo de la RTX 3090 de la LAN ([ADR-0010](../02-design/adr/ADR-0010-llm-propio-con-llama-cpp.md)):

| Proyecto compose | Dónde | Contenedores |
|---|---|---|
| `hive-llm` | 192.168.10.74 (`engine/deploy/llm/docker-compose.yml`) | `hive-llm`: llama.cpp con Gemma 4 26B A4B |

## Lo que necesita cada worker

| Worker | Bifröst | Andvari | Además |
|---|---|---|---|
| Clasificador | `credenciales/classifier.env` | — | El LLM, con `keys/llm-api-key` |
| Coordinador | `credenciales/coordinator.env` | Rol `motor-coordinator`: su clave para cada cliente | La API del gateway, y el LLM con `keys/llm-api-key` para resumir lo que sale del contexto (HIG-143) |
| De respuesta | `credenciales/responder.env` | — | El LLM, con `keys/llm-api-key` |
| Evaluador | `credenciales/evaluator.env` y el consumidor `evaluator` (HIG-146) | — | El LLM, con `keys/llm-api-key`, en su ranura de `hive-llm` (ADR-0012) |
| Notificador | `credenciales/notifier.env`, el consumidor `notifier-aprobada` (HIG-146), el bucket `enviados` y los consumidores `notifier-leido` y `notifier-escribiendo` (ADR-0011) | Rol `motor-notifier`: su clave para cada cliente | La API del gateway y `graph.facebook.com` |

Cada cliente con bots necesita las claves del motor (`ops.engine_keys` del gateway). La instancia de medición ya las
tiene para su cliente.

## Puesta en marcha

1. **Las imágenes**, en midgard, desde `develop`:

   ```bash
   # Sin autocrlf: desde una copia de Windows, git archive daría los ficheros con CRLF.
   git -c core.autocrlf=false archive develop | ssh midgard 'rm -rf ~/hive-build && mkdir ~/hive-build && tar -x -C ~/hive-build'
   docker build -t ghcr.io/jeremialcala/hive-gateway:<etiqueta> ~/hive-build
   docker build -f ~/hive-build/engine/Dockerfile -t ghcr.io/jeremialcala/hive-engine:<etiqueta> ~/hive-build
   docker build -f ~/hive-build/deploy/docs/Dockerfile -t ghcr.io/jeremialcala/hive-docs:<etiqueta> ~/hive-build
   ```
2. **Andvari**, con un token raíz temporal y en una terminal propia: el `secret_id` del coordinador y del
   notificador, que firman con su clave para cada cliente. Pide la clave de recuperación sin eco:

   ```bash
   sudo bash raiz-temporal.sh bash motor/emitir-trabajadores.sh /srv/apps/hive-engine/keys coordinator notifier
   ```

   Los `role_id` que imprime van al `.env` del motor.
3. **El LLM**, en el equipo de la RTX 3090:
   - el modelo en `C:\hive-llm\modelos`, con su SHA-256 comprobado contra el de Hugging Face, y una copia en el
     volumen `hive-llm-modelos`, dentro de la VM, que es de donde lo lee llama-server. Desde Windows se lee a unos
     100 MB/s, y con la VM apretada de memoria `cp` falló con «Out of memory»: `dd` con bloques de 1 MB no.
     La copia tarda unos 3 minutos y el `.sha256` lleva el `\r` de Windows:

     ```bash
     docker volume create hive-llm-modelos
     docker run --rm -v C:/hive-llm/modelos:/origen:ro -v hive-llm-modelos:/modelos alpine:3.20 sh -c '
       dd if=/origen/gemma-4-26B-A4B-it-MXFP4_MOE.gguf of=/modelos/gemma-4-26B-A4B-it-MXFP4_MOE.gguf bs=1M &&
       cd /modelos && [ "$(cut -d" " -f1 /origen/*.sha256 | tr -d "\r\n")" = "$(sha256sum *.gguf | cut -d" " -f1)" ] &&
       chmod 444 *.gguf && echo "SHA-256 coincide"'
     ```
   - la clave en `C:\hive-llm\api-key`, nacida allí, con un solo salto de línea (`\n`, no `\r\n`: llama-server toma
     el `\r` como parte de la clave) y legible solo por su usuario;
   - `docker compose up -d` en `engine/deploy/llm`. Cargar el modelo desde el volumen tarda unos 40 s (eran 3 a 6
     minutos desde Windows);
   - la VM de Docker Desktop, con 12 GB en `%USERPROFILE%\.wslconfig` (`memory=12GB`). Con 8 GB se colgó el
     2026-10-21, con el modelo y dos clústeres kind dentro;
   - en el cortafuegos de Windows, desde un PowerShell de administrador, que solo entre midgard. Una regla que
     permita a midgard no basta: la de «Docker Desktop Backend» ya deja entrar a cualquiera en el perfil Público, y
     así está la red del equipo. Hace falta bloquear al resto, y en Windows un bloqueo gana a un permiso:

     ```powershell
     New-NetFirewallRule -DisplayName "Hive LLM: solo midgard" -Direction Inbound -Protocol TCP -LocalPort 8080 `
       -RemoteAddress 0.0.0.0-192.168.10.0,192.168.10.2-255.255.255.255 -Action Block -Profile Any
     ```

     Desde otro equipo de la LAN, `curl -m 5 http://192.168.10.74:8080/health` no llega; desde midgard, sí.
   - la copia de la clave a midgard, por la entrada estándar, sin pasar por la pantalla:

     ```bash
     ssh midgard 'sudo install -m 400 -o 10001 -g 10001 /dev/stdin /srv/apps/hive-engine/keys/llm-api-key' \
       < /c/hive-llm/api-key
     ```
4. **El gateway**, con la imagen nueva y `GATEWAY_AUDIENCE=https://hive-api.higerotech.com` en su `.env`. Su
   fichero `keys/gateway-keys.json` necesita, además del par de cifrado, el de firma de los tokens de acceso
   (ADR-0025): `python -m ops.gateway_key generate-signing /keys/gateway-keys.json` con la imagen, una vez. Sin él, el
   gateway no arranca. El gateway y el motor se despliegan juntos: el motor de antes firmaba un token por llamada, y
   el gateway nuevo ya no lo acepta.
5. **El motor:** `/srv/apps/hive-engine` con su `docker-compose.yml`, su `.env` (`deploy/.env.example`) y `keys/`
   (root:10001, 750) con los dos `secret_id`, la clave del LLM y una copia de `gateway-public.json`. Después:

   ```bash
   sudo docker compose --project-directory /srv/apps/hive-engine up -d
   ```
6. **El túnel:** se crea en Cloudflare con sus dos nombres públicos, y su token va al `.env` del gateway sin pasar por
   la pantalla ni por un argumento. Después, `COMPOSE_PROFILES=tunel` y `docker compose up -d`.
7. **El bot de prueba:**
   - se da de alta en el gateway, con sus credenciales de Meta, su número y sus reglas;
   - en Meta, la URL del webhook es `https://hive-webhook.higerotech.com/chat/{bot_id}`, con su `verifyToken`, y se
     suscribe a `messages`.
8. **El vigía** (`deploy/vigia/` de la raíz): Docker marca `unhealthy` un contenedor cuyo healthcheck falla, pero
   no lo reinicia. `hive-vigia.timer` mira cada minuto, en el segundo 30, el gateway, el webhook, la ingesta, la
   documentación y los cuatro workers. Reinicia el que esté `unhealthy` y avisa por ntfy, por el mismo canal que los otros vigías de
   midgard. Si uno sigue `unhealthy` tras 3 reinicios en una hora, deja de reiniciarlo y avisa con urgencia.

   ```bash
   sudo install -m 755 deploy/vigia/hive-vigia.sh /usr/local/sbin/hive-vigia.sh
   sudo install -m 644 deploy/vigia/hive-vigia.service deploy/vigia/hive-vigia.timer /etc/systemd/system/
   sudo ln -s /etc/smartd-aviso.env /etc/hive-aviso.env
   sudo systemctl daemon-reload && sudo systemctl enable --now hive-vigia.timer
   sudo /usr/local/sbin/hive-vigia.sh prueba
   ```

## Comprobar

- Los cuatro workers `healthy`, sin reinicios.
- Un mensaje de WhatsApp al número de prueba vuelve con su respuesta. Su traza cruza el webhook, la ingesta y los
  cuatro workers en Saga.
- El log del clasificador y del de respuesta dice los tokens de cada llamada.
- El LLM: `docker logs hive-llm` y `nvidia-smi`, en el equipo de la RTX 3090.
- El historial del gateway guarda el mensaje y la respuesta.
- El vigía: `systemctl list-timers hive-vigia.timer` y `journalctl -u hive-vigia` (una línea por reinicio).

## Volver atrás

- **El vigía:** `sudo systemctl disable --now hive-vigia.timer`. Su estado está en `/var/lib/hive-vigia`.

- **El motor:** `docker compose --project-directory /srv/apps/hive-engine down`. Lo que quede en `MOTOR` caduca en 24
  horas.
- **El gateway:** su `docker-compose.yml` y su `.env` anteriores, con la imagen anterior.
- **El túnel:** quitar `COMPOSE_PROFILES=tunel` y `docker compose up -d --remove-orphans`, o revocar el túnel en
  Cloudflare.
