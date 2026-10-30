# -*- coding: utf-8 -*-
"""
    El bucle común de los workers (src/workers/runtime.py): qué se hace con un mensaje según su resultado, y que el
    bucle sobrevive a los silencios y a los cortes de Bifröst.
"""
import asyncio
from types import SimpleNamespace

import nats.errors
import pytest

from src.workers.runtime import Resultado, consumir, ejecutar, entregas, resolver


class Mensaje:

    def __init__(self, entregas=1):
        self.metadata = SimpleNamespace(num_delivered=entregas)
        self.hecho = None

    async def ack(self):
        self.hecho = "ack"

    async def term(self):
        self.hecho = "term"


def resultado_de(atender):
    mensaje = Mensaje()
    return asyncio.run(resolver(mensaje, atender)), mensaje.hecho


@pytest.mark.parametrize("resultado, hecho", [
    (Resultado.CONFIRMADO, "ack"), (Resultado.DESCARTADO, "term"), (Resultado.EN_ESPERA, None)])
def test_cada_resultado_hace_lo_suyo_con_el_mensaje(resultado, hecho):
    async def atender(_):
        return resultado

    assert resultado_de(atender) == (resultado, hecho)


def test_un_fallo_no_previsto_deja_el_mensaje_en_espera_en_vez_de_perderlo():
    async def atender(_):
        raise RuntimeError("¿tienen zapatos rojos?")

    assert resultado_de(atender) == (Resultado.EN_ESPERA, None)


def test_las_entregas_las_cuenta_bifrost():
    assert entregas(Mensaje(entregas=3)) == 3
    assert entregas(SimpleNamespace()) == 1


class Suscripcion:
    """Contesta una vez cada cosa de la lista y después para el bucle."""

    def __init__(self, stop, *respuestas):
        self._stop, self._respuestas = stop, list(respuestas)

    async def fetch(self, _lote, timeout):
        assert timeout > 0
        if not self._respuestas:
            self._stop.set()
            raise asyncio.TimeoutError
        respuesta = self._respuestas.pop(0)
        if isinstance(respuesta, BaseException):
            raise respuesta
        return respuesta


def test_el_bucle_sigue_tras_un_silencio_y_un_corte_y_atiende_lo_que_llega(tmp_path, monkeypatch):
    monkeypatch.setattr("src.workers.runtime.asyncio.sleep", _no_esperar)
    vivo = tmp_path / "vivo"
    uno, otro = Mensaje(), Mensaje()
    atendidos = []

    async def atender(mensaje):
        atendidos.append(mensaje)
        return Resultado.CONFIRMADO

    async def correr():
        stop = asyncio.Event()
        await consumir(Suscripcion(stop, asyncio.TimeoutError(), nats.errors.ConnectionReconnectingError(), [uno],
                                   [otro]), stop, atender, vivo)

    asyncio.run(correr())

    assert atendidos == [uno, otro] and (uno.hecho, otro.hecho) == ("ack", "ack")
    assert vivo.exists()


async def _no_esperar(_segundos):
    return None


class Bifrost:
    """La conexión con Bifröst de ejecutar: un consumidor por durable, cada uno con lo suyo."""

    def __init__(self, por_durable):
        self.atados, self.drenado, self._por_durable = [], False, por_durable

    def jetstream(self):
        return self

    async def pull_subscribe_bind(self, durable, stream):
        self.atados.append((stream, durable))
        return self._por_durable[durable]

    async def drain(self):
        self.drenado = True


class Consumidor:
    """Da sus mensajes y después, o se queda callado, o se cae."""

    def __init__(self, mensajes, max_deliver, caida=None):
        self._mensajes, self._max_deliver, self._caida = list(mensajes), max_deliver, caida

    async def consumer_info(self):
        return SimpleNamespace(config=SimpleNamespace(max_deliver=self._max_deliver))

    async def fetch(self, _lote, timeout):
        await asyncio.sleep(0)
        if self._mensajes:
            return [self._mensajes.pop(0)]
        if self._caida:
            raise self._caida
        raise asyncio.TimeoutError


def test_un_worker_atiende_otros_consumidores_a_la_vez_y_si_uno_se_cae_se_cae_el_worker(monkeypatch, tmp_path):
    propio, extra = Mensaje(), Mensaje()
    bifrost = Bifrost({"notifier": Consumidor([propio], 5),
                       "notifier-leido": Consumidor([extra], 1, caida=RuntimeError("caído"))})
    monkeypatch.setattr("src.workers.runtime.conectar", lambda *_: _devolver(bifrost))
    monkeypatch.setattr("src.workers.runtime.configurar_log", lambda _nivel: None)
    monkeypatch.setattr("src.workers.runtime.pathlib.Path", lambda ruta: tmp_path / ruta.rsplit("/", 1)[-1])
    vistos, cerrados = [], []

    def crear(nombre):
        async def preparar(max_deliver, conexion):
            assert conexion is bifrost
            vistos.append((nombre, max_deliver))

            async def atender(_mensaje):
                return Resultado.CONFIRMADO

            async def cerrar():
                cerrados.append(nombre)

            return atender, cerrar
        return preparar

    settings = SimpleNamespace(log_level="INFO")
    with pytest.raises(RuntimeError):
        asyncio.run(ejecutar("notifier", settings, "MOTOR", "notifier", crear("propio"),
                             ademas=[("MENSAJES", "notifier-leido", crear("leido"))]))

    assert bifrost.atados == [("MOTOR", "notifier"), ("MENSAJES", "notifier-leido")]
    assert vistos == [("propio", 5), ("leido", 1)] and (propio.hecho, extra.hecho) == ("ack", "ack")
    assert cerrados == ["propio", "leido"] and bifrost.drenado
    # El fichero de vida del contenedor es el del consumidor propio.
    assert (tmp_path / "notifier-alive").exists() and (tmp_path / "notifier-leido-alive").exists()


async def _devolver(valor):
    return valor
