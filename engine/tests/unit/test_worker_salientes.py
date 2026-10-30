# -*- coding: utf-8 -*-
"""
    El notificador con cada pedido de envío del cliente (ADR-0031 del gateway, HIG-84): lee un PedidoDeEnvio de
    SALIENTES, envía el texto o la plantilla desde el número del bot al del contacto, no envía dos veces el mismo
    pedido, y publica lo enviado en RESPUESTAS con su origen. El gateway, Meta, el bucket `enviados` y Bifröst los
    sustituyen dobles.
"""
import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from shared import bus
from shared.contract import FailedSend, SentReply
from src.domain.ports.envio import (
    CanalDeMeta, Enviados, EnvioFallido, EnvioRechazado, RegistroNoDisponible, TokenRechazado, TokensDelBot,
)
from src.domain.ports.fuente_del_bot import FuenteNoDisponible, NoEncontrado
from src.workers.notifier import Salientes
from src.workers.runtime import Resultado

BOT, CLIENTE, CONTACTO, PEDIDO = uuid4(), uuid4(), uuid4(), uuid4()
NUMERO, CUENTA = "12025550143", "106540352242922"


def pedido(**cambios) -> dict:
    return {"schemaVersion": 2, "uuid": str(PEDIDO), "botId": str(BOT), "clientId": str(CLIENTE),
            "contactId": str(CONTACTO), "channel": "whatsapp", "kind": "text", "sentBy": "key abc",
            "requestedAt": "2026-10-29T19:00:00+00:00", "to": NUMERO, "phoneId": CUENTA,
            "text": "Su cita es mañana a las 10", **cambios}


PLANTILLA = {"kind": "template", "text": None,
             "template": {"name": "cita", "language": "es", "category": "UTILITY", "parameters": ["mañana"]}}


class Mensaje:
    def __init__(self, datos, entregas=1):
        datos = {nombre: valor for nombre, valor in datos.items() if valor is not None}
        self.data = json.dumps(datos).encode()
        self.subject = f"gateway.outbound.3.{BOT}.{CONTACTO}"
        self.headers = {}
        self.metadata = SimpleNamespace(num_delivered=entregas)


class Bifrost:
    def __init__(self, rechaza=False):
        self.publicado, self.rechaza = [], rechaza

    async def publish(self, subject, payload, message_id, headers=None):
        if self.rechaza:
            raise bus.BusUnavailable("TimeoutError")
        self.publicado.append(SimpleNamespace(subject=subject, evento=json.loads(payload), message_id=message_id))
        return bus.Published(stream="RESPUESTAS", sequence=len(self.publicado), duplicate=False)

    async def close(self):
        pass


class Gateway(TokensDelBot):
    def __init__(self, *tokens):
        self.olvidados, self._tokens = [], list(tokens) or ["EAAG-wa"]

    async def token_de_whatsapp(self, client_id, bot_id):
        token = self._tokens.pop(0) if len(self._tokens) > 1 else self._tokens[0]
        if isinstance(token, Exception):
            raise token
        return token

    def olvidar(self, bot_id):
        self.olvidados.append(bot_id)


class Meta(CanalDeMeta):
    def __init__(self, *respuestas):
        self.textos, self.plantillas = [], []
        self._respuestas = list(respuestas) or ["wamid.ENVIADO"]

    def _siguiente(self):
        respuesta = self._respuestas.pop(0) if len(self._respuestas) > 1 else self._respuestas[0]
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta

    async def enviar_texto(self, token, cuenta, destinatario, texto):
        self.textos.append((token, cuenta, destinatario, texto))
        return self._siguiente()

    async def enviar_plantilla(self, token, cuenta, destinatario, nombre, idioma, valores):
        self.plantillas.append((token, cuenta, destinatario, nombre, idioma, valores))
        return self._siguiente()

    async def marcar_leido(self, token, cuenta, message_id, escribiendo):
        raise AssertionError("un pedido de envío no marca nada como leído")


class Registro(Enviados):
    def __init__(self, falla=False):
        self.apuntados, self._falla = {}, falla

    async def enviado(self, bot_id, message_id):
        if self._falla:
            raise RegistroNoDisponible("TimeoutError")
        return self.apuntados.get((bot_id, message_id))

    async def apuntar(self, bot_id, message_id, envio):
        self.apuntados[(bot_id, message_id)] = envio


def atender(datos, meta=None, gateway=None, registro=None, bifrost=None, entregas=1, max_deliver=5):
    meta, gateway, registro, bifrost = meta or Meta(), gateway or Gateway(), registro or Registro(), bifrost or Bifrost()
    salientes = Salientes(gateway, meta, registro, bifrost, max_deliver)
    resultado = asyncio.run(salientes(Mensaje(datos, entregas)))
    return resultado, SimpleNamespace(meta=meta, gateway=gateway, registro=registro, bifrost=bifrost)


def test_un_texto_sale_desde_el_numero_del_bot_al_del_contacto_y_va_al_historial_como_del_cliente():
    resultado, piezas = atender(pedido())

    assert resultado is Resultado.CONFIRMADO
    assert piezas.meta.textos == [("EAAG-wa", CUENTA, NUMERO, "Su cita es mañana a las 10")]
    (publicado,) = piezas.bifrost.publicado
    assert publicado.subject == f"engine.reply.sent.{BOT}"
    evento = SentReply.model_validate(publicado.evento)
    assert (evento.origin, evento.sentBy, evento.requestId, evento.inReplyTo) == ("client", "key abc", PEDIDO, None)
    assert (evento.contactId, evento.messageId, evento.text) == (CONTACTO, "wamid.ENVIADO", "Su cita es mañana a las 10")


def test_una_plantilla_sale_con_su_nombre_su_idioma_y_sus_valores():
    resultado, piezas = atender(pedido(**PLANTILLA))

    assert resultado is Resultado.CONFIRMADO
    assert piezas.meta.plantillas == [("EAAG-wa", CUENTA, NUMERO, "cita", "es", ["mañana"])]
    assert piezas.bifrost.publicado[0].evento["text"] == "[cita] | mañana"


def test_el_mismo_pedido_entregado_otra_vez_no_sale_dos_veces():
    meta, registro = Meta(), Registro()

    primero, _ = atender(pedido(), meta=meta, registro=registro)
    otra_vez, piezas = atender(pedido(), meta=meta, registro=registro)

    assert (primero, otra_vez) == (Resultado.CONFIRMADO, Resultado.CONFIRMADO)
    assert len(meta.textos) == 1
    # Se publica otra vez lo que ya salió: si lo que faltó fue publicar, el historial lo recibe.
    assert piezas.bifrost.publicado[0].evento["messageId"] == "wamid.ENVIADO"


@pytest.mark.parametrize("codigo", [131047, 132015, 132016],
                         ids=["la ventana se cerró", "plantilla en pausa", "plantilla desactivada"])
def test_lo_que_meta_no_aceptará_nunca_se_descarta_y_se_avisa_con_su_código(codigo):
    resultado, piezas = atender(pedido(), meta=Meta(EnvioRechazado(f"Meta {codigo}")))

    assert resultado is Resultado.DESCARTADO
    (aviso,) = piezas.bifrost.publicado
    assert (aviso.subject, aviso.message_id) == (f"engine.reply.failed.{BOT}", f"{BOT}:fallido:{PEDIDO}")
    fallo = FailedSend.model_validate(aviso.evento)
    assert (fallo.requestId, fallo.code, fallo.reason, fallo.contactId) == (PEDIDO, codigo, "rejected", CONTACTO)
    # Ni el texto ni el número viajan en el aviso.
    assert NUMERO not in json.dumps(aviso.evento) and "cita" not in json.dumps(aviso.evento)


def test_un_rechazo_sin_código_de_meta_se_avisa_sin_código():
    (aviso,) = atender(pedido(), meta=Meta(EnvioRechazado("aceptado sin id")))[1].bifrost.publicado

    assert (aviso.evento["code"], aviso.evento["reason"]) == (None, "rejected")


def test_si_bifröst_no_toma_el_aviso_el_pedido_espera():
    resultado, _ = atender(pedido(), meta=Meta(EnvioRechazado("Meta 131047")), bifrost=Bifrost(rechaza=True))

    assert resultado is Resultado.EN_ESPERA


@pytest.mark.parametrize("fallo", [EnvioFallido("Meta 130429"), FuenteNoDisponible("el gateway no responde")],
                         ids=["Meta limita la tasa", "el gateway no responde"])
def test_lo_que_puede_ir_más_tarde_queda_en_espera_y_en_la_última_entrega_se_descarta(fallo):
    meta = Meta(fallo) if isinstance(fallo, EnvioFallido) else None
    gateway = Gateway(fallo) if isinstance(fallo, FuenteNoDisponible) else None

    assert atender(pedido(), meta=meta, gateway=gateway, entregas=1)[0] is Resultado.EN_ESPERA
    ultima, piezas = atender(pedido(), meta=meta, gateway=gateway, entregas=5)
    assert ultima is Resultado.DESCARTADO
    (aviso,) = piezas.bifrost.publicado
    assert (aviso.evento["reason"], aviso.evento["code"]) == ("gave_up", None)


def test_en_la_última_entrega_un_aviso_que_bifröst_no_toma_queda_en_el_log():
    resultado, _ = atender(pedido(), meta=Meta(EnvioFallido("Meta 130429")), bifrost=Bifrost(rechaza=True),
                           entregas=5)

    assert resultado is Resultado.DESCARTADO


def test_un_token_rechazado_se_pide_otra_vez_al_gateway():
    resultado, piezas = atender(pedido(), meta=Meta(TokenRechazado("Meta 190"), "wamid.ENVIADO"),
                                gateway=Gateway("EAAG-viejo", "EAAG-nuevo"))

    assert resultado is Resultado.CONFIRMADO
    assert piezas.gateway.olvidados == [BOT]
    assert [envio[0] for envio in piezas.meta.textos] == ["EAAG-viejo", "EAAG-nuevo"]


def test_un_bot_que_ya_no_existe_descarta_el_pedido_y_lo_avisa():
    resultado, piezas = atender(pedido(), gateway=Gateway(NoEncontrado("token")))

    assert resultado is Resultado.DESCARTADO
    assert [aviso.evento["reason"] for aviso in piezas.bifrost.publicado] == ["no_token"]


def test_sin_saber_qué_salió_ya_no_se_envía():
    resultado, piezas = atender(pedido(), registro=Registro(falla=True))

    assert resultado is Resultado.EN_ESPERA
    assert piezas.meta.textos == []


def test_si_bifröst_no_toma_lo_enviado_queda_en_espera_y_no_sale_otra_vez():
    meta, registro = Meta(), Registro()

    primero, _ = atender(pedido(), meta=meta, registro=registro, bifrost=Bifrost(rechaza=True))
    luego, piezas = atender(pedido(), meta=meta, registro=registro)

    assert (primero, luego) == (Resultado.EN_ESPERA, Resultado.CONFIRMADO)
    assert len(meta.textos) == 1 and len(piezas.bifrost.publicado) == 1


@pytest.mark.parametrize("datos", [
    {"schemaVersion": 1},
    pedido(channel="messenger"),
    pedido(to="no es un número"),
    pedido(kind="template", text=None),
    pedido(kind="text", text=None),
], ids=["otra versión", "otro canal", "un número que no lo es", "plantilla sin plantilla", "texto sin texto"])
def test_un_pedido_roto_se_descarta_sin_enviar(datos):
    resultado, piezas = atender(datos)

    assert resultado is Resultado.DESCARTADO
    assert piezas.meta.textos == [] and piezas.meta.plantillas == []
