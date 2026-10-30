# -*- coding: utf-8 -*-
"""
    El coordinador contra un NATS JetStream de verdad, con los permisos que Bifröst da a su usuario
    (nats-test.conf): se ata al consumidor `MOTOR/coordinator`, lee el sobre del clasificador, publica el pedido en
    `motor.pedido.…` del mismo stream y solo entonces confirma. El gateway lo sustituye un doble.

        NATS_TEST_ADDR=127.0.0.1:24222 pytest tests/integration

    Sin NATS_TEST_ADDR se salta; con NATS_TEST_REQUIRED, falla.
"""
import asyncio
import json
import os
from datetime import datetime, timezone
from uuid import uuid4

import nats
import nats.js.errors
import pytest
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy, StreamConfig

from src.domain.models.sobre import MensajePedido, Persona, Vuelta
from src.workers.coordinator import CONSUMIDOR, STREAM, Coordinar
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


async def _como_admin(url, trabajo):
    conexion = await nats.connect(url, user="admin", password="test")
    try:
        return await trabajo(conexion.jetstream())
    finally:
        await conexion.close()


@pytest.fixture
def motor(url):
    """MOTOR con el consumidor del coordinador, como lo tiene Bifröst (consumidor-motor-coordinator.json)."""
    async def crear(js):
        try:
            await js.delete_stream("MOTOR")
        except nats.js.errors.NotFoundError:
            # No quedó de una ejecución anterior: nada que borrar.
            pass
        await js.add_stream(StreamConfig(name="MOTOR", subjects=["motor.>"], max_age=24 * 3600, duplicate_window=120))
        await js.add_consumer("MOTOR", ConsumerConfig(
            durable_name="coordinator", filter_subject="motor.clasificado.>", ack_policy=AckPolicy.EXPLICIT,
            ack_wait=1, deliver_policy=DeliverPolicy.ALL, max_ack_pending=1, max_deliver=4, backoff=[1, 1, 1]))
    asyncio.run(_como_admin(url, crear))
    return url


class Gateway:

    async def persona(self, _client_id, _bot_id):
        return Persona(nombre="Mine", rol="Sales Assistant", tono="helpful", reglas=["Be kind"])

    async def vueltas(self, _client_id, _bot_id, _contact_id, _sin, _hasta):
        return [Vuelta(de="persona", texto="hola", enviadaEn=datetime(2026, 10, 20, 8, tzinfo=timezone.utc))]


def clasificado(bot, contacto):
    return {"schemaVersion": 2, "botId": str(bot), "clientId": str(uuid4()), "messageId": f"wamid.{uuid4().hex}",
            "channel": "whatsapp", "receivedAt": "2026-10-20T09:00:00+00:00", "orderKey": f"{bot}.{contacto}",
            "contact": {"uuid": str(contacto), "waId": "12025550143"},
            "entrada": {"texto": "¿tienen zapatos rojos?", "adjuntos": []},
            "clasificacion": {"permitido": True, "motivo": "ninguno", "etiqueta": "pregunta", "confianza": 0.9,
                              "entidades": [], "modelo": "gemma-4-26b-a4b"}}


def test_el_coordinador_lee_lo_clasificado_publica_el_pedido_y_confirma(motor):
    bot, contacto = uuid4(), uuid4()
    sobre = clasificado(bot, contacto)

    async def publicar(js):
        await js.publish(f"motor.clasificado.5.{bot}.{contacto}", json.dumps(sobre).encode(),
                         headers={"Nats-Msg-Id": f"clasificado:{bot}:{sobre['messageId']}"})

    async def confirmado(js):
        return (await js.consumer_info("MOTOR", "coordinator")).ack_floor.stream_seq >= 1

    async def correr():
        await _como_admin(motor, publicar)
        settings = WorkerSettings(nats_url=motor, nats_user="coordinator", nats_password="test",
                                  nats_inbox_prefix="_INBOX_coordinator", andvari_addr="https://andvari:8200",
                                  engine_role_id="no-se-usa", engine_secret_id_file="/no/se/usa")
        conexion = await conectar(settings, "coordinator")
        publisher = publicador(settings, "coordinator")
        try:
            suscripcion = await conexion.jetstream().pull_subscribe_bind(durable=CONSUMIDOR, stream=STREAM)
            info = await suscripcion.consumer_info()
            stop = asyncio.Event()

            async def vigilar():
                for _ in range(100):
                    if await _como_admin(motor, confirmado):
                        break
                    await asyncio.sleep(0.1)
                stop.set()

            class Vivo:
                def touch(self):
                    pass

            await asyncio.gather(consumir(suscripcion, stop, Coordinar(Gateway(), publisher, info.config.max_deliver),
                                          Vivo()), vigilar())
        finally:
            await publisher.close()
            await conexion.drain()

    asyncio.run(correr())

    async def pedido(js):
        return await js.get_last_msg("MOTOR", "motor.pedido.>")

    mensaje = asyncio.run(_como_admin(motor, pedido))
    assert mensaje.subject == f"motor.pedido.5.{bot}.{contacto}"
    assert mensaje.headers["Nats-Msg-Id"] == f"pedido:{bot}:{sobre['messageId']}"
    leido = MensajePedido.model_validate(json.loads(mensaje.data))
    assert leido.pedido.respuesta == "modelo" and leido.pedido.persona.reglas == ["Be kind"]
    # El pedido no vuelve a entrar al coordinador: su filtro es motor.clasificado.>.
    assert asyncio.run(_como_admin(motor, confirmado))
