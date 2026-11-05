# -*- coding: utf-8 -*-
"""
    Consumo acotado y destinos fijos (T7, LLM10, T10; HIG-154).

    - **Lo que llega al modelo está acotado:** un mensaje enorme se recorta antes del clasificador, sin caracteres de
      control y normalizado (NFKC).
    - **El notificador solo habla con Graph,** y desde un número de cuenta que es solo dígitos: uno con una ruta o una
      consulta no forma un sobre, así que nunca llega a una URL.
"""
import asyncio
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.domain.models.sobre import Clasificacion, MensajeClasificado
from src.domain.ports.clasificador_de_entrada import ClasificadorDeEntrada
from src.infrastructure.adapters.sanitize import MAX_TEXTO, sanitizar_texto
from src.infrastructure.meta.whatsapp import GRAPH, WhatsAppCloud
from src.workers.classifier import Clasificar
from src.workers.settings import NotifierSettings
from tests.unit.test_worker_clasificador import Bifrost, Mensaje, evento, whatsapp


class Anotador(ClasificadorDeEntrada):
    """Hace de clasificador: apunta el texto que le llega."""

    def __init__(self):
        self.textos = []

    async def clasificar(self, texto, temas=()):
        self.textos.append(texto)
        return Clasificacion(permitido=True, motivo="ninguno", etiqueta="pregunta", confianza=0.9)


def clasificado(texto: str) -> str:
    anotador = Anotador()
    mensaje = Mensaje(evento(whatsapp({"type": "text", "text": {"body": texto}})))
    asyncio.run(Clasificar(anotador, Bifrost(), max_deliver=4)(mensaje))
    (llego,) = anotador.textos
    return llego


def test_un_mensaje_enorme_llega_al_modelo_recortado():
    llego = clasificado("zapatos " * 50_000)

    assert len(llego) == MAX_TEXTO


def test_al_modelo_no_llegan_caracteres_de_control():
    llego = clasificado("ho\x00la\x1b[31m\x7f, ¿tienen\x08 zapatos?")

    assert llego == "hola[31m, ¿tienen zapatos?"
    assert not any(ord(caracter) < 32 and caracter not in "\n\t" for caracter in llego)


def test_lo_que_llega_al_modelo_esta_normalizado():
    """NFKC: la anchura completa y las ligaduras se leen como lo que parecen, también para el filtro de las marcas."""
    saneado = sanitizar_texto("ＡＢＣ ﬁn ①")

    assert saneado == "ABC fin 1"


def test_sin_texto_no_se_pregunta_al_modelo():
    anotador = Anotador()
    mensaje = Mensaje(evento(whatsapp({"type": "text", "text": {"body": " \x00\x01 "}})))

    asyncio.run(Clasificar(anotador, Bifrost(), max_deliver=4)(mensaje))

    assert anotador.textos == []


@pytest.mark.parametrize("cuenta", ["1/../../me", "123?fields=access_token", "123#x", "123 456", "+1234", "",
                                    "1" * 33, "１２３"],
                         ids=["una ruta", "una consulta", "un fragmento", "un espacio", "un signo", "vacía",
                              "demasiado larga", "dígitos de anchura completa"])
def test_un_numero_de_cuenta_que_no_es_solo_digitos_no_forma_un_sobre(cuenta):
    with pytest.raises(ValidationError):
        MensajeClasificado.model_validate(sobre_con(cuenta))


def test_un_numero_de_cuenta_de_digitos_si_forma_un_sobre():
    """El mismo sobre, con un número de verdad: lo que se rechaza arriba es el número y nada más."""
    sobre = MensajeClasificado.model_validate(sobre_con("106540352242922"))

    assert sobre.cuentaDelBot == "106540352242922"


def sobre_con(cuenta: str) -> dict:
    return {"schemaVersion": 2, "botId": str(uuid4()), "clientId": str(uuid4()), "messageId": "wamid.A",
            "channel": "whatsapp", "receivedAt": "2026-11-04T09:00:00+00:00", "orderKey": "a.b",
            "contact": {"uuid": str(uuid4())}, "cuentaDelBot": cuenta,
            "entrada": {"texto": "hola"}, "clasificacion": {"permitido": False, "motivo": "error"}}


def test_el_notificador_habla_con_graph_por_https_salvo_que_el_despliegue_diga_otra_cosa():
    """La URL se configura para la prueba de punta a punta, que pone una Meta falsa; en producción no se toca."""
    assert GRAPH == "https://graph.facebook.com"
    assert NotifierSettings.model_fields["graph_url"].default == GRAPH
    canal = WhatsAppCloud()
    assert canal._url == GRAPH  # pylint: disable=protected-access
