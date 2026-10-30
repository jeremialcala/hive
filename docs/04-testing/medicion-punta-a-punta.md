# Cuánto espera una persona la respuesta

* **Estado:** review
* **Fecha:** 2026-10-30
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 04-testing
* **Versión:** 1.0.0

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
en midgard, dentro de la imagen del motor y con la credencial de administración de Bifröst:

```sh
# Desde el repositorio, la copia del script:
ssh midgard 'mkdir -p ~/medicion && cat > ~/medicion/measure_end_to_end.py' < tests/performance/measure_end_to_end.py
# En midgard:
sudo docker run --rm --network bifrost --env-file /srv/infra/bifrost/credenciales/admin.env \
    -v /srv/infra/bifrost/tls/ca.crt:/ca.crt:ro -v ~/medicion:/m --user "$(id -u):$(id -g)" \
    --entrypoint python ghcr.io/jeremialcala/hive-engine:<versión> /m/measure_end_to_end.py --json /m/result.json
```

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

Por paso, el 2026-10-30:

| Paso | Mediana | p95 |
|---|---|---|
| Ingesta (del webhook a MENSAJES) | 1,8 s | 2,1 s |
| Clasificador | 1,5 s | 1,7 s |
| Coordinador | 1,8 s | 2,0 s |
| Redactor | 3,1 s | 3,9 s |
| Meta (del redactor a que Meta acepta) | 1,1 s | 3,6 s |

**Antes, sin este script:**
- **2026-10-21:** unos 9 s de mediana con siete respuestas del modelo.
- **2026-10-22:** 4–5 s en caliente con la caché del coordinador, y 17,8 s el primero tras 25 horas sin uso.
- **2026-10-26:** 72 s el primero tras la noche, con los procesos en swap. Después se corrigió la swappiness, y
  volver a medirlo es HIG-14.
