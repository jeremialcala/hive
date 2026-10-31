# -*- coding: utf-8 -*-
"""
    La memoria del coordinador (ADR-0017 del gateway, HIG-19): de dónde salen las vueltas anteriores de un mensaje y
    cómo se apunta lo que pasa en la conversación.

    - **Primero el contexto** de Bifröst. Sirve si no tiene nada posterior al mensaje: un mensaje que se atiende con
      retraso, o que llega otra vez, no puede tomar como contexto lo que vino después. Si el mensaje ya está apuntado
      (una reentrega), sus vueltas son las de antes de él.
    - **Si no hay contexto, o no sirve,** las vueltas se piden al gateway, como hasta ahora, y con ellas se siembra
      un contexto que no había.
    - **Al publicar el pedido** se apunta lo que dijo la persona; **al enviar**, lo que contestó el bot. Solo sobre
      un contexto completo, aunque esté vacío (el de un contacto nuevo, HIG-144): uno al que le faltara lo anterior
      engañaría al modelo. Si otro coordinador escribió entre medias, se lee otra vez y se apunta encima.
    - **Si no se puede apuntar**, el contexto ya no está completo: se borra, y el mensaje siguiente lo siembra otra vez
      desde el gateway. Pasa si Bifröst o Andvari fallan, si otro coordinador escribe todo el rato, si la respuesta es
      a un mensaje que el contexto no tiene o si un mensaje llega después de otros posteriores. Si tampoco se puede
      borrar, caduca en 24 horas.

    Nada de esto para un mensaje: si Bifröst o Andvari fallan, se usa el gateway y el log lo dice. El historial del
    gateway es la fuente de verdad; el contexto, una copia de trabajo que se rehace.
"""
import logging
from datetime import datetime, timezone

from src.domain.models.sobre import MAX_VUELTAS, MensajeAprobado, MensajeClasificado, Vuelta
from src.domain.ports.contexto import Apunte, Choque, Contexto, ContextoNoDisponible, Conversacion
from src.domain.ports.fuente_del_bot import FuenteDelBot

log = logging.getLogger(__name__)

# Lo que cabe guardar de una conversación, como lo da el gateway (RF15): las últimas vueltas de la persona y lo que
# vino después, y nunca más de lo que el coordinador pasa.
PERSONAS = 10
GUARDADAS = MAX_VUELTAS
# Veces que se vuelve a leer cuando otro coordinador escribió entre medias.
INTENTOS = 3
# El mensaje al que pertenece una vuelta que vino del historial del gateway, que no lo dice.
DEL_HISTORIAL = ""


def recortar(apuntes: list[Apunte]) -> list[Apunte]:
    """Desde la décima vuelta de la persona empezando por el final, y no más de GUARDADAS."""
    de_la_persona = [at for at, apunte in enumerate(apuntes) if apunte.vuelta.de == "persona"]
    desde = de_la_persona[-PERSONAS] if len(de_la_persona) > PERSONAS else 0
    return apuntes[desde:][-GUARDADAS:]


def antes_de(conversacion: Conversacion, sobre: MensajeClasificado) -> list[Vuelta] | None:
    """Las vueltas del contexto anteriores al mensaje, o None si el contexto no sirve para él."""
    apuntes = conversacion.apuntes
    for at, apunte in enumerate(apuntes):
        if apunte.vuelta.de == "persona" and apunte.mensaje == sobre.messageId:
            return [each.vuelta for each in recortar(apuntes[:at])]
    if any(apunte.vuelta.enviadaEn > sobre.receivedAt for apunte in apuntes):
        return None
    return [apunte.vuelta for apunte in recortar(apuntes)]


class Memoria:
    """Las vueltas de cada conversación: del contexto si sirve, si no del gateway."""

    def __init__(self, fuente: FuenteDelBot | None, contexto: Contexto):
        # Sin fuente, solo apunta: el consumidor de las respuestas no pide vueltas.
        self._fuente = fuente
        self._contexto = contexto

    async def _leer(self, sobre: MensajeClasificado) -> Conversacion | None:
        return await self._contexto.leer(sobre.clientId, sobre.botId, sobre.contact.uuid)

    async def vueltas(self, sobre: MensajeClasificado) -> list[Vuelta]:
        """
        :raises NoEncontrado, FuenteNoDisponible: como la fuente, si hubo que preguntarle.
        """
        conversacion = None
        try:
            conversacion = await self._leer(sobre)
        except ContextoNoDisponible as e:
            log.warning("Sin el contexto del bot %s, se pide al gateway: %s", sobre.botId, e)
        if conversacion is not None and conversacion.completa:
            vueltas = antes_de(conversacion, sobre)
            if vueltas is not None:
                return vueltas
        vueltas = await self._fuente.vueltas(sobre.clientId, sobre.botId, sobre.contact.uuid, sobre.messageId,
                                             sobre.receivedAt)
        if conversacion is None or not conversacion.completa:
            # No había contexto, o no estaba completo: se siembra con lo que dice el historial, aunque no diga nada.
            await self._escribir(sobre, [Apunte(vuelta=vuelta, mensaje=DEL_HISTORIAL) for vuelta in vueltas],
                                 conversacion.revision if conversacion else 0, "sembrar")
        return vueltas

    async def apuntar_persona(self, sobre: MensajeClasificado) -> None:
        """Lo que dijo la persona, una vez publicado el pedido."""
        vuelta = Vuelta(de="persona", texto=sobre.entrada.texto, adjuntos=sobre.entrada.adjuntos,
                        enviadaEn=sobre.receivedAt)
        await self._apuntar(sobre, Apunte(vuelta=vuelta, mensaje=sobre.messageId), "persona")

    async def apuntar_bot(self, sobre: MensajeAprobado) -> None:
        """Lo que contestó el bot, cuando el de respuesta lo publica."""
        vuelta = Vuelta(de="bot", texto=sobre.respuesta.texto, enviadaEn=datetime.now(timezone.utc))
        await self._apuntar(sobre, Apunte(vuelta=vuelta, mensaje=sobre.messageId), "bot")

    async def _apuntar(self, sobre: MensajeClasificado, apunte: Apunte, quien: str) -> None:
        for _ in range(INTENTOS):
            try:
                conversacion = await self._leer(sobre)
            except ContextoNoDisponible as e:
                await self._olvidar(sobre, f"no se apuntó lo de la {quien}: {e}")
                return
            if conversacion is None or not conversacion.completa:
                # Sin lo anterior, mejor nada: el mensaje siguiente lo pedirá al gateway y sembrará.
                return
            ya = [each for each in conversacion.apuntes if each.mensaje == apunte.mensaje]
            if any(each.vuelta.de == apunte.vuelta.de for each in ya):
                # Ya está: una reentrega.
                return
            if apunte.vuelta.de == "bot" and not ya:
                # Responde a un mensaje que el contexto no tiene: le falta algo.
                await self._olvidar(sobre, "una respuesta a un mensaje que no tenía")
                return
            if apunte.vuelta.de == "persona" and any(each.vuelta.de == "persona"
                                                      and each.vuelta.enviadaEn > apunte.vuelta.enviadaEn
                                                      for each in conversacion.apuntes):
                # Llegó después de otros posteriores de la persona: apuntado al final, el orden mentiría. Que una
                # respuesta del bot sea posterior es normal: la persona escribe dos veces seguidas.
                await self._olvidar(sobre, "un mensaje atendido con retraso")
                return
            apuntes = recortar([*conversacion.apuntes, apunte])
            try:
                await self._contexto.guardar(sobre.clientId, sobre.botId, sobre.contact.uuid, apuntes,
                                             conversacion.revision)
                return
            except Choque:
                continue
            except ContextoNoDisponible as e:
                await self._olvidar(sobre, f"no se apuntó lo de la {quien}: {e}")
                return
        await self._olvidar(sobre, f"no se apuntó lo de la {quien}: otro coordinador escribía a la vez")

    async def _olvidar(self, sobre: MensajeClasificado, motivo: str) -> None:
        """Borra un contexto al que le falta algo: el mensaje siguiente lo siembra otra vez."""
        try:
            await self._contexto.olvidar(sobre.clientId, sobre.botId, sobre.contact.uuid)
        except ContextoNoDisponible as e:
            log.warning("El contexto del bot %s ya no está completo (%s) y no se pudo borrar; caduca en 24 horas: %s",
                        sobre.botId, motivo, e)
            return
        log.warning("El contexto del bot %s ya no estaba completo (%s): borrado, se sembrará otra vez",
                    sobre.botId, motivo)

    async def _escribir(self, sobre: MensajeClasificado, apuntes: list[Apunte], revision: int, que: str) -> None:
        try:
            await self._contexto.guardar(sobre.clientId, sobre.botId, sobre.contact.uuid, recortar(apuntes), revision)
        except Choque:
            # Otro coordinador lo sembró antes: vale el suyo.
            return
        except ContextoNoDisponible as e:
            log.warning("No se pudo %s el contexto del bot %s: %s", que, sobre.botId, e)
