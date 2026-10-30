# -*- coding: utf-8 -*-
"""
    El servidor de llama.cpp de Hive (ADR-0010), por su API compatible con la de OpenAI:

        POST {url}/v1/chat/completions      Authorization: Bearer <clave>

    Una llamada por pregunta, sin reintentos aquí: si falla, Bifröst entrega el mensaje otra vez. Ni el log ni las
    excepciones llevan lo que se envió ni lo que volvió: solo el tipo de fallo, el estado HTTP y los tokens.
"""
import asyncio
from dataclasses import dataclass

import requests


def leer_clave(path: str) -> str:
    """La clave del servidor, del fichero que monta el despliegue. Sin ella el worker no arranca."""
    try:
        with open(path, "r", encoding="utf-8") as fichero:
            clave = fichero.read().strip()
    except OSError as e:
        raise SystemExit(f"No se pudo leer la clave del LLM: {type(e).__name__}") from None
    if not clave:
        raise SystemExit("El fichero de la clave del LLM está vacío")
    return clave


class LlmNoDisponible(Exception):
    """El servidor no respondió, o no como se esperaba. Lleva el tipo de fallo, nunca el texto."""


@dataclass(frozen=True)
class Completado:
    """Lo que volvió de una pregunta."""
    texto: str
    # `stop` si terminó; `length` si se cortó por max_tokens.
    fin: str
    tokens_entrada: int | None
    tokens_salida: int | None


class ClienteLlm:
    """El servidor de llama.cpp, con su clave."""

    def __init__(self, url: str, clave: str, modelo: str, timeout: float):
        self._url = url.rstrip("/")
        self._clave = clave
        self.modelo = modelo
        self._timeout = timeout

    def _preguntar(self, cuerpo: dict) -> Completado:
        try:
            respuesta = requests.post(f"{self._url}/v1/chat/completions", json=cuerpo, timeout=self._timeout,
                                      headers={"Authorization": f"Bearer {self._clave}"}, allow_redirects=False)
        except (requests.RequestException, OSError) as e:
            # Solo el tipo: el texto de estas excepciones lleva la dirección y más.
            raise LlmNoDisponible(type(e).__name__) from None
        if respuesta.status_code != 200:
            raise LlmNoDisponible(f"el servidor respondió {respuesta.status_code}")
        try:
            datos = respuesta.json()
            eleccion = datos["choices"][0]
            texto = eleccion["message"].get("content") or ""
            fin = eleccion.get("finish_reason") or ""
            uso = datos.get("usage") or {}
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise LlmNoDisponible("respuesta inesperada") from None
        return Completado(texto=texto, fin=fin, tokens_entrada=uso.get("prompt_tokens"),
                          tokens_salida=uso.get("completion_tokens"))

    async def preguntar(self, mensajes: list[dict], max_tokens: int, esquema: dict | None = None) -> Completado:
        """
        :param mensajes: la conversación, con el `system` primero.
        :param esquema: si lo hay, la respuesta queda atada a ese JSON Schema (llama.cpp lo convierte en gramática).
        :raises LlmNoDisponible: si no hubo respuesta.
        """
        cuerpo = {"model": self.modelo, "messages": mensajes, "max_tokens": max_tokens,
                  # Sin pensamiento: el modelo contesta directamente, y lo que piensa no gasta el tope.
                  "chat_template_kwargs": {"enable_thinking": False}}
        if esquema is not None:
            cuerpo["response_format"] = {"type": "json_schema", "json_schema": {"name": "respuesta", "strict": True,
                                                                                "schema": esquema}}
        return await asyncio.to_thread(self._preguntar, cuerpo)
