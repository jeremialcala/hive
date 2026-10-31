# -*- coding: utf-8 -*-
"""
    El notificador contra un NATS JetStream de verdad, con los permisos que Bifröst da a su usuario
    (nats-test.conf): se ata al consumidor `MOTOR/notifier-aprobada` (HIG-146), mira y apunta en el bucket `enviados` lo que envía,
    publica lo enviado en `engine.reply.sent.{botId}` (stream RESPUESTAS) y solo entonces confirma. Meta y el gateway
    los sustituyen dobles.

        NATS_TEST_ADDR=127.0.0.1:24222 pytest tests/integration

    Sin NATS_TEST_ADDR se salta; con NATS_TEST_REQUIRED, falla.
"""
import asyncio
import json
import os
from uuid import uuid4

import nats
import nats.js.errors
import pytest
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy, KeyValueConfig, RetentionPolicy, StreamConfig

from shared.contract import SentReply
from src.infrastructure.bifrost import EnviadosEnBifrost
from src.workers.notifier import CONSUMIDOR, STREAM, Notificar
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
def bifrost(url):
    """MOTOR con el consumidor del notificador, RESPUESTAS y el bucket `enviados`, como los tiene Bifröst."""
    async def crear(js):
        for nombre in ("MOTOR", "RESPUESTAS", "KV_enviados", "KV_contexto"):
            try:
                await js.delete_stream(nombre)
            except nats.js.errors.NotFoundError:
                # No quedó de una ejecución anterior: nada que borrar.
                pass
        await js.add_stream(StreamConfig(name="MOTOR", subjects=["motor.>"], max_age=24 * 3600, duplicate_window=120))
        await js.add_stream(StreamConfig(name="RESPUESTAS", subjects=["engine.reply.sent.>", "engine.reply.failed.>"],
                                         retention=RetentionPolicy.WORK_QUEUE, max_age=24 * 3600,
                                         duplicate_window=120))
        await js.add_consumer("MOTOR", ConsumerConfig(
            durable_name="notifier-aprobada", filter_subject="motor.aprobada.>", ack_policy=AckPolicy.EXPLICIT,
            ack_wait=1, deliver_policy=DeliverPolicy.ALL, max_ack_pending=1, max_deliver=5, backoff=[1, 1, 1, 1]))
        await js.create_key_value(KeyValueConfig(bucket="enviados", history=1, ttl=24 * 3600, max_value_size=1024))
        # Un bucket de otro worker, que el notificador no debe alcanzar.
        await js.create_key_value(KeyValueConfig(bucket="contexto", history=1, ttl=24 * 3600))
    asyncio.run(_como_admin(url, crear))
    return url


class Gateway:

    async def token_de_whatsapp(self, _client_id, _bot_id):
        return "EAAG-wa"

    def olvidar(self, _bot_id):
        pass


class Meta:

    def __init__(self):
        self.envios = 0

    async def enviar_texto(self, _token, _cuenta, _destinatario, _texto):
        self.envios += 1
        return f"wamid.ENVIADO.{self.envios}"


def respondido(bot, contacto, message_id):
    return {"schemaVersion": 2, "botId": str(bot), "clientId": str(uuid4()), "messageId": message_id,
            "channel": "whatsapp", "receivedAt": "2026-10-20T09:00:00+00:00", "orderKey": f"{bot}.{contacto}",
            "contact": {"uuid": str(contacto), "waId": "12025550143"}, "cuentaDelBot": "106540352242922",
            "entrada": {"texto": "¿tienen zapatos rojos?", "adjuntos": []},
            "clasificacion": {"permitido": True, "motivo": "ninguno", "etiqueta": "pregunta", "confianza": 0.9,
                              "entidades": [], "modelo": "gemma-4-26b-a4b"},
            "respuesta": {"texto": "Sí, tenemos zapatos rojos.", "fuente": "modelo", "modelo": "gemma-4-26b-a4b"},
            "evaluacion": {"veredicto": "aprobada", "criterio": "ninguno", "regla": 0, "modelo": "gemma-4-26b-a4b"}}


def ajustes(url):
    return WorkerSettings(nats_url=url, nats_user="notifier", nats_password="test",
                          nats_inbox_prefix="_INBOX_notifier", andvari_addr="https://andvari:8200",
                          engine_role_id="no-se-usa", engine_secret_id_file="/no/se/usa")


class _Vivo:
    def touch(self):
        pass


async def _notificar(url, meta, sobres, confirmados):
    """Publica los sobres como el de respuesta y deja correr al notificador hasta que confirme `confirmados`."""
    async def publicar(js):
        for subject, sobre in sobres:
            await js.publish(subject, json.dumps(sobre).encode(), headers={"Nats-Msg-Id": f"x:{uuid4()}"})
    await _como_admin(url, publicar)

    async def hecho(js):
        return (await js.consumer_info("MOTOR", CONSUMIDOR)).ack_floor.stream_seq >= confirmados

    settings = ajustes(url)
    conexion = await conectar(settings, "notifier")
    publisher = publicador(settings, "notifier")
    try:
        suscripcion = await conexion.jetstream().pull_subscribe_bind(durable=CONSUMIDOR, stream=STREAM)
        info = await suscripcion.consumer_info()
        atender = Notificar(Gateway(), meta, EnviadosEnBifrost(conexion.jetstream()), publisher,
                            info.config.max_deliver)
        stop = asyncio.Event()

        async def vigilar():
            for _ in range(100):
                if await _como_admin(url, hecho):
                    break
                await asyncio.sleep(0.1)
            stop.set()

        await asyncio.gather(consumir(suscripcion, stop, atender, _Vivo()), vigilar())
    finally:
        await publisher.close()
        await conexion.drain()


async def _respuestas(js):
    return (await js.stream_info("RESPUESTAS")).state.messages


def test_envia_apunta_publica_lo_enviado_y_no_envia_dos_veces_la_misma_respuesta(bifrost):
    bot, contacto = uuid4(), uuid4()
    sobre = respondido(bot, contacto, f"wamid.{uuid4().hex}")
    subject = f"motor.aprobada.5.{bot}.{contacto}"
    meta = Meta()

    # La misma respuesta dos veces, como si Bifröst la entregara otra vez tras un fallo.
    asyncio.run(_notificar(bifrost, meta, [(subject, sobre), (subject, sobre)], confirmados=2))

    assert meta.envios == 1
    # La segunda publica lo mismo, con el mismo Nats-Msg-Id: RESPUESTAS la descarta por duplicada.
    assert asyncio.run(_como_admin(bifrost, _respuestas)) == 1

    async def enviado(js):
        return await js.get_last_msg("RESPUESTAS", f"engine.reply.sent.{bot}")

    mensaje = asyncio.run(_como_admin(bifrost, enviado))
    evento = SentReply.model_validate(json.loads(mensaje.data))
    assert (evento.inReplyTo, evento.messageId) == (sobre["messageId"], "wamid.ENVIADO.1")
    assert mensaje.headers["Nats-Msg-Id"] == f"{bot}:wamid.ENVIADO.1"


def test_el_usuario_del_notificador_no_alcanza_otros_buckets_ni_otros_pasos(bifrost):
    async def intentar():
        settings = ajustes(bifrost)
        conexion = await conectar(settings, "notifier")
        publisher = publicador(settings, "notifier")
        try:
            with pytest.raises(Exception):
                await asyncio.wait_for(conexion.jetstream().key_value("contexto"), 3)
            for subject in ("motor.respuesta.5.a.b", "gateway.message.5.a.b", "$KV.contexto.x"):
                with pytest.raises(Exception) as fallo:
                    await publisher.publish(subject, b"{}", f"prueba:{uuid4()}")
                assert type(fallo.value).__name__ == "BusUnavailable"
        finally:
            await publisher.close()
            await conexion.drain()

    asyncio.run(intentar())
