# -*- coding: utf-8 -*-
"""
    El worker coordinador con cada mensaje clasificado (E4): qué pide, dónde y con qué cabeceras, cuándo confirma,
    cuándo espera y cuándo descarta. El gateway y Bifröst los sustituyen dobles; la API del gateway se prueba en
    test_api_del_gateway.py y el worker contra un NATS de verdad en tests/integration.
"""
import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from shared import bus
from shared.auth.gateway_keys import new_key, public_part
from src.domain.models.sobre import Clasificacion, MensajePedido, Persona, Vuelta
from src.domain.ports.fuente_del_bot import FuenteDelBot, FuenteNoDisponible, NoEncontrado
from src.workers.coordinator import Coordinar, al_configurar, preparar
from src.workers.runtime import Resultado
from src.workers.settings import CoordinatorSettings

BOT, CLIENTE, CONTACTO = uuid4(), uuid4(), uuid4()
TRAZA = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
PERSONA = Persona(nombre="Mine", rol="Sales Assistant", tono="helpful", reglas=["Be kind"])
VUELTAS = [Vuelta(de="persona", texto="hola", enviadaEn=datetime(2026, 10, 20, 8, tzinfo=timezone.utc)),
           Vuelta(de="bot", texto="¡Hola!", enviadaEn=datetime(2026, 10, 20, 8, 0, 5, tzinfo=timezone.utc))]
PERMITIDO = Clasificacion(permitido=True, motivo="ninguno", etiqueta="pregunta", confianza=0.9,
                          modelo="gemma-4-26b-a4b")


def clasificado(clasificacion=PERMITIDO, **cambios) -> dict:
    return {"schemaVersion": 2, "botId": str(BOT), "clientId": str(CLIENTE), "messageId": "wamid.A",
            "channel": "whatsapp", "receivedAt": "2026-10-20T09:00:00+00:00", "orderKey": f"{BOT}.{CONTACTO}",
            "contact": {"uuid": str(CONTACTO), "waId": "12025550143"},
            "entrada": {"texto": "¿tienen zapatos rojos?", "adjuntos": []},
            "clasificacion": clasificacion.model_dump(), **cambios}


class Mensaje:

    def __init__(self, datos, subject=None, entregas=1, headers=None):
        self.data = datos if isinstance(datos, bytes) else json.dumps(datos).encode()
        self.subject = subject or f"motor.clasificado.7.{BOT}.{CONTACTO}"
        self.headers = {"traceparent": TRAZA} if headers is None else headers
        self.metadata = SimpleNamespace(num_delivered=entregas)


class Bifrost:

    def __init__(self, rechaza=False):
        self.publicado, self.rechaza = [], rechaza

    async def publish(self, subject, payload, message_id, headers=None):
        if self.rechaza:
            raise bus.BusUnavailable("TimeoutError")
        self.publicado.append(SimpleNamespace(subject=subject, sobre=json.loads(payload), message_id=message_id,
                                              headers=headers or {}))
        return bus.Published(stream="MOTOR", sequence=len(self.publicado), duplicate=False)

    async def close(self):
        pass


class Gateway(FuenteDelBot):

    def __init__(self, fallo=None):
        self.preguntas, self._fallo = [], fallo

    async def persona(self, client_id, bot_id):
        self.preguntas.append(("persona", client_id, bot_id))
        if self._fallo:
            raise self._fallo
        return PERSONA

    async def vueltas(self, client_id, bot_id, contact_id, sin, hasta):
        self.preguntas.append(("vueltas", client_id, bot_id, contact_id, sin, hasta))
        return VUELTAS


def atender(mensaje, gateway=None, bifrost=None, max_deliver=4):
    gateway, bifrost = gateway or Gateway(), bifrost or Bifrost()
    return asyncio.run(Coordinar(gateway, bifrost, max_deliver)(mensaje)), gateway, bifrost


def test_el_pedido_lleva_el_resumen_de_lo_anterior_que_da_la_memoria():
    """HIG-143: con el contexto, las vueltas y el resumen de lo que ya salió de ellas."""
    from src.domain.memoria import Recuerdo  # pylint: disable=import-outside-toplevel

    class Memoria:
        async def recuerdo(self, _sobre):
            return Recuerdo(vueltas=VUELTAS, resumen="Ana pidió una cita para el martes.")

        async def apuntar_persona(self, _sobre):
            pass

    bifrost = Bifrost()
    asyncio.run(Coordinar(Gateway(), bifrost, 4, Memoria())(Mensaje(clasificado())))

    pedido = MensajePedido.model_validate(bifrost.publicado[0].sobre).pedido
    assert pedido.vueltas == VUELTAS and pedido.resumen == "Ana pidió una cita para el martes."


def test_un_mensaje_que_sigue_se_pide_con_claude_la_persona_y_las_vueltas():
    resultado, gateway, bifrost = atender(Mensaje(clasificado()))

    assert resultado is Resultado.CONFIRMADO
    (publicado,) = bifrost.publicado
    assert publicado.subject == f"motor.pedido.7.{BOT}.{CONTACTO}"
    assert publicado.message_id == f"pedido:{BOT}:wamid.A"
    sobre = MensajePedido.model_validate(publicado.sobre)
    assert sobre.pedido.respuesta == "modelo"
    assert sobre.pedido.persona == PERSONA and sobre.pedido.vueltas == VUELTAS
    # Lo del clasificador sigue en el sobre.
    assert sobre.entrada.texto == "¿tienen zapatos rojos?" and sobre.clasificacion == PERMITIDO
    # Las vueltas, hasta que llegó el mensaje que se responde.
    assert gateway.preguntas == [("persona", CLIENTE, BOT), ("vueltas", CLIENTE, BOT, CONTACTO, "wamid.A",
                                                           datetime(2026, 10, 20, 9, tzinfo=timezone.utc))]


def test_la_traza_sigue_el_hilo_del_mensaje():
    _, _, bifrost = atender(Mensaje(clasificado()))

    assert bifrost.publicado[0].headers["traceparent"].split("-")[1] == TRAZA.split("-")[1]


@pytest.mark.parametrize("motivo, respuesta", [
    ("inyeccion", "no_puedo"), ("abuso", "no_puedo"), ("otro", "no_puedo"), ("rechazo", "no_puedo"),
    ("sin_texto", "solo_texto"), ("error", "no_disponible")])
def test_un_mensaje_que_no_sigue_se_pide_con_su_texto_fijo_sin_preguntar_al_gateway(motivo, respuesta):
    clasificacion = Clasificacion(permitido=False, motivo=motivo)

    resultado, gateway, bifrost = atender(Mensaje(clasificado(clasificacion)))

    assert resultado is Resultado.CONFIRMADO and gateway.preguntas == []
    pedido = bifrost.publicado[0].sobre["pedido"]
    assert pedido == {"respuesta": respuesta, "persona": None, "vueltas": [], "reescribir": None, "resumen": None}


@pytest.mark.parametrize("entrega", [1, 2, 3])
def test_sin_el_gateway_se_espera_mientras_queden_entregas(entrega):
    resultado, _, bifrost = atender(Mensaje(clasificado(), entregas=entrega),
                                    gateway=Gateway(FuenteNoDisponible("el gateway respondió 503")))

    assert resultado is Resultado.EN_ESPERA and bifrost.publicado == []


def test_sin_el_gateway_en_la_ultima_entrega_se_pide_el_texto_de_no_disponible():
    resultado, _, bifrost = atender(Mensaje(clasificado(), entregas=4),
                                    gateway=Gateway(FuenteNoDisponible("el gateway respondió 503")))

    assert resultado is Resultado.CONFIRMADO
    assert bifrost.publicado[0].sobre["pedido"]["respuesta"] == "no_disponible"


def test_si_el_bot_ya_no_existe_no_hay_a_quien_responder():
    resultado, _, bifrost = atender(Mensaje(clasificado()), gateway=Gateway(NoEncontrado("rules")))

    assert resultado is Resultado.DESCARTADO and bifrost.publicado == []


def test_si_bifrost_no_lo_guarda_se_espera():
    resultado, _, _ = atender(Mensaje(clasificado()), bifrost=Bifrost(rechaza=True))

    assert resultado is Resultado.EN_ESPERA


@pytest.mark.parametrize("datos, subject", [
    (b"no es json", None),
    (clasificado(schemaVersion=1), None),
    (clasificado(contact={"uuid": str(CONTACTO), "name": "Ana"}), None),
    (clasificado(), f"motor.clasificado.7.{uuid4()}.{CONTACTO}"),
    (clasificado(), f"motor.pedido.7.{BOT}.{CONTACTO}"),
], ids=["no JSON", "otra versión", "con el nombre", "otro bot", "otro paso"])
def test_lo_que_nunca_se_podra_coordinar_se_descarta_sin_preguntar_nada(datos, subject):
    resultado, gateway, bifrost = atender(Mensaje(datos, subject=subject))

    assert resultado is Resultado.DESCARTADO and gateway.preguntas == [] and bifrost.publicado == []


def test_el_log_no_lleva_el_texto_ni_el_numero_ni_el_id_del_mensaje(caplog):
    caplog.set_level("DEBUG")

    atender(Mensaje(clasificado()))
    atender(Mensaje(clasificado()), gateway=Gateway(FuenteNoDisponible("el gateway respondió 503")))

    assert caplog.records
    for prohibido in ("zapatos", "12025550143", "wamid", "Be kind"):
        assert prohibido not in caplog.text


def test_al_arrancar_lee_la_clave_del_gateway_de_su_fichero(tmp_path):
    fichero = tmp_path / "gateway-public.json"
    fichero.write_text(json.dumps({"keys": [public_part(new_key())]}), encoding="utf-8")
    settings = CoordinatorSettings(nats_url="tls://bifrost:4222", andvari_addr="https://andvari:8200",
                                   engine_role_id="rol", engine_secret_id_file="/run/andvari/secret-id",
                                   gateway_url="http://hive-api:8000", gateway_audience="https://bots.example.com",
                                   gateway_public_keys_file=str(fichero))

    async def arrancar():
        atender_, cerrar = await preparar(settings, 4, store=object())
        try:
            return atender_._fuente._worker, atender_._fuente._url, atender_._max_deliver
        finally:
            await cerrar()

    assert asyncio.run(arrancar()) == ("coordinator", "http://hive-api:8000", 4)


# --- Los avisos de configuración (ADR-0035 del gateway) ----------------------------------------------------------

class Fuente:
    def __init__(self):
        self.olvidados = []

    def configuracion_cambiada(self, client_id, bot_id, version):
        self.olvidados.append((client_id, bot_id, version))


def aviso(**cambios) -> bytes:
    datos = {"schemaVersion": 2, "uuid": str(uuid4()), "clientId": str(CLIENTE), "botId": str(BOT), "version": 4,
             "configuredAt": "2026-10-30T16:00:00+00:00", **cambios}
    return json.dumps(datos).encode()


def test_un_aviso_de_configuracion_hace_olvidar_la_version_anterior():
    fuente = Fuente()

    asyncio.run(al_configurar(fuente)(SimpleNamespace(data=aviso())))

    assert fuente.olvidados == [(CLIENTE, BOT, 4)]


@pytest.mark.parametrize("roto", [b"no es json", aviso(version=0), aviso(version="4"), aviso(schemaVersion=1),
                                  aviso(otro="campo"), aviso(botId="no-es-un-uuid")],
                         ids=["no JSON", "version 0", "version de texto", "otro esquema", "un campo de más",
                              "bot que no es UUID"])
def test_un_aviso_roto_se_ignora_y_no_dice_que_traia(roto, caplog):
    fuente = Fuente()

    asyncio.run(al_configurar(fuente)(SimpleNamespace(data=roto)))

    assert fuente.olvidados == []
    assert "gateway.bot.configured se ignora" in caplog.text and "no-es-un-uuid" not in caplog.text


class Conexion:
    def __init__(self):
        self.sujetos, self.bajas = [], 0

    def jetstream(self):
        return object()

    async def subscribe(self, sujeto, cb):
        self.sujetos.append(sujeto)
        conexion = self

        class Suscripcion:
            async def unsubscribe(self):
                conexion.bajas += 1
        return Suscripcion()


def test_al_arrancar_escucha_los_avisos_de_configuracion_y_al_cerrar_deja_de_hacerlo(tmp_path):
    fichero = tmp_path / "gateway-public.json"
    fichero.write_text(json.dumps({"keys": [public_part(new_key())]}), encoding="utf-8")
    settings = CoordinatorSettings(nats_url="tls://bifrost:4222", andvari_addr="https://andvari:8200",
                                   engine_role_id="rol", engine_secret_id_file="/run/andvari/secret-id",
                                   gateway_url="http://hive-api:8000", gateway_audience="https://bots.example.com",
                                   gateway_public_keys_file=str(fichero))
    conexion = Conexion()

    async def arrancar():
        _, cerrar = await preparar(settings, 4, store=object(), conexion=conexion)
        await cerrar()

    asyncio.run(arrancar())

    assert (conexion.sujetos, conexion.bajas) == (["gateway.bot.configured"], 1)
