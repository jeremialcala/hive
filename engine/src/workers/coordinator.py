# -*- coding: utf-8 -*-
"""
    El coordinador (E4 de engine/docs/00-project/plan-de-los-workers.md). Lee cada mensaje clasificado del stream
    `MOTOR` con el consumidor `coordinator` y pide la respuesta en `motor.pedido.{partición}.{botId}.{contactId}`,
    para el de respuesta.

        python -m src.workers.coordinator

    - **Un mensaje que sigue** se pide al modelo: el coordinador pregunta al gateway la persona del bot con sus
      reglas y las últimas vueltas de la conversación, con su clave para el cliente dueño del bot, y las pone en el
      sobre. La persona la recuerda 5 minutos, salvo que el gateway avise de que el bot cambió (ADR-0035 del
      gateway, `gateway.bot.configured`): entonces la lee otra vez en el mensaje siguiente.
    - **Un mensaje que no sigue** se pide con un texto fijo según su motivo, sin preguntar nada (decisión de Jeremi
      del 2026-10-20): la persona siempre recibe algo.
    - **Si el gateway no responde**, el mensaje queda en espera y Bifröst lo entrega otra vez. En la última entrega
      se pide el texto fijo de «no disponible», para no dejar a nadie sin respuesta.
    - **Si el bot o el contacto ya no existen**, no hay a quién responder: se descarta.

    - **El contexto de cada conversación** vive en el bucket `contexto` de Bifröst, cifrado con la clave `contexto`
      de Andvari (ADR-0017 del gateway, HIG-19, src/domain/memoria.py). Las vueltas salen de ahí; si no están, o no
      sirven para el mensaje, se piden al gateway como antes. El coordinador apunta en él lo que dijo la persona, y
      con su consumidor `MOTOR/coordinator-aprobada`, lo que se le envió: lo que aprobó el evaluador (HIG-146).
    - **Lo que el gateway suprime** lo borra del contexto: el consumidor `SUPRESIONES/coordinator` purga las
      conversaciones del contacto, el bot o el cliente borrados. Si un aviso se pierde, el bucket lo olvida en 24 horas.

    En el esqueleto no llama al enlace (ADR-0008), ni guarda el resumen que pide la ADR-0017 (HIG-143).
"""
import asyncio
import json
import logging
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError

from shared import bus, tracing
from shared.contract import CONFIGURED_SUBJECT, BotConfigured
from shared.engine_key_store import EngineKeyStore
from shared.workers import Worker
from src.domain.memoria import Memoria, Recuerdo
from src.domain.models.sobre import (RESPUESTA_POR_MOTIVO, MensajeAprobado, MensajeClasificado, MensajePedido,
                                     Pedido, subject_pedido)
from src.domain.ports.contexto import Contexto, ContextoNoDisponible
from src.domain.ports.fuente_del_bot import FuenteDelBot, FuenteNoDisponible, NoEncontrado
from src.infrastructure.bifrost.contexto import ContextoEnBifrost
from src.infrastructure.gateway import ApiDelGateway
from src.infrastructure.gateway.api import clave_publica_del_gateway
from src.infrastructure.llm.cliente import ClienteLlm, leer_clave
from src.infrastructure.llm.resumidor import LlmResumidor
from src.workers.runtime import Descartado, Resultado, ejecutar, entregas, leer_del_motor, publicador
from src.workers.settings import CoordinatorSettings

log = logging.getLogger(__name__)

NOMBRE = Worker.COORDINATOR.value
STREAM, CONSUMIDOR = "MOTOR", NOMBRE
# Lo que contestó el bot, para el contexto; y lo que suprimió el gateway (bifrost: consumidores del coordinador).
# Desde el evaluador (HIG-146), lo que se envía; el de antes, `coordinator-respuesta`, leía lo propuesto.
CONSUMIDOR_RESPUESTAS = f"{NOMBRE}-aprobada"
SUPRESIONES = "SUPRESIONES"


class Coordinar:
    """Lo que hace el worker con cada mensaje."""

    def __init__(self, fuente: FuenteDelBot, publisher: bus.EventPublisher, max_deliver: int,
                 memoria: Memoria | None = None):
        self._fuente = fuente
        self._publisher = publisher
        # Sin límite (-1), nunca es la última entrega: se espera siempre.
        self._max_deliver = max_deliver
        # Sin ella (las pruebas que no la necesitan), las vueltas se piden al gateway en cada mensaje.
        self._memoria = memoria

    async def _recuerdo(self, sobre: MensajeClasificado) -> Recuerdo:
        if self._memoria is not None:
            return await self._memoria.recuerdo(sobre)
        return Recuerdo(vueltas=await self._fuente.vueltas(sobre.clientId, sobre.botId, sobre.contact.uuid,
                                                          sobre.messageId, sobre.receivedAt))

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
            recuerdo = await self._recuerdo(sobre)
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
        return Pedido(respuesta="modelo", persona=persona, vueltas=recuerdo.vueltas, resumen=recuerdo.resumen)

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
        if self._memoria is not None:
            await self._memoria.apuntar_persona(sobre)
        return Resultado.CONFIRMADO


class Respondido:
    """Lo que hace el coordinador con cada respuesta del de respuesta: apuntarla en el contexto. Nunca la retiene."""

    def __init__(self, memoria: Memoria):
        self._memoria = memoria

    async def __call__(self, mensaje) -> Resultado:
        try:
            sobre, _ = leer_del_motor(mensaje, MensajeAprobado, "aprobada")
        except Descartado as e:
            log.warning("Una respuesta de %s se descarta: %s", STREAM, e)
            return Resultado.DESCARTADO
        await self._memoria.apuntar_bot(sobre)
        return Resultado.CONFIRMADO


class Supresion(BaseModel):
    """El aviso de que el gateway suprimió un contacto, un bot o un cliente (Supresion del AsyncAPI v2 del gateway)."""
    model_config = ConfigDict(extra="ignore", frozen=True)

    schemaVersion: Literal[2]
    entity: Literal["contact", "bot", "client"]
    clientId: UUID
    botId: Optional[UUID] = None
    contactId: Optional[UUID] = None


class Suprimido:
    """Lo que hace el coordinador con cada supresión del gateway: borrar sus conversaciones del contexto."""

    def __init__(self, contexto: Contexto):
        self._contexto = contexto

    async def __call__(self, mensaje) -> Resultado:
        try:
            aviso = Supresion.model_validate(json.loads(mensaje.data))
            if (aviso.entity == "contact" and (aviso.botId is None or aviso.contactId is None)) or (
                    aviso.entity == "bot" and aviso.botId is None):
                raise ValueError("le falta a quién")
        except (ValueError, UnicodeDecodeError) as e:
            # ValidationError es un ValueError. Sus errores llevan los valores: solo se dice el tipo.
            log.warning("Un aviso de %s se descarta: %s", SUPRESIONES, type(e).__name__)
            return Resultado.DESCARTADO
        try:
            await self._contexto.olvidar(aviso.clientId, aviso.botId if aviso.entity != "client" else None,
                                         aviso.contactId if aviso.entity == "contact" else None)
        except ContextoNoDisponible as e:
            # Borrar es una obligación: se intenta otra vez. Si nunca se pudiera, la caducidad lo borra en 24 horas.
            log.warning("No se pudo borrar del contexto lo que suprimió el gateway, se intentará otra vez: %s", e)
            return Resultado.EN_ESPERA
        log.info("Contexto borrado: %s de %s", aviso.entity, aviso.clientId)
        return Resultado.CONFIRMADO


def al_configurar(fuente: ApiDelGateway):
    """
        Lo que hace el coordinador con cada aviso de que cambió la configuración de un bot: olvida lo que recordaba de
        una versión anterior. Un aviso roto se ignora, y lo recordado caduca solo.
    """
    async def oir(mensaje) -> None:
        try:
            aviso = BotConfigured.model_validate_json(mensaje.data)
        except ValidationError as e:
            # Sus errores llevan los valores: solo se dice cuántos.
            log.warning("Un aviso de %s se ignora: no cumple el contrato (%d errores)", CONFIGURED_SUBJECT,
                        e.error_count())
            return
        fuente.configuracion_cambiada(aviso.clientId, aviso.botId, aviso.version)
        log.info("El bot %s va por la versión %d: se leerá otra vez", aviso.botId, aviso.version)
    return oir


async def preparar(settings: CoordinatorSettings, max_deliver: int, store: EngineKeyStore | None = None,
                   conexion=None):
    """
        Lo que el worker abre al arrancar: la API del gateway, con la clave pública del gateway del fichero y el
        almacén de claves de Andvari, el publicador y, con la conexión con Bifröst, la escucha de los avisos de
        configuración. Devuelve cómo atender cada mensaje y cómo cerrar lo abierto.
    """
    store = store or almacen(settings)
    fuente = ApiDelGateway(settings.gateway_url, settings.gateway_audience,
                           clave_publica_del_gateway(settings.gateway_public_keys_file), store, NOMBRE,
                           ca_file=settings.gateway_ca_file, timeout=settings.coordinator_timeout)
    publisher = publicador(settings, NOMBRE)
    avisos = await conexion.subscribe(CONFIGURED_SUBJECT, cb=al_configurar(fuente)) if conexion is not None else None
    memoria = Memoria(fuente, ContextoEnBifrost(conexion.jetstream(), store)) if conexion is not None else None

    async def cerrar() -> None:
        if avisos is not None:
            await avisos.unsubscribe()
        await publisher.close()

    return Coordinar(fuente, publisher, max_deliver, memoria), cerrar


def almacen(settings: CoordinatorSettings) -> EngineKeyStore:
    """Andvari con el rol del coordinador: su clave para cada cliente, y la clave Transit `contexto`."""
    return EngineKeyStore(settings.andvari_addr, settings.engine_role_id, settings.engine_secret_id_file,
                          settings.andvari_ca_file)


def resumidor(settings: CoordinatorSettings) -> LlmResumidor | None:
    """El LLM que resume lo que sale de las vueltas (HIG-143), si está configurado; si no, no se resume."""
    if not (settings.llm_url and settings.llm_api_key_file):
        log.warning("Sin LLM_URL ni LLM_API_KEY_FILE: lo que salga de las vueltas no se resumirá")
        return None
    cliente = ClienteLlm(settings.llm_url, leer_clave(settings.llm_api_key_file), settings.llm_model,
                         settings.coordinator_resumen_timeout)
    return LlmResumidor(cliente, settings.coordinator_resumen_max_tokens)


async def preparar_respuestas(settings: CoordinatorSettings, conexion, store: EngineKeyStore | None = None):
    """
        El consumidor de lo aprobado: apunta en el contexto lo que se le envió a la persona y, si salen vueltas, las
        resume (HIG-143). Va en un bucle aparte del de los mensajes: nadie espera al resumen.
    """
    async def cerrar() -> None:
        return None

    memoria = Memoria(None, ContextoEnBifrost(conexion.jetstream(), store or almacen(settings)), resumidor(settings))
    return Respondido(memoria), cerrar


async def preparar_supresiones(settings: CoordinatorSettings, conexion, store: EngineKeyStore | None = None):
    """El consumidor de las supresiones: borra del contexto lo que el gateway borró."""
    async def cerrar() -> None:
        return None

    return Suprimido(ContextoEnBifrost(conexion.jetstream(), store or almacen(settings))), cerrar


def main() -> None:
    """El worker: su configuración y su bucle sobre MOTOR."""
    settings = CoordinatorSettings()
    asyncio.run(ejecutar(NOMBRE, settings, STREAM, CONSUMIDOR,
                         lambda max_deliver, conexion: preparar(settings, max_deliver, conexion=conexion),
                         ademas=[(STREAM, CONSUMIDOR_RESPUESTAS,
                                  lambda _max_deliver, conexion: preparar_respuestas(settings, conexion)),
                                 (SUPRESIONES, NOMBRE,
                                  lambda _max_deliver, conexion: preparar_supresiones(settings, conexion))]))


if __name__ == "__main__":
    main()
