# -*- coding: utf-8 -*-
"""
    El evaluador (E8 de engine/docs/00-project/plan-de-los-workers.md, HIG-146, ADR-0012). Lee cada respuesta del stream
    `MOTOR` con el consumidor `evaluator` y publica lo que se envía en `motor.aprobada.{partición}.{botId}.{contactId}`,
    para el notificador y el coordinador.

        python -m src.workers.evaluator

    Decisiones de Jeremi del 2026-10-31:

    - **Un texto fijo pasa sin juzgarse:** lo escribimos nosotros.
    - **Si la respuesta del modelo cumple** la persona y las reglas del bot, sale igual.
    - **Si no cumple, se reescribe una vez:** pide al de respuesta que la escriba otra vez con el motivo (un pedido en
      `motor.pedido.…` con `reescribir`), y juzga la nueva cuando llega. Si tampoco cumple, sale el texto fijo de «no
      puedo».
    - **Falla cerrado:** si no puede juzgarla, el mensaje queda en espera y Bifröst lo entrega otra vez. En la última
      entrega sale el texto fijo de «no disponible». Nada sale sin evaluar.

    Juzga con la persona y las vueltas que usó el de respuesta, que vienen en su sobre: no pregunta al gateway ni a
    Andvari. Lo que publica no las lleva. Cada sobre aprobado dice qué se decidió, sin el texto (RS07), para las
    métricas de cumplimiento (HIG-147).
"""
import asyncio
import logging

from shared import bus, tracing
from shared.workers import Worker
from src.domain.models.sobre import (Contestacion, Evaluacion, MensajeAprobado, MensajePedido, MensajeRespondido,
                                     Pedido, Reescritura, subject_aprobada, subject_pedido)
from src.domain.ports.evaluador import Evaluador, EvaluacionFallida, Juicio
from src.domain.textos_fijos import TEXTOS_FIJOS
from src.infrastructure.llm import ClienteLlm, LlmEvaluador
from src.infrastructure.llm import caliente
from src.infrastructure.llm.cliente import leer_clave
from src.workers.runtime import Descartado, Resultado, ejecutar, entregas, leer_del_motor, publicador
from src.workers.settings import EvaluatorSettings

log = logging.getLogger(__name__)

NOMBRE = Worker.EVALUATOR.value
STREAM, CONSUMIDOR = "MOTOR", NOMBRE


class Evaluar:
    """Lo que hace el worker con cada respuesta."""

    def __init__(self, evaluador: Evaluador, publisher: bus.EventPublisher, max_deliver: int):
        self._evaluador = evaluador
        self._publisher = publisher
        # Sin límite (-1), nunca es la última entrega: se espera siempre.
        self._max_deliver = max_deliver

    def _ultima(self, mensaje) -> bool:
        return 0 < self._max_deliver <= entregas(mensaje)

    async def __call__(self, mensaje) -> Resultado:
        try:
            sobre, particion = leer_del_motor(mensaje, MensajeRespondido, "respuesta")
        except Descartado as e:
            log.warning("Un mensaje de %s se descarta: %s", STREAM, e)
            return Resultado.DESCARTADO
        traza = (mensaje.headers or {}).get("traceparent")
        with tracing.span("engine.evaluator", parent=traza, kind=tracing.SpanKind.CONSUMER, **{
                "gateway.bot_id": str(sobre.botId), "gateway.client_id": str(sobre.clientId),
                "gateway.channel": sobre.channel, "messaging.system": "nats"}) as current:
            resultado = await self._evaluar(mensaje, sobre, particion)
            current.set(**{"gateway.outcome": resultado.value})
            return resultado

    async def _evaluar(self, mensaje, sobre: MensajeRespondido, particion: str) -> Resultado:
        if sobre.respuesta.fuente == "fijo":
            return await self._aprobar(sobre, particion, sobre.respuesta, Evaluacion(veredicto="fija"))
        pedido = sobre.pedido
        if pedido is None or pedido.persona is None:
            # Solo pasa con lo que publicó el de respuesta de antes del evaluador: sin con qué juzgarla, no sale.
            log.warning("Una respuesta del bot %s llega sin el pedido: sale el texto fijo de «no disponible»",
                        sobre.botId)
            return await self._fijo(sobre, particion, "no_disponible", Evaluacion(veredicto="no_evaluada"))
        try:
            juicio = await self._evaluador.juzgar(pedido.persona, pedido.vueltas, sobre.entrada.texto,
                                                  sobre.respuesta.texto)
        except EvaluacionFallida as e:
            if not self._ultima(mensaje):
                log.warning("Sin juicio para una respuesta del bot %s, se intentará otra vez: %s", sobre.botId, e)
                return Resultado.EN_ESPERA
            log.error("Sin juicio para una respuesta del bot %s en la última entrega: sale «no disponible»: %s",
                      sobre.botId, e)
            return await self._fijo(sobre, particion, "no_disponible", Evaluacion(veredicto="no_evaluada"))
        anterior = pedido.reescribir
        if juicio.cumple:
            evaluacion = (Evaluacion(veredicto="aprobada", modelo=juicio.modelo) if anterior is None else
                          Evaluacion(veredicto="reescrita", criterio=anterior.criterio, regla=anterior.regla,
                                     modelo=juicio.modelo))
            return await self._aprobar(sobre, particion, sobre.respuesta, evaluacion)
        if anterior is None:
            return await self._reescribir(sobre, particion, pedido, juicio)
        log.warning("Tampoco cumple la respuesta reescrita del bot %s (%s): sale «no puedo»", sobre.botId,
                    juicio.criterio)
        return await self._fijo(sobre, particion, "no_puedo", Evaluacion(
            veredicto="cambiada", criterio=juicio.criterio, regla=juicio.regla, modelo=juicio.modelo))

    async def _fijo(self, sobre: MensajeRespondido, particion: str, cual: str, evaluacion: Evaluacion) -> Resultado:
        return await self._aprobar(sobre, particion, Contestacion(texto=TEXTOS_FIJOS[cual], fuente="fijo"),
                                   evaluacion)

    async def _publicar(self, subject: str, payload: bytes, message_id: str, sobre: MensajeRespondido) -> bool:
        traza = tracing.traceparent()
        try:
            await self._publisher.publish(subject, payload, message_id, {"traceparent": traza} if traza else {})
        except bus.BusUnavailable as e:
            log.error("Lo del evaluador para un mensaje del bot %s no se pudo publicar: %s", sobre.botId, e)
            return False
        return True

    async def _aprobar(self, sobre: MensajeRespondido, particion: str, respuesta: Contestacion,
                       evaluacion: Evaluacion) -> Resultado:
        salida = MensajeAprobado(**sobre.model_dump(exclude={"respuesta", "pedido"}), respuesta=respuesta,
                                 evaluacion=evaluacion)
        if not await self._publicar(subject_aprobada(particion, sobre.botId, sobre.contact.uuid),
                                    salida.model_dump_json().encode(), f"aprobada:{sobre.botId}:{sobre.messageId}",
                                    sobre):
            return Resultado.EN_ESPERA
        log.info("Respuesta a un mensaje del bot %s: %s (%s)", sobre.botId, evaluacion.veredicto, evaluacion.criterio)
        return Resultado.CONFIRMADO

    async def _reescribir(self, sobre: MensajeRespondido, particion: str, pedido: Pedido,
                          juicio: Juicio) -> Resultado:
        reescribir = Reescritura(texto=sobre.respuesta.texto, motivo=juicio.motivo, criterio=juicio.criterio,
                                 regla=juicio.regla)
        salida = MensajePedido(**sobre.model_dump(exclude={"respuesta", "pedido"}),
                               pedido=pedido.model_copy(update={"reescribir": reescribir}))
        if not await self._publicar(subject_pedido(particion, sobre.botId, sobre.contact.uuid),
                                    salida.model_dump_json().encode(),
                                    f"pedido:{sobre.botId}:{sobre.messageId}:reescrita", sobre):
            return Resultado.EN_ESPERA
        log.info("Una respuesta del bot %s no cumple (%s): se pide reescribirla", sobre.botId, juicio.criterio)
        return Resultado.CONFIRMADO


async def preparar(settings: EvaluatorSettings, max_deliver: int):
    """
        Lo que el worker abre al arrancar: el cliente del LLM de Hive, con su clave, y el publicador. Devuelve cómo
        atender cada mensaje y cómo cerrar lo abierto.
    """
    cliente = ClienteLlm(settings.llm_url, leer_clave(settings.llm_api_key_file), settings.llm_model,
                         settings.evaluator_timeout)
    publisher = publicador(settings, NOMBRE)
    evaluador = LlmEvaluador(cliente, settings.evaluator_max_tokens)
    # Que su ranura no se enfríe mientras no llegan mensajes.
    parar = caliente.arrancar(evaluador.llm, settings.llm_calentar_cada)

    async def cerrar() -> None:
        parar()
        await publisher.close()

    return Evaluar(evaluador, publisher, max_deliver), cerrar


def main() -> None:
    """El worker: su configuración y su bucle sobre MOTOR."""
    settings = EvaluatorSettings()
    asyncio.run(ejecutar(NOMBRE, settings, STREAM, CONSUMIDOR,
                         lambda max_deliver, _conexion: preparar(settings, max_deliver)))


if __name__ == "__main__":
    main()
