# -*- coding: utf-8 -*-
"""
    Puerto del clasificador de la entrada: en un solo paso, si el mensaje se atiende y cuál es su intención. Es lo
    que hacían por separado `GuardrailsPolicy` e `IntentEngine`, con NeMo y Ollama, y ahora hace una sola llamada a
    el LLM de Hive (ADR-0009, punto 3, y ADR-0010).
"""
from abc import ABC, abstractmethod
from typing import Sequence

from src.domain.models.sobre import Clasificacion, TemaDelBot


class ClasificacionFallida(Exception):
    """
        No hubo veredicto: la llamada falló o la respuesta no valida. Lleva el tipo de fallo, nunca el texto. Quien
        llama vuelve a intentarlo mientras le queden entregas, y en la última deja el mensaje sin seguir.
    """


class ClasificadorDeEntrada(ABC):
    """Decide si un texto se atiende y cuál es su intención."""

    @abstractmethod
    async def clasificar(self, texto: str, temas: Sequence[TemaDelBot] = ()) -> Clasificacion:
        """
        :param texto: lo que dijo la persona, ya saneado. Es lo único que recibe de ella: ni el número, ni el
            nombre, ni el cliente.
        :param temas: los temas propios del bot, por encima de la base de la plataforma (RF27, HIG-103).
        :return: el veredicto. Un rechazo del modelo es un veredicto (`motivo="rechazo"`), no un fallo.
        :raises ClasificacionFallida: si no hubo veredicto.
        """
