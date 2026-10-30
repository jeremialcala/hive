# -*- coding: utf-8 -*-
"""
    El clasificador con el LLM de Hive (ADR-0010) contra un servidor HTTP local que hace de llama-server: comprueba la
    clave, guarda cada petición tal como sale y contesta lo que contestaría la API compatible con la de OpenAI. Solo
    el texto va al modelo, siempre en el turno de usuario, y todo lo que no es un veredicto válido deja el mensaje sin
    seguir.
"""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src.domain.ports.clasificador_de_entrada import ClasificacionFallida
from src.infrastructure.llm import ClienteLlm, LlmClasificador
from src.infrastructure.llm.clasificador import ESQUEMA, SYSTEM

CLAVE = "clave-de-prueba"
VEREDICTO = {"permitido": True, "motivo": "ninguno", "etiqueta": "pregunta", "confianza": 0.92,
             "entidades": [{"nombre": "producto", "valor": "zapatos rojos"}]}


class Llama(BaseHTTPRequestHandler):
    """Hace de llama-server: responde con la clave buena lo que diga `respuesta`, y guarda cada petición."""
    respuesta: tuple = (200, {})
    peticiones: list = []

    def log_message(self, *_args):
        pass

    def do_POST(self):  # pylint: disable=invalid-name
        largo = int(self.headers.get("Content-Length", 0))
        Llama.peticiones.append((self.path, json.loads(self.rfile.read(largo))))
        if self.headers.get("Authorization") != f"Bearer {CLAVE}":
            codigo, cuerpo = 401, {"error": {"message": "Invalid API Key", "type": "authentication_error"}}
        else:
            codigo, cuerpo = Llama.respuesta
        datos = json.dumps(cuerpo).encode() if not isinstance(cuerpo, bytes) else cuerpo
        self.send_response(codigo)
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)


@pytest.fixture(scope="module")
def servidor():
    http = ThreadingHTTPServer(("127.0.0.1", 0), Llama)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{http.server_address[1]}"
    http.shutdown()


def completado(texto, fin="stop"):
    return (200, {"id": "chatcmpl-1", "object": "chat.completion", "model": "gemma-4-26b-a4b",
                  "choices": [{"index": 0, "finish_reason": fin, "message": {"role": "assistant", "content": texto}}],
                  "usage": {"prompt_tokens": 367, "completion_tokens": 81, "total_tokens": 448}})


@pytest.fixture(autouse=True)
def limpio():
    Llama.peticiones = []
    Llama.respuesta = completado(json.dumps(VEREDICTO))


def clasificar(url, texto="¿tienen zapatos rojos?", clave=CLAVE):
    cliente = ClienteLlm(url, clave, "gemma-4-26b-a4b", timeout=5)
    return asyncio.run(LlmClasificador(cliente, 256).clasificar(texto))


def test_una_sola_llamada_con_el_texto_en_el_turno_de_usuario_y_la_salida_atada_al_esquema(servidor):
    clasificar(servidor, "¿tienen zapatos rojos?")

    ((ruta, cuerpo),) = Llama.peticiones
    assert ruta == "/v1/chat/completions"
    assert cuerpo == {
        "model": "gemma-4-26b-a4b", "max_tokens": 256,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "¿tienen zapatos rojos?"}],
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "respuesta", "strict": True, "schema": ESQUEMA}}}


def test_el_esquema_es_uno_que_la_gramatica_admite():
    # Todo objeto cierra sus propiedades y todas son obligatorias, y no hay límites de longitud ni de rango: los
    # comprueba el clasificador al leer.
    def objetos(nodo):
        if isinstance(nodo, dict):
            if nodo.get("type") == "object":
                yield nodo
            for valor in nodo.values():
                yield from objetos(valor)

    for objeto in objetos(ESQUEMA):
        assert objeto["additionalProperties"] is False
        assert set(objeto["required"]) == set(objeto["properties"])
    assert not {"minimum", "maximum", "minLength", "maxLength", "maxItems"} & set(json.dumps(ESQUEMA).split('"'))


def test_un_mensaje_normal_sigue_con_su_intencion(servidor):
    clasificacion = clasificar(servidor)

    assert (clasificacion.permitido, clasificacion.motivo, clasificacion.etiqueta) == (True, "ninguno", "pregunta")
    assert clasificacion.confianza == 0.92
    assert [(e.nombre, e.valor) for e in clasificacion.entidades] == [("producto", "zapatos rojos")]
    assert clasificacion.modelo == "gemma-4-26b-a4b"


@pytest.mark.parametrize("permitido, motivo, esperado", [
    (False, "inyeccion", "inyeccion"),
    (False, "abuso", "abuso"),
    # Incoherente: un motivo para pararlo pesa más que el permiso.
    (True, "abuso", "abuso"),
    # Incoherente: sin permiso y sin motivo, no sigue igual.
    (False, "ninguno", "otro"),
])
def test_solo_sigue_con_permiso_y_sin_motivo_para_pararlo(servidor, permitido, motivo, esperado):
    Llama.respuesta = completado(json.dumps({**VEREDICTO, "permitido": permitido, "motivo": motivo}))

    clasificacion = clasificar(servidor)

    assert (clasificacion.permitido, clasificacion.motivo) == (False, esperado)


def test_las_entidades_mal_formadas_se_descartan_y_no_pasan_de_diez(servidor):
    entidades = ([{"nombre": "Producto Raro", "valor": "x"}, {"nombre": "fecha", "valor": "y" * 201}]
                 + [{"nombre": f"dato_{n}", "valor": str(n)} for n in range(12)])
    Llama.respuesta = completado(json.dumps({**VEREDICTO, "entidades": entidades}))

    clasificacion = clasificar(servidor)

    assert [e.nombre for e in clasificacion.entidades] == [f"dato_{n}" for n in range(10)]


@pytest.mark.parametrize("respuesta", [
    completado("no es json"),
    completado(json.dumps({**VEREDICTO, "confianza": 1.5})),
    completado(json.dumps({**VEREDICTO, "etiqueta": "comprar_ya"})),
    completado(json.dumps({**VEREDICTO, "extra": "lo que sea"})),
    completado(json.dumps({k: v for k, v in VEREDICTO.items() if k != "motivo"})),
    completado(""),
    # Cortada por max_tokens: no se interpreta a medias.
    completado('{"permitido": true, "motivo": "ning', fin="length"),
], ids=["no es JSON", "confianza fuera de rango", "etiqueta desconocida", "un campo de más", "falta el motivo",
        "vacía", "cortada"])
def test_una_respuesta_que_no_valida_no_es_un_veredicto(servidor, respuesta):
    Llama.respuesta = respuesta

    with pytest.raises(ClasificacionFallida):
        clasificar(servidor)


@pytest.mark.parametrize("respuesta", [
    (500, {"error": {"message": "boom con zapatos rojos"}}),
    (503, {"error": {"message": "Loading model"}}),
    (200, {"no": "es una respuesta de chat"}),
    (200, b"<html>no es JSON</html>"),
], ids=["500", "cargando el modelo", "otra forma", "no JSON"])
def test_un_fallo_del_servidor_dice_solo_su_tipo(servidor, respuesta):
    Llama.respuesta = respuesta

    with pytest.raises(ClasificacionFallida) as error:
        clasificar(servidor)

    assert "zapatos" not in str(error.value)


def test_con_la_clave_equivocada_no_hay_veredicto(servidor):
    with pytest.raises(ClasificacionFallida, match="401"):
        clasificar(servidor, clave="otra")


def test_un_servidor_apagado_dice_solo_el_tipo_de_fallo():
    with pytest.raises(ClasificacionFallida) as error:
        clasificar("http://127.0.0.1:1")

    assert "127.0.0.1" not in str(error.value)
