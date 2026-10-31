# -*- coding: utf-8 -*-
"""
    Puerto del que resume una conversación (HIG-143, ADR-0017 del gateway): con el resumen que había y las vueltas
    que salen del contexto, el resumen nuevo de todo lo que ya no está en las vueltas.
"""
from abc import ABC, abstractmethod

from src.domain.models.sobre import Vuelta


class ResumenFallido(Exception):
    """No hubo resumen: la llamada falló, se cortó o volvió vacía. Lleva el tipo de fallo, nunca el texto."""


class Resumidor(ABC):
    """Resume lo que sale del contexto."""

    @abstractmethod
    async def resumir(self, anterior: str | None, vueltas: list[Vuelta]) -> str:
        """
        :param anterior: el resumen que había, o None si es el primero.
        :param vueltas: las que salen del contexto, la más antigua primero.
        :raises ResumenFallido: si no hubo resumen.
        """
