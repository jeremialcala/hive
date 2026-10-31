# -*- coding: utf-8 -*-
"""
    El contexto del coordinador contra un NATS JetStream de verdad, con los permisos que Bifröst da a su usuario
    (nats-test.conf): lee y escribe el bucket `contexto` con compare-and-swap, purga lo suprimido por sujeto, se ata a
    sus dos consumidores extra y no alcanza ningún otro bucket. Andvari lo sustituye un doble que cifra en memoria;
    el de verdad se prueba en tests/integration/test_engine_key_store.py del gateway.

        NATS_TEST_ADDR=127.0.0.1:24222 pytest tests/integration

    Sin NATS_TEST_ADDR se salta; con NATS_TEST_REQUIRED, falla.
"""
import asyncio
import os
from datetime import datetime, timezone
from uuid import uuid4

import nats
import nats.errors
import nats.js.errors
import pytest
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy, KeyValueConfig, StreamConfig

from src.domain.models.sobre import Vuelta
from src.domain.ports.contexto import Apunte, Choque
from src.infrastructure.bifrost.contexto import ContextoEnBifrost
from src.workers.coordinator import CONSUMIDOR_RESPUESTAS, NOMBRE, STREAM, SUPRESIONES

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
def bifrost(url):
    """El bucket `contexto`, MOTOR, SUPRESIONES y los consumidores del coordinador, como los tiene Bifröst."""
    async def crear(js):
        for nombre in ("MOTOR", "SUPRESIONES", "KV_contexto", "KV_enviados"):
            try:
                await js.delete_stream(nombre)
            except nats.js.errors.NotFoundError:
                # No quedó de una ejecución anterior: nada que borrar.
                pass
        await js.create_key_value(KeyValueConfig(bucket="contexto", history=1, ttl=24 * 3600,
                                                 max_value_size=262144))
        await js.create_key_value(KeyValueConfig(bucket="enviados", history=1, ttl=24 * 3600, max_value_size=1024))
        await js.add_stream(StreamConfig(name="MOTOR", subjects=["motor.>"], max_age=24 * 3600, duplicate_window=120))
        await js.add_stream(StreamConfig(name="SUPRESIONES", subjects=["gateway.contact.erased", "gateway.bot.erased",
                                                                       "gateway.client.erased"], max_age=7 * 24 * 3600))
        await js.add_consumer("MOTOR", ConsumerConfig(
            durable_name="coordinator-aprobada", filter_subject="motor.aprobada.>", ack_policy=AckPolicy.EXPLICIT,
            ack_wait=30, deliver_policy=DeliverPolicy.NEW, max_ack_pending=1, max_deliver=3, backoff=[30, 60]))
        await js.add_consumer("SUPRESIONES", ConsumerConfig(
            durable_name="coordinator", ack_policy=AckPolicy.EXPLICIT, ack_wait=30, deliver_policy=DeliverPolicy.ALL,
            max_ack_pending=1, max_deliver=6, backoff=[30, 60, 300, 900, 3600]))
    asyncio.run(_como_admin(url, crear))
    return url


class Andvari:
    """Transit en memoria: lo que sale no lleva el texto en claro."""

    def encrypt(self, _key, client_id, plaintext):
        return f"vault:v1:{client_id}:{plaintext.hex()}"

    def decrypt(self, _key, _client_id, ciphertext):
        return bytes.fromhex(ciphertext.split(":", 3)[3])


async def _como_coordinador(url, trabajo, espera=5):
    conexion = await nats.connect(url, user="coordinator", password="test", inbox_prefix="_INBOX_coordinator")
    try:
        return await trabajo(conexion.jetstream(timeout=espera))
    finally:
        await conexion.close()


def apunte(texto, mensaje="wamid.A"):
    return Apunte(vuelta=Vuelta(de="persona", texto=texto, enviadaEn=datetime(2026, 10, 30, 9, tzinfo=timezone.utc)),
                  mensaje=mensaje)


def test_el_coordinador_escribe_y_lee_el_contexto_con_su_usuario(bifrost):
    cliente, bot, contacto = uuid4(), uuid4(), uuid4()

    async def trabajo(js):
        contexto = ContextoEnBifrost(js, Andvari())
        assert await contexto.leer(cliente, bot, contacto) is None
        revision = await contexto.guardar(cliente, bot, contacto, [apunte("hola")], 0)
        revision = await contexto.guardar(cliente, bot, contacto, [apunte("hola"), apunte("¿y?", "wamid.B")], revision)
        with pytest.raises(Choque):
            await contexto.guardar(cliente, bot, contacto, [apunte("pisado")], revision - 1)
        with pytest.raises(Choque):
            await contexto.guardar(cliente, bot, contacto, [apunte("pisado")], 0)
        return await contexto.leer(cliente, bot, contacto)

    leido = asyncio.run(_como_coordinador(bifrost, trabajo))

    assert [a.vuelta.texto for a in leido.apuntes] == ["hola", "¿y?"] and leido.revision >= 2

    async def en_claro(js):
        return (await js.get_last_msg("KV_contexto", f"$KV.contexto.{cliente}.{bot}.{contacto}")).data

    assert b"hola" not in asyncio.run(_como_admin(bifrost, en_claro))


def test_lo_suprimido_se_purga_por_sujeto_y_lo_demas_se_queda(bifrost):
    cliente, otro, bot, otro_bot = uuid4(), uuid4(), uuid4(), uuid4()
    a, b, c = uuid4(), uuid4(), uuid4()

    async def trabajo(js):
        contexto = ContextoEnBifrost(js, Andvari())
        for clave in ((cliente, bot, a), (cliente, bot, b), (cliente, otro_bot, c), (otro, bot, a)):
            await contexto.guardar(*clave, [apunte("hola")], 0)

        async def quedan():
            return [clave for clave in ((cliente, bot, a), (cliente, bot, b), (cliente, otro_bot, c), (otro, bot, a))
                    if await contexto.leer(*clave) is not None]

        await contexto.olvidar(cliente, bot, a)
        tras_contacto = await quedan()
        await contexto.olvidar(cliente, bot)
        tras_bot = await quedan()
        await contexto.olvidar(cliente)
        tras_cliente = await quedan()
        # Una conversación purgada se puede empezar otra vez desde la revisión 0.
        await contexto.guardar(cliente, bot, a, [apunte("de nuevo")], 0)
        return tras_contacto, tras_bot, tras_cliente

    tras_contacto, tras_bot, tras_cliente = asyncio.run(_como_coordinador(bifrost, trabajo))

    assert tras_contacto == [(cliente, bot, b), (cliente, otro_bot, c), (otro, bot, a)]
    assert tras_bot == [(cliente, otro_bot, c), (otro, bot, a)]
    assert tras_cliente == [(otro, bot, a)]


def test_el_coordinador_se_ata_a_sus_dos_consumidores_extra(bifrost):
    async def trabajo(js):
        respuestas = await js.pull_subscribe_bind(durable=CONSUMIDOR_RESPUESTAS, stream=STREAM)
        supresiones = await js.pull_subscribe_bind(durable=NOMBRE, stream=SUPRESIONES)
        return (await respuestas.consumer_info()).config.filter_subject, \
            (await supresiones.consumer_info()).config.max_deliver

    assert asyncio.run(_como_coordinador(bifrost, trabajo)) == ("motor.aprobada.>", 6)


def test_el_coordinador_no_alcanza_otro_bucket(bifrost):
    async def trabajo(js):
        with pytest.raises((nats.errors.Error, asyncio.TimeoutError)):
            kv = await js.key_value("enviados")
            await kv.put("x", b"y")
        with pytest.raises((nats.errors.Error, asyncio.TimeoutError)):
            await js.purge_stream("KV_enviados")

    # Lo que no le está permitido no recibe respuesta: se espera poco.
    asyncio.run(_como_coordinador(bifrost, trabajo, espera=1))
