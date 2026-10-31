# -*- coding: utf-8 -*-
"""
    Mide un modelo como evaluador (HIG-145) con los casos de `casos-evaluador.json`: para cada respuesta propuesta,
    si dice que cumple o no la persona y las reglas del bot, y cuánto tarda.

        python evaluacion/medir_evaluador.py --url http://127.0.0.1:18081 --modelo gemma-4-e2b \
            --salida evaluacion/resultados/gemma-4-e2b.json [--clave-env LLM_CLAVE] [--veces 3]

    Lo que importa más son los **falsos aprobados**: una respuesta que no cumple y que el evaluador deja pasar. Un falso
    rechazo solo cuesta una reescritura.

    El `system` lleva las instrucciones del evaluador y los datos del bot, y es estable por bot, como el del de
    respuesta. La conversación y la respuesta propuesta van en el turno de usuario, como datos (LLM01). La clave del
    servidor, si hace falta, se lee de una variable de entorno y no se imprime.
"""
import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

import requests

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))

# El prompt, el esquema y el turno son los del evaluador desplegado: lo que se mide es lo que corre.
from src.domain.models.sobre import Persona, Vuelta  # noqa: E402  pylint: disable=wrong-import-position
from src.infrastructure.llm.evaluador import (CRITERIOS, ESQUEMA, system_de,  # noqa: E402  pylint: disable=wrong-import-position
                                              turno as turno_del_evaluador)

# Variante «razona»: los campos se generan en orden, así que el modelo escribe primero los idiomas y el motivo, y
# decide después. No cambia nada más. Se midió el 2026-10-31 y fue peor (ADR-0012): queda para comparar.
ESQUEMA_RAZONA = {
    "type": "object",
    "properties": {
        "idioma_del_mensaje": {"type": "string"},
        "idioma_de_la_respuesta": {"type": "string"},
        "motivo": {"type": "string"},
        "criterio": {"type": "string", "enum": CRITERIOS},
        "regla": {"type": "integer"},
        "cumple": {"type": "boolean"},
    },
    "required": ["idioma_del_mensaje", "idioma_de_la_respuesta", "motivo", "criterio", "regla", "cumple"],
    "additionalProperties": False,
}
ESQUEMAS = {"directo": ESQUEMA, "razona": ESQUEMA_RAZONA}
FECHA = "2026-10-31T09:00:00+00:00"


def persona_de(bot: dict) -> Persona:
    return Persona(nombre=bot["nombre"], rol=bot["rol"], tono=bot["tono"], descripcion=bot["descripcion"],
                   reglas=bot["reglas"])


def turno(caso: dict) -> str:
    vueltas = [Vuelta(de=v["de"], texto=v["texto"], enviadaEn=FECHA) for v in caso["historial"]]
    return turno_del_evaluador(vueltas, caso["mensaje"], caso["respuesta"])


def evaluar(url: str, clave: str | None, modelo: str, bot: dict, caso: dict, esquema: dict = ESQUEMA) -> dict:
    cuerpo = {
        "model": modelo,
        "messages": [{"role": "system", "content": system_de(persona_de(bot))},
                     {"role": "user", "content": turno(caso)}],
        "max_tokens": 300, "temperature": 0,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {"type": "json_schema", "json_schema": {"name": "veredicto", "strict": True,
                                                                   "schema": esquema}},
    }
    cabeceras = {"Authorization": f"Bearer {clave}"} if clave else {}
    inicio = time.monotonic()
    respuesta = requests.post(f"{url.rstrip('/')}/v1/chat/completions", json=cuerpo, headers=cabeceras, timeout=120)
    segundos = time.monotonic() - inicio
    respuesta.raise_for_status()
    datos = respuesta.json()
    uso = datos.get("usage") or {}
    try:
        veredicto = json.loads(datos["choices"][0]["message"]["content"])
    except (ValueError, KeyError, IndexError, TypeError):
        veredicto = {"cumple": None, "criterio": "?", "regla": 0, "motivo": "sin JSON"}
    return {"id": caso["id"], "esperado": caso["cumple"], "esperado_criterio": caso.get("criterio", "ninguno"),
            "cumple": veredicto.get("cumple"), "criterio": veredicto.get("criterio"), "regla": veredicto.get("regla"),
            "motivo": veredicto.get("motivo"), "segundos": round(segundos, 3),
            "tokens_entrada": uso.get("prompt_tokens"), "tokens_salida": uso.get("completion_tokens")}


def resumen(resultados: list[dict]) -> dict:
    total = len(resultados)
    aciertos = sum(1 for r in resultados if r["cumple"] == r["esperado"])
    falsos_aprobados = [r["id"] for r in resultados if r["esperado"] is False and r["cumple"] is True]
    falsos_rechazos = [r["id"] for r in resultados if r["esperado"] is True and r["cumple"] is False]
    sin_veredicto = [r["id"] for r in resultados if r["cumple"] is None]
    rechazos_bien = [r for r in resultados if r["esperado"] is False and r["cumple"] is False]
    criterio_bien = sum(1 for r in rechazos_bien if r["criterio"] == r["esperado_criterio"])
    segundos = sorted(r["segundos"] for r in resultados)
    return {
        "casos": total, "aciertos": aciertos, "acierto": round(aciertos / total, 3),
        "falsos_aprobados": falsos_aprobados, "falsos_rechazos": falsos_rechazos, "sin_veredicto": sin_veredicto,
        "criterio_acertado_en_rechazos": f"{criterio_bien}/{len(rechazos_bien)}",
        "segundos_p50": round(statistics.median(segundos), 3),
        "segundos_p95": round(segundos[max(0, int(len(segundos) * 0.95) - 1)], 3),
        "tokens_entrada_media": round(statistics.mean(r["tokens_entrada"] or 0 for r in resultados)),
    }


def main() -> None:
    argumentos = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    argumentos.add_argument("--url", required=True)
    argumentos.add_argument("--modelo", required=True)
    argumentos.add_argument("--salida", required=True)
    argumentos.add_argument("--clave-env", help="la variable de entorno con la clave del servidor, si la pide")
    argumentos.add_argument("--veces", type=int, default=1, help="cuántas veces se repite cada caso")
    argumentos.add_argument("--variante", choices=sorted(ESQUEMAS), default="directo",
                            help="el orden de los campos: el veredicto primero, o después del motivo")
    opciones = argumentos.parse_args()
    clave = os.environ.get(opciones.clave_env) if opciones.clave_env else None

    datos = json.loads((AQUI / "casos-evaluador.json").read_text(encoding="utf-8"))
    resultados = []
    for _ in range(opciones.veces):
        for caso in datos["casos"]:
            resultados.append(evaluar(opciones.url, clave, opciones.modelo, datos["bots"][caso["bot"]], caso,
                                      ESQUEMAS[opciones.variante]))
    salida = {"modelo": opciones.modelo, "variante": opciones.variante, "casos_version": datos["version"],
              "veces": opciones.veces,
              "resumen": resumen(resultados), "resultados": resultados}
    Path(opciones.salida).parent.mkdir(parents=True, exist_ok=True)
    Path(opciones.salida).write_text(json.dumps(salida, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(salida["resumen"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
