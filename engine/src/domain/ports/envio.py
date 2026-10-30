# -*- coding: utf-8 -*-
"""
    Puertos del notificador: el token con el que envía cada bot, el canal de Meta por el que envía, y lo que ya
    envió, para no enviarlo dos veces (ADR-0008, punto 4).
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


class EnvioFallido(Exception):
    """
        Meta no aceptó el envío, pero puede aceptarlo más tarde: no se pudo llegar, está saturado, o hubo un límite
        de envíos. Lleva el tipo o el código de Meta, nunca el texto ni el destinatario.
    """


class TokenRechazado(EnvioFallido):
    """Meta rechazó el token del bot (caducado o revocado): con uno nuevo del gateway puede ir."""


class EnvioRechazado(Exception):
    """
        Meta no lo aceptará nunca así: el destinatario no está en WhatsApp, pasaron las 24 horas de la ventana de
        atención, el número del bot no está registrado... Lleva el código de Meta.
    """


class RegistroNoDisponible(Exception):
    """No se pudo saber, o apuntar, qué se envió. Lleva el tipo de fallo."""


@dataclass(frozen=True)
class Envio:
    """Lo que Meta aceptó: el id que le dio al mensaje enviado y cuándo."""
    message_id: str
    sent_at: datetime


class TokensDelBot(ABC):
    """Los tokens de Meta con los que envía cada bot, que guarda el gateway."""

    @abstractmethod
    async def token_de_whatsapp(self, client_id: UUID, bot_id: UUID) -> str:
        """
        :raises NoEncontrado: si el bot ya no existe o no tiene token de WhatsApp.
        :raises FuenteNoDisponible: si no se pudo saber.
        """

    @abstractmethod
    def olvidar(self, bot_id: UUID) -> None:
        """Deja de recordar el token del bot: Meta lo rechazó y hay que pedirlo otra vez."""


class CanalDeMeta(ABC):
    """Por donde se le envía a la persona."""

    @abstractmethod
    async def enviar_texto(self, token: str, cuenta: str, destinatario: str, texto: str) -> str:
        """
        :param cuenta: el `phone_number_id` desde el que se envía.
        :param destinatario: el número de WhatsApp de la persona.
        :return: el id que Meta dio al mensaje.
        :raises EnvioFallido: si puede ir más tarde (TokenRechazado si es el token).
        :raises EnvioRechazado: si no irá nunca así.
        """

    @abstractmethod
    async def enviar_plantilla(self, token: str, cuenta: str, destinatario: str, nombre: str, idioma: str,
                               valores: list[str]) -> str:
        """
        Una plantilla aprobada de Meta, con sus valores en orden ({{1}}, {{2}}...), dentro o fuera de la ventana de 24
        horas (ADR-0031 del gateway).

        :return: el id que Meta dio al mensaje.
        :raises EnvioFallido: si puede ir más tarde (TokenRechazado si es el token).
        :raises EnvioRechazado: si no irá nunca así: la plantilla en pausa, desactivada o con otros valores.
        """

    @abstractmethod
    async def marcar_leido(self, token: str, cuenta: str, message_id: str, escribiendo: bool) -> None:
        """
        Pone en azul los ticks del mensaje de la persona, y de los anteriores de la conversación.

        :param cuenta: el `phone_number_id` al que escribió la persona.
        :param message_id: el id de Meta del mensaje que se marca.
        :param escribiendo: si además se le muestra «escribiendo…», que dura hasta la respuesta o 25 segundos.
        :raises EnvioFallido: si puede ir más tarde (TokenRechazado si es el token).
        :raises EnvioRechazado: si no irá nunca así.
        """


class Enviados(ABC):
    """Lo que el notificador ya envió, para no enviarlo dos veces si Bifröst se lo entrega otra vez."""

    @abstractmethod
    async def enviado(self, bot_id: UUID, message_id: str) -> Envio | None:
        """
        :param message_id: el id de Meta del mensaje que se responde.
        :return: lo que se envió en respuesta a él, o None si aún nada.
        :raises RegistroNoDisponible: si no se pudo saber.
        """

    @abstractmethod
    async def apuntar(self, bot_id: UUID, message_id: str, envio: Envio) -> None:
        """
        :raises RegistroNoDisponible: si no se pudo apuntar.
        """
