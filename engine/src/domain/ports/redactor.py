# -*- coding: utf-8 -*-
"""
    Puerto del que escribe la respuesta: con la persona del bot y sus reglas, las vueltas anteriores y lo que la
    persona acaba de decir, el texto que se le contesta (ADR-0009, punto 4).
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass

from src.domain.models.sobre import Persona, Vuelta


class RedaccionFallida(Exception):
    """
        No hubo respuesta: la llamada falló, se cortó o volvió vacía. Lleva el tipo de fallo, nunca el texto. Quien
        llama vuelve a intentarlo mientras le queden entregas.
    """


@dataclass(frozen=True)
class Redaccion:
    """Lo que volvió. Sin texto si el modelo, y los que lo sustituyen, rechazaron escribirla."""
    texto: str | None
    modelo: str

    @property
    def rechazada(self) -> bool:
        """Si nadie la escribió: el modelo la rechazó."""
        return self.texto is None


class Redactor(ABC):
    """Escribe la respuesta a un mensaje."""

    @abstractmethod
    async def redactar(self, persona: Persona, vueltas: list[Vuelta], texto: str) -> Redaccion:
        """
        :param persona: quién es el bot y sus reglas.
        :param vueltas: los mensajes anteriores, el más antiguo primero.
        :param texto: lo que la persona acaba de decir, ya saneado.
        :raises RedaccionFallida: si no hubo respuesta.
        """
