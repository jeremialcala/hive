# -*- coding: utf-8 -*-
"""
    El LLM, caliente (ADR-0010, medido el 2026-10-22). Tras horas sin uso, la primera consulta del día tardó 17,8 s de
    punta a punta, frente a 4 s las siguientes: la clasificación, 5,4 s en vez de 1, y la respuesta, 6,7 s en vez de
    1,3, con la GPU leyendo la consulta diez veces más despacio. Lo que se enfría está del lado de Windows y de la GPU,
    no en la memoria de la VM, que ya va bloqueada.

    Por eso el clasificador y el de respuesta no dejan que pase mucho tiempo sin usarlo: si llevan `cada` segundos sin
    llamarlo, le mandan una consulta mínima con su propio `system`, de un token de salida. Así su ranura conserva el
    prefijo, y la GPU, lo que necesita. Desde fuera no serviría: una consulta con otro `system` echaría de la ranura
    el prefijo del worker.

    - **Nunca a la vez que una consulta de verdad del mismo worker:** un candado las ordena. Si coinciden, la de
      verdad espera lo que tarda la otra, menos de un segundo.
    - **Un fallo no es más que un aviso:** si el LLM no responde, se vuelve a intentar a los `cada` segundos, no antes.
"""
import asyncio
import logging
import time
from typing import Callable, Protocol

from src.infrastructure.llm.cliente import ClienteLlm, Completado, LlmNoDisponible

log = logging.getLogger(__name__)

MIRAR = 60  # segundos entre una mirada y otra a cuánto lleva sin usarse


class Calentable(Protocol):
    """Lo que se puede mantener caliente: sabe cuándo se usó por última vez, y cómo calentarse."""
    ultimo_uso: float

    async def calentar(self) -> bool:
        """Le manda al LLM lo que lo mantiene caliente. Devuelve si había algo que mandar."""


class Caliente:
    """
        El cliente del LLM de un worker, con su candado, la hora de su último uso y el prefijo con el que se calienta:
        su `system` y un turno de usuario de nada. El adaptador lo pone; sin él, no se calienta.
    """

    def __init__(self, cliente: ClienteLlm, reloj: Callable[[], float] = time.monotonic):
        self._cliente = cliente
        self._reloj = reloj
        self._candado = asyncio.Lock()
        self.ultimo_uso = reloj()
        self.prefijo: list[dict] | None = None

    @property
    def modelo(self) -> str:
        """El del cliente."""
        return self._cliente.modelo

    async def preguntar(self, mensajes: list[dict], max_tokens: int, esquema: dict | None = None) -> Completado:
        """Lo de ClienteLlm.preguntar, de una en una, y apuntando cuándo."""
        async with self._candado:
            try:
                return await self._cliente.preguntar(mensajes, max_tokens, esquema)
            finally:
                self.ultimo_uso = self._reloj()

    async def calentar(self) -> bool:
        """
            Un token de salida: lo que importa es que el modelo vuelva a leer el prefijo. Sin prefijo (el de respuesta
            antes de su primera respuesta) no manda nada, y lo dice.
        """
        if not self.prefijo:
            return False
        await self.preguntar(self.prefijo, 1)
        return True


def prefijo(system: str) -> list[dict]:
    """Lo que se manda para calentar: el `system` del worker y un turno de usuario de nada."""
    return [{"role": "system", "content": system}, {"role": "user", "content": "."}]


async def mantener(calentable: Calentable, cada: int, reloj: Callable[[], float] = time.monotonic,
                   mirar: float = MIRAR) -> None:
    """Hasta que se cancela: cada `mirar` segundos, si lleva `cada` sin usarse, lo calienta."""
    while True:
        await asyncio.sleep(mirar)
        if reloj() - calentable.ultimo_uso < cada:
            continue
        try:
            if await calentable.calentar():
                log.info("El LLM, calentado tras %d s sin uso", cada)
        except LlmNoDisponible as e:
            log.warning("No se pudo calentar el LLM: %s", e)
        # Pase lo que pase, la próxima vez dentro de `cada`, no en la siguiente mirada.
        calentable.ultimo_uso = reloj()


def arrancar(calentable: Calentable, cada: int) -> Callable[[], None]:
    """La tarea que lo mantiene caliente, si `cada` no es 0. Devuelve cómo pararla."""
    if cada <= 0:
        return lambda: None
    tarea = asyncio.create_task(mantener(calentable, cada))
    return tarea.cancel
