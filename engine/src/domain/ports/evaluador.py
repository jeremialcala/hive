# -*- coding: utf-8 -*-
"""
    Puerto del evaluador (E8, ADR-0012): si una respuesta propuesta cumple las indicaciones del motor y la persona y
    las reglas del bot, antes de enviarla.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass

from src.domain.models.sobre import Criterio, Persona, Vuelta


class EvaluacionFallida(Exception):
    """No hubo juicio: la llamada falló, se cortó o no dio un veredicto válido. Lleva el tipo de fallo, no el texto."""


@dataclass(frozen=True)
class Juicio:
    """Lo que dice el evaluador. `regla` empieza en 1, y es 0 si no viene al caso."""
    cumple: bool
    criterio: Criterio
    regla: int
    motivo: str
    modelo: str


class Evaluador(ABC):
    """Juzga una respuesta propuesta."""

    @abstractmethod
    async def juzgar(self, persona: Persona, vueltas: list[Vuelta], texto: str, respuesta: str) -> Juicio:
        """
        :param persona: quién es el bot y sus reglas.
        :param vueltas: los mensajes anteriores, el más antiguo primero.
        :param texto: lo que la persona acaba de decir.
        :param respuesta: lo que se le propone contestar.
        :raises EvaluacionFallida: si no hubo juicio.
        """
