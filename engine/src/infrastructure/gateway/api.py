# -*- coding: utf-8 -*-
"""
    La API de gestión del gateway para un worker del motor, con OAuth 2.0 (ADR-0025 del gateway). Por cada cliente
    pide un token de acceso a `/oauth/token`, con una aserción firmada con la clave que ese worker tiene para el
    cliente y cifrada para el gateway (`shared.auth.client`, docs/03-implementation/autenticacion.md del gateway), y
    lo usa en cada llamada hasta un minuto antes de que caduque. Qué rutas alcanza lo decide el gateway por el worker
    de la clave: al coordinador le deja las reglas, las herramientas y las vueltas; al notificador, los tokens de Meta.

    El token va atado por DPoP a esa misma clave (ADR-0033 del gateway): cada petición, también la del token, lleva
    una prueba nueva que la clave firma para su método y su URL. Un token robado no sirve sin la clave.

    El token de Meta de cada bot se recuerda unos minutos, para no pedirlo en cada mensaje: cada entrega queda en el
    log del gateway. Si Meta lo rechaza, el notificador lo olvida y se pide otra vez.

    La persona del bot con sus reglas también se recuerda unos minutos (decisión de Jeremi del 2026-10-21): en
    midgard, las dos lecturas del coordinador al gateway eran lo más lento de un mensaje después del LLM. Cada cambio
    de la configuración del bot es una versión (RF26 del gateway), y el gateway avisa de ella por Bifröst (ADR-0035
    del gateway): con el aviso, lo recordado de una versión anterior se olvida y se lee otra vez. Sin el aviso, el
    cambio tarda esos minutos en llegar. Las vueltas no se recuerdan: cambian con cada mensaje.

    La clave privada de cada cliente se lee de Andvari la primera vez que hace falta y se guarda en memoria. Si el
    gateway rechaza la aserción (401), se vuelve a leer una vez: puede haber cambiado. Si rechaza el token de acceso,
    se pide otro, una vez.

    Nada de lo que se envía ni de lo que vuelve llega al log ni a una excepción: solo el tipo de fallo y el código.
"""
import asyncio
import json
import time
from datetime import datetime, timezone
from uuid import UUID

import requests
from pydantic import ValidationError

from shared.auth.client import TOKEN_PATH, build_assertion, dpop_proof, token_request
from shared.engine_key_store import EngineKeyStore, KeyStoreUnavailable
from src.domain.models.sobre import MAX_VUELTAS, Persona, Vuelta
from src.domain.ports.envio import TokensDelBot
from src.domain.ports.fuente_del_bot import FuenteDelBot, FuenteNoDisponible, NoEncontrado

TIMEOUT = 5            # segundos por llamada
MARGEN_ACCESO = 60     # segundos antes de que caduque un token de acceso en que se pide otro
RECORDAR_TOKEN = 600   # segundos que se recuerda el token de Meta de un bot
RECORDAR_REGLAS = 300  # segundos que se recuerda la persona de un bot con sus reglas


def clave_publica_del_gateway(path: str) -> dict:
    """
        La clave a la que se cifran los tokens: la última del fichero, que es la más nueva mientras el gateway rota
        la suya (`python -m ops.gateway_key show`).
    """
    with open(path, "r", encoding="utf-8") as file:
        claves = json.load(file)["keys"]
    if not claves:
        raise ValueError("el fichero de claves del gateway no tiene ninguna")
    return claves[-1]


class ApiDelGateway(FuenteDelBot, TokensDelBot):
    """La API de gestión del gateway con la clave de un worker, para los clientes que haga falta."""

    def __init__(self, url: str, audience: str, gateway_key: dict, store: EngineKeyStore, worker: str,
                 ca_file: str | None = None, timeout: float = TIMEOUT):
        self._url = url.rstrip("/")
        self._audience = audience
        self._gateway_key = gateway_key
        self._store = store
        self._worker = worker
        self._verify = ca_file or True
        self._timeout = timeout
        self._claves: dict[UUID, dict] = {}
        # Por cliente: el token de acceso y hasta cuándo se usa.
        self._accesos: dict[UUID, tuple[str, float]] = {}
        self._tokens: dict[UUID, tuple[str, float]] = {}
        # Por cliente y bot: la persona de un bot solo vale para el cliente con cuya clave se leyó. Con la versión
        # de la configuración que se leyó, y la última de la que avisó el gateway.
        self._personas: dict[tuple[UUID, UUID], tuple[Persona, float, int]] = {}
        self._avisadas: dict[tuple[UUID, UUID], int] = {}

    def _clave(self, client_id: UUID, otra_vez: bool = False) -> dict:
        if otra_vez or client_id not in self._claves:
            try:
                clave = self._store.read(client_id, self._worker)
            except KeyStoreUnavailable as e:
                raise FuenteNoDisponible(f"Andvari: {e}") from None
            if clave is None:
                # El cliente no tiene claves del motor: las da de alta ops.engine_keys del gateway.
                raise FuenteNoDisponible("sin clave del worker para el cliente")
            self._claves[client_id] = clave
        return self._claves[client_id]

    def _acceso(self, client_id: UUID, otro: bool = False) -> str:
        """El token de acceso para el cliente: el que se tiene, si le queda más de un minuto, o uno nuevo."""
        recordado = self._accesos.get(client_id)
        if recordado and not otro and time.monotonic() < recordado[1]:
            return recordado[0]
        respuesta = self._pedir_acceso(client_id, self._clave(client_id))
        if respuesta.status_code == 401:
            # La clave puede haber cambiado en Andvari: se lee otra vez, una.
            respuesta = self._pedir_acceso(client_id, self._clave(client_id, otra_vez=True))
        if respuesta.status_code != 200:
            raise FuenteNoDisponible(f"el gateway no dio token de acceso: {respuesta.status_code}")
        try:
            datos = respuesta.json()
            token, dura = datos["access_token"], int(datos["expires_in"])
        except (ValueError, KeyError, TypeError):
            raise FuenteNoDisponible("token de acceso inesperado") from None
        if not isinstance(token, str) or not token:
            raise FuenteNoDisponible("token de acceso inesperado")
        self._accesos[client_id] = (token, time.monotonic() + dura - MARGEN_ACCESO)
        return token

    def _pedir_acceso(self, client_id: UUID, clave: dict) -> requests.Response:
        url = f"{self._url}{TOKEN_PATH}"
        try:
            asercion = build_assertion(clave, client_id, self._audience, self._gateway_key)
            prueba = dpop_proof(clave, "POST", url)
        except Exception as e:  # pylint: disable=broad-except
            # Una clave mal formada en Andvari: jwcrypto lo dice con varios tipos de error.
            raise FuenteNoDisponible(f"no se pudo firmar: {type(e).__name__}") from None
        try:
            return requests.post(url, data=token_request(asercion), headers={"DPoP": prueba}, timeout=self._timeout,
                                 verify=self._verify, allow_redirects=False)
        except (requests.RequestException, OSError) as e:
            # Solo el tipo: el texto de estas excepciones lleva la dirección y más.
            raise FuenteNoDisponible(type(e).__name__) from None

    def _get(self, client_id: UUID, path: str) -> dict:
        respuesta = self._llamar(path, self._acceso(client_id), self._clave(client_id))
        if respuesta.status_code == 401:
            # Caducó entre medias, o el gateway rotó su clave de firma: uno nuevo, una vez.
            respuesta = self._llamar(path, self._acceso(client_id, otro=True), self._clave(client_id))
        if respuesta.status_code == 404:
            raise NoEncontrado(path.rsplit("/", 1)[-1])
        if respuesta.status_code != 200:
            raise FuenteNoDisponible(f"el gateway respondió {respuesta.status_code}")
        try:
            return respuesta.json()["data"]
        except (ValueError, KeyError, TypeError):
            raise FuenteNoDisponible("respuesta inesperada") from None

    def _llamar(self, path: str, token: str, clave: dict) -> requests.Response:
        url = f"{self._url}{path}"
        try:
            prueba = dpop_proof(clave, "GET", url, token)
        except Exception as e:  # pylint: disable=broad-except
            raise FuenteNoDisponible(f"no se pudo firmar: {type(e).__name__}") from None
        try:
            return requests.get(url, headers={"Authorization": f"DPoP {token}", "DPoP": prueba},
                                timeout=self._timeout, verify=self._verify, allow_redirects=False)
        except (requests.RequestException, OSError) as e:
            # Solo el tipo: el texto de estas excepciones lleva la dirección y más.
            raise FuenteNoDisponible(type(e).__name__) from None

    def configuracion_cambiada(self, client_id: UUID, bot_id: UUID, version: int) -> None:
        """
            El gateway avisó de que la configuración del bot va por `version`: lo que se recuerda de una anterior ya no
            vale, y tampoco lo que se lea de una anterior mientras llega el aviso.
        """
        clave = (client_id, bot_id)
        self._avisadas[clave] = max(version, self._avisadas.get(clave, 0))
        recordada = self._personas.get(clave)
        if recordada and recordada[2] < self._avisadas[clave]:
            del self._personas[clave]

    async def persona(self, client_id: UUID, bot_id: UUID) -> Persona:
        clave = (client_id, bot_id)
        recordada = self._personas.get(clave)
        if recordada and time.monotonic() < recordada[1] and recordada[2] >= self._avisadas.get(clave, 0):
            return recordada[0]
        datos = await asyncio.to_thread(self._get, client_id, f"/chat/{bot_id}/rules")
        try:
            persona = Persona(nombre=datos["name"], rol=datos["role"], tono=datos["tone"],
                              descripcion=datos.get("description") or "",
                              reglas=[regla for regla in datos.get("rules") or [] if isinstance(regla, str)])
        except (KeyError, TypeError, AttributeError, ValidationError):
            raise FuenteNoDisponible("reglas inesperadas") from None
        # Un gateway de antes de las versiones no la dice: cuenta como 0, y cualquier aviso la deja vieja.
        version = datos.get("version")
        version = version if isinstance(version, int) and not isinstance(version, bool) else 0
        self._personas[clave] = (persona, time.monotonic() + RECORDAR_REGLAS, version)
        return persona

    async def vueltas(self, client_id: UUID, bot_id: UUID, contact_id: UUID, sin: str,
                      hasta: datetime) -> list[Vuelta]:
        datos = await asyncio.to_thread(self._get, client_id, f"/chat/{bot_id}/contacts/{contact_id}/turns")
        hasta = hasta if hasta.tzinfo else hasta.replace(tzinfo=timezone.utc)
        try:
            mensajes = datos["messages"]
            # El gateway da las últimas vueltas de ahora. Un mensaje atendido con retraso ya tiene detrás lo que la
            # persona escribió después, y lo que el bot le respondió: las vueltas terminan antes de él. La ingesta lo
            # guarda justo después de publicarlo, así que puede no estar todavía: entonces cuenta cuándo llegó.
            suyo = next((n for n, mensaje in enumerate(mensajes)
                         if mensaje["direction"] == "in" and mensaje.get("messageId") == sin), None)
            vueltas = [Vuelta(de="persona" if mensaje["direction"] == "in" else "bot",
                              texto=(mensaje.get("text") or "")[:4096],
                              adjuntos=[str(tipo) for tipo in mensaje.get("attachments") or []][:10],
                              enviadaEn=datetime.fromisoformat(mensaje["sentAt"]),
                              ) for mensaje in (mensajes if suyo is None else mensajes[:suyo])]
            if suyo is None:
                vueltas = [vuelta for vuelta in vueltas if vuelta.enviadaEn <= hasta]
        except (KeyError, TypeError, ValueError, AttributeError):
            # ValidationError es un ValueError. TypeError también cubre una fecha sin zona del gateway.
            raise FuenteNoDisponible("vueltas inesperadas") from None
        return vueltas[-MAX_VUELTAS:]

    async def token_de_whatsapp(self, client_id: UUID, bot_id: UUID) -> str:
        recordado = self._tokens.get(bot_id)
        if recordado and time.monotonic() < recordado[1]:
            return recordado[0]
        datos = await asyncio.to_thread(self._get, client_id, f"/chat/{bot_id}/tokens")
        token = datos.get("whatsappToken") if isinstance(datos, dict) else None
        if not isinstance(token, str) or not token:
            # Un bot sin token de WhatsApp no puede responder por WhatsApp hasta que su cliente lo guarde.
            raise NoEncontrado("token de WhatsApp")
        self._tokens[bot_id] = (token, time.monotonic() + RECORDAR_TOKEN)
        return token

    def olvidar(self, bot_id: UUID) -> None:
        self._tokens.pop(bot_id, None)
