# -*- coding: utf-8 -*-
"""
    La Cloud API de WhatsApp para enviar un texto:

        POST {graph}/{versión}/{phone_number_id}/messages
        {"messaging_product": "whatsapp", "recipient_type": "individual", "to": <número>, "type": "text",
         "text": {"body": <texto>, "preview_url": false}}

    Solo texto, sin vista previa de enlaces (ADR-0009, punto 5), y sin citar el mensaje de la persona (decisión de
    Jeremi del 2026-10-20). Y una plantilla aprobada, para lo que el cliente envía por iniciativa propia (ADR-0031 del
    gateway), con sus valores como parámetros de texto del cuerpo:

        {"messaging_product": "whatsapp", "recipient_type": "individual", "to": <número>, "type": "template",
         "template": {"name": <nombre>, "language": {"code": <idioma>},
                      "components": [{"type": "body",
                                      "parameters": [{"type": "text", "text": <valor>}, ...]}]}}

    Fuera de la ventana, un texto da el 131047; una plantilla en pausa, el 132015; desactivada, el 132016; con otros
    valores, el 132000; y el límite de marketing por persona, el 131049, que no se arregla antes de 24 horas. Ninguno
    está entre los que se reintentan (developers.facebook.com, «Error codes», consultada el 2026-10-29).

    Lo que hace con cada error lo dice el `code` de Meta, como pide su documentación de errores
    (developers.facebook.com, consultada el 2026-10-20): los que se arreglan solos se reintentan, el del token se
    reintenta con uno nuevo, y los demás no irán nunca así.

    Y para marcar como leído un mensaje de la persona, con «escribiendo…» o sin él (ADR-0011; developers.facebook.com,
    «Typing indicators», consultada el 2026-10-21), a la misma ruta:

        {"messaging_product": "whatsapp", "status": "read", "message_id": <wamid>,
         "typing_indicator": {"type": "text"}}

    Ni el log ni las excepciones llevan el texto, el número ni el token. Del error de Meta se guarda el código y,
    detrás, lo que Meta explica: su título o su `message`, sus `details` y el `fbtrace_id`, que pide su soporte. Su
    `message` y sus `details` pueden llevar el número, así que antes se tapan las cifras de cinco o más, los `wamid`
    (llevan el número dentro), los correos y las cadenas largas que podrían ser un token. Va en una línea y con 300
    caracteres como mucho. Un código que Meta no documenta, como el 131058 del 2026-10-30, se entiende así por el log.
"""
import asyncio
import re

import requests

from src.domain.ports.envio import CanalDeMeta, EnvioFallido, EnvioRechazado, TokenRechazado

GRAPH = "https://graph.facebook.com"
VERSION = "v26.0"   # la más nueva el 2026-10-20 (julio de 2026); Meta retira cada versión a los dos años
TIMEOUT = 10        # segundos

# Los que, según Meta, se arreglan solos o esperando.
REINTENTABLES = {1, 2, 4, 130429, 131000, 131016, 131056, 80007}
# Los del token: con uno nuevo del gateway puede ir. 10 y 131005 son permisos, que se arreglan en Meta, no aquí,
# pero dan tiempo a que el operador lo vea mientras Bifröst reintenta.
DEL_TOKEN = {0, 190}
PERMISOS = {10, 131005}

# Lo que se tapa de la explicación de Meta, en este orden: el código que ya va delante, los wamid, los correos, lo que
# podría ser un token y las cifras de cinco o más, que podrían ser el número.
TAPAR = [(re.compile(r"^\(#\d+\)\s*"), ""), (re.compile(r"wamid\.[\w=+/-]+"), "wamid.…"),
         (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "…@…"), (re.compile(r"[A-Za-z0-9_-]{32,}"), "…"),
         (re.compile(r"\d{5,}"), "…"), (re.compile(r"[\s\x00-\x1f\x7f]+"), " ")]
EXPLICACION = 300  # caracteres


class WhatsAppCloud(CanalDeMeta):
    """La Cloud API de WhatsApp."""

    def __init__(self, url: str = GRAPH, version: str = VERSION, timeout: float = TIMEOUT):
        self._url = url.rstrip("/")
        self._version = version
        self._timeout = timeout

    def _post(self, token: str, cuenta: str, cuerpo: dict) -> requests.Response:
        try:
            return requests.post(f"{self._url}/{self._version}/{cuenta}/messages", json=cuerpo,
                                 headers={"Authorization": f"Bearer {token}"}, timeout=self._timeout,
                                 allow_redirects=False)
        except (requests.RequestException, OSError) as e:
            # Solo el tipo: el texto de estas excepciones lleva la dirección y más.
            raise EnvioFallido(type(e).__name__) from None

    def _enviar(self, token: str, cuenta: str, destinatario: str, texto: str) -> str:
        return self._mandar(token, cuenta, {"messaging_product": "whatsapp", "recipient_type": "individual",
                                            "to": destinatario, "type": "text",
                                            "text": {"body": texto, "preview_url": False}})

    def _plantilla(self, token: str, cuenta: str, destinatario: str, nombre: str, idioma: str,
                   valores: list[str]) -> str:
        plantilla = {"name": nombre, "language": {"code": idioma}}
        if valores:
            plantilla["components"] = [{"type": "body",
                                        "parameters": [{"type": "text", "text": valor} for valor in valores]}]
        return self._mandar(token, cuenta, {"messaging_product": "whatsapp", "recipient_type": "individual",
                                            "to": destinatario, "type": "template", "template": plantilla})

    def _mandar(self, token: str, cuenta: str, cuerpo: dict) -> str:
        respuesta = self._post(token, cuenta, cuerpo)
        if respuesta.status_code == 200:
            try:
                return respuesta.json()["messages"][0]["id"]
            except (ValueError, KeyError, IndexError, TypeError):
                # Meta dijo que sí sin decir con qué id: no se reintenta, que llegaría dos veces.
                raise EnvioRechazado("aceptado sin id") from None
        raise self._fallo(respuesta)

    def _marcar(self, token: str, cuenta: str, message_id: str, escribiendo: bool) -> None:
        cuerpo = {"messaging_product": "whatsapp", "status": "read", "message_id": message_id}
        if escribiendo:
            cuerpo["typing_indicator"] = {"type": "text"}
        respuesta = self._post(token, cuenta, cuerpo)
        if respuesta.status_code == 200:
            return
        raise self._fallo(respuesta)

    def _fallo(self, respuesta: requests.Response) -> Exception:
        """Qué es un no de Meta, por su código."""
        codigo = self._codigo(respuesta)
        explicacion = self._explicacion(respuesta)
        if codigo in DEL_TOKEN:
            return TokenRechazado(f"Meta {codigo}{explicacion}")
        motivo = f"Meta {codigo if codigo is not None else respuesta.status_code}{explicacion}"
        if self._se_reintenta(codigo, respuesta.status_code):
            return EnvioFallido(motivo)
        return EnvioRechazado(motivo)

    @staticmethod
    def _se_reintenta(codigo: int | None, estado: int) -> bool:
        """Si puede ir más tarde. Sin código de Meta, lo dice el estado HTTP: saturado o caído."""
        if codigo is None:
            return estado >= 500 or estado == 429
        return codigo in REINTENTABLES or codigo in PERMISOS

    @staticmethod
    def _codigo(respuesta: requests.Response) -> int | None:
        try:
            codigo = respuesta.json()["error"]["code"]
        except (ValueError, KeyError, TypeError):
            return None
        return codigo if isinstance(codigo, int) and not isinstance(codigo, bool) else None

    @staticmethod
    def _explicacion(respuesta: requests.Response) -> str:
        """Lo que Meta explica de su error, tapado (ver el docstring del módulo), o nada."""
        try:
            error = respuesta.json()["error"]
            datos = error.get("error_data") or {}
            piezas = [error.get("error_user_title") or error.get("message"),
                      datos.get("details") if isinstance(datos, dict) else None]
            traza = error.get("fbtrace_id")
        except (ValueError, KeyError, TypeError, AttributeError):
            return ""
        texto = " · ".join(str(pieza) for pieza in piezas if isinstance(pieza, str) and pieza.strip())
        for patron, en_su_lugar in TAPAR:
            texto = patron.sub(en_su_lugar, texto)
        texto = texto.strip()[:EXPLICACION].rstrip()
        if isinstance(traza, str) and re.fullmatch(r"[\w-]{1,64}", traza):
            texto = f"{texto} · fbtrace_id {traza}" if texto else f"fbtrace_id {traza}"
        return f" ({texto})" if texto else ""

    async def enviar_texto(self, token: str, cuenta: str, destinatario: str, texto: str) -> str:
        return await asyncio.to_thread(self._enviar, token, cuenta, destinatario, texto)

    async def enviar_plantilla(self, token: str, cuenta: str, destinatario: str, nombre: str, idioma: str,
                               valores: list[str]) -> str:
        return await asyncio.to_thread(self._plantilla, token, cuenta, destinatario, nombre, idioma, valores)

    async def marcar_leido(self, token: str, cuenta: str, message_id: str, escribiendo: bool) -> None:
        await asyncio.to_thread(self._marcar, token, cuenta, message_id, escribiendo)
