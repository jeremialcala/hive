# -*- coding: utf-8 -*-
"""
    El evaluador (HIG-146, ADR-0012) con dobles de Bifröst y del LLM: qué publica en cada caso (lo aprobado, el pedido
    de reescritura o un texto fijo), con qué `Nats-Msg-Id`, y que falla cerrado. Contra NATS de verdad, en
    tests/integration/test_evaluador_bifrost.py.
"""
import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from shared import bus
from src.domain.models.sobre import Clasificacion, MensajeAprobado, MensajePedido, Persona
from src.domain.ports.evaluador import Evaluador, EvaluacionFallida, Juicio
from src.domain.textos_fijos import TEXTOS_FIJOS
from src.workers.evaluator import Evaluar, preparar
from src.workers.runtime import Resultado
from src.workers.settings import EvaluatorSettings

BOT, CLIENTE, CONTACTO = uuid4(), uuid4(), uuid4()
TRAZA = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
PERSONA = Persona(nombre="Mine", rol="Asistente de ventas", tono="cercano", reglas=["No damos descuentos."])
VUELTAS = [{"de": "persona", "texto": "hola", "adjuntos": [], "enviadaEn": "2026-10-31T08:00:00+00:00"}]
PROPUESTA = "¡Te dejo un 10 % de descuento!"


def respondido(texto=PROPUESTA, fuente="modelo", reescribir=None, con_pedido=True) -> dict:
    pedido = {"respuesta": "modelo", "persona": PERSONA.model_dump(), "vueltas": VUELTAS, "reescribir": reescribir}
    return {"schemaVersion": 2, "botId": str(BOT), "clientId": str(CLIENTE), "messageId": "wamid.A",
            "channel": "whatsapp", "receivedAt": "2026-10-31T09:00:00+00:00", "orderKey": f"{BOT}.{CONTACTO}",
            "contact": {"uuid": str(CONTACTO), "waId": "12025550143"},
            "entrada": {"texto": "¿me haces un descuento?", "adjuntos": []},
            "clasificacion": Clasificacion(permitido=True, motivo="ninguno", etiqueta="pedido", confianza=0.9,
                                           modelo="gemma-4-26b-a4b").model_dump(),
            "respuesta": {"texto": texto, "fuente": fuente,
                          "modelo": "gemma-4-26b-a4b" if fuente == "modelo" else None},
            "pedido": pedido if con_pedido and fuente == "modelo" else None}


REESCRITURA = {"texto": PROPUESTA, "motivo": "Promete un descuento.", "criterio": "regla", "regla": 1}


class Mensaje:

    def __init__(self, datos, subject=None, entregas=1):
        self.data = datos if isinstance(datos, bytes) else json.dumps(datos).encode()
        self.subject = subject or f"motor.respuesta.7.{BOT}.{CONTACTO}"
        self.headers = {"traceparent": TRAZA}
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


class Juez(Evaluador):

    def __init__(self, cumple=True, criterio="ninguno", regla=0, falla=False):
        self.juicios, self._falla = [], falla
        self._juicio = Juicio(cumple=cumple, criterio=criterio, regla=regla, motivo="Promete un descuento.",
                              modelo="gemma-4-26b-a4b")

    async def juzgar(self, persona, vueltas, texto, respuesta):
        self.juicios.append((persona, vueltas, texto, respuesta))
        if self._falla:
            raise EvaluacionFallida("TimeoutError")
        return self._juicio


def atender(mensaje, juez=None, bifrost=None, max_deliver=4):
    juez, bifrost = juez or Juez(), bifrost or Bifrost()
    return asyncio.run(Evaluar(juez, bifrost, max_deliver)(mensaje)), juez, bifrost


def aprobado(bifrost) -> MensajeAprobado:
    (publicado,) = bifrost.publicado
    assert publicado.subject == f"motor.aprobada.7.{BOT}.{CONTACTO}"
    assert publicado.message_id == f"aprobada:{BOT}:wamid.A"
    return MensajeAprobado.model_validate(publicado.sobre)


def test_una_respuesta_que_cumple_sale_igual_y_sin_la_persona_ni_las_vueltas():
    resultado, juez, bifrost = atender(Mensaje(respondido(texto="Lo siento, no hacemos descuentos.")))

    sobre = aprobado(bifrost)
    assert resultado is Resultado.CONFIRMADO
    assert sobre.respuesta.texto == "Lo siento, no hacemos descuentos." and sobre.evaluacion.veredicto == "aprobada"
    assert "pedido" not in bifrost.publicado[0].sobre and "No damos descuentos" not in json.dumps(
        bifrost.publicado[0].sobre)
    # Juzga con la persona y las vueltas que usó el de respuesta.
    persona, vueltas, texto, _ = juez.juicios[0]
    assert persona == PERSONA and vueltas[0].texto == "hola" and texto == "¿me haces un descuento?"


def test_un_texto_fijo_pasa_sin_juzgarse():
    resultado, juez, bifrost = atender(Mensaje(respondido(texto=TEXTOS_FIJOS["no_puedo"], fuente="fijo")))

    assert resultado is Resultado.CONFIRMADO and juez.juicios == []
    assert aprobado(bifrost).evaluacion.veredicto == "fija"


def test_una_que_no_cumple_se_pide_reescribir_una_vez_con_el_motivo():
    resultado, _, bifrost = atender(Mensaje(respondido()), Juez(cumple=False, criterio="regla", regla=1))

    (publicado,) = bifrost.publicado
    assert resultado is Resultado.CONFIRMADO
    assert publicado.subject == f"motor.pedido.7.{BOT}.{CONTACTO}"
    assert publicado.message_id == f"pedido:{BOT}:wamid.A:reescrita"
    pedido = MensajePedido.model_validate(publicado.sobre).pedido
    assert pedido.persona == PERSONA and pedido.vueltas[0].texto == "hola"
    assert pedido.reescribir.model_dump() == REESCRITURA


def test_si_la_reescrita_cumple_sale_y_dice_que_fallo_la_primera():
    resultado, _, bifrost = atender(Mensaje(respondido(texto="No hacemos descuentos, ¡pero el envío es barato!",
                                                       reescribir=REESCRITURA)))

    sobre = aprobado(bifrost)
    assert resultado is Resultado.CONFIRMADO and sobre.respuesta.texto.startswith("No hacemos descuentos")
    assert (sobre.evaluacion.veredicto, sobre.evaluacion.criterio, sobre.evaluacion.regla) == ("reescrita", "regla", 1)


def test_si_tampoco_la_reescrita_cumple_sale_no_puedo():
    resultado, _, bifrost = atender(Mensaje(respondido(reescribir=REESCRITURA)), Juez(cumple=False, criterio="tono"))

    sobre = aprobado(bifrost)
    assert resultado is Resultado.CONFIRMADO
    assert sobre.respuesta.texto == TEXTOS_FIJOS["no_puedo"] and sobre.respuesta.fuente == "fijo"
    assert (sobre.evaluacion.veredicto, sobre.evaluacion.criterio) == ("cambiada", "tono")


def test_sin_juicio_queda_en_espera_y_no_sale_nada():
    resultado, _, bifrost = atender(Mensaje(respondido(), entregas=1), Juez(falla=True))

    assert resultado is Resultado.EN_ESPERA and bifrost.publicado == []


def test_sin_juicio_en_la_ultima_entrega_falla_cerrado_con_no_disponible():
    resultado, _, bifrost = atender(Mensaje(respondido(), entregas=4), Juez(falla=True), max_deliver=4)

    sobre = aprobado(bifrost)
    assert resultado is Resultado.CONFIRMADO and sobre.respuesta.texto == TEXTOS_FIJOS["no_disponible"]
    assert sobre.evaluacion.veredicto == "no_evaluada" and sobre.evaluacion.modelo is None


def test_una_respuesta_del_modelo_sin_pedido_no_sale_sin_evaluar():
    # La del de respuesta de antes del evaluador, durante el despliegue.
    resultado, juez, bifrost = atender(Mensaje(respondido(con_pedido=False)))

    sobre = aprobado(bifrost)
    assert resultado is Resultado.CONFIRMADO and juez.juicios == []
    assert sobre.respuesta.texto == TEXTOS_FIJOS["no_disponible"] and sobre.evaluacion.veredicto == "no_evaluada"


@pytest.mark.parametrize("juez", [Juez(), Juez(cumple=False)], ids=["aprobada", "reescritura"])
def test_si_bifrost_no_toma_lo_publicado_queda_en_espera(juez):
    resultado, _, _ = atender(Mensaje(respondido()), juez, Bifrost(rechaza=True))

    assert resultado is Resultado.EN_ESPERA


@pytest.mark.parametrize("mensaje", [
    Mensaje(b"no es json"),
    Mensaje({**respondido(), "schemaVersion": 1}),
    Mensaje(respondido(), subject=f"motor.respuesta.7.{uuid4()}.{CONTACTO}"),
    Mensaje(respondido(), subject=f"motor.aprobada.7.{BOT}.{CONTACTO}"),
], ids=["no JSON", "otra versión", "otro bot", "otro paso"])
def test_lo_que_no_es_una_respuesta_se_descarta(mensaje):
    resultado, juez, bifrost = atender(mensaje)

    assert resultado is Resultado.DESCARTADO and juez.juicios == [] and bifrost.publicado == []


def test_nada_del_texto_llega_al_log(caplog):
    atender(Mensaje(respondido()), Juez(cumple=False, criterio="regla", regla=1))
    atender(Mensaje(respondido(reescribir=REESCRITURA)), Juez(cumple=False))

    assert "descuento" not in caplog.text and "12025550143" not in caplog.text


def test_preparar_abre_el_llm_y_el_publicador(tmp_path, monkeypatch):
    clave = tmp_path / "llm-api-key"
    clave.write_text("clave\n", encoding="utf-8")
    monkeypatch.setenv("NATS_URL", "nats://bifrost:4222")
    monkeypatch.setenv("NATS_USER", "evaluator")
    monkeypatch.setenv("NATS_PASSWORD", "x")
    monkeypatch.setenv("LLM_URL", "http://hive-llm:8080")
    monkeypatch.setenv("LLM_API_KEY_FILE", str(clave))
    monkeypatch.setenv("LLM_CALENTAR_CADA", "0")

    async def abrir():
        evaluar, cerrar = await preparar(EvaluatorSettings(), 4)
        await cerrar()
        return evaluar

    assert isinstance(asyncio.run(abrir()), Evaluar)
