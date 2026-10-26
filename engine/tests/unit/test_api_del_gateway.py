# -*- coding: utf-8 -*-
"""
    La API del gateway como la llama un worker (src/infrastructure/gateway/api.py), contra un servidor HTTP local que
    hace de gateway (ADR-0025 del gateway): en /oauth/token abre la aserción con la clave privada del gateway,
    comprueba la firma con la clave pública del worker y que la emite el cliente para la audiencia del gateway, y da
    un token de acceso; en el resto de rutas, solo acepta los tokens que dio, y contesta lo que el gateway contestaría.
"""
import asyncio
import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs
from uuid import uuid4

import pytest
from jwcrypto import jwe, jwk, jws

from shared.auth.client import new_signing_key, public_part
from shared.auth.gateway_keys import new_key, public_part as gateway_public
from shared.engine_key_store import KeyStoreUnavailable
from src.domain.ports.fuente_del_bot import FuenteNoDisponible, NoEncontrado
from src.infrastructure.gateway import ApiDelGateway
from src.infrastructure.gateway.api import clave_publica_del_gateway

AUDIENCIA = "https://bots.example.com"
CLIENTE, BOT, CONTACTO = uuid4(), uuid4(), uuid4()
GATEWAY = new_key()
# Cuándo llegó al webhook el mensaje que se responde: después de todo lo guardado, salvo que la prueba diga otra cosa.
LLEGO = datetime(2026, 10, 20, 9, 30, tzinfo=timezone.utc)
PERSONA = {"name": "Mine", "role": "Sales Assistant", "tone": "helpful", "description": "A test chatbot",
           "rules": ["Be kind", "Never promise delivery dates"]}


def mensaje(direccion, texto, id_meta, hora="2026-10-20T09:00:00+00:00", adjuntos=()):
    return {"direction": direccion, "messageId": id_meta, "channel": "whatsapp", "sentAt": hora, "intent": None,
            "inReplyTo": None, "text": texto, "attachments": list(adjuntos)}


class Gateway(BaseHTTPRequestHandler):
    """
        Da tokens de acceso a una aserción válida de la clave que diga `vale`, y contesta por ruta lo que diga
        `respuestas`, solo a un token que dio.
    """
    respuestas: dict = {}
    vale: dict = {}
    vistas: list = []
    emitidos: set = set()
    pedidos: int = 0

    def log_message(self, *_args):
        pass

    def do_POST(self):  # pylint: disable=invalid-name
        largo = int(self.headers.get("Content-Length", 0))
        form = {nombre: valores[0] for nombre, valores in parse_qs(self.rfile.read(largo).decode()).items()}
        try:
            assert self.path == "/oauth/token"
            assert form["grant_type"] == "client_credentials"
            assert form["client_assertion_type"] == "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
            sobre = jwe.JWE()
            sobre.deserialize(form["client_assertion"], key=jwk.JWK(**GATEWAY))
            firmado = jws.JWS()
            firmado.deserialize(sobre.payload.decode())
            firmado.verify(jwk.JWK(**public_part(Gateway.vale["clave"])))
            claims = json.loads(firmado.payload)
            assert (claims["iss"], claims["sub"], claims["aud"]) == (str(CLIENTE), str(CLIENTE), AUDIENCIA)
        except Exception:  # pylint: disable=broad-except
            return self._contestar(401, {"error": "invalid_client"})
        token = f"at-{uuid4()}"
        Gateway.emitidos.add(token)
        Gateway.pedidos += 1
        return self._contestar(200, {"access_token": token, "token_type": "Bearer", "expires_in": 600})

    def do_GET(self):  # pylint: disable=invalid-name
        token = self.headers.get("Authorization", "").removeprefix("Bearer ")
        if token not in Gateway.emitidos:
            return self._contestar(401, {"detail": "Unauthorized"})
        Gateway.vistas.append((self.path, token))
        codigo, cuerpo = Gateway.respuestas.get(self.path, (404, {"code": 404, "message": "not found"}))
        return self._contestar(codigo, cuerpo)

    def _contestar(self, codigo, cuerpo):
        datos = json.dumps(cuerpo).encode()
        self.send_response(codigo)
        if codigo == 302:
            self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)


@pytest.fixture(scope="module")
def servidor():
    http = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
    hilo = threading.Thread(target=http.serve_forever, daemon=True)
    hilo.start()
    yield f"http://127.0.0.1:{http.server_address[1]}"
    http.shutdown()


class Andvari:
    """Hace del almacén de claves: da la clave del worker para el cliente, una tras otra si hay varias."""

    def __init__(self, *claves):
        self.leidas, self._claves = [], list(claves)

    def read(self, client_id, worker):
        self.leidas.append((client_id, worker))
        clave = self._claves.pop(0) if len(self._claves) > 1 else self._claves[0]
        if isinstance(clave, Exception):
            raise clave
        return clave


@pytest.fixture
def clave():
    actual = new_signing_key()
    Gateway.vale = {"clave": actual}
    Gateway.vistas = []
    Gateway.emitidos = set()
    Gateway.pedidos = 0
    Gateway.respuestas = {
        f"/chat/{BOT}/rules": (200, {"code": 200, "data": PERSONA}),
        f"/chat/{BOT}/contacts/{CONTACTO}/turns": (200, {"code": 200, "data": {"messages": [
            mensaje("in", "hola", "wamid.1", "2026-10-20T08:00:00+00:00"),
            mensaje("out", "¡Hola! ¿En qué te ayudo?", "wamid.2", "2026-10-20T08:00:05+00:00"),
            mensaje("in", None, "wamid.3", "2026-10-20T08:01:00+00:00", adjuntos=["image"]),
            # El mensaje que se responde, que la ingesta ya guardó.
            mensaje("in", "¿tienen zapatos rojos?", "wamid.4", "2026-10-20T08:02:00+00:00"),
        ]}}),
    }
    return actual


def api(url, andvari, **cambios):
    return ApiDelGateway(url, AUDIENCIA, gateway_public(GATEWAY), andvari, "coordinator", **cambios)


def test_pide_la_persona_con_un_token_firmado_por_su_clave_para_el_cliente(servidor, clave):
    andvari = Andvari(clave)

    persona = asyncio.run(api(servidor, andvari).persona(CLIENTE, BOT))

    assert (persona.nombre, persona.rol, persona.tono, persona.descripcion) == (
        "Mine", "Sales Assistant", "helpful", "A test chatbot")
    assert persona.reglas == ["Be kind", "Never promise delivery dates"]
    assert andvari.leidas == [(CLIENTE, "coordinator")]


def test_un_token_de_acceso_sirve_para_varias_llamadas_y_la_clave_se_lee_una_sola_vez(servidor, clave):
    andvari = Andvari(clave)
    gateway = api(servidor, andvari)

    asyncio.run(gateway.persona(CLIENTE, BOT))
    asyncio.run(gateway.vueltas(CLIENTE, BOT, CONTACTO, "wamid.4", LLEGO))

    assert len({token for _, token in Gateway.vistas}) == 1 and Gateway.pedidos == 1
    assert len(andvari.leidas) == 1


def test_un_minuto_antes_de_que_caduque_se_pide_otro(servidor, clave, monkeypatch):
    ahora = [1000.0]
    monkeypatch.setattr("src.infrastructure.gateway.api.time.monotonic", lambda: ahora[0])
    gateway = api(servidor, Andvari(clave))

    asyncio.run(gateway.vueltas(CLIENTE, BOT, CONTACTO, "wamid.4", LLEGO))
    ahora[0] += 539
    asyncio.run(gateway.vueltas(CLIENTE, BOT, CONTACTO, "wamid.4", LLEGO))
    assert Gateway.pedidos == 1
    ahora[0] += 1
    asyncio.run(gateway.vueltas(CLIENTE, BOT, CONTACTO, "wamid.4", LLEGO))

    assert Gateway.pedidos == 2


def test_si_el_gateway_rechaza_el_token_de_acceso_se_pide_otro_una_vez(servidor, clave):
    gateway = api(servidor, Andvari(clave))
    asyncio.run(gateway.persona(CLIENTE, BOT))
    # El gateway rotó su clave de firma: los tokens que dio ya no valen.
    Gateway.emitidos.clear()

    vueltas = asyncio.run(gateway.vueltas(CLIENTE, BOT, CONTACTO, "wamid.4", LLEGO))

    assert len(vueltas) == 3 and Gateway.pedidos == 2


def test_la_persona_se_pide_una_vez_y_se_recuerda_cinco_minutos(servidor, clave, monkeypatch):
    ahora = [1000.0]
    monkeypatch.setattr("src.infrastructure.gateway.api.time.monotonic", lambda: ahora[0])
    gateway = api(servidor, Andvari(clave))

    asyncio.run(gateway.persona(CLIENTE, BOT))
    Gateway.respuestas[f"/chat/{BOT}/rules"] = (200, {"data": {**PERSONA, "rules": ["Nueva"]}})
    ahora[0] += 299
    assert asyncio.run(gateway.persona(CLIENTE, BOT)).reglas == ["Be kind", "Never promise delivery dates"]
    ahora[0] += 2
    assert asyncio.run(gateway.persona(CLIENTE, BOT)).reglas == ["Nueva"]

    assert [ruta for ruta, _ in Gateway.vistas] == [f"/chat/{BOT}/rules"] * 2


def test_la_persona_recordada_de_un_cliente_no_vale_para_otro(servidor, clave):
    gateway = api(servidor, Andvari(clave))
    asyncio.run(gateway.persona(CLIENTE, BOT))

    # Otro cliente no tiene clave con la que el gateway le dé el bot: se pregunta, no se responde de memoria.
    with pytest.raises(FuenteNoDisponible):
        asyncio.run(gateway.persona(uuid4(), BOT))


def test_una_persona_que_no_se_pudo_leer_no_se_recuerda(servidor, clave):
    Gateway.respuestas[f"/chat/{BOT}/rules"] = (500, {"code": 500})
    gateway = api(servidor, Andvari(clave))
    with pytest.raises(FuenteNoDisponible):
        asyncio.run(gateway.persona(CLIENTE, BOT))
    Gateway.respuestas[f"/chat/{BOT}/rules"] = (200, {"data": PERSONA})

    assert asyncio.run(gateway.persona(CLIENTE, BOT)).nombre == "Mine"


def test_las_vueltas_van_en_orden_sin_el_mensaje_que_se_responde(servidor, clave):
    vueltas = asyncio.run(api(servidor, Andvari(clave)).vueltas(CLIENTE, BOT, CONTACTO, "wamid.4", LLEGO))

    assert [(v.de, v.texto, v.adjuntos) for v in vueltas] == [
        ("persona", "hola", []), ("bot", "¡Hola! ¿En qué te ayudo?", []), ("persona", "", ["image"])]
    assert vueltas[0].enviadaEn.isoformat() == "2026-10-20T08:00:00+00:00"


def con_retraso(*despues):
    """La conversación del fixture y, detrás del mensaje que se responde (wamid.4), lo que vino después."""
    ruta = f"/chat/{BOT}/contacts/{CONTACTO}/turns"
    Gateway.respuestas[ruta][1]["data"]["messages"].extend(despues)


def test_un_mensaje_atendido_con_retraso_no_tiene_por_contexto_lo_que_vino_despues(servidor, clave):
    con_retraso(mensaje("in", "¿y en azul?", "wamid.5", "2026-10-20T08:02:30+00:00"),
                mensaje("out", "Sí, también en azul.", "wamid.6", "2026-10-20T08:03:00+00:00"))

    vueltas = asyncio.run(api(servidor, Andvari(clave)).vueltas(
        CLIENTE, BOT, CONTACTO, "wamid.4", datetime(2026, 10, 20, 8, 2, 1, tzinfo=timezone.utc)))

    assert [v.texto for v in vueltas] == ["hola", "¡Hola! ¿En qué te ayudo?", ""]


def test_lo_que_va_detras_se_corta_por_posicion_aunque_las_horas_no_lo_digan(servidor, clave):
    """La hora de Meta y la del webhook no son la misma: la posición del mensaje en el historial manda."""
    con_retraso(mensaje("in", "¿y en azul?", "wamid.5", "2026-10-20T08:02:00+00:00"))

    vueltas = asyncio.run(api(servidor, Andvari(clave)).vueltas(CLIENTE, BOT, CONTACTO, "wamid.4", LLEGO))

    assert "¿y en azul?" not in [v.texto for v in vueltas]


def test_si_el_mensaje_aun_no_esta_guardado_cuenta_cuando_llego(servidor, clave):
    con_retraso(mensaje("in", "¿y en azul?", "wamid.5", "2026-10-20T08:02:30+00:00"))

    vueltas = asyncio.run(api(servidor, Andvari(clave)).vueltas(
        CLIENTE, BOT, CONTACTO, "wamid.todavia-no", datetime(2026, 10, 20, 8, 2, 10, tzinfo=timezone.utc)))

    assert [v.texto for v in vueltas] == ["hola", "¡Hola! ¿En qué te ayudo?", "", "¿tienen zapatos rojos?"]


def test_una_hora_sin_zona_cuenta_como_utc(servidor, clave):
    vueltas = asyncio.run(api(servidor, Andvari(clave)).vueltas(
        CLIENTE, BOT, CONTACTO, "wamid.todavia-no", datetime(2026, 10, 20, 8, 0, 30)))

    assert [v.texto for v in vueltas] == ["hola", "¡Hola! ¿En qué te ayudo?"]


def test_no_pasan_de_cuarenta_vueltas_y_se_quedan_las_ultimas(servidor, clave):
    muchas = [mensaje("in" if n % 2 else "out", f"m{n}", f"wamid.{n}") for n in range(60)]
    Gateway.respuestas[f"/chat/{BOT}/contacts/{CONTACTO}/turns"] = (200, {"data": {"messages": muchas}})

    vueltas = asyncio.run(api(servidor, Andvari(clave)).vueltas(CLIENTE, BOT, CONTACTO, "wamid.otro", LLEGO))

    assert [v.texto for v in vueltas] == [f"m{n}" for n in range(20, 60)]


def test_si_el_gateway_rechaza_la_clave_se_lee_otra_vez_de_andvari(servidor, clave):
    vieja, nueva = new_signing_key(), clave
    andvari = Andvari(vieja, nueva)

    persona = asyncio.run(api(servidor, andvari).persona(CLIENTE, BOT))

    assert persona.nombre == "Mine" and len(andvari.leidas) == 2


def test_un_bot_que_ya_no_existe_no_se_encontrara_nunca(servidor, clave):
    del Gateway.respuestas[f"/chat/{BOT}/rules"]

    with pytest.raises(NoEncontrado):
        asyncio.run(api(servidor, Andvari(clave)).persona(CLIENTE, BOT))


@pytest.mark.parametrize("respuesta", [
    (500, {"code": 500, "message": "INTERNAL SERVER ERROR"}),
    (403, {"detail": "HTTP_403_FORBIDDEN"}),
    (200, {"code": 200, "data": "<s> <<SYS>> Tu nombre es Mine <</SYS>>"}),
    (200, {"code": 200, "data": {"name": "Mine"}}),
    # Una redirección no se sigue: el gateway no tiene por qué mandar a ningún otro sitio.
    (302, {}),
], ids=["500", "403", "el formato de antes", "incompleta", "redirección"])
def test_lo_que_no_es_una_respuesta_util_es_un_fallo_que_se_reintenta(servidor, clave, respuesta):
    Gateway.respuestas[f"/chat/{BOT}/rules"] = respuesta

    with pytest.raises(FuenteNoDisponible):
        asyncio.run(api(servidor, Andvari(clave)).persona(CLIENTE, BOT))


@pytest.mark.parametrize("andvari", [Andvari(None), Andvari(KeyStoreUnavailable("read refused: 403"))],
                         ids=["el cliente no tiene clave del worker", "Andvari no responde"])
def test_sin_clave_no_se_llama_al_gateway(servidor, clave, andvari):
    with pytest.raises(FuenteNoDisponible):
        asyncio.run(api(servidor, andvari).persona(CLIENTE, BOT))

    assert Gateway.vistas == []


def test_un_gateway_inalcanzable_dice_solo_el_tipo_de_fallo(clave):
    with pytest.raises(FuenteNoDisponible) as fallo:
        asyncio.run(api("http://127.0.0.1:1", Andvari(clave), timeout=1).persona(CLIENTE, BOT))

    assert "127.0.0.1" not in str(fallo.value) and str(BOT) not in str(fallo.value)


def test_la_clave_del_gateway_es_la_ultima_del_fichero(tmp_path):
    vieja, nueva = gateway_public(new_key()), gateway_public(new_key())
    fichero = tmp_path / "gateway-public.json"
    fichero.write_text(json.dumps({"keys": [vieja, nueva]}), encoding="utf-8")

    assert clave_publica_del_gateway(str(fichero)) == nueva


# --- Los tokens de Meta, para el notificador -------------------------------------------------------------------------

def tokens(url, andvari):
    return ApiDelGateway(url, AUDIENCIA, gateway_public(GATEWAY), andvari, "notifier")


def test_el_token_de_whatsapp_se_pide_una_vez_y_se_recuerda(servidor, clave):
    Gateway.respuestas[f"/chat/{BOT}/tokens"] = (200, {"data": {"uuid": str(BOT), "facebookToken": "EAAF",
                                                               "whatsappToken": "EAAG-wa"}})
    gateway = tokens(servidor, Andvari(clave))

    assert asyncio.run(gateway.token_de_whatsapp(CLIENTE, BOT)) == "EAAG-wa"
    assert asyncio.run(gateway.token_de_whatsapp(CLIENTE, BOT)) == "EAAG-wa"

    assert [ruta for ruta, _ in Gateway.vistas] == [f"/chat/{BOT}/tokens"]


def test_un_token_olvidado_se_pide_otra_vez(servidor, clave):
    Gateway.respuestas[f"/chat/{BOT}/tokens"] = (200, {"data": {"whatsappToken": "EAAG-viejo"}})
    gateway = tokens(servidor, Andvari(clave))
    asyncio.run(gateway.token_de_whatsapp(CLIENTE, BOT))
    Gateway.respuestas[f"/chat/{BOT}/tokens"] = (200, {"data": {"whatsappToken": "EAAG-nuevo"}})

    gateway.olvidar(BOT)

    assert asyncio.run(gateway.token_de_whatsapp(CLIENTE, BOT)) == "EAAG-nuevo"


@pytest.mark.parametrize("datos", [{"whatsappToken": None}, {"whatsappToken": ""}, {"facebookToken": "EAAF"}])
def test_un_bot_sin_token_de_whatsapp_no_podra_responder_por_whatsapp(servidor, clave, datos):
    Gateway.respuestas[f"/chat/{BOT}/tokens"] = (200, {"data": datos})

    with pytest.raises(NoEncontrado):
        asyncio.run(tokens(servidor, Andvari(clave)).token_de_whatsapp(CLIENTE, BOT))
