# ADR-0012: El evaluador y el resumen usan el mismo Gemma 4 26B, en una tercera ranura

* **Estado:** accepted
* **Fecha:** 2026-10-31
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 02-design
* **Versión:** 0.1.0
* **ID:** ADR-0012
* **Supersede:** — (amplía [ADR-0010](ADR-0010-llm-propio-con-llama-cpp.md): una ranura más del mismo servidor)
* **OWASP:** LLM01 (la conversación y la respuesta van al evaluador como datos, nunca en su `system`) · LLM05 (no
  sale nada sin evaluar: falla cerrado) · LLM10 (el evaluador tiene su propia ranura y su tope de tokens)

Decisión de Jeremi del 2026-10-31, con lo medido ese día (HIG-145).

## Contexto

El evaluador (E8, HIG-16) comprueba cada respuesta antes de enviarla: que cumpla la persona y las reglas del bot. Si
no cumple, se reescribe una vez y, si sigue sin cumplir, sale el texto fijo `no_puedo`. Si el evaluador no responde,
falla cerrado con `no_disponible`. Está en el camino de cada mensaje, así que la persona espera mientras evalúa.

El resumen de la conversación (HIG-143) usa el mismo modelo, fuera del camino del mensaje.

Hoy el LLM es un solo Gemma 4 26B A4B en llama.cpp, en la RTX 3090 de la LAN, con dos ranuras de 8K tokens: la del
clasificador y la del de respuesta (ADR-0010). Jeremi eligió primero un modelo pequeño aparte, y se midió antes de
decidir.

### Lo medido el 2026-10-31

Son 41 casos inventados, en `engine/evaluacion/casos-evaluador.json`: tres bots (una zapatería, una clínica dental y
una pizzería) y respuestas propuestas, 20 que cumplen y 21 que no. Las que no cumplen caen en distintos criterios:

- contradicen una regla;
- inventan precios;
- dan contactos;
- equivocan el idioma, el tono o el formato;
- revelan sus instrucciones u obedecen las del mensaje;
- dejan su papel.

Las mide `engine/evaluacion/medir_evaluador.py` a temperatura 0, con el veredicto atado a un esquema JSON. Los
resultados están en `engine/evaluacion/resultados/`.

| Modelo (GGUF) | Acierto | Falsos aprobados | Falsos rechazos | p50 | Memoria de GPU añadida |
|---|---|---|---|---|---|
| Gemma 4 26B A4B MXFP4 (el de hoy) | **100 %** (41/41) | **0** | 0 | 1,14 s | una ranura de 8K: menos de 1 GiB (estimado) |
| Gemma 4 E4B Q4_K_M, 4,64 GiB | 90 % | 1 caso: responde en español a quien escribe en inglés | 3 casos | 0,81 s | 3,4 GiB, con 1,7 GiB libres después |
| Gemma 4 E2B Q4_K_M, 2,89 GiB | 84 % | 2 casos: el idioma, y una cita en domingo con la clínica cerrada | 4 a 5 casos | 0,61 s | 1,8 GiB |

- **Un falso aprobado es lo que importa:** una respuesta que no cumple y sale. Un falso rechazo solo cuesta una
  reescritura.
- **Las repeticiones:** los modelos pequeños se midieron dos veces por caso, y el 26B una, con el de producción.
- **La memoria compartida de la GPU** no se movió con ningún modelo cargado (0,18 a 0,30 GiB): el 26B siguió entero en
  la GPU.
- **Los modelos pequeños necesitan `--reasoning-budget 0`.** Con un esquema JSON se ponen a razonar aunque se pida
  `enable_thinking: false`, agotan el tope y no responden.
- **Una variante del prompt que razona antes de decidir** (los idiomas y el motivo antes del veredicto) empeoró al E4B:
  85 % y 2 falsos aprobados.
- **La memoria de la ranura nueva está estimada.** Hoy el 26B ocupa unos 17 GiB, 15,4 de ellos del modelo, así que sus
  dos ranuras y sus búferes suman unos 1,6 GiB. Se mide al reiniciar `hive-llm`.

## Decisión

1. **El evaluador usa el mismo Gemma 4 26B A4B** del clasificador y el de respuesta, en una **tercera ranura de 8K
   tokens**: `--parallel 3` y `--ctx-size 24576` en `engine/deploy/llm/docker-compose.yml`. Cada ranura se queda con su
   `system`, porque llama.cpp le da cada petición a la ranura con el prefijo más parecido. Así, una evaluación nunca
   espera detrás de una respuesta.
2. **El resumen de HIG-143 va en la misma ranura**, fuera del camino del mensaje.
3. **Si la memoria medida tras el reinicio no cabe**, es decir, si la memoria compartida crece o el escritorio se queda
   sin margen, se vuelve a dos ranuras y se elige otra vez con estos datos.
4. **El prompt del evaluador:** su `system` lleva las indicaciones del de respuesta y los datos y reglas del bot, y es
   estable por bot. El turno de usuario lleva la conversación, el último mensaje y la respuesta propuesta, marcados
   como datos. El veredicto va atado a un esquema JSON (`cumple`, `criterio`, `regla`, `motivo`).

## Alternativas consideradas

| Opción | Por qué no |
|---|---|
| Gemma 4 E4B en un segundo servidor | Falló el 10 % y dejó pasar una respuesta en el idioma equivocado. Ocupa 3,4 GiB y deja solo 1,7 GiB libres en la GPU, y es un segundo modelo que operar y descargar |
| Gemma 4 E2B en un segundo servidor | El más ligero y rápido, pero falló el 16 % y dejó pasar dos respuestas que no cumplían |
| El 26B en la ranura del de respuesta | Sin reiniciar nada, pero cada mensaje en cola espera también la evaluación de la respuesta anterior |
| Claude (lo que decía el plan del motor) | Anthropic no atiende desde Venezuela (ADR-0009 y ADR-0010) |

## Consecuencias

- **Un minuto sin LLM al reiniciar `hive-llm`**, por la carga del modelo, y con el «despliega» de Jeremi. Mientras
  tanto el clasificador y el de respuesta dejan los mensajes en espera, como con cualquier caída del LLM.
- **Las tres ranuras comparten la GPU.** Con la evaluación, cada respuesta tarda unos 1,1 s más en salir. Si coinciden
  las tres a la vez, cada una va algo más lenta.
- **Los ficheros E2B y E4B** (7,5 GiB, con su SHA-256) quedan en el volumen `hive-llm-modelos`. Borrarlos lo decide
  Jeremi.
- **Los casos y el script se quedan en el repositorio**, para medir otra vez al cambiar de modelo, de cuantización o de
  prompt.

## Condiciones de revisión

- Si la memoria de la tercera ranura no cabe, o el escritorio necesita la GPU para otra cosa.
- Si las respuestas tardan demasiado con la evaluación: un objetivo de latencia nuevo, o más de un mensaje por segundo
  sostenido.
- Si un modelo pequeño nuevo iguala al 26B en estos casos, sin falsos aprobados.
- Si se amplían los casos con conversaciones reales calificadas (RF28) y el 26B empieza a fallar.
