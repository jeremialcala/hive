# -*- coding: utf-8 -*-
"""
    Lo que la persona ve mientras Hive le responde (ADR-0011), del notificador, el único worker con el token de Meta
    del bot. Dos consumidores más, que el notificador atiende a la vez que el suyo:

    - **`notifier-leido`**, sobre `MENSAJES`: en cuanto llega un mensaje, sus ticks se ponen en azul, y los de los
      anteriores de la conversación.
    - **`notifier-escribiendo`**, sobre `motor.clasificado`: cuando el clasificador deja seguir el mensaje, la persona
      ve «escribiendo…» hasta la respuesta, o 25 segundos. Un mensaje que no sigue recibe enseguida su texto fijo: no
      lo necesita.

    Las dos cosas son un extra: nada espera por ellas y no se reintentan (sus consumidores tienen `max_deliver` 1).
    Si Meta, el gateway o el bucket no responden, queda en el log y el mensaje se confirma igual. Y «escribiendo…»
    no se pide si ya no sirve: si el mensaje llegó hace más de VIGENCIA segundos, o si su respuesta ya salió, se
    quedaría encendido después de ella.

    Solo WhatsApp: Messenger e Instagram llegan en el E10. El log no lleva el número, el token ni los ids de los
    mensajes.
"""
import logging
from datetime import datetime, timezone

from shared import tracing
from src.domain.models.sobre import MensajeClasificado
from src.domain.ports.envio import (
    CanalDeMeta, Enviados, EnvioFallido, EnvioRechazado, RegistroNoDisponible, TokenRechazado, TokensDelBot,
)
from src.domain.ports.fuente_del_bot import FuenteNoDisponible, NoEncontrado
from src.workers.classifier import Descartado as NoEsUnMensaje
from src.workers.classifier import cuenta_del_bot, leer
from src.workers.runtime import Descartado, Resultado, leer_del_motor

log = logging.getLogger(__name__)

LEIDO, ESCRIBIENDO = "notifier-leido", "notifier-escribiendo"
# Segundos tras su llegada en los que aún tiene sentido «escribiendo…»: la respuesta tarda unos 9 de mediana, y el
# indicador se apaga solo a los 25.
VIGENCIA = 20


async def _marcar(tokens: TokensDelBot, canal: CanalDeMeta, client_id, bot_id, cuenta: str, message_id: str,
                  escribiendo: bool) -> str:
    """Lo pide a Meta, con un token nuevo si rechaza el que había. Devuelve cómo fue, con un valor de
    `gateway.outcome` para la traza: «sent» o «failed»."""
    try:
        token = await tokens.token_de_whatsapp(client_id, bot_id)
        try:
            await canal.marcar_leido(token, cuenta, message_id, escribiendo)
        except TokenRechazado:
            tokens.olvidar(bot_id)
            token = await tokens.token_de_whatsapp(client_id, bot_id)
            await canal.marcar_leido(token, cuenta, message_id, escribiendo)
    except (NoEncontrado, FuenteNoDisponible, EnvioFallido, EnvioRechazado) as e:
        que = "«escribiendo…»" if escribiendo else "el recibo de lectura"
        log.warning("Sin %s para un mensaje del bot %s: %s", que, bot_id, e)
        return "failed"
    return "sent"


class Leido:
    """En cuanto llega un mensaje de WhatsApp, lo marca como leído."""

    def __init__(self, tokens: TokensDelBot, canal: CanalDeMeta):
        self._tokens = tokens
        self._canal = canal

    async def __call__(self, mensaje) -> Resultado:
        try:
            evento, _ = leer(mensaje)
        except NoEsUnMensaje as e:
            log.warning("Un mensaje de MENSAJES no se marca como leído: %s", e)
            return Resultado.DESCARTADO
        cuenta = cuenta_del_bot(evento)
        if evento.channel != "whatsapp" or cuenta is None:
            return Resultado.CONFIRMADO
        traza = (mensaje.headers or {}).get("traceparent")
        with tracing.span("engine.notifier.read", parent=traza, kind=tracing.SpanKind.CONSUMER, **{
                "gateway.bot_id": str(evento.botId), "gateway.channel": evento.channel,
                "messaging.system": "nats"}) as current:
            como = await _marcar(self._tokens, self._canal, evento.clientId, evento.botId, cuenta, evento.messageId,
                                 escribiendo=False)
            current.set(**{"gateway.outcome": como})
        return Resultado.CONFIRMADO


class Escribiendo:
    """Cuando un mensaje sigue, le muestra a la persona «escribiendo…»."""

    def __init__(self, tokens: TokensDelBot, canal: CanalDeMeta, enviados: Enviados, ahora=None):
        self._tokens = tokens
        self._canal = canal
        self._enviados = enviados
        self._ahora = ahora or (lambda: datetime.now(timezone.utc))

    async def __call__(self, mensaje) -> Resultado:
        try:
            sobre, _ = leer_del_motor(mensaje, MensajeClasificado, "clasificado")
        except Descartado as e:
            log.warning("Un mensaje clasificado no tendrá «escribiendo…»: %s", e)
            return Resultado.DESCARTADO
        if not await self._sirve(sobre):
            return Resultado.CONFIRMADO
        traza = (mensaje.headers or {}).get("traceparent")
        with tracing.span("engine.notifier.typing", parent=traza, kind=tracing.SpanKind.CONSUMER, **{
                "gateway.bot_id": str(sobre.botId), "gateway.channel": sobre.channel,
                "messaging.system": "nats"}) as current:
            como = await _marcar(self._tokens, self._canal, sobre.clientId, sobre.botId, sobre.cuentaDelBot,
                                 sobre.messageId, escribiendo=True)
            current.set(**{"gateway.outcome": como})
        return Resultado.CONFIRMADO

    async def _sirve(self, sobre: MensajeClasificado) -> bool:
        if sobre.channel != "whatsapp" or not sobre.cuentaDelBot or not sobre.clasificacion.permitido:
            return False
        llegada = sobre.receivedAt if sobre.receivedAt.tzinfo else sobre.receivedAt.replace(tzinfo=timezone.utc)
        if (self._ahora() - llegada).total_seconds() > VIGENCIA:
            log.info("Un mensaje del bot %s llegó hace más de %d s: sin «escribiendo…»", sobre.botId, VIGENCIA)
            return False
        try:
            if await self._enviados.enviado(sobre.botId, sobre.messageId) is not None:
                return False
        except RegistroNoDisponible as e:
            # Sin saber si ya salió la respuesta, mejor sin indicador que con uno encendido después de ella.
            log.warning("Sin «escribiendo…» para un mensaje del bot %s: enviados: %s", sobre.botId, e)
            return False
        return True
