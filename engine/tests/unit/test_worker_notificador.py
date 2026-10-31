# -*- coding: utf-8 -*-
"""
    El worker notificador con cada respuesta (E6): qué envía y a quién, qué publica para el historial del gateway,
    que no envía dos veces lo mismo, y cuándo confirma, espera o descarta. El gateway, Meta, el bucket `enviados` y
    Bifröst los sustituyen dobles; las piezas de verdad se prueban en sus propios ficheros y contra un NATS en
    tests/integration.
"""
import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from shared import bus
from shared.contract import SentReply
from src.domain.models.sobre import Clasificacion
from src.domain.ports.envio import (
    CanalDeMeta, Envio, Enviados, EnvioFallido, EnvioRechazado, RegistroNoDisponible, TokenRechazado, TokensDelBot,
)
from src.domain.ports.fuente_del_bot import FuenteNoDisponible, NoEncontrado
from src.workers.notifier import Notificar
from src.workers.runtime import Resultado

BOT, CLIENTE, CONTACTO = uuid4(), uuid4(), uuid4()
TRAZA = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
NUMERO, CUENTA = "12025550143", "106540352242922"
ANTES = datetime(2026, 10, 20, 9, 0, 3, tzinfo=timezone.utc)


def respondido(**cambios) -> dict:
    return {"schemaVersion": 2, "botId": str(BOT), "clientId": str(CLIENTE), "messageId": "wamid.A",
            "channel": "whatsapp", "receivedAt": "2026-10-20T09:00:00+00:00", "orderKey": f"{BOT}.{CONTACTO}",
            "contact": {"uuid": str(CONTACTO), "waId": NUMERO}, "cuentaDelBot": CUENTA,
            "entrada": {"texto": "¿tienen zapatos rojos?", "adjuntos": []},
            "clasificacion": Clasificacion(permitido=True, motivo="ninguno", etiqueta="pregunta", confianza=0.9,
                                           modelo="gemma-4-26b-a4b").model_dump(),
            "respuesta": {"texto": "Sí, tenemos zapatos rojos.", "fuente": "modelo", "modelo": "gemma-4-26b-a4b"},
            "evaluacion": {"veredicto": "aprobada", "criterio": "ninguno", "regla": 0, "modelo": "gemma-4-26b-a4b"},
            **cambios}


class Mensaje:

    def __init__(self, datos, entregas=1, subject=None):
        self.data = json.dumps(datos).encode()
        self.subject = subject or f"motor.aprobada.7.{BOT}.{CONTACTO}"
        self.headers = {"traceparent": TRAZA}
        self.metadata = SimpleNamespace(num_delivered=entregas)


class Bifrost:

    def __init__(self, rechaza=False):
        self.publicado, self.rechaza = [], rechaza

    async def publish(self, subject, payload, message_id, headers=None):
        if self.rechaza:
            raise bus.BusUnavailable("TimeoutError")
        self.publicado.append(SimpleNamespace(subject=subject, evento=json.loads(payload), message_id=message_id,
                                              headers=headers or {}))
        return bus.Published(stream="RESPUESTAS", sequence=len(self.publicado), duplicate=False)

    async def close(self):
        pass


class Gateway(TokensDelBot):

    def __init__(self, *tokens):
        self.pedidos, self.olvidados = [], []
        self._tokens = list(tokens) or ["EAAG-wa"]

    async def token_de_whatsapp(self, client_id, bot_id):
        self.pedidos.append((client_id, bot_id))
        token = self._tokens.pop(0) if len(self._tokens) > 1 else self._tokens[0]
        if isinstance(token, Exception):
            raise token
        return token

    def olvidar(self, bot_id):
        self.olvidados.append(bot_id)


class Meta(CanalDeMeta):

    def __init__(self, *respuestas):
        self.envios = []
        self._respuestas = list(respuestas) or ["wamid.ENVIADO"]

    async def enviar_texto(self, token, cuenta, destinatario, texto):
        self.envios.append((token, cuenta, destinatario, texto))
        respuesta = self._respuestas.pop(0) if len(self._respuestas) > 1 else self._respuestas[0]
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta

    async def enviar_plantilla(self, token, cuenta, destinatario, nombre, idioma, valores):
        raise AssertionError("aquí no se envían plantillas")

    async def marcar_leido(self, token, cuenta, message_id, escribiendo):
        raise AssertionError("el consumidor de las respuestas no marca nada como leído")


class Registro(Enviados):

    def __init__(self, previo=None, falla_al_leer=False, falla_al_apuntar=False):
        self.apuntados = {}
        if previo:
            self.apuntados[(BOT, "wamid.A")] = previo
        self._falla_al_leer, self._falla_al_apuntar = falla_al_leer, falla_al_apuntar

    async def enviado(self, bot_id, message_id):
        if self._falla_al_leer:
            raise RegistroNoDisponible("TimeoutError")
        return self.apuntados.get((bot_id, message_id))

    async def apuntar(self, bot_id, message_id, envio):
        if self._falla_al_apuntar:
            raise RegistroNoDisponible("TimeoutError")
        self.apuntados[(bot_id, message_id)] = envio


def atender(mensaje, gateway=None, meta=None, registro=None, bifrost=None, max_deliver=5):
    gateway, meta, registro, bifrost = gateway or Gateway(), meta or Meta(), registro or Registro(), \
        bifrost or Bifrost()
    resultado = asyncio.run(Notificar(gateway, meta, registro, bifrost, max_deliver)(mensaje))
    return resultado, SimpleNamespace(gateway=gateway, meta=meta, registro=registro, bifrost=bifrost)


def test_envia_el_texto_al_numero_de_la_persona_y_publica_lo_enviado_para_el_historial():
    resultado, lo = atender(Mensaje(respondido()))

    assert resultado is Resultado.CONFIRMADO
    assert lo.gateway.pedidos == [(CLIENTE, BOT)]
    assert lo.meta.envios == [("EAAG-wa", CUENTA, NUMERO, "Sí, tenemos zapatos rojos.")]
    (publicado,) = lo.bifrost.publicado
    assert publicado.subject == f"engine.reply.sent.{BOT}"
    assert publicado.message_id == f"{BOT}:wamid.ENVIADO"
    evento = SentReply.model_validate(publicado.evento)
    assert (evento.botId, evento.clientId, evento.contactId, evento.channel) == (BOT, CLIENTE, CONTACTO, "whatsapp")
    assert (evento.inReplyTo, evento.messageId, evento.text) == ("wamid.A", "wamid.ENVIADO",
                                                                 "Sí, tenemos zapatos rojos.")
    assert lo.registro.apuntados[(BOT, "wamid.A")].message_id == "wamid.ENVIADO"


def test_la_traza_sigue_el_hilo_del_mensaje():
    _, lo = atender(Mensaje(respondido()))

    assert lo.bifrost.publicado[0].headers["traceparent"].split("-")[1] == TRAZA.split("-")[1]


def test_lo_que_ya_salio_no_se_envia_otra_vez_y_se_publica_igual():
    previo = Envio(message_id="wamid.YA", sent_at=ANTES)

    resultado, lo = atender(Mensaje(respondido(), entregas=2), registro=Registro(previo))

    assert resultado is Resultado.CONFIRMADO and lo.meta.envios == [] and lo.gateway.pedidos == []
    evento = lo.bifrost.publicado[0].evento
    assert (evento["messageId"], evento["sentAt"]) == ("wamid.YA", "2026-10-20T09:00:03Z")
    assert lo.bifrost.publicado[0].message_id == f"{BOT}:wamid.YA"


def test_si_bifrost_falla_tras_enviar_la_siguiente_entrega_no_lo_envia_otra_vez():
    registro = Registro()
    primera, lo = atender(Mensaje(respondido()), registro=registro, bifrost=Bifrost(rechaza=True))
    segunda, otra = atender(Mensaje(respondido(), entregas=2), registro=registro)

    assert (primera, segunda) == (Resultado.EN_ESPERA, Resultado.CONFIRMADO)
    assert len(lo.meta.envios) == 1 and otra.meta.envios == []
    assert otra.bifrost.publicado[0].evento["messageId"] == "wamid.ENVIADO"


def test_sin_saber_si_ya_salio_no_se_envia():
    resultado, lo = atender(Mensaje(respondido()), registro=Registro(falla_al_leer=True))

    assert resultado is Resultado.EN_ESPERA and lo.meta.envios == []


def test_si_no_se_puede_apuntar_lo_enviado_se_publica_igual():
    resultado, lo = atender(Mensaje(respondido()), registro=Registro(falla_al_apuntar=True))

    assert resultado is Resultado.CONFIRMADO and len(lo.bifrost.publicado) == 1


def test_un_token_rechazado_se_pide_otra_vez_y_se_envia_con_el_nuevo():
    gateway = Gateway("EAAG-viejo", "EAAG-nuevo")
    meta = Meta(TokenRechazado("Meta 190"), "wamid.ENVIADO")

    resultado, lo = atender(Mensaje(respondido()), gateway=gateway, meta=meta)

    assert resultado is Resultado.CONFIRMADO
    assert [token for token, *_ in lo.meta.envios] == ["EAAG-viejo", "EAAG-nuevo"]
    assert lo.gateway.olvidados == [BOT]


@pytest.mark.parametrize("fallo", [
    {"meta": Meta(EnvioFallido("Meta 130429"))},
    {"meta": Meta(TokenRechazado("Meta 190"))},
    {"gateway": Gateway(FuenteNoDisponible("el gateway respondió 503"))},
], ids=["meta saturado", "el token sigue rechazado", "sin el gateway"])
def test_lo_que_puede_ir_mas_tarde_espera_y_en_la_ultima_entrega_se_descarta(fallo):
    espera, lo = atender(Mensaje(respondido(), entregas=4), **fallo)
    ultima, _ = atender(Mensaje(respondido(), entregas=5), **fallo)

    assert (espera, ultima) == (Resultado.EN_ESPERA, Resultado.DESCARTADO)
    assert lo.bifrost.publicado == []


@pytest.mark.parametrize("fallo", [
    {"meta": Meta(EnvioRechazado("Meta 131047"))},
    {"gateway": Gateway(NoEncontrado("token de WhatsApp"))},
], ids=["fuera de la ventana de 24 h", "el bot ya no tiene token"])
def test_lo_que_no_ira_nunca_se_descarta_a_la_primera(fallo):
    resultado, lo = atender(Mensaje(respondido()), **fallo)

    assert resultado is Resultado.DESCARTADO and lo.bifrost.publicado == []


@pytest.mark.parametrize("cambios", [
    {"channel": "messenger", "contact": {"uuid": str(CONTACTO), "fbId": "6789"}},
    {"cuentaDelBot": None},
    {"contact": {"uuid": str(CONTACTO)}},
], ids=["messenger, hasta el E10", "sin el número del bot", "sin el número de la persona"])
def test_lo_que_no_se_puede_enviar_por_whatsapp_se_descarta_sin_llamar_a_nadie(cambios):
    resultado, lo = atender(Mensaje(respondido(**cambios)))

    assert resultado is Resultado.DESCARTADO and lo.meta.envios == [] and lo.gateway.pedidos == []


def test_un_sobre_que_no_es_del_paso_de_respuesta_se_descarta():
    resultado, lo = atender(Mensaje(respondido(), subject=f"motor.pedido.7.{BOT}.{CONTACTO}"))

    assert resultado is Resultado.DESCARTADO and lo.meta.envios == []


def test_el_log_no_lleva_el_texto_ni_el_numero_ni_el_token_ni_los_ids(caplog):
    caplog.set_level("DEBUG")

    atender(Mensaje(respondido()))
    atender(Mensaje(respondido()), meta=Meta(EnvioRechazado("Meta 131026")))
    atender(Mensaje(respondido(), entregas=5), meta=Meta(EnvioFallido("Meta 2")))

    assert caplog.records
    for prohibido in ("zapatos", NUMERO, "EAAG", "wamid", CUENTA):
        assert prohibido not in caplog.text
