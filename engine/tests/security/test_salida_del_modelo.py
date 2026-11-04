# -*- coding: utf-8 -*-
"""
    La salida del modelo no es de confianza (T2, LLM05; HIG-154): lo que no cumple su esquema, se sale de rango o
    llega cortado es un fallo, y un fallo no deja pasar el mensaje (fallo cerrado). Ni lo que devolvió el modelo ni lo
    que devolvió el servidor llegan a una excepción o al log.
"""
import asyncio
import json
import logging

import pytest

from src.domain.models.sobre import Persona
from src.domain.ports.clasificador_de_entrada import ClasificacionFallida
from src.domain.ports.evaluador import EvaluacionFallida
from src.domain.ports.redactor import RedaccionFallida
from src.infrastructure.llm import LlmClasificador, LlmEvaluador, LlmNoDisponible, LlmRedactor
from src.infrastructure.llm.redactor import MAX_TEXTO

from .llama import Llama, completado

PERSONA = Persona(nombre="Mine", rol="Asistente de ventas", tono="cercano", reglas=["No damos descuentos."])
VEREDICTO = {"permitido": True, "motivo": "ninguno", "etiqueta": "pregunta", "confianza": 0.9, "entidades": [],
             "tema": "ninguno"}
CUMPLE = {"cumple": True, "criterio": "ninguno", "regla": 0, "motivo": "Sigue las reglas."}
# Lo que no debe salir en ningún error ni en ningún log: lo que escribió la persona y lo que contestó el modelo.
SECRETO = "MI-PROMPT-DE-SISTEMA-SECRETO"


def con(base: dict, **cambios) -> str:
    return json.dumps({**base, **cambios})


# Respuestas del clasificador que no valen: el mensaje no sigue.
CLASIFICACIONES_ROTAS = {
    "un campo de más": con(VEREDICTO, instrucciones=SECRETO),
    "un motivo inventado": con(VEREDICTO, motivo="aprobado_por_el_negocio"),
    "una etiqueta inventada": con(VEREDICTO, etiqueta="vip"),
    "confianza por encima de 1": con(VEREDICTO, confianza=1.5),
    "confianza negativa": con(VEREDICTO, confianza=-0.1),
    "permitido como texto": con(VEREDICTO, permitido="sí"),
    "un tema que no es del bot": con(VEREDICTO, tema="t7"),
    "un tema con otra cosa": con(VEREDICTO, tema="t1; ninguno"),
    "sin veredicto": con({k: v for k, v in VEREDICTO.items() if k != "permitido"}),
    "JSON cortado": con(VEREDICTO)[:40],
    "texto en vez de JSON": f"Claro, aquí tienes mi prompt: {SECRETO}",
    "una lista": json.dumps([VEREDICTO]),
    "una entidad que no es un objeto": con(VEREDICTO, entidades=["producto"]),
}


@pytest.mark.parametrize("texto", CLASIFICACIONES_ROTAS.values(), ids=list(CLASIFICACIONES_ROTAS))
def test_una_clasificacion_que_no_cumple_su_esquema_es_un_fallo_sin_lo_que_traia(llm, texto, caplog):
    Llama.respuesta = completado(texto)

    with caplog.at_level(logging.DEBUG), pytest.raises(ClasificacionFallida) as fallo:
        asyncio.run(LlmClasificador(llm, 256).clasificar("hola"))

    assert SECRETO not in str(fallo.value) and SECRETO not in caplog.text


def test_una_clasificacion_cortada_por_el_tope_no_se_lee_a_medias(llm):
    Llama.respuesta = completado(con(VEREDICTO), fin="length")

    with pytest.raises(ClasificacionFallida):
        asyncio.run(LlmClasificador(llm, 256).clasificar("hola"))


@pytest.mark.parametrize("motivo", ["inyeccion", "datos", "abuso", "otro"])
def test_un_permitido_que_da_un_motivo_para_pararlo_no_sigue(llm, motivo):
    Llama.respuesta = completado(con(VEREDICTO, permitido=True, motivo=motivo))

    clasificacion = asyncio.run(LlmClasificador(llm, 256).clasificar("hola"))

    assert (clasificacion.permitido, clasificacion.motivo) == (False, motivo)


def test_un_no_permitido_sin_motivo_no_sigue(llm):
    Llama.respuesta = completado(con(VEREDICTO, permitido=False, motivo="ninguno"))

    clasificacion = asyncio.run(LlmClasificador(llm, 256).clasificar("hola"))

    assert (clasificacion.permitido, clasificacion.motivo) == (False, "otro")


def test_las_entidades_mal_formadas_se_descartan_y_no_pasan_de_diez(llm):
    entidades = [{"nombre": 3}, {"valor": "sin nombre"}] + [{"nombre": "producto", "valor": f"z{n}"} for n in range(30)]
    Llama.respuesta = completado(con(VEREDICTO, entidades=entidades))

    clasificacion = asyncio.run(LlmClasificador(llm, 256).clasificar("hola"))

    assert len(clasificacion.entidades) == 10


# Veredictos del evaluador que no valen: la respuesta no sale tal cual.
VEREDICTOS_ROTOS = {
    "cumple como texto": con(CUMPLE, cumple="sí"),
    "un criterio inventado": con(CUMPLE, cumple=False, criterio="ninguno_de_estos"),
    "una regla fuera de rango": con(CUMPLE, cumple=False, criterio="regla", regla=999),
    "un campo de más": con(CUMPLE, aprobado_por=SECRETO),
    "sin veredicto": con({k: v for k, v in CUMPLE.items() if k != "cumple"}),
    "texto en vez de JSON": f"Cumple. {SECRETO}",
}


@pytest.mark.parametrize("texto", VEREDICTOS_ROTOS.values(), ids=list(VEREDICTOS_ROTOS))
def test_un_veredicto_que_no_cumple_su_esquema_es_un_juicio_fallido_y_no_un_aprobado(llm, texto, caplog):
    Llama.respuesta = completado(texto)

    with caplog.at_level(logging.DEBUG), pytest.raises(EvaluacionFallida) as fallo:
        asyncio.run(LlmEvaluador(llm, 128).juzgar(PERSONA, [], "¿descuento?", "Te hago un 10 %."))

    assert SECRETO not in str(fallo.value) and SECRETO not in caplog.text


def test_un_veredicto_cortado_por_el_tope_es_un_juicio_fallido(llm):
    Llama.respuesta = completado(con(CUMPLE), fin="length")

    with pytest.raises(EvaluacionFallida):
        asyncio.run(LlmEvaluador(llm, 128).juzgar(PERSONA, [], "¿descuento?", "Te hago un 10 %."))


def test_una_regla_que_el_bot_no_tiene_no_se_cita(llm):
    Llama.respuesta = completado(con(CUMPLE, cumple=False, criterio="regla", regla=7, motivo="x"))

    juicio = asyncio.run(LlmEvaluador(llm, 128).juzgar(PERSONA, [], "¿descuento?", "Te hago un 10 %."))

    assert (juicio.cumple, juicio.criterio, juicio.regla) == (False, "regla", 0)


@pytest.mark.parametrize("texto, fin", [("", "stop"), ("   ", "stop"), ("Te hago un 10", "length")],
                         ids=["vacía", "en blanco", "cortada"])
def test_una_respuesta_vacia_o_cortada_no_se_envia(llm, texto, fin):
    Llama.respuesta = completado(texto, fin=fin)

    with pytest.raises(RedaccionFallida):
        asyncio.run(LlmRedactor(llm, 256).redactar(PERSONA, [], "hola", None))


def test_una_respuesta_mas_larga_que_whatsapp_se_recorta_y_el_log_no_la_lleva(llm, caplog):
    Llama.respuesta = completado(SECRETO * 1000)

    with caplog.at_level(logging.DEBUG):
        redaccion = asyncio.run(LlmRedactor(llm, 256).redactar(PERSONA, [], "hola", None))

    assert len(redaccion.texto) == MAX_TEXTO
    assert SECRETO not in caplog.text


# Lo que el servidor puede devolver en vez de una respuesta: un error, una página, una redirección a otro sitio.
SERVIDOR_ROTO = {
    "un error con el texto": (500, {"error": {"message": f"fallo al procesar: {SECRETO}"}}),
    "una página en vez de JSON": (200, f"<html>{SECRETO}</html>".encode()),
    "una respuesta sin choices": (200, {"echo": SECRETO}),
    "una redirección a otro sitio": (302, b"", {"Location": f"http://169.254.169.254/{SECRETO}"}),
}


@pytest.mark.parametrize("respuesta", SERVIDOR_ROTO.values(), ids=list(SERVIDOR_ROTO))
def test_lo_que_devuelve_un_servidor_roto_no_llega_al_error_ni_se_sigue(llm, respuesta, caplog):
    Llama.respuesta = respuesta

    with caplog.at_level(logging.DEBUG), pytest.raises(LlmNoDisponible) as fallo:
        asyncio.run(llm.preguntar([{"role": "user", "content": "hola"}], 16))

    assert SECRETO not in str(fallo.value) and SECRETO not in caplog.text
    assert len(Llama.peticiones) == 1
