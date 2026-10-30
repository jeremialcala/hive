# ADR-0020: Placement de despliegue — Bifröst, el NATS JetStream, corre en midgard

* **Estado:** accepted
* **Fecha:** 2026-10-18
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 02-design
* **Versión:** 0.1.0
* **ID:** ADR-0020
* **Supersede:** —
* **Controles OWASP afectados:** A02 (configuración del host y del contenedor), A04 (datos personales en disco)

Aceptada el 2026-10-18 al aprobar el Gate 1 del refactor, con una condición que se cumplió el 2026-10-19 (ver [ADR-0019](0019-nats-jetstream-como-bus.md)).

Propuesta como entregable del Gate 1 del refactor, con [ADR-0019](0019-nats-jetstream-como-bus.md). El nombre lo eligió Jeremi
el 2026-10-18: Bifröst, el puente que une los mundos, que en el mito guarda Heimdall.

## Contexto

Bifröst es un componente de infraestructura: un servidor NATS JetStream con conexiones persistentes de todos los
servicios y un almacén en disco. Por donde pasa el contenido de los mensajes, que es dato restringido.

El punto de partida es el mismo que en [ADR-0008](0008-placement-del-gateway.md). `midgard` ya corre Hermod y
Andvari y los servicios del gateway que se conectarán a él. Sus límites:

- 2 núcleos y 4 hilos, compartidos con el enrutamiento de la red;
- 7,6 GB de RAM;
- un disco mecánico de 5400 rpm de 2013, sin SMART;
- sin UPS, con cortes de luz frecuentes.

Ninguno de ellos impide NATS, que es un binario de Go sin requisitos de AVX2.

## Decisión

**Bifröst se despliega en `midgard` igual que Hermod y Andvari**, fuera del receptor de despliegue continuo:

- **Proyecto y repositorio:** proyecto compose `bifrost` en `/srv/infra/bifrost`, y repositorio solo local en
  `Projects/bifrost`, sin remoto, como los otros dos.
- **Contenedor:** `bifrost-nats`, con la imagen oficial `nats` fijada por digest en la 2.14 (2.14.3 como mínimo).
- **Cuenta y arranque:** cuenta de sistema propia y una unidad `bifrost-arranque.service` para que vuelva sola tras
  un apagón (RNF06).
- **Red:** red Docker externa `bifrost`, por la que entran los servicios como `bifrost:4222`, con TLS. Ningún puerto
  a la LAN. El puerto de monitorización, 8222, solo en loopback, para que lo lea Mimir.
- **Almacén en disco:**
  - `/var/lib/bifrost/jetstream`;
  - `sync_interval: always`;
  - `max_file_store` en 10 GB, sobre los 456 GB del disco, que está al 7 %;
  - `max_memory_store` en 64 MB, porque todos los streams son en fichero.
- **Recursos:** 384 MB de RAM y media CPU como tope del contenedor.
- **Secretos en Andvari:** las credenciales de cada servicio, la clave de cifrado del almacén y la clave privada de
  la CA de Bifröst.
- **Respaldos: ninguno del almacén.** Los streams guardan como mucho 7 días, y el contexto se rehace desde el
  historial. Sí se respaldan la configuración y la CA, que están en el repositorio.

No se calcula la matriz de rendimiento por dólar. Como en ADR-0008, el candidato usa hardware que ya existe, con
coste marginal nulo, y se aplica la regla de proporcionalidad. Tampoco se buscaron precios de un servicio gestionado,
porque llevar el bus fuera del servidor traería costes que ningún precio compensa aquí (ver alternativas).

```mermaid
quadrantChart
    title Placement de Bifrost - valoracion cualitativa, sin precios
    x-axis Coste bajo --> Coste alto
    y-axis Rendimiento bajo --> Rendimiento alto
    quadrant-1 Caro y bueno
    quadrant-2 Zona objetivo
    quadrant-3 Barato y pobre
    quadrant-4 Evitar
    midgard junto a los servicios: [0.1, 0.75]
    NATS gestionado en la nube: [0.7, 0.55]
    Cluster de tres nodos propio: [0.85, 0.85]
```
*Eje trazabilidad · placement · Gate 1. Valoración cualitativa: midgard tiene coste marginal nulo y la latencia de
una red Docker local; el servicio gestionado añade la ida y vuelta a internet en cada acuse.*

## Desplegado en I0 (2026-10-19)

Desplegado tal como se describe, con la imagen `nats:2.14.8-alpine` y el repositorio local `Projects/bifrost`
(`instalar.sh`, `aprovisionar.sh`, `medir.sh`). La medición del `fsync` está en
[ADR-0019](0019-nats-jetstream-como-bus.md): 44,5 ms de mediana por mensaje en el disco de midgard, por debajo de los
50 ms de la condición de revisión, pero cerca. Las notificaciones de varios mensajes se resolvieron con un evento
por notificación ([ADR-0021](0021-el-webhook-solo-publica.md)), con `max_payload` en 4 MB.

**El SSD (2026-10-19):** el almacén de Bifröst está en un HP SSD S700 de 1 TB, instalado por Jeremi en midgard y montado
en `/srv/rapido` con `nofail`. `/srv/rapido/bifrost` se monta sobre `/var/lib/bifrost`, y sin el SSD Bifröst no
arranca (comprobado), en vez de crear un almacén vacío en el HDD. Lo vigilan smartd y un temporizador que avisa por
ntfy de los errores del enlace.

El SSD se cayó una vez, a los tres minutos de escritura sostenida, y volvió a funcionar al reconectarlo: después pasó
60 GB de escritura a 193 MB/s sin errores. Por eso Docker, containerd, Bragi y Andvari se moverán después de vigilarlo
2 o 3 días.

| Acuse con `fsync` en cada escritura | HDD | SSD |
|---|---|---|
| 1 KB, mediana / p99 / máximo | 44,5 / 89 / 289 ms | 2,2 / 9,7 / 18,5 ms |
| 3 MB, mediana | 196 ms | 69 ms |

El HDD de midgard, además, tiene 16 sectores pendientes de reasignar (eran 8 el 2026-10-05).

Pendiente de ese despliegue:
- mover a Andvari los secretos de `.env`, `ca/` y `credenciales/`;
- guardar las contraseñas como `bcrypt`, porque NATS avisa de que están en claro;
- que Mimir lea el 8222 y que Gjallarhorn avise.

## Alternativas consideradas

| Opción | Pros | Contras | Riesgo de seguridad |
|---|---|---|---|
| **midgard, junto a los servicios** | Coste marginal nulo; el acuse va por una red Docker local; los datos no salen de casa; el mismo patrón que Hermod y Andvari | Un solo nodo, sin UPS, con un disco viejo | El host es compartido (T15) |
| NATS gestionado en la nube | Disponibilidad y réplicas sin operarlas | Cada acuse cruza internet desde midgard, dentro del plazo del webhook; coste mensual | Un subencargado nuevo con el contenido de los mensajes; más superficie expuesta |
| Clúster de tres nodos propio | Sobrevive a la caída de un nodo | Hace falta hardware que no existe | Igual que midgard, multiplicado por tres |

## Consecuencias

- **Disponibilidad limitada por midgard.** Si cae, el webhook responde `503` y Meta reenvía: 7 días en WhatsApp y
  36 horas en Instagram. Messenger da de baja la página tras una hora de fallos, igual que ahora.
- **El disco es el riesgo.** Si `fsync` en cada escritura no cabe en el plazo del webhook, o el disco falla, lo
  siguiente es un SSD en midgard. Medirlo es parte de I0.
- **La monitorización entra en Yggdrasil:** Mimir lee el puerto 8222 y Gjallarhorn avisa si Bifröst no responde.
  Un exportador queda para la implementación.
- **El despliegue (I0) lo ejecuta Jeremi** o se hace con su visto bueno, como el de Hermod y Andvari.

## Condiciones de revisión

- Se admite al primer cliente externo y hace falta comprometer un SLO.
- El bus pasa de 1 GB de datos en reposo, o el acuse supera los 50 ms de mediana.
- midgard cambia de disco o de hardware.
