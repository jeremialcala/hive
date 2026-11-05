# Cuánto espera una persona la respuesta

* **Estado:** review
* **Fecha:** 2026-11-04
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 04-testing
* **Versión:** 1.1.0

La latencia de punta a punta de Hive y su objetivo (RNF10, HIG-136, Gate 3).

## El objetivo

Lo decidió Jeremi el 2026-10-30.

| Medida | Objetivo |
|---|---|
| Mediana (p50) | 10 s o menos |
| Percentil 95 | 20 s o menos |

- **Qué se mide:** desde que el webhook recibe el mensaje (`receivedAt`) hasta que Meta acepta la respuesta
  (`sentAt` del bucket `enviados`).
- **Sobre qué mensajes:** todos los respondidos en la ventana, también el primero tras horas sin uso.
- **Cuándo cuenta:** con 20 respuestas o más. Con menos, la cifra se apunta, pero no cuenta para el gate.

Es lo que Hive cumple hoy, con margen para el primer mensaje del día. Vigila que no empeore. Bajar la latencia sería
trabajo aparte: la ingesta tarda unos 2 s, sobre todo en viajes a Atlas.

## Cómo se mide

La medición es pasiva:
- **Qué lee:** lo que Bifröst guarda del tráfico real durante 24 horas: MENSAJES, MOTOR y el bucket `enviados`.
- **Qué hace:** calcula la mediana y el p95 de la espera y de cada paso.
- **Qué no hace:** no cambia nada ni envía nada, e imprime solo tiempos, nunca un texto, un número o un id.

El script es `tests/performance/measure_end_to_end.py`, y lo prueba `tests/unit/test_measure_end_to_end.py`. Se ejecuta
en nidavellir (192.168.10.2), donde corre Hive desde el 2026-11-02, dentro de la imagen del motor y con la credencial
de administración de Bifröst. El script llega por la entrada estándar, así que en el servidor no se escribe nada:

```sh
# Desde el repositorio. La CA se monta donde dice NATS_CA de la credencial, que es una ruta y no un secreto.
ssh -i ~/.ssh/id_ed25519_hgtech001 jalcala@192.168.10.2 'sudo bash -c "B=/srv/infra/bifrost; \
  CA=\$(grep -m1 ^NATS_CA= \$B/credenciales/admin.env | cut -d= -f2); \
  docker run --rm -i --network bifrost --env-file \$B/credenciales/admin.env -v \$B/ca/ca.crt:\$CA:ro \
  --entrypoint python ghcr.io/jeremialcala/hive-engine:<versión> - --since 00:00"' \
  < tests/performance/measure_end_to_end.py
```

Para el desglose de un mensaje por paso, que incluye el evaluador y el «escribiendo…», sirve su traza en Saga: se
busca con `tags=service.name%3Dgateway-webhook` desde `yggdrasil-grafana`, en midgard (ver ADR-0008 de Yggdrasil).

- **Salida:** una fila por mensaje con lo que tardó cada paso, y el resumen con el veredicto.
- **Código de salida:** `1` si el objetivo no se cumple.
- **`--since HH:MM`:** deja fuera lo anterior a esa hora UTC.
- **Cuándo medir:** cada semana, o tras una release que toque el camino del mensaje. Con poco tráfico, mejor justo
  después de un día con uso real.
- **Al terminar:** se añade una fila a la tabla de abajo, con su fecha y su versión.

## Mediciones

| Fecha (UTC) | Versión | Respondidos | Mediana | p95 | Objetivo |
|---|---|---|---|---|---|
| 2026-10-30 12:03 | 0.5.0 | 3 de 3 | 9,5 s | 10,0 s | Se cumple, pero no cuenta: menos de 20 respuestas |
| 2026-11-04 01:34 | 0.18.0 | 23 de 23 | 5,0 s | 31,0 s | **No se cumple**: el LLM arrancó lento (ver abajo) |
| 2026-11-04 01:42 | 0.18.0 | 23 de 23 | **4,8 s** | **5,5 s** | **Se cumple, y cuenta** (Gate 3) |
| 2026-11-04 14:54 | 0.18.0 | 1 de 1 | 11,1 s | 11,1 s | El primero tras 13 horas sin uso (HIG-14); no cuenta |

Por paso, el 2026-10-30:

| Paso | Mediana | p95 |
|---|---|---|
| Ingesta (del webhook a MENSAJES) | 1,8 s | 2,1 s |
| Clasificador | 1,5 s | 1,7 s |
| Coordinador | 1,8 s | 2,0 s |
| Redactor | 3,1 s | 3,9 s |
| Meta (del redactor a que Meta acepta) | 1,1 s | 3,6 s |

Por paso, el 2026-11-04 a las 01:42, en nidavellir y con el LLM caliente:

| Paso | Mediana | p95 |
|---|---|---|
| Ingesta (del webhook a MENSAJES) | 0,3 s | 0,7 s |
| Clasificador | 1,1 s | 1,3 s |
| Coordinador | 0,0 s | 0,0 s |
| De respuesta | 1,1 s | 1,3 s |
| Meta (del de respuesta a que Meta acepta, con el evaluador) | 2,3 s | 2,4 s |

**Las dos tandas del 2026-11-04,** de unos 20 mensajes de Jeremi cada una:
- **La de las 01:34 no cumple.** Durante su primer minuto, `hive-llm` generó a 5–9 tokens por segundo, cuando lo normal
  son unos 100. Lo más probable es que parte de él estuviera en el swap del PC. Los primeros 7 mensajes llegaron
  cada 4 a 9 s, y como cada worker atiende uno a la vez (`max_ack_pending: 1`), se encolaron tras los lentos. A partir
  de las 01:35, los 16 siguientes tardaron entre 2,1 y 6,8 s.
- **La de las 01:42 cumple** con el LLM caliente. Es la que cuenta para el Gate 3.
- **El arranque lento del LLM sigue abierto** en HIG-156. Una ráfaga de muchos contactos a la vez se encola siempre,
  por diseño: repartir por particiones es la condición de revisión de ADR-0008 del motor.

**Antes, sin este script:**
- **2026-10-21:** unos 9 s de mediana con siete respuestas del modelo.
- **2026-10-22:** 4–5 s en caliente con la caché del coordinador, y 17,8 s el primero tras 25 horas sin uso.
- **2026-10-26:** 72 s el primero tras la noche, con los procesos en swap. Después se corrigió la swappiness. Medido otra
  vez el 2026-11-04 (HIG-14): 11,1 s, con unos 4 s del LLM leyendo el prompt desde el swap del PC (HIG-156).
