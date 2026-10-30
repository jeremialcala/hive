# -*- coding: utf-8 -*-
"""
    El coordinador (E4 de engine/docs/00-project/plan-de-los-workers.md). Lee cada mensaje clasificado del stream
    `MOTOR` con el consumidor `coordinator` y pide la respuesta en `motor.pedido.{partición}.{botId}.{contactId}`,
    para el de respuesta.

        python -m src.workers.coordinator

    - **Un mensaje que sigue** se pide al modelo: el coordinador pregunta al gateway la persona del bot con sus
      reglas y las últimas vueltas de la conversación, con su clave para el cliente dueño del bot, y las pone en el
      sobre. La persona la recuerda 5 minutos: un cambio de las reglas tarda eso en llegar a las respuestas.
    - **Un mensaje que no sigue** se pide con un texto fijo según su motivo, sin preguntar nada (decisión de Jeremi
      del 2026-10-20): la persona siempre recibe algo.
    - **Si el gateway no responde**, el mensaje queda en espera y Bifröst lo entrega otra vez. En la última entrega
      se pide el texto fijo de «no disponible», para no dejar a nadie sin respuesta.
    - **Si el bot o el contacto ya no existen**, no hay a quién responder: se descarta.

    En el esqueleto no llama al enlace (ADR-0008) ni guarda el contexto en el bucket de Bifröst (ADR-0017 del
    gateway): pide las vueltas al gateway en cada mensaje.
"""
import asyncio
import logging

from shared import bus, tracing
from shared.engine_key_store import EngineKeyStore
from shared.workers import Worker
from src.domain.models.sobre import RESPUESTA_POR_MOTIVO, MensajeClasificado, MensajePedido, Pedido, subject_pedido
from src.domain.ports.fuente_del_bot import FuenteDelBot, FuenteNoDisponible, NoEncontrado
from src.infrastructure.gateway import ApiDelGateway
from src.infrastructure.gateway.api import clave_publica_del_gateway
from src.workers.runtime import Descartado, Resultado, ejecutar, entregas, leer_del_motor, publicador
from src.workers.settings import CoordinatorSettings

log = logging.getLogger(__name__)

NOMBRE = Worker.COORDINATOR.value
STREAM, CONSUMIDOR = "MOTOR", NOMBRE


class Coordinar:
    """Lo que hace el worker con cada mensaje."""

    def __init__(self, fuente: FuenteDelBot, publisher: bus.EventPublisher, max_deliver: int):
        self._fuente = fuente
        self._publisher = publisher
        # Sin límite (-1), nunca es la última entrega: se espera siempre.
        self._max_deliver = max_deliver

    def _ultima(self, mensaje) -> bool:
        return 0 < self._max_deliver <= entregas(mensaje)

    async def __call__(self, mensaje) -> Resultado:
        try:
            sobre, particion = leer_del_motor(mensaje, MensajeClasificado, "clasificado")
        except Descartado as e:
            log.warning("Un mensaje de %s se descarta: %s", STREAM, e)
            return Resultado.DESCARTADO
        traza = (mensaje.headers or {}).get("traceparent")
        with tracing.span("engine.coordinator", parent=traza, kind=tracing.SpanKind.CONSUMER, **{
                "gateway.bot_id": str(sobre.botId), "gateway.client_id": str(sobre.clientId),
                "gateway.channel": sobre.channel, "messaging.system": "nats"}) as current:
            resultado = await self._coordinar(mensaje, sobre, particion)
            current.set(**{"gateway.outcome": resultado.value})
            return resultado

    async def _pedido(self, mensaje, sobre: MensajeClasificado) -> Pedido | Resultado:
        if not sobre.clasificacion.permitido:
            return Pedido(respuesta=RESPUESTA_POR_MOTIVO[sobre.clasificacion.motivo])
        try:
            persona = await self._fuente.persona(sobre.clientId, sobre.botId)
            vueltas = await self._fuente.vueltas(sobre.clientId, sobre.botId, sobre.contact.uuid, sobre.messageId,
                                                 sobre.receivedAt)
        except NoEncontrado as e:
            log.warning("El bot %s o su contacto ya no existen (%s): el mensaje se descarta", sobre.botId, e)
            return Resultado.DESCARTADO
        except FuenteNoDisponible as e:
            if not self._ultima(mensaje):
                log.warning("Sin lo necesario del gateway para un mensaje del bot %s, se intentará otra vez: %s",
                            sobre.botId, e)
                return Resultado.EN_ESPERA
            log.error("Sin lo necesario del gateway para un mensaje del bot %s en la última entrega: %s",
                      sobre.botId, e)
            return Pedido(respuesta="no_disponible")
        return Pedido(respuesta="modelo", persona=persona, vueltas=vueltas)

    async def _coordinar(self, mensaje, sobre: MensajeClasificado, particion: str) -> Resultado:
        pedido = await self._pedido(mensaje, sobre)
        if isinstance(pedido, Resultado):
            return pedido
        salida = MensajePedido(**sobre.model_dump(), pedido=pedido)
        subject = subject_pedido(particion, sobre.botId, sobre.contact.uuid)
        traza = tracing.traceparent()
        headers = {"traceparent": traza} if traza else {}
        try:
            await self._publisher.publish(subject, salida.model_dump_json().encode(),
                                          f"pedido:{sobre.botId}:{sobre.messageId}", headers)
        except bus.BusUnavailable as e:
            log.error("El pedido de un mensaje del bot %s no se pudo publicar: %s", sobre.botId, e)
            return Resultado.EN_ESPERA
        log.info("Pedido de un mensaje del bot %s: %s, con %d vueltas", sobre.botId, pedido.respuesta,
                 len(pedido.vueltas))
        return Resultado.CONFIRMADO


async def preparar(settings: CoordinatorSettings, max_deliver: int, store: EngineKeyStore | None = None):
    """
        Lo que el worker abre al arrancar: la API del gateway, con la clave pública del gateway del fichero y el
        almacén de claves de Andvari, y el publicador. Devuelve cómo atender cada mensaje y cómo cerrar lo abierto.
    """
    store = store or EngineKeyStore(settings.andvari_addr, settings.engine_role_id, settings.engine_secret_id_file,
                                    settings.andvari_ca_file)
    fuente = ApiDelGateway(settings.gateway_url, settings.gateway_audience,
                           clave_publica_del_gateway(settings.gateway_public_keys_file), store, NOMBRE,
                           ca_file=settings.gateway_ca_file, timeout=settings.coordinator_timeout)
    publisher = publicador(settings, NOMBRE)

    async def cerrar() -> None:
        await publisher.close()

    return Coordinar(fuente, publisher, max_deliver), cerrar


def main() -> None:
    """El worker: su configuración y su bucle sobre MOTOR."""
    settings = CoordinatorSettings()
    asyncio.run(ejecutar(NOMBRE, settings, STREAM, CONSUMIDOR,
                         lambda max_deliver, _conexion: preparar(settings, max_deliver)))


if __name__ == "__main__":
    main()
