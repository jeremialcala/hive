# -*- coding: utf-8 -*-
"""
    La memoria del coordinador (HIG-19, ADR-0017 del gateway): las vueltas salen del contexto de Bifröst si sirve para
    el mensaje, si no del gateway, que lo siembra; se apuntan lo que dijo la persona y lo que contestó el bot; y lo que
    el gateway suprime se borra. Bifröst y Andvari los sustituyen dobles; tests/integration lo prueba contra un NATS.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.domain.memoria import GUARDADAS, PERSONAS, Memoria, antes_de, recortar
from src.domain.models.sobre import (Clasificacion, Contestacion, ContactoDelCanal, Entrada, MensajeClasificado,
                                     MensajeRespondido, Vuelta)
from src.domain.ports.contexto import Apunte, Choque, Contexto, ContextoNoDisponible, Conversacion
from src.domain.ports.fuente_del_bot import FuenteDelBot
from src.infrastructure.bifrost.contexto import ContextoEnBifrost, clave
from src.workers.coordinator import Respondido, Suprimido
from src.workers.runtime import Resultado
from shared.engine_key_store import KeyStoreUnavailable

BOT, CLIENTE, CONTACTO = uuid4(), uuid4(), uuid4()
AHORA = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
HISTORIAL = [Vuelta(de="persona", texto="hola", enviadaEn=AHORA - timedelta(minutes=5)),
             Vuelta(de="bot", texto="¡Hola!", enviadaEn=AHORA - timedelta(minutes=4))]


def sobre(message_id="wamid.B", recibido=AHORA, texto="¿y el horario?") -> MensajeClasificado:
    return MensajeClasificado(
        schemaVersion=2, botId=BOT, clientId=CLIENTE, messageId=message_id, channel="whatsapp", receivedAt=recibido,
        orderKey=f"{BOT}.{CONTACTO}", contact=ContactoDelCanal(uuid=CONTACTO, waId="12025550143"),
        entrada=Entrada(texto=texto), clasificacion=Clasificacion(permitido=True, motivo="ninguno"))


def respondido(message_id="wamid.B", texto="De 9 a 18.") -> MensajeRespondido:
    return MensajeRespondido(**sobre(message_id).model_dump(), respuesta=Contestacion(texto=texto, fuente="modelo"))


class Gateway(FuenteDelBot):
    def __init__(self, vueltas=HISTORIAL):
        self.pedidas = 0
        self._vueltas = vueltas

    async def persona(self, client_id, bot_id):
        raise AssertionError("no hace falta")

    async def vueltas(self, client_id, bot_id, contact_id, sin, hasta):
        self.pedidas += 1
        return list(self._vueltas)


class EnMemoria(Contexto):
    """El bucket, en memoria: una conversación por clave, con su revisión. Las de `ilegibles` no se entienden."""

    def __init__(self, caido=False):
        self.claves, self.caido, self.olvidos, self.ilegibles = {}, caido, [], set()
        # Para probar los fallos al escribir: Bifröst o Andvari caídos solo al guardar, u otro que siempre gana.
        self.no_guarda, self.siempre_choca = False, False

    async def leer(self, client_id, bot_id, contact_id):
        if self.caido:
            raise ContextoNoDisponible("TimeoutError")
        clave = (client_id, bot_id, contact_id)
        guardado = self.claves.get(clave)
        if guardado is None:
            return None
        if clave in self.ilegibles:
            return Conversacion(apuntes=[], revision=guardado[1], completa=False)
        return Conversacion(apuntes=list(guardado[0]), revision=guardado[1])

    async def guardar(self, client_id, bot_id, contact_id, apuntes, revision):
        if self.caido or self.no_guarda:
            raise ContextoNoDisponible("TimeoutError")
        actual = self.claves.get((client_id, bot_id, contact_id))
        if self.siempre_choca or (actual[1] if actual else 0) != revision:
            raise Choque()
        self.claves[(client_id, bot_id, contact_id)] = (list(apuntes), revision + 1)
        self.ilegibles.discard((client_id, bot_id, contact_id))
        return revision + 1

    async def olvidar(self, client_id, bot_id=None, contact_id=None):
        if self.caido:
            raise ContextoNoDisponible("TimeoutError")
        self.olvidos.append((client_id, bot_id, contact_id))
        for suprimida in [c for c in self.claves if c[0] == client_id and bot_id in (None, c[1])
                          and contact_id in (None, c[2])]:
            del self.claves[suprimida]

    def apuntes(self):
        return self.claves[(CLIENTE, BOT, CONTACTO)][0]


def correr(corrutina):
    return asyncio.run(corrutina)


# --- De dónde salen las vueltas -------------------------------------------------------------------------------------

def test_sin_contexto_se_piden_al_gateway_y_se_siembra():
    gateway, contexto = Gateway(), EnMemoria()

    vueltas = correr(Memoria(gateway, contexto).vueltas(sobre()))

    assert vueltas == HISTORIAL and gateway.pedidas == 1
    assert [apunte.vuelta for apunte in contexto.apuntes()] == HISTORIAL


def test_con_contexto_no_se_pregunta_al_gateway():
    gateway, contexto = Gateway(), EnMemoria()
    memoria = Memoria(gateway, contexto)
    correr(memoria.vueltas(sobre("wamid.A", AHORA - timedelta(minutes=3))))
    correr(memoria.apuntar_persona(sobre("wamid.A", AHORA - timedelta(minutes=3), texto="primero")))

    vueltas = correr(memoria.vueltas(sobre()))

    assert gateway.pedidas == 1
    assert [vuelta.texto for vuelta in vueltas] == ["hola", "¡Hola!", "primero"]


def test_un_mensaje_que_llega_otra_vez_ve_solo_lo_de_antes_de_el():
    contexto = EnMemoria()
    memoria = Memoria(Gateway(), contexto)
    correr(memoria.vueltas(sobre()))
    correr(memoria.apuntar_persona(sobre()))
    correr(memoria.apuntar_bot(respondido()))

    assert [vuelta.texto for vuelta in correr(memoria.vueltas(sobre()))] == ["hola", "¡Hola!"]


def test_un_mensaje_atendido_con_retraso_no_toma_como_contexto_lo_que_vino_despues():
    gateway, contexto = Gateway(), EnMemoria()
    memoria = Memoria(gateway, contexto)
    correr(memoria.vueltas(sobre()))
    correr(memoria.apuntar_persona(sobre()))

    viejo = sobre("wamid.VIEJO", AHORA - timedelta(minutes=2))
    correr(memoria.vueltas(viejo))

    # El contexto tiene algo posterior al mensaje: lo decide el gateway, y el contexto no se toca.
    assert gateway.pedidas == 2 and len(contexto.apuntes()) == 3


def test_si_bifrost_o_andvari_fallan_se_pregunta_al_gateway_y_el_mensaje_sigue(caplog):
    gateway = Gateway()

    vueltas = correr(Memoria(gateway, EnMemoria(caido=True)).vueltas(sobre()))

    assert vueltas == HISTORIAL and gateway.pedidas == 1
    assert "Sin el contexto del bot" in caplog.text and "hola" not in caplog.text


def test_un_contexto_que_no_se_entiende_se_siembra_otra_vez():
    contexto = EnMemoria()
    contexto.claves[(CLIENTE, BOT, CONTACTO)] = ([], 7)
    contexto.ilegibles.add((CLIENTE, BOT, CONTACTO))
    gateway = Gateway()

    correr(Memoria(gateway, contexto).vueltas(sobre()))

    assert gateway.pedidas == 1
    assert len(contexto.apuntes()) == 2 and contexto.claves[(CLIENTE, BOT, CONTACTO)][1] == 8


def test_un_contacto_nuevo_tiene_contexto_desde_el_primer_mensaje():
    """HIG-144: el contexto sembrado de un historial vacío está completo, y sobre él se apunta."""
    gateway, contexto = Gateway(vueltas=[]), EnMemoria()
    memoria = Memoria(gateway, contexto)
    # Lo que contesta el bot lleva la hora en que se apunta: el segundo mensaje llega después, como en producción.
    ahora = datetime.now(timezone.utc)
    primero = sobre("wamid.A", ahora - timedelta(minutes=1), texto="hola")

    assert correr(memoria.vueltas(primero)) == []
    correr(memoria.apuntar_persona(primero))
    correr(memoria.apuntar_bot(respondido("wamid.A", texto="¡Hola! ¿En qué te ayudo?")))
    vueltas = correr(memoria.vueltas(sobre("wamid.B", ahora + timedelta(minutes=1))))

    assert gateway.pedidas == 1
    assert [(v.de, v.texto) for v in vueltas] == [("persona", "hola"), ("bot", "¡Hola! ¿En qué te ayudo?")]


def test_se_guardan_como_mucho_las_vueltas_que_se_pasan():
    # Una pregunta y muchas respuestas seguidas (pedidos del cliente): el tope es el de las vueltas.
    largas = [Vuelta(de="persona" if n == 0 else "bot", texto=str(n),
                     enviadaEn=AHORA - timedelta(hours=1, minutes=-n)) for n in range(50)]
    contexto = EnMemoria()

    correr(Memoria(Gateway(largas), contexto).vueltas(sobre()))

    assert [apunte.vuelta.texto for apunte in contexto.apuntes()] == [str(n) for n in range(50 - GUARDADAS, 50)]


# --- Lo que se apunta -----------------------------------------------------------------------------------------------

def test_se_apunta_lo_que_dijo_la_persona_y_lo_que_contesto_el_bot():
    contexto = EnMemoria()
    memoria = Memoria(Gateway(), contexto)
    correr(memoria.vueltas(sobre()))

    correr(memoria.apuntar_persona(sobre()))
    correr(memoria.apuntar_bot(respondido()))

    ultimos = contexto.apuntes()[-2:]
    assert [(a.vuelta.de, a.vuelta.texto, a.mensaje) for a in ultimos] == [
        ("persona", "¿y el horario?", "wamid.B"), ("bot", "De 9 a 18.", "wamid.B")]


def test_lo_ya_apuntado_no_se_apunta_dos_veces():
    contexto = EnMemoria()
    memoria = Memoria(Gateway(), contexto)
    correr(memoria.vueltas(sobre()))
    for _ in range(2):
        correr(memoria.apuntar_persona(sobre()))
        correr(memoria.apuntar_bot(respondido()))

    assert len(contexto.apuntes()) == 4


def test_sin_contexto_no_se_apunta_nada_que_engane_al_modelo():
    contexto = EnMemoria()
    memoria = Memoria(Gateway(), contexto)

    correr(memoria.apuntar_persona(sobre()))
    correr(memoria.apuntar_bot(respondido()))

    assert contexto.claves == {}


def test_la_respuesta_a_un_mensaje_que_el_contexto_no_tiene_lo_borra(caplog):
    gateway, contexto = Gateway(), EnMemoria()
    memoria = Memoria(gateway, contexto)
    correr(memoria.vueltas(sobre()))

    correr(memoria.apuntar_bot(respondido("wamid.OTRO")))

    # Le falta algo: el mensaje siguiente lo siembra otra vez desde el gateway.
    assert contexto.claves == {} and "una respuesta a un mensaje que no tenía" in caplog.text
    correr(memoria.vueltas(sobre("wamid.C", AHORA + timedelta(minutes=1))))
    assert gateway.pedidas == 2 and len(contexto.apuntes()) == 2


def test_un_mensaje_atendido_con_retraso_borra_el_contexto_al_apuntarse():
    contexto = EnMemoria()
    memoria = Memoria(Gateway(), contexto)
    correr(memoria.vueltas(sobre()))
    correr(memoria.apuntar_persona(sobre()))

    correr(memoria.apuntar_persona(sobre("wamid.VIEJO", AHORA - timedelta(minutes=2))))

    # Al final, el orden mentiría.
    assert contexto.claves == {}


def test_dos_mensajes_seguidos_de_la_persona_no_borran_el_contexto():
    # El segundo llega antes de que se apunte la respuesta al primero: la respuesta es posterior, y eso es normal.
    contexto = EnMemoria()
    memoria = Memoria(Gateway(), contexto)
    correr(memoria.vueltas(sobre()))
    correr(memoria.apuntar_persona(sobre()))
    correr(memoria.apuntar_bot(respondido()))

    correr(memoria.apuntar_persona(sobre("wamid.C", AHORA + timedelta(seconds=30), texto="¿y los sábados?")))

    assert [a.vuelta.texto for a in contexto.apuntes()][-3:] == ["¿y el horario?", "De 9 a 18.", "¿y los sábados?"]


@pytest.mark.parametrize("fallo", ["no_guarda", "siempre_choca"])
def test_si_no_se_puede_apuntar_se_borra_el_contexto(fallo, caplog):
    contexto = EnMemoria()
    memoria = Memoria(Gateway(), contexto)
    correr(memoria.vueltas(sobre()))
    setattr(contexto, fallo, True)

    correr(memoria.apuntar_persona(sobre()))

    assert contexto.claves == {} and "borrado, se sembrará otra vez" in caplog.text


def test_si_tampoco_se_puede_borrar_el_log_dice_que_caduca(caplog):
    contexto = EnMemoria()
    memoria = Memoria(Gateway(), contexto)
    correr(memoria.vueltas(sobre()))
    contexto.caido = True

    correr(memoria.apuntar_persona(sobre()))

    assert "caduca en 24 horas" in caplog.text and "¿y el horario?" not in caplog.text


def test_si_otro_coordinador_escribio_entre_medias_se_apunta_encima():
    contexto = EnMemoria()
    memoria = Memoria(Gateway(), contexto)
    correr(memoria.vueltas(sobre()))
    leer = contexto.leer
    veces = []

    async def leer_y_que_otro_escriba(*args):
        conversacion = await leer(*args)
        if not veces:
            veces.append(1)
            otro = (CLIENTE, BOT, CONTACTO)
            contexto.claves[otro] = (contexto.claves[otro][0], contexto.claves[otro][1] + 1)
        return conversacion

    contexto.leer = leer_y_que_otro_escriba
    correr(memoria.apuntar_persona(sobre()))

    assert contexto.apuntes()[-1].mensaje == "wamid.B"


# --- Los consumidores del coordinador -------------------------------------------------------------------------------

class Mensaje:
    def __init__(self, data: bytes, subject="motor.respuesta.3.b.c"):
        self.data, self.subject, self.headers = data, subject, {}


def test_la_respuesta_publicada_se_apunta_y_se_confirma():
    contexto = EnMemoria()
    memoria = Memoria(Gateway(), contexto)
    correr(memoria.vueltas(sobre()))
    correr(memoria.apuntar_persona(sobre()))
    publicado = respondido()

    resultado = correr(Respondido(memoria)(Mensaje(publicado.model_dump_json().encode(),
                                                    f"motor.respuesta.3.{BOT}.{CONTACTO}")))

    assert resultado is Resultado.CONFIRMADO and contexto.apuntes()[-1].vuelta.de == "bot"


@pytest.mark.parametrize("aviso,olvido", [
    ({"entity": "contact", "botId": str(BOT), "contactId": str(CONTACTO)}, (CLIENTE, BOT, CONTACTO)),
    ({"entity": "bot", "botId": str(BOT)}, (CLIENTE, BOT, None)),
    ({"entity": "client"}, (CLIENTE, None, None)),
], ids=["contacto", "bot", "cliente"])
def test_lo_que_suprime_el_gateway_se_borra_del_contexto(aviso, olvido):
    contexto = EnMemoria()
    datos = {"schemaVersion": 2, "uuid": str(uuid4()), "clientId": str(CLIENTE),
             "erasedAt": AHORA.isoformat(), **aviso}

    assert correr(Suprimido(contexto)(Mensaje(json.dumps(datos).encode()))) is Resultado.CONFIRMADO
    assert contexto.olvidos == [olvido]


@pytest.mark.parametrize("roto", [b"no", b'{"schemaVersion": 2, "entity": "contact", "clientId": "x"}',
                                  json.dumps({"schemaVersion": 2, "entity": "bot", "clientId": str(CLIENTE)}).encode()],
                         ids=["no JSON", "cliente que no es UUID", "bot sin botId"])
def test_un_aviso_roto_se_descarta_sin_borrar_nada(roto):
    contexto = EnMemoria()

    assert correr(Suprimido(contexto)(Mensaje(roto))) is Resultado.DESCARTADO and contexto.olvidos == []


def test_si_no_se_puede_borrar_se_intenta_otra_vez():
    datos = {"schemaVersion": 2, "entity": "client", "clientId": str(CLIENTE)}

    assert correr(Suprimido(EnMemoria(caido=True))(Mensaje(json.dumps(datos).encode()))) is Resultado.EN_ESPERA


# --- El bucket y el cifrado -----------------------------------------------------------------------------------------

class Andvari:
    """Transit en memoria: cifra a un texto que lleva el cliente, y no abre lo de otro."""

    def __init__(self, caido=False):
        self.caido, self.cifrados = caido, []

    def encrypt(self, key, client_id, plaintext):
        if self.caido:
            raise KeyStoreUnavailable("ConnectionError")
        self.cifrados.append(plaintext)
        return f"vault:v1:{client_id}:{plaintext.hex()}"

    def decrypt(self, key, client_id, ciphertext):
        _, _, dueno, cuerpo = ciphertext.split(":", 3)
        if dueno != str(client_id):
            raise KeyStoreUnavailable("decrypt refused: 400")
        return bytes.fromhex(cuerpo)


class Kv:
    def __init__(self):
        self.valores = {}

    async def get(self, nombre):
        import nats.js.errors
        if nombre not in self.valores:
            raise nats.js.errors.KeyNotFoundError()
        return SimpleNamespace(value=self.valores[nombre][0], revision=self.valores[nombre][1])

    async def create(self, nombre, valor):
        import nats.js.errors
        if nombre in self.valores:
            raise nats.js.errors.KeyWrongLastSequenceError()
        self.valores[nombre] = (valor, 1)
        return 1

    async def update(self, nombre, valor, last):
        import nats.js.errors
        if self.valores.get(nombre, (None, 0))[1] != last:
            raise nats.js.errors.KeyWrongLastSequenceError()
        self.valores[nombre] = (valor, last + 1)
        return last + 1


class JetStream:
    def __init__(self):
        self.kv, self.purgas = Kv(), []

    async def key_value(self, bucket):
        return self.kv

    async def purge_stream(self, nombre, subject=None):
        self.purgas.append((nombre, subject))


def test_el_bucket_guarda_lo_cifrado_y_nada_del_texto_en_claro():
    js, andvari = JetStream(), Andvari()
    contexto = ContextoEnBifrost(js, andvari)
    apuntes = [Apunte(vuelta=HISTORIAL[0], mensaje="wamid.A")]

    revision = correr(contexto.guardar(CLIENTE, BOT, CONTACTO, apuntes, 0))

    guardado = js.kv.valores[clave(CLIENTE, BOT, CONTACTO)][0]
    assert revision == 1 and guardado.startswith(b"vault:v1:") and b"hola" not in guardado
    assert correr(contexto.leer(CLIENTE, BOT, CONTACTO)).apuntes == apuntes
    assert clave(CLIENTE, BOT, CONTACTO) == f"{CLIENTE}.{BOT}.{CONTACTO}"


def test_escribir_sobre_una_revision_vieja_es_un_choque():
    contexto = ContextoEnBifrost(JetStream(), Andvari())
    correr(contexto.guardar(CLIENTE, BOT, CONTACTO, [], 0))

    with pytest.raises(Choque):
        correr(contexto.guardar(CLIENTE, BOT, CONTACTO, [], 0))


def test_sin_andvari_el_contexto_no_esta_disponible():
    with pytest.raises(ContextoNoDisponible):
        correr(ContextoEnBifrost(JetStream(), Andvari(caido=True)).guardar(CLIENTE, BOT, CONTACTO, [], 0))


def test_lo_de_otro_cliente_no_se_abre():
    js = JetStream()
    contexto = ContextoEnBifrost(js, Andvari())
    correr(contexto.guardar(CLIENTE, BOT, CONTACTO, [Apunte(vuelta=HISTORIAL[0], mensaje="x")], 0))
    otro = uuid4()
    js.kv.valores[clave(otro, BOT, CONTACTO)] = js.kv.valores[clave(CLIENTE, BOT, CONTACTO)]

    with pytest.raises(ContextoNoDisponible):
        correr(contexto.leer(otro, BOT, CONTACTO))


@pytest.mark.parametrize("bot,contacto,sujeto", [
    (BOT, CONTACTO, f"$KV.contexto.{CLIENTE}.{BOT}.{CONTACTO}"),
    (BOT, None, f"$KV.contexto.{CLIENTE}.{BOT}.>"),
    (None, None, f"$KV.contexto.{CLIENTE}.>"),
], ids=["contacto", "bot", "cliente"])
def test_olvidar_purga_por_sujeto_en_una_peticion(bot, contacto, sujeto):
    js = JetStream()

    correr(ContextoEnBifrost(js, Andvari()).olvidar(CLIENTE, bot, contacto))

    assert js.purgas == [("KV_contexto", sujeto)]


def test_un_valor_que_no_se_entiende_se_lee_vacio_con_su_revision():
    js = JetStream()
    contexto = ContextoEnBifrost(js, Andvari())
    js.kv.valores[clave(CLIENTE, BOT, CONTACTO)] = (f"vault:v1:{CLIENTE}:{b'no json'.hex()}".encode(), 5)

    leido = correr(contexto.leer(CLIENTE, BOT, CONTACTO))

    assert (leido.apuntes, leido.revision, leido.completa) == ([], 5, False)


def _cifrado(datos: dict) -> bytes:
    return f"vault:v1:{CLIENTE}:{json.dumps(datos).encode().hex()}".encode()


APUNTE_GUARDADO = {"de": "persona", "texto": "hola", "adjuntos": [], "enviadaEn": AHORA.isoformat(),
                   "mensaje": "wamid.A"}


@pytest.mark.parametrize("version,apuntes,completa", [
    (2, [], True),
    (2, [APUNTE_GUARDADO], True),
    # La 0.10.0 no apuntaba sobre un contexto vacío: uno vacío de entonces no tiene lo que vino después.
    (1, [], False),
    (1, [APUNTE_GUARDADO], True),
], ids=["vacío del formato 2", "con vueltas del formato 2", "vacío del formato 1", "con vueltas del formato 1"])
def test_cuando_esta_completo_lo_que_se_lee(version, apuntes, completa):
    js = JetStream()
    js.kv.valores[clave(CLIENTE, BOT, CONTACTO)] = (_cifrado({"v": version, "apuntes": apuntes}), 3)

    leido = correr(ContextoEnBifrost(js, Andvari()).leer(CLIENTE, BOT, CONTACTO))

    assert (len(leido.apuntes), leido.completa) == (len(apuntes), completa)


def test_se_escribe_el_formato_2():
    andvari = Andvari()

    correr(ContextoEnBifrost(JetStream(), andvari).guardar(CLIENTE, BOT, CONTACTO, [], 0))

    assert json.loads(andvari.cifrados[-1]) == {"v": 2, "apuntes": []}


def test_antes_de_un_mensaje_nuevo_estan_todas():
    conversacion = Conversacion(apuntes=[Apunte(vuelta=vuelta, mensaje="") for vuelta in HISTORIAL], revision=1)

    assert antes_de(conversacion, sobre()) == HISTORIAL


def test_se_guardan_las_diez_ultimas_de_la_persona_y_lo_que_vino_despues():
    apuntes = []
    for n in range(15):
        apuntes += [Apunte(vuelta=Vuelta(de="persona", texto=f"p{n}", enviadaEn=AHORA), mensaje=f"wamid.{n}"),
                    Apunte(vuelta=Vuelta(de="bot", texto=f"b{n}", enviadaEn=AHORA), mensaje=f"wamid.{n}")]

    recortados = recortar(apuntes)

    assert [a.vuelta.texto for a in recortados][:2] == [f"p{15 - PERSONAS}", f"b{15 - PERSONAS}"]
    assert len(recortados) == 2 * PERSONAS
