# -*- coding: utf-8 -*-
"""
    El contrato interno del motor (docs/02-design/contracts/asyncapi-motor.yaml). El fichero es coherente: cada
    referencia existe y cada esquema es un JSON Schema válido. Y lo que construye el código cumple el contrato: el
    sobre que publica el clasificador, con cada veredicto posible, y sus cabeceras.
"""
import asyncio
import json
import pathlib
import re
from uuid import uuid4

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker

from src.domain.models.sobre import ESQUEMA, Clasificacion, Entidad, Persona, Vuelta
from src.domain.ports.clasificador_de_entrada import ClasificacionFallida
from src.workers.classifier import Clasificar
from src.workers.coordinator import Coordinar
from src.workers.responder import Responder
from src.domain.ports.redactor import Redaccion

CONTRATO = pathlib.Path(__file__).resolve().parents[2] / "docs" / "02-design" / "contracts" / "asyncapi-motor.yaml"
DOCUMENTO = yaml.safe_load(CONTRATO.read_text(encoding="utf-8"))
SCHEMAS = DOCUMENTO["components"]["schemas"]


def validador(nombre):
    return Draft202012Validator({"$ref": f"#/components/schemas/{nombre}", "components": DOCUMENTO["components"]},
                                format_checker=FormatChecker())


def referencias(nodo):
    if isinstance(nodo, dict):
        for clave, valor in nodo.items():
            if clave == "$ref":
                yield valor
            else:
                yield from referencias(valor)
    elif isinstance(nodo, list):
        for valor in nodo:
            yield from referencias(valor)


def resolver(puntero):
    assert puntero.startswith("#/"), puntero
    nodo = DOCUMENTO
    for parte in puntero[2:].split("/"):
        nodo = nodo[parte]
    return nodo


def test_es_la_version_2_del_sobre():
    assert (DOCUMENTO["asyncapi"], DOCUMENTO["info"]["version"]) == ("3.0.0", "2.0.0")
    assert SCHEMAS["Sobre"]["properties"]["schemaVersion"]["const"] == ESQUEMA


@pytest.mark.parametrize("puntero", sorted(set(referencias(DOCUMENTO))))
def test_cada_referencia_existe(puntero):
    assert resolver(puntero) is not None


@pytest.mark.parametrize("nombre", sorted(SCHEMAS))
def test_cada_esquema_es_valido(nombre):
    Draft202012Validator.check_schema(SCHEMAS[nombre])


def test_cada_parametro_de_un_canal_esta_declarado():
    for canal in DOCUMENTO["channels"].values():
        nombrados = set(re.findall(r"{([a-zA-Z]+)}", canal["address"]))
        assert nombrados == set(canal["parameters"])


class Mensaje:

    def __init__(self, evento, subject):
        self.data, self.subject = json.dumps(evento).encode(), subject
        self.headers = {"traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"}

        class Metadata:
            num_delivered = 4
        self.metadata = Metadata()


class Bifrost:

    def __init__(self):
        self.publicado = []

    async def publish(self, subject, payload, message_id, headers=None):
        self.publicado.append((subject, json.loads(payload), {**(headers or {}), "Nats-Msg-Id": message_id}))


class Modelo:

    def __init__(self, respuesta):
        self._respuesta = respuesta

    async def clasificar(self, _texto):
        if isinstance(self._respuesta, Exception):
            raise self._respuesta
        return self._respuesta


@pytest.mark.parametrize("respuesta", [
    Clasificacion(permitido=True, motivo="ninguno", etiqueta="pedido", confianza=0.8,
                  entidades=[Entidad(nombre="pedido", valor="A-1234")], modelo="gemma-4-26b-a4b"),
    Clasificacion(permitido=False, motivo="inyeccion", etiqueta="otro", confianza=0.99, modelo="gemma-4-26b-a4b"),
    Clasificacion.sin_veredicto("rechazo", modelo="gemma-4-26b-a4b"),
    ClasificacionFallida("APITimeoutError"),
], ids=["permitido", "bloqueado", "rechazo", "error"])
@pytest.mark.parametrize("canal", ["whatsapp", "messenger"])
def test_el_sobre_del_clasificador_cumple_el_contrato(respuesta, canal):
    bot, contacto = uuid4(), uuid4()
    contenido = ({"contacts": [{"wa_id": "12025550143"}], "messages": [{"type": "text", "text": {"body": "hola"}}]}
                 if canal == "whatsapp" else {"sender": {"id": "6789"}, "message": {"text": "hola"}})
    quien = {"waId": "12025550143"} if canal == "whatsapp" else {"fbId": "6789"}
    evento = {"schemaVersion": 2, "botId": str(bot), "clientId": str(uuid4()), "messageId": "wamid.A",
              "message": contenido, "channel": canal, "receivedAt": "2026-10-20T09:00:00+00:00",
              "orderKey": f"{bot}.{contacto}", "contact": {"uuid": str(contacto), "name": "Ana", **quien}}
    bifrost = Bifrost()

    asyncio.run(Clasificar(Modelo(respuesta), bifrost, 4)(Mensaje(evento, f"gateway.message.3.{bot}.{contacto}")))

    (subject, sobre, cabeceras), = bifrost.publicado
    validador("MensajeClasificado").validate(sobre)
    validador("Cabeceras").validate(cabeceras)
    assert re.fullmatch(DOCUMENTO["channels"]["clasificado"]["address"]
                        .replace(".", r"\.").replace("{particion}", "[0-9]{1,2}")
                        .replace("{botId}", "[0-9a-f-]{36}").replace("{contactId}", "[0-9a-f-]{36}"), subject)


def test_un_sobre_con_el_nombre_del_contacto_no_cumple_el_contrato():
    sobre = {"uuid": str(uuid4()), "name": "Ana"}

    assert not validador("ContactoDelCanal").is_valid(sobre)


class Gateway:

    async def persona(self, _client_id, _bot_id):
        return Persona(nombre="Mine", rol="Sales Assistant", tono="helpful", descripcion="", reglas=["Be kind"])

    async def vueltas(self, _client_id, _bot_id, _contact_id, _sin, _hasta):
        return [Vuelta(de="persona", texto="hola", enviadaEn="2026-10-20T08:00:00+00:00"),
                Vuelta(de="bot", texto="", adjuntos=["image"], enviadaEn="2026-10-20T08:00:05+00:00")]


@pytest.mark.parametrize("clasificacion", [
    Clasificacion(permitido=True, motivo="ninguno", etiqueta="pedido", confianza=0.8, modelo="gemma-4-26b-a4b"),
    Clasificacion(permitido=False, motivo="abuso", etiqueta="otro", confianza=0.99, modelo="gemma-4-26b-a4b"),
    Clasificacion.sin_veredicto("sin_texto"),
], ids=["con modelo", "no_puedo", "solo_texto"])
def test_el_pedido_del_coordinador_cumple_el_contrato(clasificacion):
    bot, contacto = uuid4(), uuid4()
    clasificado = {"schemaVersion": 2, "botId": str(bot), "clientId": str(uuid4()), "messageId": "wamid.A",
                   "channel": "whatsapp", "receivedAt": "2026-10-20T09:00:00+00:00", "orderKey": f"{bot}.{contacto}",
                   "contact": {"uuid": str(contacto), "waId": "12025550143"},
                   "entrada": {"texto": "hola", "adjuntos": []}, "clasificacion": clasificacion.model_dump()}
    bifrost = Bifrost()
    mensaje = Mensaje(clasificado, f"motor.clasificado.3.{bot}.{contacto}")

    asyncio.run(Coordinar(Gateway(), bifrost, 4)(mensaje))

    (subject, sobre, cabeceras), = bifrost.publicado
    validador("MensajePedido").validate(sobre)
    validador("Cabeceras").validate(cabeceras)
    assert subject == f"motor.pedido.3.{bot}.{contacto}"


class ModeloQueResponde:

    def __init__(self, redaccion):
        self._redaccion = redaccion

    async def redactar(self, _persona, _vueltas, _texto):
        return self._redaccion


@pytest.mark.parametrize("respuesta, redaccion", [
    ("modelo", Redaccion(texto="Sí, tenemos zapatos rojos.", modelo="gemma-4-26b-a4b")),
    ("modelo", Redaccion(texto=None, modelo="gemma-4-26b-a4b")),
    ("solo_texto", None),
], ids=["de modelo", "rechazo", "texto fijo"])
def test_la_respuesta_cumple_el_contrato(respuesta, redaccion):
    bot, contacto = uuid4(), uuid4()
    pedido = {"schemaVersion": 2, "botId": str(bot), "clientId": str(uuid4()), "messageId": "wamid.A",
              "channel": "whatsapp", "receivedAt": "2026-10-20T09:00:00+00:00", "orderKey": f"{bot}.{contacto}",
              "contact": {"uuid": str(contacto), "waId": "12025550143"},
              "entrada": {"texto": "hola", "adjuntos": []},
              "clasificacion": Clasificacion(permitido=True, motivo="ninguno", etiqueta="saludo", confianza=0.9,
                                             modelo="gemma-4-26b-a4b").model_dump(),
              "pedido": {"respuesta": respuesta,
                         "persona": ({"nombre": "Mine", "rol": "Sales", "tono": "helpful", "descripcion": "",
                                      "reglas": []} if respuesta == "modelo" else None),
                         "vueltas": []}}
    bifrost = Bifrost()

    asyncio.run(Responder(ModeloQueResponde(redaccion), bifrost, 4)(Mensaje(pedido, f"motor.pedido.3.{bot}.{contacto}")))

    (subject, sobre, cabeceras), = bifrost.publicado
    validador("MensajeRespondido").validate(sobre)
    validador("Cabeceras").validate(cabeceras)
    assert subject == f"motor.respuesta.3.{bot}.{contacto}"


# --- Lo que el notificador devuelve al gateway, según el contrato v2 de este ----------------------------------------

V2 = yaml.safe_load((pathlib.Path(__file__).resolve().parents[3] / "docs" / "02-design" / "contracts"
                     / "asyncapi-v2.yaml").read_text(encoding="utf-8"))


def test_lo_que_publica_el_notificador_cumple_el_contrato_v2_del_gateway():
    from src.workers.notifier import Notificar  # pylint: disable=import-outside-toplevel

    class Tokens:
        async def token_de_whatsapp(self, _cliente, _bot):
            return "EAAG-wa"

        def olvidar(self, _bot):
            pass

    class Meta:
        async def enviar_texto(self, _token, _cuenta, _destinatario, _texto):
            return "wamid.ENVIADO"

    class Registro:
        async def enviado(self, _bot, _mensaje):
            return None

        async def apuntar(self, _bot, _mensaje, _envio):
            pass

    bot, contacto = uuid4(), uuid4()
    respondido = {"schemaVersion": 2, "botId": str(bot), "clientId": str(uuid4()), "messageId": "wamid.A",
                  "channel": "whatsapp", "receivedAt": "2026-10-20T09:00:00+00:00", "orderKey": f"{bot}.{contacto}",
                  "contact": {"uuid": str(contacto), "waId": "12025550143"}, "cuentaDelBot": "106540352242922",
                  "entrada": {"texto": "hola", "adjuntos": []},
                  "clasificacion": Clasificacion.sin_veredicto("sin_texto").model_dump(),
                  "respuesta": {"texto": "Por ahora solo puedo leer mensajes de texto.", "fuente": "fijo",
                                "modelo": None}}
    validador("MensajeRespondido").validate(respondido)
    bifrost = Bifrost()

    asyncio.run(Notificar(Tokens(), Meta(), Registro(), bifrost, 5)(
        Mensaje(respondido, f"motor.respuesta.3.{bot}.{contacto}")))

    (subject, evento, cabeceras), = bifrost.publicado
    assert subject == f"engine.reply.sent.{bot}"
    Draft202012Validator({"$ref": "#/components/schemas/RespuestaEnviada", "components": V2["components"]},
                         format_checker=FormatChecker()).validate(evento)
    assert cabeceras["Nats-Msg-Id"] == f"{bot}:wamid.ENVIADO"
