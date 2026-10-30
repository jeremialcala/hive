# -*- coding: utf-8 -*-
"""
    La Cloud API de WhatsApp como la llama el notificador (src/infrastructure/meta/whatsapp.py), contra un servidor
    HTTP local que hace de Meta: comprueba la ruta, el token y el cuerpo, y contesta lo que Meta contestaría, con los
    códigos de error de su documentación.
"""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src.domain.ports.envio import EnvioFallido, EnvioRechazado, TokenRechazado
from src.infrastructure.meta import WhatsAppCloud

CUENTA, NUMERO, TOKEN = "106540352242922", "12025550143", "EAAG-token-de-prueba"


class Meta(BaseHTTPRequestHandler):
    respuesta: tuple = (200, {})
    vistas: list = []

    def log_message(self, *_args):
        pass

    def do_POST(self):  # pylint: disable=invalid-name
        largo = int(self.headers.get("Content-Length", 0))
        Meta.vistas.append((self.path, self.headers.get("Authorization"), json.loads(self.rfile.read(largo))))
        codigo, cuerpo = Meta.respuesta
        datos = json.dumps(cuerpo).encode() if cuerpo is not None else b"<html>Bad Gateway</html>"
        self.send_response(codigo)
        if codigo == 302:
            self.send_header("Location", "http://169.254.169.254/")
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)


@pytest.fixture(scope="module")
def servidor():
    http = ThreadingHTTPServer(("127.0.0.1", 0), Meta)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{http.server_address[1]}"
    http.shutdown()


@pytest.fixture(autouse=True)
def limpio():
    Meta.vistas = []
    Meta.respuesta = (200, {"messaging_product": "whatsapp", "contacts": [{"input": NUMERO, "wa_id": NUMERO}],
                            "messages": [{"id": "wamid.ENVIADO", "message_status": "accepted"}]})


def error(codigo, http=400):
    return (http, {"error": {"message": f"(#{codigo}) algo con {NUMERO}", "type": "OAuthException", "code": codigo,
                             "error_data": {"messaging_product": "whatsapp", "details": f"para {NUMERO}"},
                             "fbtrace_id": "A1"}})


# Lo que queda de error() en el motivo: el código no se repite, y el número se tapa.
EXPLICADO = " (algo con … · para … · fbtrace_id A1)"


def enviar(url, texto="Sí, tenemos zapatos rojos."):
    return asyncio.run(WhatsAppCloud(url, "v26.0", 2).enviar_texto(TOKEN, CUENTA, NUMERO, texto))


def test_envia_solo_texto_desde_el_numero_del_bot_y_devuelve_el_id_que_dio_meta(servidor):
    assert enviar(servidor) == "wamid.ENVIADO"

    ((ruta, autorizacion, cuerpo),) = Meta.vistas
    assert ruta == f"/v26.0/{CUENTA}/messages"
    assert autorizacion == f"Bearer {TOKEN}"
    assert cuerpo == {"messaging_product": "whatsapp", "recipient_type": "individual", "to": NUMERO, "type": "text",
                      "text": {"body": "Sí, tenemos zapatos rojos.", "preview_url": False}}
    # Sin citar el mensaje de la persona (decisión de Jeremi del 2026-10-20).
    assert "context" not in cuerpo


@pytest.mark.parametrize("codigo", [2, 4, 130429, 131000, 131016, 131056, 80007, 10, 131005])
def test_lo_que_meta_dice_que_se_arregla_esperando_se_reintenta(servidor, codigo):
    Meta.respuesta = error(codigo)

    with pytest.raises(EnvioFallido) as fallo:
        enviar(servidor)

    assert not isinstance(fallo.value, TokenRechazado) and str(fallo.value) == f"Meta {codigo}{EXPLICADO}"


@pytest.mark.parametrize("codigo", [0, 190])
def test_un_token_rechazado_se_dice_aparte(servidor, codigo):
    Meta.respuesta = error(codigo, 401)

    with pytest.raises(TokenRechazado):
        enviar(servidor)


@pytest.mark.parametrize("codigo", [100, 131008, 131009, 131021, 131026, 131031, 131047, 131051, 133010, 368])
def test_lo_que_no_ira_nunca_asi_no_se_reintenta(servidor, codigo):
    Meta.respuesta = error(codigo)

    with pytest.raises(EnvioRechazado):
        enviar(servidor)


@pytest.mark.parametrize("respuesta, reintenta", [
    ((502, None), True), ((503, {"detail": "unavailable"}), True), ((429, None), True),
    ((404, None), False), ((302, None), False),
    # Dijo que sí sin id: no se reintenta, que llegaría dos veces.
    ((200, {"messages": []}), False),
], ids=["502 sin JSON", "503", "429", "404", "redirección", "aceptado sin id"])
def test_sin_codigo_de_meta_lo_dice_el_estado_http(servidor, respuesta, reintenta):
    Meta.respuesta = respuesta

    with pytest.raises(EnvioFallido if reintenta else EnvioRechazado):
        enviar(servidor)


def test_ni_el_numero_ni_el_texto_ni_el_token_quedan_en_el_error(servidor):
    Meta.respuesta = error(131026)

    with pytest.raises(EnvioRechazado) as fallo:
        enviar(servidor, texto="mis zapatos rojos")

    for prohibido in (NUMERO, "zapatos", TOKEN):
        assert prohibido not in str(fallo.value)


@pytest.mark.parametrize("cuerpo, motivo", [
    ({"message": "(#131058) Lo que Meta explique", "error_data": {"details": f"to {NUMERO}"}, "fbtrace_id": "AXyZ-12_q"},
     "Meta 131058 (Lo que Meta explique · to … · fbtrace_id AXyZ-12_q)"),
    ({"message": "(#131009) Parameter value is not valid", "error_user_title": "Valor no válido"},
     "Meta 131009 (Valor no válido)"),
    ({"message": "Message wamid.HBgLMTIwMjU1NTAxNDMVAgARGBI5QTNDQTVCM0M0Q0Q2RTY3NkUA not found for ana@example.com"},
     "Meta 131009 (Message wamid.… not found for …@…)"),
    ({"message": "token EAAGm0PX4ZCpsBAKZCZBnxyzABCDEFGHIJKLMNOPQRST1234 rejected\r\nFake: line"},
     "Meta 131009 (token … rejected Fake: line)"),
    ({"message": "abcd " * 80}, "Meta 131009 (" + ("abcd " * 60).strip() + ")"),
    ({"message": "", "fbtrace_id": "A1 ) inyectado"}, "Meta 131009"),
    ({"message": ["no es texto"], "error_data": "tampoco"}, "Meta 131009"),
], ids=["131058, sin documentar", "su título", "wamid y correo", "token y saltos de línea", "largo", "traza rara",
        "tipos raros"])
def test_lo_que_meta_explica_va_detras_del_codigo_y_tapado(servidor, cuerpo, motivo):
    codigo = 131058 if "131058" in str(cuerpo) else 131009
    Meta.respuesta = (400, {"error": {"code": codigo, "type": "OAuthException", **cuerpo}})

    with pytest.raises(EnvioRechazado) as rechazo:
        plantilla(servidor)

    assert str(rechazo.value) == motivo
    assert NUMERO not in str(rechazo.value) and "\n" not in str(rechazo.value)


def test_meta_inalcanzable_dice_solo_el_tipo_de_fallo():
    with pytest.raises(EnvioFallido) as fallo:
        enviar("http://127.0.0.1:1")

    assert "127.0.0.1" not in str(fallo.value) and CUENTA not in str(fallo.value)


# --- Leído y «escribiendo…» (ADR-0011) -------------------------------------------------------------------------------

def marcar(url, escribiendo):
    return asyncio.run(WhatsAppCloud(url, "v26.0", 2).marcar_leido(TOKEN, CUENTA, "wamid.A", escribiendo))


@pytest.mark.parametrize("escribiendo", [False, True], ids=["solo leído", "con escribiendo"])
def test_marca_el_mensaje_como_leido_desde_el_numero_del_bot(servidor, escribiendo):
    Meta.respuesta = (200, {"success": True})

    marcar(servidor, escribiendo)

    ((ruta, autorizacion, cuerpo),) = Meta.vistas
    assert (ruta, autorizacion) == (f"/v26.0/{CUENTA}/messages", f"Bearer {TOKEN}")
    esperado = {"messaging_product": "whatsapp", "status": "read", "message_id": "wamid.A"}
    if escribiendo:
        esperado["typing_indicator"] = {"type": "text"}
    assert cuerpo == esperado


@pytest.mark.parametrize("respuesta, fallo", [
    (error(190, 401), TokenRechazado), (error(131000), EnvioFallido), (error(131009), EnvioRechazado),
], ids=["token", "se arregla esperando", "id que no existe"])
def test_lo_que_meta_no_acepta_al_marcar_se_dice_como_al_enviar(servidor, respuesta, fallo):
    Meta.respuesta = respuesta

    with pytest.raises(fallo):
        marcar(servidor, True)


# --- Una plantilla, lo que el cliente envía por iniciativa propia (ADR-0031 del gateway) ---------------------------

def plantilla(url, valores=("mañana a las 10",)):
    return asyncio.run(WhatsAppCloud(url, "v26.0", 2).enviar_plantilla(TOKEN, CUENTA, NUMERO, "cita", "es",
                                                                       list(valores)))


def test_envia_una_plantilla_con_sus_valores_como_parametros_del_cuerpo(servidor):
    assert plantilla(servidor) == "wamid.ENVIADO"
    ((ruta, autorizacion, cuerpo),) = Meta.vistas
    assert (ruta, autorizacion) == (f"/v26.0/{CUENTA}/messages", f"Bearer {TOKEN}")
    assert cuerpo == {"messaging_product": "whatsapp", "recipient_type": "individual", "to": NUMERO,
                      "type": "template",
                      "template": {"name": "cita", "language": {"code": "es"},
                                   "components": [{"type": "body", "parameters": [
                                       {"type": "text", "text": "mañana a las 10"}]}]}}


def test_una_plantilla_sin_valores_no_lleva_componentes(servidor):
    plantilla(servidor, valores=())

    assert Meta.vistas[0][2]["template"] == {"name": "cita", "language": {"code": "es"}}


@pytest.mark.parametrize("codigo", [131047, 132000, 132001, 132015, 132016, 131049],
                         ids=["fuera de la ventana", "otros valores", "no existe", "en pausa", "desactivada",
                              "límite de marketing por persona"])
def test_lo_que_meta_rechaza_de_un_envio_del_cliente_no_se_reintenta(servidor, codigo):
    Meta.respuesta = error(codigo)

    with pytest.raises(EnvioRechazado) as rechazo:
        plantilla(servidor)
    assert str(rechazo.value) == f"Meta {codigo}{EXPLICADO}"
    assert NUMERO not in str(rechazo.value)
