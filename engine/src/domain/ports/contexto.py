# -*- coding: utf-8 -*-
"""
    Puerto del contexto de cada conversación (ADR-0017 del gateway, HIG-19): las últimas vueltas, la memoria de trabajo
    del coordinador. Vive 24 horas desde la última escritura; el historial del gateway sigue siendo la fuente de verdad,
    y lo que aquí no está se le pide a él.

    Cada vuelta guardada lleva el id de Meta del mensaje de la persona al que pertenece: el suyo, o el que responde
    el bot. Así se sabe qué hay antes de un mensaje que se atiende otra vez.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from uuid import UUID

from src.domain.models.sobre import Vuelta


class ContextoNoDisponible(Exception):
    """No se pudo leer o escribir: Bifröst o Andvari no respondieron. Lleva el tipo de fallo, nunca el contenido."""


class Choque(Exception):
    """Otro coordinador escribió la conversación desde que se leyó: hay que leerla otra vez."""


@dataclass(frozen=True)
class Apunte:
    """Una vuelta guardada, con el id de Meta del mensaje de la persona al que pertenece."""
    vuelta: Vuelta
    mensaje: str


@dataclass(frozen=True)
class Conversacion:
    """Lo que hay de una conversación, la vuelta más antigua primero, y la revisión con que se leyó (0: no había)."""
    apuntes: list[Apunte] = field(default_factory=list)
    revision: int = 0


class Contexto(ABC):
    """Dónde guarda el coordinador el contexto de cada conversación."""

    @abstractmethod
    async def leer(self, client_id: UUID, bot_id: UUID, contact_id: UUID) -> Conversacion | None:
        """
        :return: la conversación, o None si no hay nada guardado (caducó, se perdió o es nueva).
        :raises ContextoNoDisponible: si no se pudo leer.
        """

    @abstractmethod
    async def guardar(self, client_id: UUID, bot_id: UUID, contact_id: UUID, apuntes: list[Apunte],
                      revision: int) -> int:
        """
        Guarda la conversación solo si sigue en la revisión `revision` (0: si no había ninguna).

        :return: la revisión nueva.
        :raises Choque: si otro la escribió entre medias.
        :raises ContextoNoDisponible: si no se pudo escribir.
        """

    @abstractmethod
    async def olvidar(self, client_id: UUID, bot_id: UUID | None = None, contact_id: UUID | None = None) -> None:
        """
        Borra las conversaciones de un contacto, de un bot o de un cliente: lo que el gateway suprimió.

        :raises ContextoNoDisponible: si no se pudo borrar.
        """
