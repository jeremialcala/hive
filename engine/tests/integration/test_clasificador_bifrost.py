# -*- coding: utf-8 -*-
"""
    El clasificador contra un NATS JetStream de verdad, con los permisos que Bifröst da a su usuario
    (nats-test.conf): se ata al consumidor `MENSAJES/classifier`, lee el mensaje identificado que publicó el gateway,
    publica el sobre en `MOTOR` y solo entonces lo confirma. Modelo lo sustituye un doble.

        docker run --rm -p 127.0.0.1:24222:4222 -v "$PWD/tests/integration/nats-test.conf:/etc/nats/test.conf:ro" \
            nats:2.14.8-alpine -c /etc/nats/test.conf
        NATS_TEST_ADDR=127.0.0.1:24222 pytest tests/integration

    Sin NATS_TEST_ADDR se salta; con NATS_TEST_REQUIRED, falla.
"""
import asyncio
import json
import os
from uuid import uuid4

import nats
import nats.errors
import nats.js.errors
import pytest
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy, StreamConfig

from src.domain.models.sobre import Clasificacion, MensajeClasificado
from src.domain.ports.clasificador_de_entrada import ClasificacionFallida, ClasificadorDeEntrada
from src.workers.classifier import CONSUMIDOR, STREAM, Clasificar
from src.workers.runtime import conectar, consumir, publicador
from src.workers.settings import WorkerSettings

DIRECCION = os.environ.get("NATS_TEST_ADDR")


@pytest.fixture(scope="module")
def url():
    if not DIRECCION:
        if os.environ.get("NATS_TEST_REQUIRED"):
            pytest.fail("Aquí hace falta NATS_TEST_ADDR")
        pytest.skip("Sin NATS contra el que probar: NATS_TEST_ADDR")
    return f"nats://{DIRECCION}"


def ajustes(url) -> WorkerSettings:
    return WorkerSettings(nats_url=url, nats_user="classifier", nats_password="test",
                          nats_inbox_prefix="_INBOX_classifier", andvari_addr="https://andvari:8200",
                          engine_role_id="no-se-usa", engine_secret_id_file="/no/se/usa")


async def _como_admin(url, trabajo):
    conexion = await nats.connect(url, user="admin", password="test")
    try:
        return await trabajo(conexion.jetstream())
    finally:
        await conexion.close()


def como_admin(url, trabajo):
    return asyncio.run(_como_admin(url, trabajo))


@pytest.fixture
def streams(url):
    """MENSAJES con el consumidor del clasificador y MOTOR, como los tiene Bifröst (repositorio bifrost, jetstream/)."""
    async def crear(js):
        for nombre in ("MENSAJES", "MOTOR"):
            try:
                await js.delete_stream(nombre)
            except nats.js.errors.NotFoundError:
                # No quedó de una ejecución anterior: nada que borrar.
                pass
        await js.add_stream(StreamConfig(name="MENSAJES", subjects=["gateway.message.>"], max_age=24 * 3600,
                                         duplicate_window=120))
        await js.add_stream(StreamConfig(name="MOTOR", subjects=["motor.>"], max_age=24 * 3600, duplicate_window=120))
        # ack_wait corto para que una prueba vea la reentrega; el resto, como consumidor-mensajes-classifier.json.
        await js.add_consumer("MENSAJES", ConsumerConfig(
            durable_name="classifier", filter_subject="gateway.message.>", ack_policy=AckPolicy.EXPLICIT,
            ack_wait=1, deliver_policy=DeliverPolicy.NEW, max_ack_pending=1, max_deliver=4, backoff=[1, 1, 1]))
    como_admin(url, crear)
    return url


def identificado(bot, contacto, texto="¿tienen zapatos rojos?"):
    return {"schemaVersion": 2, "botId": str(bot), "clientId": str(uuid4()), "messageId": f"wamid.{uuid4().hex}",
            "message": {"contacts": [{"wa_id": "12025550143"}],
                        "messages": [{"from": "12025550143", "type": "text", "text": {"body": texto}}]},
            "channel": "whatsapp", "receivedAt": "2026-10-20T09:00:00+00:00", "orderKey": f"{bot}.{contacto}",
            "contact": {"uuid": str(contacto), "name": "Ana", "waId": "12025550143"}}


class Modelo(ClasificadorDeEntrada):

    def __init__(self, *respuestas):
        self._respuestas = list(respuestas)

    async def clasificar(self, texto):
        respuesta = self._respuestas.pop(0)
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta


PERMITIDO = Clasificacion(permitido=True, motivo="ninguno", etiqueta="pregunta", confianza=0.9,
                          modelo="gemma-4-26b-a4b")


async def _clasificar(url, modelo, evento, subject, hasta):
    """Publica el evento como el gateway y deja correr al clasificador hasta que `hasta` diga que ya está."""
    async def publicar(js):
        await js.publish(subject, json.dumps(evento).encode(), headers={"Nats-Msg-Id": f"x:{uuid4()}"})
    await _como_admin(url, publicar)

    settings = ajustes(url)
    conexion = await conectar(settings, "classifier")
    publisher = publicador(settings, "classifier")
    try:
        suscripcion = await conexion.jetstream().pull_subscribe_bind(durable=CONSUMIDOR, stream=STREAM)
        info = await suscripcion.consumer_info()
        atender = Clasificar(modelo, publisher, info.config.max_deliver)
        stop = asyncio.Event()

        async def vigilar():
            for _ in range(100):
                if await _como_admin(url, hasta):
                    break
                await asyncio.sleep(0.1)
            stop.set()

        await asyncio.gather(consumir(suscripcion, stop, atender, _Vivo()), vigilar())
    finally:
        await publisher.close()
        await conexion.drain()


class _Vivo:
    def touch(self):
        pass


async def _clasificados(js):
    try:
        info = await js.stream_info("MOTOR")
    except nats.js.errors.NotFoundError:
        return 0
    return info.state.messages


async def _confirmados(js):
    return (await js.consumer_info("MENSAJES", "classifier")).ack_floor.stream_seq


async def _sobre(js):
    mensaje = await js.get_last_msg("MOTOR", "motor.clasificado.>")
    return mensaje.subject, json.loads(mensaje.data), mensaje.headers or {}


def test_el_clasificador_lee_mensajes_publica_el_sobre_en_motor_y_confirma(streams):
    bot, contacto = uuid4(), uuid4()
    evento = identificado(bot, contacto)

    async def hecho(js):
        return await _confirmados(js) == 1

    asyncio.run(_clasificar(streams, Modelo(PERMITIDO), evento, f"gateway.message.5.{bot}.{contacto}", hecho))

    subject, sobre, cabeceras = como_admin(streams, _sobre)
    assert subject == f"motor.clasificado.5.{bot}.{contacto}"
    assert cabeceras["Nats-Msg-Id"] == f"clasificado:{bot}:{evento['messageId']}"
    assert MensajeClasificado.model_validate(sobre).clasificacion.permitido is True
    assert como_admin(streams, _confirmados) == 1


def test_sin_veredicto_bifrost_lo_entrega_otra_vez_y_la_segunda_sale(streams):
    bot, contacto = uuid4(), uuid4()

    async def hecho(js):
        return await _confirmados(js) == 1

    asyncio.run(_clasificar(streams, Modelo(ClasificacionFallida("APITimeoutError"), PERMITIDO),
                            identificado(bot, contacto), f"gateway.message.5.{bot}.{contacto}", hecho))

    assert como_admin(streams, _clasificados) == 1
    _, sobre, _ = como_admin(streams, _sobre)
    assert sobre["clasificacion"]["motivo"] == "ninguno"


def test_el_usuario_del_clasificador_solo_publica_su_paso(streams):
    async def intentar():
        settings = ajustes(streams)
        publisher = publicador(settings, "classifier")
        try:
            for subject in ("motor.pedido.5.a.b", "gateway.message.5.a.b", "engine.reply.sent.a"):
                with pytest.raises(Exception) as fallo:
                    await publisher.publish(subject, b"{}", f"prueba:{uuid4()}")
                assert type(fallo.value).__name__ == "BusUnavailable"
        finally:
            await publisher.close()

    asyncio.run(intentar())
    assert como_admin(streams, _clasificados) == 0
