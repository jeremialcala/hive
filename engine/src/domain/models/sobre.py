# -*- coding: utf-8 -*-
"""
    El sobre que se pasan los workers del motor por el stream `MOTOR` de Bifröst (ADR-0008, punto 6): lo que llegó
    del gateway, sin lo que ningún paso necesita, y lo que añade cada paso. Ningún worker guarda nada entre
    mensajes: todo lo que el siguiente necesita va aquí. Es el contrato interno del motor, versionado con
    `schemaVersion` (docs/02-design/contracts/asyncapi-motor.yaml).

    Los campos que vienen del gateway conservan sus nombres del contrato v2 (`shared.contract`); los que añade el
    motor van en el idioma del motor. Se lee estricto, como el contrato v2: un campo que falta o de otro tipo es un
    sobre roto, no un valor por defecto.

        motor.clasificado.{partición}.{botId}.{contactId}    MensajeClasificado, del clasificador al coordinador
        motor.pedido.{partición}.{botId}.{contactId}         MensajePedido, del coordinador al de respuesta
        motor.respuesta.{partición}.{botId}.{contactId}      MensajeRespondido, del de respuesta al notificador
"""
from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# 2 desde ADR-0010: lo que responde el LLM se dice `modelo`, no `claude`. Todos los workers se despliegan juntos.
ESQUEMA = 2

# La partición es la del evento del gateway, que nadie vuelve a calcular (ADR-0008, punto 2).
PARTICION = r"^[0-9]{1,2}$"

# Lo que decide el clasificador de la entrada. Los cuatro primeros los da el modelo; los demás, el worker:
#   rechazo     el modelo rechazó el mensaje (llama.cpp no lo dice aparte: queda para otro proveedor)
#   error       no hubo veredicto válido tras todos los intentos
#   sin_texto   el mensaje no trae texto que clasificar (una imagen sin pie, una ubicación...)
Motivo = Literal["ninguno", "inyeccion", "abuso", "otro", "rechazo", "error", "sin_texto"]
MOTIVOS_DEL_MODELO = ("ninguno", "inyeccion", "abuso", "otro")

# La intención, entre unas pocas genéricas mientras el clasificador no lea los intents del bot. Ningún texto libre
# del modelo pasa de aquí (ADR-0009, punto 3).
Etiqueta = Literal["saludo", "pregunta", "pedido", "queja", "despedida", "otro"]
ETIQUETAS = ("saludo", "pregunta", "pedido", "queja", "despedida", "otro")


# Cómo se responde (E4, decisión de Jeremi del 2026-10-20): con el modelo, o con un texto fijo del de respuesta, sin
# llamarlo, cuando el mensaje no sigue. Así la persona siempre recibe algo.
Respuesta = Literal["modelo", "no_puedo", "solo_texto", "no_disponible"]
RESPUESTA_POR_MOTIVO = {"inyeccion": "no_puedo", "abuso": "no_puedo", "otro": "no_puedo", "rechazo": "no_puedo",
                        "sin_texto": "solo_texto", "error": "no_disponible"}

# Las vueltas que el coordinador pasa como mucho: las 10 últimas de la persona y lo que vino después (RF15 del
# gateway) caben de sobra.
MAX_VUELTAS = 40


def subject_clasificado(particion: str, bot_id, contact_id) -> str:
    """Dónde publica el clasificador, en el stream MOTOR."""
    return f"motor.clasificado.{particion}.{bot_id}.{contact_id}"


def subject_pedido(particion: str, bot_id, contact_id) -> str:
    """Dónde publica el coordinador, en el stream MOTOR."""
    return f"motor.pedido.{particion}.{bot_id}.{contact_id}"


def subject_respuesta(particion: str, bot_id, contact_id) -> str:
    """Dónde publica el de respuesta, en el stream MOTOR."""
    return f"motor.respuesta.{particion}.{bot_id}.{contact_id}"


class _Estricto(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ContactoDelCanal(_Estricto):
    """
        A quién se responde: el contacto del gateway sin su nombre, que ningún paso necesita. Dato personal: va al
        notificador, nunca al modelo.
    """
    uuid: UUID
    waId: Optional[str] = None
    fbId: Optional[str] = None
    igsid: Optional[str] = None


class Entrada(_Estricto):
    """Lo que dijo la persona, saneado: el texto y el tipo de cada adjunto, nunca su dirección."""
    texto: str = Field(max_length=4096)
    adjuntos: list[str] = Field(default_factory=list, max_length=10)


class Entidad(_Estricto):
    """Un dato que el clasificador sacó del texto de la persona."""
    nombre: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    valor: str = Field(max_length=200)


class Clasificacion(_Estricto):
    """
        El veredicto del clasificador. Fail-closed (ADR-0005, que ADR-0009 mantiene): solo `permitido` deja seguir
        al mensaje, y solo cuando el modelo no vio ningún motivo para pararlo.
    """
    permitido: bool
    motivo: Motivo
    etiqueta: Optional[Etiqueta] = None
    confianza: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    entidades: list[Entidad] = Field(default_factory=list, max_length=10)
    # El modelo que decidió, o None si no se le preguntó.
    modelo: Optional[str] = None

    @classmethod
    def sin_veredicto(cls, motivo: Literal["rechazo", "error", "sin_texto"],
                      modelo: Optional[str] = None) -> "Clasificacion":
        """Un mensaje que no sigue, sin que el modelo lo haya clasificado."""
        return cls(permitido=False, motivo=motivo, modelo=modelo)


class _Sobre(_Estricto):
    schemaVersion: Literal[2]
    botId: UUID
    clientId: UUID
    messageId: str = Field(min_length=1)
    channel: Literal["whatsapp", "messenger", "instagram"]
    receivedAt: datetime
    orderKey: str = Field(min_length=1)
    contact: ContactoDelCanal
    # Desde dónde se responde: el `phone_number_id` de WhatsApp, o el id de la página de Messenger o de la cuenta de
    # Instagram, al que escribió la persona. No es un dato personal: es del negocio.
    cuentaDelBot: Optional[str] = Field(default=None, pattern=r"^[0-9]{1,32}$")


class MensajeClasificado(_Sobre):
    """Lo que publica el clasificador: el mensaje, lo que dijo la persona y el veredicto."""
    entrada: Entrada
    clasificacion: Clasificacion


class Persona(_Estricto):
    """Quién es el bot y sus reglas, como las guarda el gateway (`GET /chat/{bot_id}/rules`)."""
    nombre: str
    rol: str
    tono: str
    descripcion: str = ""
    reglas: list[str] = Field(default_factory=list)


class Vuelta(_Estricto):
    """Un mensaje anterior de la conversación, de la persona o del bot, como lo guarda el historial del gateway."""
    de: Literal["persona", "bot"]
    texto: str = Field(max_length=4096)
    adjuntos: list[str] = Field(default_factory=list, max_length=10)
    enviadaEn: datetime


class Pedido(_Estricto):
    """
        Lo que el coordinador pide al de respuesta. Con `modelo`, la persona del bot y las vueltas anteriores, la más
        antigua primero; con un texto fijo, ninguna de las dos: no hacen falta.
    """
    respuesta: Respuesta
    persona: Optional[Persona] = None
    vueltas: list[Vuelta] = Field(default_factory=list, max_length=MAX_VUELTAS)


class MensajePedido(MensajeClasificado):
    """Lo que publica el coordinador: lo clasificado y lo que hace falta para responderlo."""
    pedido: Pedido


class Contestacion(_Estricto):
    """Lo que se le envía a la persona: solo texto (ADR-0009, punto 5), del tamaño que admite WhatsApp."""
    texto: str = Field(min_length=1, max_length=4096)
    # Del modelo, o uno de los textos fijos del de respuesta.
    fuente: Literal["modelo", "fijo"]
    # El modelo que lo escribió, o None si es un texto fijo.
    modelo: Optional[str] = None


class MensajeRespondido(MensajeClasificado):
    """
        Lo que publica el de respuesta: lo clasificado y la contestación. Sin la persona del bot ni las vueltas del
        pedido: el notificador no las necesita, y son datos personales que no tienen por qué seguir viajando.
    """
    respuesta: Contestacion
