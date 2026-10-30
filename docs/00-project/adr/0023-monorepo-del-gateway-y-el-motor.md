# ADR-0023: El gateway y el motor de respuestas, en un solo repositorio

* **Estado:** accepted
* **Fecha:** 2026-10-20
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 00-project
* **Versión:** 0.1.0
* **ID:** ADR-0023
* **Supersede:** la ADR-0001 del motor (`ms-python-metamsghandler`), «Adopción de la estructura AI-DLC en variante
  polyrepo», en lo que dice de un repositorio por servicio
* **Controles OWASP afectados:** A06 (componentes y su cadena de suministro), A08 (integridad del software y de los
  datos)

Aceptada el 2026-10-20 al fusionar la PR #62.

## Contexto

El motor de respuestas se va a partir en seis workers (clasificador, coordinador, enlace, respuesta, evaluador y
notificador; [plan](../plan-topologia-de-workers.md)). Vive en otro repositorio, `ms-python-metamsghandler`, que
hoy es solo el clasificador.

Revisado el 2026-10-20, los workers que faltan necesitan justo lo que ya está en este repositorio:

- el contrato v2 del bus y sus sujetos ([`asyncapi-v2.yaml`](../../02-design/contracts/asyncapi-v2.yaml));
- los nombres de los workers (`enums.Worker`), que la API de gestión valida ([ADR-0022](0022-una-credencial-por-worker.md));
- la firma de los tokens (`auth.client`) y el almacén de las claves de cada worker en Andvari
  (`crypto.engine_key_store`);
- el publicador de NATS con sus arreglos, la regla de los destinos de los intents (I7) y la lista cerrada de
  atributos de las trazas (I5).

En dos repositorios, todo eso se copia y se desincroniza: un cambio del contrato pide dos PRs que se fusionan por
separado. Además, el motor no tiene CI. Su workflow de gates solo imprime «PENDIENTE».

El receptor de despliegue de midgard ya admite varias apps de un mismo repositorio, cada una con su workflow de build
(ADR-0010 de `despliegue-continuo`).

## Decisión

**Un solo repositorio para la aplicación de chatbots: este.** El motor se importa con toda su historia. El
repositorio `ms-python-metamsghandler` se archiva.

1. **Este repositorio es el que se queda.** Así conserva sus PRs (#1 a #61 están citados en los docs y en el
   tablero), sus alertas de CodeQL, sus secretos y el webhook del receptor. Cambiarle el nombre es otra decisión, y
   puede esperar: GitHub redirige el nombre antiguo.
2. **Estructura:**

   | Ruta | Qué es |
   |---|---|
   | `src/`, `tests/`, `Dockerfile`, `deploy/` | El gateway, donde está hoy. Moverlo a `gateway/` rompería rutas en el CI, los scripts, la documentación, el compose de midgard y las alertas, a cambio de solo simetría |
   | `engine/` | El motor, con su `src/`, sus `tests/`, sus `requirements`, su `Dockerfile` y sus `docs/`. Cada worker será un punto de entrada de la misma imagen |
   | `shared/` | El código que usan los dos: contrato, firma, almacén de claves, NATS, trazas, destinos. Se crea al extraer la primera pieza que necesite el motor, no antes |

3. **Dependencias e imágenes por componente.** Cada componente tiene sus `requirements` y su imagen:
   `ghcr.io/jeremialcala/wh-python-fastapi-messenger` para el gateway y `ghcr.io/jeremialcala/chatbot-engine` para el
   motor. Lo que necesita el motor (`nemoguardrails`, que compila C++ y solo funciona en Linux, `ollama` y un
   `pydantic` fijado) no entra en la imagen del gateway.
4. **Un solo CI, filtrado por rutas.** Los jobs del gateway corren con cambios en lo suyo; los del motor, con cambios
   en `engine/`; y cualquier cambio en `shared/` o en los contratos dispara los dos. CodeQL, gitleaks y SCA
   miran el repositorio entero.
5. **El motor pasa los gates del repositorio con un umbral propio y temporal.** Su cobertura mínima empieza en la que
   tiene (60 %), y sube al 80 % del gateway antes de cerrar su Gate 3. Nada más se relaja: CodeQL y gitleaks se
   aplican igual.
6. **Las ADRs del motor se quedan en `engine/docs/`, con sus números**, y se citan como «la ADR-0004 del motor», igual
   que se cita «la ADR-0008 de Yggdrasil». Las decisiones de los dos componentes, como esta, van aquí, a partir de
   la ADR-0023.
7. **Versiones por componente.** Las etiquetas son `vX.Y.Z` para el gateway, como hasta ahora, y `engine-vX.Y.Z` para
   el motor. Cada componente tiene su CHANGELOG: el de la raíz y `engine/CHANGELOG.md`.
8. **La importación conserva la historia.** Se reescribe el motor con
   `git filter-repo --to-subdirectory-filter engine` y se une a `develop` con un merge de historias no relacionadas.
   Antes se pasa gitleaks sobre la historia del motor: su `config.env` nunca se commiteó.

## Alternativas consideradas

| Opción | Pros | Contras | Riesgo de seguridad |
|---|---|---|---|
| **Un repositorio, el gateway en la raíz y el motor en `engine/`** | Contrato y código compartido en una PR. El motor hereda el CI. Mudanza casi gratis para el gateway | Asimétrico. El CI tiene que filtrar por rutas | Un solo sitio que proteger. El motor queda bajo los mismos gates |
| Un repositorio con `gateway/` y `engine/` simétricos | Estructura limpia | Rompe rutas en el CI, los scripts, los docs, el compose de midgard y las alertas de CodeQL | El mismo |
| Dos repositorios y una librería compartida publicada | Despliegue y permisos independientes | Tres repositorios y tres CI para una persona. Cada cambio del contrato es una versión de la librería y dos PRs | El motor seguiría sin CI hasta montárselo |
| Dos repositorios y copiar lo que haga falta | Nada que cambiar hoy | El contrato y la seguridad (firma, destinos, claves) se desincronizan | Una copia vieja de la regla de destinos o de la firma es un fallo de seguridad silencioso |

## Consecuencias

- **El primer paso es la [PR #3 del motor](https://github.com/jeremialcala/ms-python-metamsghandler/pull/3)**: su
  adopción de AI-DLC estaba sin commit y tiene que entrar en la historia que se importa.
- **El CI se reorganiza por rutas**, con un job de pruebas y una imagen para el motor.
- **El tablero de Notion, la memoria de las sesiones y los enlaces de los docs** citan el repositorio del motor:
  las PRs #1 a #3 siguen en el repositorio archivado.
- **El motor sigue sin desplegarse.** Esta ADR no decide dónde corre su LLM: midgard no puede, es un i3 sin AVX2 ni
  GPU (ADR-0007 del motor).

## Construido (la importación, 2026-10-20)

- **De dónde salió:** la historia de `develop` del motor, en `baff307`, que ya incluía su adopción de AI-DLC (PR #3
  del motor). Son 13 commits, reescritos bajo `engine/` con `git filter-repo` 2.47.0 y unidos con un merge de
  historias no relacionadas.
- **Secretos:** gitleaks 8.28.0, la versión del CI, recorrió antes toda la historia del motor, con sus dos ramas, y
  no encontró nada.
- **Pruebas:** las 1287 del gateway pasan desde la raíz, porque `pytest.ini` solo recoge `tests/`, y las 29 del motor
  pasan desde `engine/`.
- **`engine/.github/` se retira.** GitHub solo lee el de la raíz, cuyo `CODEOWNERS` ya cubre todo el repositorio. El
  workflow de gates del motor era un esqueleto que solo imprimía «PENDIENTE».
- **Archivado:** `ms-python-metamsghandler` se archivó en GitHub el 2026-10-20, con un aviso en su README que apunta
  aquí.

## Construido (el CI por rutas y la imagen del motor, 2026-10-20)

- **Corrección:** el punto 4 decía que Trivy miraba el repositorio. No es así: el CI del gateway no tiene Trivy.
- **Workflows del motor:**
  - `engine`, en cada PR que toca `engine/`: pylint, pruebas con cobertura de ramas mínima del 60 % y una imagen
    de prueba;
  - `build-engine`, en `main`: publica `ghcr.io/jeremialcala/chatbot-engine` con las etiquetas `sha-<7>` y `latest`.

  No se llama `build`, porque ese nombre es el que despliega el gateway.
- **El gateway deja fuera `engine/`:** `ci` y `build` tienen un `paths-ignore`, así que un cambio que solo toca el
  motor no redespliega el gateway.
- **Gates del repositorio:**
  - `Seguridad` audita también los locks del motor y comprueba que corresponden a lo declarado;
  - el umbral de CodeQL trata `engine/tests/` como código de pruebas.
- **Dependencias del motor:**
  - se separan las de ejecución (`requirements.txt`) y las de desarrollo (`requirements-dev.txt`), cada una con su
    lock de hashes;
  - siete paquetes tenían vulnerabilidades conocidas y subieron a versiones de más de dos semanas: `cryptography`
    50.0.2 (con `cffi` 2.0.0), `pillow` 12.3.0, `pymongo` 4.18.2 (con `dnspython` 2.8.0), `jwcrypto` 1.5.7,
    `python-dotenv` 1.2.2 y `pytest` 9.0.3;
  - pip-audit ya no encuentra ninguna.
- **Dockerfile del motor:**
  - dos etapas: la de dependencias compila `annoy` desde el lock con hashes, y la imagen final es `slim`, sin
    compiladores;
  - las dos bases van por digest;
  - ya no descarga rustup con un `curl | bash`;
  - solo copia el código.
- **Pendiente:** el paquete `shared/`.

## Construido (`shared/`, 2026-10-20; E1 del plan de los workers del motor)

- **Pasan a `shared/` con `git mv`, con su historia:**
  - `bus` (el puerto y `NatsPublisher`) y `tracing`;
  - `auth.client`, `tokens`, `key_proof` y `gateway_keys`, que forman `shared.auth`;
  - el almacén de claves de Andvari, como `shared.engine_key_store`;
  - la regla de destinos, como `shared.destinations`;
  - los nombres de los workers, como `shared.workers`.

  Lo que solo es del gateway se queda en `src/`: quién llama según el registro (`auth.principal`), la validación de
  las claves de los clientes (`auth.client_keys`) y el registro de tokens usados (`repository.tokens`).
- **El gateway importa de `shared.*`, sin capas intermedias:** 64 imports en 35 ficheros. Su comportamiento no
  cambia, y la cobertura del CI mide `src` y `shared`.
- **El contrato v2 en modelos:** `shared.contract` tiene `IdentifiedMessage` (lo que lee el clasificador),
  `SentReply` (lo que escribirá el notificador), `SCHEMA_VERSION` y los sujetos. Las pruebas de la ingesta comprueban
  que lo que publica el gateway se lee con el modelo, y que lo construido con `SentReply` valida contra
  `asyncapi-v2.yaml` y se guarda en el historial.
- **El motor importa `shared`:**
  - en las pruebas, porque su `pythonpath` incluye la raíz;
  - en la imagen, que se construye desde la raíz (`docker build -f engine/Dockerfile .`) con
    `engine/Dockerfile.dockerignore`, de modo que en el contexto solo entra el código del motor y `shared/`.

  Sus dependencias para `shared` son las mismas versiones que las del gateway.
- **CI:** los workflows del motor se disparan también con cambios en `shared/`, y los del gateway ya lo hacían.

## Condiciones de revisión

- Otro equipo o persona pasa a mantener uno de los dos componentes por su cuenta.
- El CI filtrado por rutas tarda tanto que frena las PRs pequeñas.
- Un componente necesita un ciclo de publicación que el otro no puede seguir.
