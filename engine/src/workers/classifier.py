# -*- coding: utf-8 -*-
"""
    El clasificador (E3 de engine/docs/00-project/plan-de-los-workers.md). Lee cada mensaje identificado del stream
    `MENSAJES` con el consumidor `classifier`, decide con el LLM de Hive si se atiende y cuál es su intención, y
    publica el sobre en `motor.clasificado.{partición}.{botId}.{contactId}`, para el coordinador.

        python -m src.workers.classifier

    Fail-closed (ADR-0009, punto 3): un mensaje sin veredicto sale igual, con `permitido=false`, para que el
    coordinador lo sepa y no se quede sin respuesta. Pero antes se vuelve a intentar: si el LLM no contesta o su
    respuesta no valida, el mensaje queda en espera y Bifröst lo entrega otra vez, y solo en la última entrega sale
    con el motivo `error`. Un mensaje sin texto no llega al LLM.

    Solo el texto va al LLM, que corre en la LAN (ADR-0010). El sobre lleva el contacto sin el nombre (ADR-0008,
    punto 6), y el log no lleva ni el texto, ni el número, ni el id del mensaje, que en WhatsApp lleva el número
    dentro.
"""
import asyncio
import json
import logging
import re

from pydantic import ValidationError

from shared import bus, tracing
from shared.contract import IdentifiedMessage, what_was_said
from shared.workers import Worker
from src.domain.models.sobre import (
    ESQUEMA, PARTICION, Clasificacion, ContactoDelCanal, Entrada, MensajeClasificado, subject_clasificado,
)
from src.domain.ports.clasificador_de_entrada import ClasificacionFallida, ClasificadorDeEntrada
from src.infrastructure.adapters.sanitize import sanitizar_texto
from src.infrastructure.llm import ClienteLlm, LlmClasificador
from src.infrastructure.llm import caliente
from src.infrastructure.llm.cliente import leer_clave
from src.workers.runtime import Resultado, ejecutar, entregas, publicador
from src.workers.settings import ClassifierSettings

log = logging.getLogger(__name__)

NOMBRE = Worker.CLASSIFIER.value
STREAM, CONSUMIDOR = "MENSAJES", NOMBRE
_PARTICION = re.compile(PARTICION)
_CUENTA = re.compile(r"^[0-9]{1,32}$")


class Descartado(Exception):
    """Un mensaje que nunca se podrá clasificar. Lleva por qué, nunca qué traía."""


def leer(mensaje) -> tuple[IdentifiedMessage, str]:
    """El evento del gateway y su partición, comprobando que el sujeto dice lo mismo que el evento."""
    try:
        datos = json.loads(mensaje.data)
    except (ValueError, UnicodeDecodeError):
        raise Descartado("no es JSON") from None
    try:
        evento = IdentifiedMessage.model_validate(datos)
    except ValidationError as e:
        # Sus errores llevan los valores: solo se dice cuántos.
        raise Descartado(f"no cumple el contrato v2 ({e.error_count()} errores)") from None
    partes = (mensaje.subject or "").split(".")
    if len(partes) != 5 or partes[:2] != ["gateway", "message"] or not _PARTICION.match(partes[2]):
        raise Descartado("sujeto inesperado")
    if partes[3] != str(evento.botId) or partes[4] != str(evento.contact.uuid):
        raise Descartado("el sujeto no es el del evento")
    return evento, partes[2]


def entrada(evento: IdentifiedMessage) -> Entrada:
    """Lo que dijo la persona, como lo lee el historial del gateway, saneado."""
    texto, adjuntos, _ = what_was_said(evento.channel, evento.message)
    return Entrada(texto=sanitizar_texto(texto), adjuntos=list(adjuntos)[:10])


def cuenta_del_bot(evento: IdentifiedMessage) -> str | None:
    """
        A qué número o página escribió la persona, que es desde donde se le responde: en WhatsApp, el
        `phone_number_id` de los metadatos del cambio; en Messenger e Instagram, el destinatario del mensaje.
    """
    if evento.channel == "whatsapp":
        metadata = evento.message.get("metadata")
        valor = metadata.get("phone_number_id") if isinstance(metadata, dict) else None
    else:
        destinatario = evento.message.get("recipient")
        valor = destinatario.get("id") if isinstance(destinatario, dict) else None
    return valor if isinstance(valor, str) and _CUENTA.match(valor) else None


def sobre(evento: IdentifiedMessage, lo_dicho: Entrada, clasificacion: Clasificacion) -> MensajeClasificado:
    """El sobre para el coordinador: el evento sin lo que ningún paso necesita, más lo dicho y el veredicto."""
    contacto = evento.contact
    return MensajeClasificado(
        schemaVersion=ESQUEMA, botId=evento.botId, clientId=evento.clientId, messageId=evento.messageId,
        channel=evento.channel, receivedAt=evento.receivedAt, orderKey=evento.orderKey,
        contact=ContactoDelCanal(uuid=contacto.uuid, waId=contacto.waId, fbId=contacto.fbId, igsid=contacto.igsid),
        cuentaDelBot=cuenta_del_bot(evento), entrada=lo_dicho, clasificacion=clasificacion)


class Clasificar:
    """Lo que hace el worker con cada mensaje."""

    def __init__(self, clasificador: ClasificadorDeEntrada, publisher: bus.EventPublisher, max_deliver: int):
        self._clasificador = clasificador
        self._publisher = publisher
        # Sin límite (-1), nunca es la última entrega: se espera siempre.
        self._max_deliver = max_deliver

    def _ultima(self, mensaje) -> bool:
        return 0 < self._max_deliver <= entregas(mensaje)

    async def __call__(self, mensaje) -> Resultado:
        try:
            evento, particion = leer(mensaje)
        except Descartado as e:
            log.warning("Un mensaje de %s se descarta: %s", STREAM, e)
            return Resultado.DESCARTADO
        traza = (mensaje.headers or {}).get("traceparent")
        with tracing.span("engine.classifier", parent=traza, kind=tracing.SpanKind.CONSUMER, **{
                "gateway.bot_id": str(evento.botId), "gateway.client_id": str(evento.clientId),
                "gateway.channel": evento.channel, "messaging.system": "nats"}) as current:
            resultado = await self._clasificar(mensaje, evento, particion)
            current.set(**{"gateway.outcome": resultado.value})
            return resultado

    async def _clasificar(self, mensaje, evento: IdentifiedMessage, particion: str) -> Resultado:
        lo_dicho = entrada(evento)
        if not lo_dicho.texto:
            clasificacion = Clasificacion.sin_veredicto("sin_texto")
        else:
            try:
                clasificacion = await self._clasificador.clasificar(lo_dicho.texto)
            except ClasificacionFallida as e:
                if not self._ultima(mensaje):
                    log.warning("Sin veredicto para un mensaje del bot %s, se intentará otra vez: %s",
                                evento.botId, e)
                    return Resultado.EN_ESPERA
                log.error("Sin veredicto para un mensaje del bot %s en la última entrega, no sigue: %s",
                          evento.botId, e)
                clasificacion = Clasificacion.sin_veredicto("error")
        subject = subject_clasificado(particion, evento.botId, evento.contact.uuid)
        traza = tracing.traceparent()
        headers = {"traceparent": traza} if traza else {}
        try:
            await self._publisher.publish(subject, sobre(evento, lo_dicho, clasificacion).model_dump_json().encode(),
                                          f"clasificado:{evento.botId}:{evento.messageId}", headers)
        except bus.BusUnavailable as e:
            log.error("El mensaje clasificado del bot %s no se pudo publicar: %s", evento.botId, e)
            return Resultado.EN_ESPERA
        log.info("Mensaje del bot %s clasificado: %s, %s", evento.botId,
                 "permitido" if clasificacion.permitido else "no sigue", clasificacion.motivo)
        return Resultado.CONFIRMADO


async def preparar(settings: ClassifierSettings, max_deliver: int):
    """
        Lo que el worker abre al arrancar: el cliente del LLM de Hive, con su clave, y el publicador. Devuelve cómo
        atender cada mensaje y cómo cerrar lo abierto.
    """
    cliente = ClienteLlm(settings.llm_url, leer_clave(settings.llm_api_key_file), settings.llm_model,
                         settings.classifier_timeout)
    publisher = publicador(settings, NOMBRE)
    clasificador = LlmClasificador(cliente, settings.classifier_max_tokens)
    # Que el LLM no se enfríe mientras no llegan mensajes.
    parar = caliente.arrancar(clasificador.llm, settings.llm_calentar_cada)

    async def cerrar() -> None:
        parar()
        await publisher.close()

    return Clasificar(clasificador, publisher, max_deliver), cerrar


def main() -> None:
    """El worker: su configuración y su bucle sobre MENSAJES."""
    settings = ClassifierSettings()
    asyncio.run(ejecutar(NOMBRE, settings, STREAM, CONSUMIDOR,
                         lambda max_deliver, _conexion: preparar(settings, max_deliver)))


if __name__ == "__main__":
    main()
