# -*- coding: utf-8 -*-
"""
    El evaluador contra un NATS JetStream de verdad, con los permisos que Bifröst da a su usuario (nats-test.conf): se
    ata al consumidor `MOTOR/evaluator`, lee lo que propone el de respuesta y publica lo aprobado en
    `motor.aprobada.…`, o el pedido de reescritura en `motor.pedido.…`, y solo entonces confirma. El LLM lo sustituye
    un doble.

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

from src.domain.models.sobre import MensajeAprobado, MensajePedido
from src.domain.ports.evaluador import Juicio
from src.workers.evaluator import CONSUMIDOR, STREAM, Evaluar
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
    """MOTOR con el consumidor del evaluador, como lo tiene Bifröst (consumidor-motor-evaluator.json)."""
    async def crear(js):
        try:
            await js.delete_stream("MOTOR")
        except nats.js.errors.NotFoundError:
            # No quedó de una ejecución anterior: nada que borrar.
            pass
        await js.add_stream(StreamConfig(name="MOTOR", subjects=["motor.>"], max_age=24 * 3600, duplicate_window=120))
        await js.add_consumer("MOTOR", ConsumerConfig(
            durable_name="evaluator", filter_subject="motor.respuesta.>", ack_policy=AckPolicy.EXPLICIT,
            ack_wait=1, deliver_policy=DeliverPolicy.ALL, max_ack_pending=1, max_deliver=4, backoff=[1, 1, 1]))
    asyncio.run(_como_admin(url, crear))
    return url


class Juez:

    def __init__(self, cumple):
        self._cumple = cumple

    async def juzgar(self, _persona, _vueltas, _texto, _respuesta):
        return Juicio(cumple=self._cumple, criterio="ninguno" if self._cumple else "regla",
                      regla=0 if self._cumple else 1, motivo="Promete un descuento.", modelo="gemma-4-26b-a4b")


def respondido(bot, contacto):
    return {"schemaVersion": 2, "botId": str(bot), "clientId": str(uuid4()), "messageId": f"wamid.{uuid4().hex}",
            "channel": "whatsapp", "receivedAt": "2026-10-31T09:00:00+00:00", "orderKey": f"{bot}.{contacto}",
            "contact": {"uuid": str(contacto), "waId": "12025550143"},
            "entrada": {"texto": "¿me haces un descuento?", "adjuntos": []},
            "clasificacion": {"permitido": True, "motivo": "ninguno", "etiqueta": "pedido", "confianza": 0.9,
                              "entidades": [], "modelo": "gemma-4-26b-a4b"},
            "respuesta": {"texto": "¡Te dejo un 10 %!", "fuente": "modelo", "modelo": "gemma-4-26b-a4b"},
            "pedido": {"respuesta": "modelo", "vueltas": [], "reescribir": None,
                       "persona": {"nombre": "Mine", "rol": "Ventas", "tono": "cercano", "descripcion": "",
                                   "reglas": ["No damos descuentos."]}}}


def ajustes(url):
    return WorkerSettings(nats_url=url, nats_user="evaluator", nats_password="test",
                          nats_inbox_prefix="_INBOX_evaluator", andvari_addr="https://andvari:8200",
                          engine_role_id="no-se-usa", engine_secret_id_file="/no/se/usa")


def _evaluar(url, juez, bot, contacto) -> dict:
    """Publica una respuesta propuesta, deja al evaluador atenderla y devuelve lo que publicó."""
    sobre = respondido(bot, contacto)

    async def publicar(js):
        await js.publish(f"motor.respuesta.5.{bot}.{contacto}", json.dumps(sobre).encode(),
                         headers={"Nats-Msg-Id": f"respuesta:{bot}:{sobre['messageId']}"})

    async def confirmado(js):
        return (await js.consumer_info("MOTOR", CONSUMIDOR)).ack_floor.stream_seq >= 1

    async def correr():
        await _como_admin(url, publicar)
        settings = ajustes(url)
        conexion = await conectar(settings, "evaluator")
        publisher = publicador(settings, "evaluator")
        try:
            suscripcion = await conexion.jetstream().pull_subscribe_bind(durable=CONSUMIDOR, stream=STREAM)
            stop = asyncio.Event()

            async def vigilar():
                for _ in range(100):
                    if await _como_admin(url, confirmado):
                        break
                    await asyncio.sleep(0.1)
                stop.set()

            class Vivo:
                def touch(self):
                    pass

            await asyncio.gather(consumir(suscripcion, stop, Evaluar(juez, publisher, 4), Vivo()), vigilar())
        finally:
            await publisher.close()
            await conexion.drain()

    asyncio.run(correr())
    assert asyncio.run(_como_admin(url, confirmado))
    return sobre


def test_lo_que_cumple_sale_en_motor_aprobada(motor):
    bot, contacto = uuid4(), uuid4()
    sobre = _evaluar(motor, Juez(cumple=True), bot, contacto)

    async def aprobada(js):
        return await js.get_last_msg("MOTOR", "motor.aprobada.>")

    mensaje = asyncio.run(_como_admin(motor, aprobada))
    assert mensaje.subject == f"motor.aprobada.5.{bot}.{contacto}"
    assert mensaje.headers["Nats-Msg-Id"] == f"aprobada:{bot}:{sobre['messageId']}"
    leido = MensajeAprobado.model_validate(json.loads(mensaje.data))
    assert leido.evaluacion.veredicto == "aprobada" and b"No damos descuentos" not in mensaje.data


def test_lo_que_no_cumple_vuelve_al_de_respuesta_como_pedido(motor):
    bot, contacto = uuid4(), uuid4()
    sobre = _evaluar(motor, Juez(cumple=False), bot, contacto)

    async def pedido(js):
        return await js.get_last_msg("MOTOR", "motor.pedido.>")

    mensaje = asyncio.run(_como_admin(motor, pedido))
    assert mensaje.subject == f"motor.pedido.5.{bot}.{contacto}"
    assert mensaje.headers["Nats-Msg-Id"] == f"pedido:{bot}:{sobre['messageId']}:reescrita"
    assert MensajePedido.model_validate(json.loads(mensaje.data)).pedido.reescribir.regla == 1


def test_el_evaluador_no_publica_donde_no_le_toca(url):
    async def trabajo():
        conexion = await nats.connect(url, user="evaluator", password="test", inbox_prefix="_INBOX_evaluator")
        try:
            js = conexion.jetstream(timeout=1)
            for subject in ("motor.respuesta.5.a.b", "motor.clasificado.5.a.b", "engine.reply.sent.a"):
                with pytest.raises((nats.errors.Error, asyncio.TimeoutError)):
                    await js.publish(subject, b"{}")
        finally:
            await conexion.close()

    asyncio.run(trabajo())
