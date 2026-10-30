# -*- coding: utf-8 -*-
"""
    El worker de respuesta con cada pedido (E5): qué contesta, dónde y con qué cabeceras, cuándo confirma, cuándo
    espera y cuándo descarta. Modelo y Bifröst los sustituyen dobles; el de respuesta con Modelo se prueba en
    test_redactor_llm.py y el worker contra un NATS de verdad en tests/integration.
"""
import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from shared import bus
from src.domain.models.sobre import Clasificacion, MensajeRespondido, Persona, Vuelta
from src.domain.ports.redactor import Redaccion, RedaccionFallida, Redactor
from src.workers.responder import TEXTOS_FIJOS, Responder, preparar
from src.workers.runtime import Resultado
from src.workers.settings import ResponderSettings

BOT, CLIENTE, CONTACTO = uuid4(), uuid4(), uuid4()
TRAZA = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
PERSONA = Persona(nombre="Mine", rol="Sales Assistant", tono="helpful", reglas=["Be kind"])
VUELTAS = [Vuelta(de="persona", texto="hola", enviadaEn=datetime(2026, 10, 20, 8, tzinfo=timezone.utc))]
PERMITIDO = Clasificacion(permitido=True, motivo="ninguno", etiqueta="pregunta", confianza=0.9,
                          modelo="gemma-4-26b-a4b")


def pedido(respuesta="modelo", persona=PERSONA, vueltas=VUELTAS, **cambios) -> dict:
    return {"schemaVersion": 2, "botId": str(BOT), "clientId": str(CLIENTE), "messageId": "wamid.A",
            "channel": "whatsapp", "receivedAt": "2026-10-20T09:00:00+00:00", "orderKey": f"{BOT}.{CONTACTO}",
            "contact": {"uuid": str(CONTACTO), "waId": "12025550143"},
            "entrada": {"texto": "¿tienen zapatos rojos?", "adjuntos": []},
            "clasificacion": PERMITIDO.model_dump(),
            "pedido": {"respuesta": respuesta, "persona": persona.model_dump() if persona else None,
                       "vueltas": [v.model_dump(mode="json") for v in vueltas]}, **cambios}


class Mensaje:

    def __init__(self, datos, subject=None, entregas=1, headers=None):
        self.data = datos if isinstance(datos, bytes) else json.dumps(datos).encode()
        self.subject = subject or f"motor.pedido.7.{BOT}.{CONTACTO}"
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


class Modelo(Redactor):

    def __init__(self, respuesta=None):
        self.pedidos = []
        self._respuesta = respuesta or Redaccion(texto="Sí, tenemos zapatos rojos.", modelo="gemma-4-26b-a4b")

    async def redactar(self, persona, vueltas, texto):
        self.pedidos.append((persona, vueltas, texto))
        if isinstance(self._respuesta, Exception):
            raise self._respuesta
        return self._respuesta


def atender(mensaje, modelo=None, bifrost=None, max_deliver=4):
    modelo, bifrost = modelo or Modelo(), bifrost or Bifrost()
    return asyncio.run(Responder(modelo, bifrost, max_deliver)(mensaje)), modelo, bifrost


def test_con_modelo_escribe_la_respuesta_y_la_publica_para_el_notificador():
    resultado, modelo, bifrost = atender(Mensaje(pedido()))

    assert resultado is Resultado.CONFIRMADO
    assert modelo.pedidos == [(PERSONA, VUELTAS, "¿tienen zapatos rojos?")]
    (publicado,) = bifrost.publicado
    assert publicado.subject == f"motor.respuesta.7.{BOT}.{CONTACTO}"
    assert publicado.message_id == f"respuesta:{BOT}:wamid.A"
    sobre = MensajeRespondido.model_validate(publicado.sobre)
    assert sobre.respuesta.model_dump() == {"texto": "Sí, tenemos zapatos rojos.", "fuente": "modelo",
                                            "modelo": "gemma-4-26b-a4b"}
    assert sobre.contact.waId == "12025550143" and sobre.messageId == "wamid.A"


def test_el_sobre_para_el_notificador_no_lleva_la_persona_ni_las_vueltas():
    _, _, bifrost = atender(Mensaje(pedido()))

    sobre = bifrost.publicado[0].sobre
    assert "pedido" not in sobre and "Be kind" not in json.dumps(sobre)


def test_la_traza_sigue_el_hilo_del_mensaje():
    _, _, bifrost = atender(Mensaje(pedido()))

    assert bifrost.publicado[0].headers["traceparent"].split("-")[1] == TRAZA.split("-")[1]


@pytest.mark.parametrize("respuesta", ["no_puedo", "solo_texto", "no_disponible"])
def test_un_texto_fijo_se_contesta_sin_llamar_a_modelo(respuesta):
    resultado, modelo, bifrost = atender(Mensaje(pedido(respuesta, persona=None, vueltas=[])))

    assert resultado is Resultado.CONFIRMADO and modelo.pedidos == []
    assert bifrost.publicado[0].sobre["respuesta"] == {"texto": TEXTOS_FIJOS[respuesta], "fuente": "fijo",
                                                       "modelo": None}


def test_si_modelo_rechaza_se_contesta_el_texto_de_no_puedo():
    resultado, _, bifrost = atender(Mensaje(pedido()), modelo=Modelo(Redaccion(texto=None, modelo="gemma-4-26b-a4b")))

    assert resultado is Resultado.CONFIRMADO
    assert bifrost.publicado[0].sobre["respuesta"]["texto"] == TEXTOS_FIJOS["no_puedo"]


@pytest.mark.parametrize("entrega", [1, 2, 3])
def test_sin_respuesta_de_modelo_se_espera_mientras_queden_entregas(entrega):
    resultado, _, bifrost = atender(Mensaje(pedido(), entregas=entrega),
                                    modelo=Modelo(RedaccionFallida("APITimeoutError")))

    assert resultado is Resultado.EN_ESPERA and bifrost.publicado == []


def test_sin_respuesta_de_modelo_en_la_ultima_entrega_se_contesta_no_disponible():
    resultado, _, bifrost = atender(Mensaje(pedido(), entregas=4), modelo=Modelo(RedaccionFallida("APITimeoutError")))

    assert resultado is Resultado.CONFIRMADO
    assert bifrost.publicado[0].sobre["respuesta"]["texto"] == TEXTOS_FIJOS["no_disponible"]


def test_un_pedido_con_modelo_sin_persona_se_contesta_en_vez_de_callar():
    resultado, modelo, bifrost = atender(Mensaje(pedido(persona=None)))

    assert resultado is Resultado.CONFIRMADO and modelo.pedidos == []
    assert bifrost.publicado[0].sobre["respuesta"]["texto"] == TEXTOS_FIJOS["no_disponible"]


def test_si_bifrost_no_lo_guarda_se_espera():
    resultado, _, _ = atender(Mensaje(pedido()), bifrost=Bifrost(rechaza=True))

    assert resultado is Resultado.EN_ESPERA


@pytest.mark.parametrize("datos, subject", [
    (b"no es json", None),
    (pedido(schemaVersion=1), None),
    ({k: v for k, v in pedido().items() if k != "pedido"}, None),
    (pedido(), f"motor.pedido.7.{uuid4()}.{CONTACTO}"),
    (pedido(), f"motor.clasificado.7.{BOT}.{CONTACTO}"),
], ids=["no JSON", "otra versión", "sin pedido", "otro bot", "otro paso"])
def test_lo_que_nunca_se_podra_responder_se_descarta_sin_llamar_a_modelo(datos, subject):
    resultado, modelo, bifrost = atender(Mensaje(datos, subject=subject))

    assert resultado is Resultado.DESCARTADO and modelo.pedidos == [] and bifrost.publicado == []


def test_el_log_no_lleva_el_texto_ni_el_numero_ni_la_respuesta(caplog):
    caplog.set_level("DEBUG")

    atender(Mensaje(pedido()))
    atender(Mensaje(pedido()), modelo=Modelo(RedaccionFallida("APITimeoutError")))

    assert caplog.records
    for prohibido in ("zapatos", "12025550143", "wamid", "Be kind"):
        assert prohibido not in caplog.text


def ajustes(clave, **cambios):
    return ResponderSettings(nats_url="tls://bifrost:4222", llm_url="http://192.168.10.74:8080",
                             llm_api_key_file=str(clave), **cambios)


def test_al_arrancar_lee_la_clave_del_llm_y_lo_configura_como_dice_su_configuracion(tmp_path):
    clave = tmp_path / "llm-api-key"
    clave.write_text("clave-de-prueba\n", encoding="utf-8")

    async def arrancar():
        atender_, cerrar = await preparar(ajustes(clave), 4)
        redactor = atender_._redactor
        try:
            cliente = redactor.llm._cliente
            return cliente.modelo, cliente._clave, cliente._timeout, redactor._max_tokens
        finally:
            await cerrar()

    assert asyncio.run(arrancar()) == ("gemma-4-26b-a4b", "clave-de-prueba", 90.0, 1024)


def test_sin_la_clave_del_llm_no_arranca(tmp_path):
    with pytest.raises(SystemExit, match="clave del LLM"):
        asyncio.run(preparar(ajustes(tmp_path / "no-existe"), 4))


@pytest.mark.parametrize("cambio", [{"responder_max_tokens": 128}, {"responder_timeout": 120.0}])
def test_la_configuracion_no_deja_pasar_limites_que_romperian_el_ack_wait_o_la_respuesta(tmp_path, cambio):
    with pytest.raises(ValueError):
        ajustes(tmp_path / "llm-api-key", **cambio)
