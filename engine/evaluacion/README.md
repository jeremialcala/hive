# Medir el modelo del evaluador

Los casos y el script con los que se eligió el modelo del evaluador ([ADR-0012](../docs/02-design/adr/ADR-0012-modelo-del-evaluador.md),
HIG-145). Sirven para medir otra vez si cambian el modelo, su cuantización o el prompt.

- **`casos-evaluador.json`:** 41 respuestas propuestas para tres bots inventados, con lo que debería decir el
  evaluador. Ningún bot ni conversación es de un cliente.
- **`medir_evaluador.py`:** pregunta cada caso al servidor de llama.cpp a temperatura 0, con el veredicto atado a un
  esquema JSON. Cuenta los aciertos, los falsos aprobados (los que importan), los falsos rechazos y la latencia.
- **`resultados/`:** lo medido el 2026-10-31.

Contra un servidor de prueba, solo en `127.0.0.1` y sin clave. Hay que vigilar que la memoria compartida de la GPU no
crezca mientras `hive-llm` está cargado:

```bash
docker run -d --name prueba-evaluador --gpus all -p 127.0.0.1:18081:8080 -v hive-llm-modelos:/models:ro \
  ghcr.io/ggml-org/llama.cpp:server-cuda-b11434 --model /models/<modelo>.gguf --host 0.0.0.0 --port 8080 \
  --n-gpu-layers 999 --flash-attn on --parallel 1 --ctx-size 8192 --jinja --no-webui --reasoning-budget 0
python evaluacion/medir_evaluador.py --url http://127.0.0.1:18081 --modelo <modelo> \
  --salida evaluacion/resultados/<modelo>.json --veces 2
docker rm -f prueba-evaluador
```

Contra `hive-llm`, con la clave en una variable de entorno, nunca como argumento:
`LLM_CLAVE="$(tr -d '\r\n' < /c/hive-llm/api-key)" python evaluacion/medir_evaluador.py --url http://192.168.10.74:8080
--modelo gemma-4-26b-a4b --clave-env LLM_CLAVE --salida …`. Ocupa una ranura mientras dura: mejor a una hora sin
tráfico.

`--reasoning-budget 0` hace falta con los Gemma 4 pequeños. Con un esquema JSON razonan aunque se les pida
`enable_thinking: false`, agotan el tope y no responden.
