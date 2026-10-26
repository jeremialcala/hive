# -*- coding: utf-8 -*-
"""
    El recibo de lectura y «escribiendo…» (ADR-0011, src/workers/presencia.py): qué se marca, cuándo no se pide, y que
    nada de esto deja un mensaje en espera. El gateway, Meta y el bucket `enviados` los sustituyen dobles.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.domain.models.sobre import Clasificacion
from src.domain.ports.envio import (
    CanalDeMeta, Envio, Enviados, EnvioFallido, EnvioRechazado, RegistroNoDisponible, TokenRechazado, TokensDelBot,
)
from src.domain.ports.fuente_del_bot import FuenteNoDisponible
from src.workers.presencia import Escribiendo, Leido
from src.workers.runtime import Resultado

BOT, CLIENTE, CONTACTO = uuid4(), uuid4(), uuid4()
NUMERO, CUENTA = "12025550143", "106540352242922"
LLEGADA = datetime(2026, 10, 21, 9, 0, 0, tzinfo=timezone.utc)
PERMITIDO = Clasificacion(permitido=True, motivo="ninguno", etiqueta="pregunta", confianza=0.9, modelo="gemma")
INYECCION = Clasificacion(permitido=False, motivo="inyeccion", etiqueta="otro", confianza=0.9, modelo="gemma")


def identificado(canal="whatsapp", cuenta=CUENTA) -> dict:
    """Un evento de MENSAJES como lo publica la ingesta del gateway (contrato v2): el `value` del cambio de Meta."""
    mensaje = {"messaging_product": "whatsapp", "contacts": [{"profile": {"name": "Ana"}, "wa_id": NUMERO}],
               "messages": [{"from": NUMERO, "id": "wamid.A", "timestamp": "1792573200", "type": "text",
                             "text": {"body": "¿tienen zapatos rojos?"}}]}
    if cuenta:
        mensaje["metadata"] = {"display_phone_number": "15550001111", "phone_number_id": cuenta}
    return {"schemaVersion": 2, "botId": str(BOT), "clientId": str(CLIENTE), "messageId": "wamid.A",
            "channel": canal, "receivedAt": LLEGADA.isoformat(), "orderKey": f"{BOT}.{CONTACTO}",
            "contact": {"uuid": str(CONTACTO), "waId": NUMERO, "name": "Ana"}, "message": mensaje}


def clasificado(clasificacion=PERMITIDO, **cambios) -> dict:
    return {"schemaVersion": 2, "botId": str(BOT), "clientId": str(CLIENTE), "messageId": "wamid.A",
            "channel": "whatsapp", "receivedAt": LLEGADA.isoformat(), "orderKey": f"{BOT}.{CONTACTO}",
            "contact": {"uuid": str(CONTACTO), "waId": NUMERO}, "cuentaDelBot": CUENTA,
            "entrada": {"texto": "¿tienen zapatos rojos?", "adjuntos": []},
            "clasificacion": clasificacion.model_dump(), **cambios}


class Mensaje:

    def __init__(self, datos, subject):
        self.data = json.dumps(datos).encode()
        self.subject = subject
        self.headers = {}
        self.metadata = SimpleNamespace(num_delivered=1)


def de_mensajes(datos):
    return Mensaje(datos, f"gateway.message.7.{BOT}.{CONTACTO}")


def del_motor(datos):
    return Mensaje(datos, f"motor.clasificado.7.{BOT}.{CONTACTO}")


class Gateway(TokensDelBot):

    def __init__(self, *tokens):
        self.olvidados = []
        self._tokens = list(tokens) or ["EAAG-wa"]

    async def token_de_whatsapp(self, client_id, bot_id):
        assert (client_id, bot_id) == (CLIENTE, BOT)
        token = self._tokens.pop(0) if len(self._tokens) > 1 else self._tokens[0]
        if isinstance(token, Exception):
            raise token
        return token

    def olvidar(self, bot_id):
        self.olvidados.append(bot_id)


class Meta(CanalDeMeta):

    def __init__(self, *respuestas):
        self.marcados = []
        self._respuestas = list(respuestas) or [None]

    async def enviar_texto(self, token, cuenta, destinatario, texto):
        raise AssertionError("aquí no se envía ninguna respuesta")

    async def marcar_leido(self, token, cuenta, message_id, escribiendo):
        self.marcados.append((token, cuenta, message_id, escribiendo))
        respuesta = self._respuestas.pop(0) if len(self._respuestas) > 1 else self._respuestas[0]
        if isinstance(respuesta, Exception):
            raise respuesta


class Registro(Enviados):

    def __init__(self, enviado=None, falla=False):
        self._enviado, self._falla = enviado, falla

    async def enviado(self, bot_id, message_id):
        if self._falla:
            raise RegistroNoDisponible("TimeoutError")
        return self._enviado

    async def apuntar(self, bot_id, message_id, envio):
        raise AssertionError("aquí no se apunta nada")


def leido(mensaje, gateway=None, meta=None):
    meta = meta or Meta()
    return asyncio.run(Leido(gateway or Gateway(), meta)(mensaje)), meta


def escribiendo(mensaje, meta=None, registro=None, segundos=3, gateway=None):
    meta = meta or Meta()
    ahora = LLEGADA + timedelta(seconds=segundos)
    return asyncio.run(Escribiendo(gateway or Gateway(), meta, registro or Registro(), lambda: ahora)(mensaje)), meta


# --- El recibo de lectura, en cuanto llega ---------------------------------------------------------------------------

def test_lo_que_llega_se_marca_como_leido_desde_el_numero_al_que_escribio():
    resultado, meta = leido(de_mensajes(identificado()))

    assert resultado is Resultado.CONFIRMADO
    assert meta.marcados == [("EAAG-wa", CUENTA, "wamid.A", False)]


def test_un_token_rechazado_se_pide_otra_vez_al_gateway():
    gateway = Gateway("EAAG-viejo", "EAAG-nuevo")

    resultado, meta = leido(de_mensajes(identificado()), gateway, Meta(TokenRechazado("Meta 190"), None))

    assert resultado is Resultado.CONFIRMADO
    assert [token for token, *_ in meta.marcados] == ["EAAG-viejo", "EAAG-nuevo"] and gateway.olvidados == [BOT]


@pytest.mark.parametrize("fallo", [EnvioFallido("Meta 131000"), EnvioRechazado("Meta 131009")],
                         ids=["se arreglaría esperando", "no irá nunca"])
def test_si_meta_no_lo_acepta_el_mensaje_se_confirma_igual_y_no_se_reintenta(fallo):
    assert leido(de_mensajes(identificado()), meta=Meta(fallo))[0] is Resultado.CONFIRMADO


def test_sin_el_gateway_se_confirma_igual():
    gateway = Gateway(FuenteNoDisponible("ReadTimeout"))

    resultado, meta = leido(de_mensajes(identificado()), gateway)

    assert resultado is Resultado.CONFIRMADO and meta.marcados == []


@pytest.mark.parametrize("datos", [identificado(canal="messenger"), identificado(cuenta=None)],
                         ids=["Messenger, en el E10", "sin el número del bot"])
def test_lo_que_no_es_de_whatsapp_o_no_dice_desde_donde_no_se_marca(datos):
    resultado, meta = leido(de_mensajes(datos))

    assert resultado is Resultado.CONFIRMADO and meta.marcados == []


def test_lo_que_no_es_un_mensaje_se_descarta():
    assert leido(Mensaje({"no": "es un evento"}, f"gateway.message.7.{BOT}.{CONTACTO}"))[0] is Resultado.DESCARTADO


# --- «Escribiendo…», cuando el mensaje sigue -------------------------------------------------------------------------

def test_un_mensaje_que_sigue_muestra_escribiendo():
    resultado, meta = escribiendo(del_motor(clasificado()))

    assert resultado is Resultado.CONFIRMADO
    assert meta.marcados == [("EAAG-wa", CUENTA, "wamid.A", True)]


def test_un_mensaje_que_no_sigue_no_lo_necesita():
    """Recibe enseguida su texto fijo."""
    resultado, meta = escribiendo(del_motor(clasificado(INYECCION)))

    assert resultado is Resultado.CONFIRMADO and meta.marcados == []


def test_pasada_la_vigencia_no_se_pide():
    assert escribiendo(del_motor(clasificado()), segundos=21)[1].marcados == []


def test_si_la_respuesta_ya_salio_no_se_pide():
    """Se quedaría encendido después de ella."""
    ya = Envio(message_id="wamid.ENVIADO", sent_at=LLEGADA + timedelta(seconds=2))

    assert escribiendo(del_motor(clasificado()), registro=Registro(enviado=ya))[1].marcados == []


def test_sin_saber_si_la_respuesta_salio_no_se_pide():
    resultado, meta = escribiendo(del_motor(clasificado()), registro=Registro(falla=True))

    assert resultado is Resultado.CONFIRMADO and meta.marcados == []


def test_sin_el_numero_del_bot_no_se_pide():
    assert escribiendo(del_motor(clasificado(cuentaDelBot=None)))[1].marcados == []


def test_si_meta_falla_el_mensaje_se_confirma_igual():
    assert escribiendo(del_motor(clasificado()), meta=Meta(EnvioFallido("ReadTimeout")))[0] is Resultado.CONFIRMADO


def test_un_sobre_que_no_lo_es_se_descarta():
    assert escribiendo(del_motor({"no": "es un sobre"}))[0] is Resultado.DESCARTADO


# --- La traza --------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("marcar", [lambda meta: leido(de_mensajes(identificado()), meta=meta),
                                    lambda meta: escribiendo(del_motor(clasificado()), meta=meta)],
                         ids=["recibo de lectura", "escribiendo"])
@pytest.mark.parametrize("meta", [Meta, lambda: Meta(EnvioFallido("Meta 131000"))], ids=["hecho", "fallido"])
def test_como_fue_llega_a_la_traza(marcar, meta, caplog):
    """La traza solo admite ciertos valores de `gateway.outcome`: uno que no esté entre ellos se queda fuera."""
    marcar(meta())

    assert "An attribute was kept out of a span" not in caplog.text
