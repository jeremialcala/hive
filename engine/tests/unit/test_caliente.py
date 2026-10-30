# -*- coding: utf-8 -*-
"""
    El LLM, caliente (src/infrastructure/llm/caliente.py): cuándo se calienta, con qué, y que nunca a la vez que una
    consulta de verdad del mismo worker. El servidor de llama.cpp lo sustituye un doble.
"""
import asyncio

import pytest

from src.domain.models.sobre import Persona
from src.infrastructure.llm import LlmClasificador, LlmRedactor
from src.infrastructure.llm.caliente import Caliente, arrancar, mantener, prefijo
from src.infrastructure.llm.clasificador import SYSTEM
from src.infrastructure.llm.cliente import Completado, LlmNoDisponible
from src.infrastructure.llm.redactor import system_de

PERSONA = Persona(nombre="Mine", rol="Sales Assistant", tono="helpful", descripcion="", reglas=["Be kind"])


class Llama:
    """Apunta lo que se le pide y cuántas consultas tiene a la vez."""
    modelo = "gemma-4-26b-a4b"

    def __init__(self, texto="Hola", falla=False, tarda=0.0):
        self.pedidos, self.a_la_vez, self._en_curso = [], 0, 0
        self._texto, self._falla, self._tarda = texto, falla, tarda

    async def preguntar(self, mensajes, max_tokens, esquema=None):
        self._en_curso += 1
        self.a_la_vez = max(self.a_la_vez, self._en_curso)
        try:
            await asyncio.sleep(self._tarda)
            self.pedidos.append((mensajes, max_tokens))
            if self._falla:
                raise LlmNoDisponible("ReadTimeout")
            return Completado(texto=self._texto, fin="stop", tokens_entrada=10, tokens_salida=1)
        finally:
            self._en_curso -= 1


class Reloj:

    def __init__(self):
        self.ahora = 1000.0

    def __call__(self):
        return self.ahora


async def unas_miradas(calentable, reloj, avances, cada=600):
    """Deja que `mantener` mire una vez tras cada avance del reloj, y lo para."""
    tarea = asyncio.create_task(mantener(calentable, cada, reloj=reloj, mirar=0))
    for segundos in avances:
        reloj.ahora += segundos
        for _ in range(5):
            await asyncio.sleep(0)
    tarea.cancel()


def test_se_calienta_con_su_prefijo_y_un_solo_token_tras_cada_segundos_sin_uso():
    llama, reloj = Llama(), Reloj()
    caliente = Caliente(llama, reloj)
    caliente.prefijo = prefijo("El system del worker")

    asyncio.run(unas_miradas(caliente, reloj, [599]))
    assert llama.pedidos == []

    asyncio.run(unas_miradas(caliente, reloj, [1]))
    assert llama.pedidos == [([{"role": "system", "content": "El system del worker"},
                               {"role": "user", "content": "."}], 1)]


def test_tras_calentarlo_espera_otros_cada_segundos():
    llama, reloj = Llama(), Reloj()
    caliente = Caliente(llama, reloj)
    caliente.prefijo = prefijo("s")

    asyncio.run(unas_miradas(caliente, reloj, [600, 60, 60, 479, 1]))

    assert len(llama.pedidos) == 2


def test_una_consulta_de_verdad_cuenta_como_uso():
    llama, reloj = Llama(), Reloj()
    caliente = Caliente(llama, reloj)
    caliente.prefijo = prefijo("s")
    reloj.ahora += 500
    asyncio.run(caliente.preguntar([{"role": "user", "content": "hola"}], 256))

    asyncio.run(unas_miradas(caliente, reloj, [500]))

    assert len(llama.pedidos) == 1


def test_si_el_llm_no_responde_queda_en_un_aviso_y_no_se_insiste_en_cada_mirada():
    llama, reloj = Llama(falla=True), Reloj()
    caliente = Caliente(llama, reloj)
    caliente.prefijo = prefijo("s")

    asyncio.run(unas_miradas(caliente, reloj, [600, 60, 60]))

    assert len(llama.pedidos) == 1


def test_sin_prefijo_no_hay_nada_que_calentar_ni_lo_dice_el_log(caplog):
    llama, reloj = Llama(), Reloj()

    with caplog.at_level("INFO"):
        asyncio.run(unas_miradas(Caliente(llama, reloj), reloj, [600]))

    assert llama.pedidos == [] and "calentado" not in caplog.text


def test_lo_que_si_se_calienta_queda_en_el_log(caplog):
    llama, reloj = Llama(), Reloj()
    caliente = Caliente(llama, reloj)
    caliente.prefijo = prefijo("s")

    with caplog.at_level("INFO"):
        asyncio.run(unas_miradas(caliente, reloj, [600]))

    assert "calentado tras 600 s sin uso" in caplog.text


def test_nunca_a_la_vez_que_una_consulta_de_verdad():
    llama = Llama(tarda=0.05)
    caliente = Caliente(llama)
    caliente.prefijo = prefijo("s")

    async def las_dos():
        await asyncio.gather(caliente.calentar(), caliente.preguntar([{"role": "user", "content": "hola"}], 256))

    asyncio.run(las_dos())

    assert llama.a_la_vez == 1 and len(llama.pedidos) == 2


def test_el_clasificador_se_calienta_con_su_system():
    clasificador = LlmClasificador(Llama(), 256)

    assert clasificador.llm.prefijo == prefijo(SYSTEM)


def test_el_de_respuesta_se_calienta_con_el_system_del_ultimo_bot_que_contesto():
    llama = Llama(texto="¡Hola!")
    redactor = LlmRedactor(llama, 1024)
    assert redactor.llm.prefijo is None

    asyncio.run(redactor.redactar(PERSONA, [], "hola"))

    assert redactor.llm.prefijo == prefijo(system_de(PERSONA))


@pytest.mark.parametrize("cada", [0, -1])
def test_con_cada_a_cero_no_arranca_nada(cada):
    async def probar():
        parar = arrancar(Caliente(Llama()), cada)
        parar()
        return len(asyncio.all_tasks())

    assert asyncio.run(probar()) == 1


def test_arrancar_deja_una_tarea_que_se_para():
    async def probar():
        parar = arrancar(Caliente(Llama()), 600)
        antes = len(asyncio.all_tasks())
        parar()
        await asyncio.sleep(0)
        return antes, len(asyncio.all_tasks())

    assert asyncio.run(probar()) == (2, 1)
