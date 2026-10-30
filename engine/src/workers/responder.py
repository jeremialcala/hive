# -*- coding: utf-8 -*-
"""
    El de respuesta (E5 de engine/docs/00-project/plan-de-los-workers.md). Lee cada pedido del stream `MOTOR` con el
    consumidor `responder`, escribe la contestación y la publica en
    `motor.respuesta.{partición}.{botId}.{contactId}`, para el notificador.

        python -m src.workers.responder

    - **Con `modelo`**, la escribe el LLM de Hive (ADR-0010), con la persona del bot y sus reglas en el `system`, y
      las vueltas y el mensaje en turnos de usuario y del asistente.
    - **Con un texto fijo**, no llama a nadie: contesta el texto de su motivo (decisión de Jeremi del 2026-10-20).
    - **Si el modelo rechaza**, contesta el texto fijo de «no puedo», y lo deja en el log (ADR-0009, punto 4).
    - **Si el modelo no contesta**, o su respuesta se corta o viene vacía, el mensaje queda en espera y Bifröst lo
      entrega otra vez. En la última entrega contesta el texto fijo de «no disponible».

    El sobre que publica no lleva la persona ni las vueltas: el notificador solo necesita el texto y a quién.
"""
import asyncio
import logging

from shared import bus, tracing
from shared.workers import Worker
from src.domain.models.sobre import Contestacion, MensajePedido, MensajeRespondido, subject_respuesta
from src.domain.ports.redactor import RedaccionFallida, Redactor
from src.infrastructure.llm import ClienteLlm, LlmRedactor
from src.infrastructure.llm import caliente
from src.infrastructure.llm.cliente import leer_clave
from src.workers.runtime import Descartado, Resultado, ejecutar, entregas, leer_del_motor, publicador
from src.workers.settings import ResponderSettings

log = logging.getLogger(__name__)

NOMBRE = Worker.RESPONDER.value
STREAM, CONSUMIDOR = "MOTOR", NOMBRE

# Los textos fijos, en el idioma de los bots de hoy. Cuando un bot pueda tener los suyos, saldrán de su
# configuración.
TEXTOS_FIJOS = {
    "no_puedo": "Lo siento, con eso no puedo ayudarte.",
    "solo_texto": "Por ahora solo puedo leer mensajes de texto. ¿Me lo escribes?",
    "no_disponible": "Ahora mismo no puedo responderte. Inténtalo de nuevo en un rato, por favor.",
}


class Responder:
    """Lo que hace el worker con cada pedido."""

    def __init__(self, redactor: Redactor, publisher: bus.EventPublisher, max_deliver: int):
        self._redactor = redactor
        self._publisher = publisher
        # Sin límite (-1), nunca es la última entrega: se espera siempre.
        self._max_deliver = max_deliver

    def _ultima(self, mensaje) -> bool:
        return 0 < self._max_deliver <= entregas(mensaje)

    async def __call__(self, mensaje) -> Resultado:
        try:
            sobre, particion = leer_del_motor(mensaje, MensajePedido, "pedido")
        except Descartado as e:
            log.warning("Un mensaje de %s se descarta: %s", STREAM, e)
            return Resultado.DESCARTADO
        traza = (mensaje.headers or {}).get("traceparent")
        with tracing.span("engine.responder", parent=traza, kind=tracing.SpanKind.CONSUMER, **{
                "gateway.bot_id": str(sobre.botId), "gateway.client_id": str(sobre.clientId),
                "gateway.channel": sobre.channel, "messaging.system": "nats"}) as current:
            resultado = await self._responder(mensaje, sobre, particion)
            current.set(**{"gateway.outcome": resultado.value})
            return resultado

    async def _contestacion(self, mensaje, sobre: MensajePedido) -> Contestacion | Resultado:
        pedido = sobre.pedido
        if pedido.respuesta != "modelo":
            return Contestacion(texto=TEXTOS_FIJOS[pedido.respuesta], fuente="fijo")
        if pedido.persona is None or not sobre.entrada.texto:
            # El coordinador nunca lo pide así: no hay con qué escribir. Se contesta en vez de callar.
            log.error("Un pedido del bot %s con el modelo no trae la persona o el texto", sobre.botId)
            return Contestacion(texto=TEXTOS_FIJOS["no_disponible"], fuente="fijo")
        try:
            redaccion = await self._redactor.redactar(pedido.persona, pedido.vueltas, sobre.entrada.texto)
        except RedaccionFallida as e:
            if not self._ultima(mensaje):
                log.warning("Sin respuesta para un mensaje del bot %s, se intentará otra vez: %s", sobre.botId, e)
                return Resultado.EN_ESPERA
            log.error("Sin respuesta para un mensaje del bot %s en la última entrega: %s", sobre.botId, e)
            return Contestacion(texto=TEXTOS_FIJOS["no_disponible"], fuente="fijo")
        if redaccion.rechazada:
            log.warning("%s rechazó responder un mensaje del bot %s: se contesta el texto fijo", redaccion.modelo,
                        sobre.botId)
            return Contestacion(texto=TEXTOS_FIJOS["no_puedo"], fuente="fijo")
        return Contestacion(texto=redaccion.texto, fuente="modelo", modelo=redaccion.modelo)

    async def _responder(self, mensaje, sobre: MensajePedido, particion: str) -> Resultado:
        contestacion = await self._contestacion(mensaje, sobre)
        if isinstance(contestacion, Resultado):
            return contestacion
        lo_clasificado = sobre.model_dump(exclude={"pedido"})
        salida = MensajeRespondido(**lo_clasificado, respuesta=contestacion)
        subject = subject_respuesta(particion, sobre.botId, sobre.contact.uuid)
        traza = tracing.traceparent()
        headers = {"traceparent": traza} if traza else {}
        try:
            await self._publisher.publish(subject, salida.model_dump_json().encode(),
                                          f"respuesta:{sobre.botId}:{sobre.messageId}", headers)
        except bus.BusUnavailable as e:
            log.error("La respuesta a un mensaje del bot %s no se pudo publicar: %s", sobre.botId, e)
            return Resultado.EN_ESPERA
        log.info("Respuesta a un mensaje del bot %s: %s, %d caracteres", sobre.botId, contestacion.fuente,
                 len(contestacion.texto))
        return Resultado.CONFIRMADO


async def preparar(settings: ResponderSettings, max_deliver: int):
    """
        Lo que el worker abre al arrancar: el cliente del LLM de Hive, con su clave, y el publicador. Devuelve cómo
        atender cada mensaje y cómo cerrar lo abierto.
    """
    cliente = ClienteLlm(settings.llm_url, leer_clave(settings.llm_api_key_file), settings.llm_model,
                         settings.responder_timeout)
    publisher = publicador(settings, NOMBRE)
    redactor = LlmRedactor(cliente, settings.responder_max_tokens)
    # Que el LLM no se enfríe mientras no llegan mensajes.
    parar = caliente.arrancar(redactor.llm, settings.llm_calentar_cada)

    async def cerrar() -> None:
        parar()
        await publisher.close()

    return Responder(redactor, publisher, max_deliver), cerrar


def main() -> None:
    """El worker: su configuración y su bucle sobre MOTOR."""
    settings = ResponderSettings()
    asyncio.run(ejecutar(NOMBRE, settings, STREAM, CONSUMIDOR,
                         lambda max_deliver, _conexion: preparar(settings, max_deliver)))


if __name__ == "__main__":
    main()
