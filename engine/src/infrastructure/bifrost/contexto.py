# -*- coding: utf-8 -*-
"""
    El contexto de cada conversación en el bucket KV `contexto` de Bifröst (ADR-0017 del gateway, HIG-19):

        clave   <clientId>.<botId>.<contactId>       sin ningún dato personal
        valor   el texto cifrado por Andvari con la clave Transit `contexto`, derivada para el cliente

    Lo que se cifra es {"v": 1, "apuntes": [{"de", "texto", "adjuntos", "enviadaEn", "mensaje"}]}: las últimas
    vueltas. La clave de cifrado no sale de Andvari y solo la alcanza el rol del coordinador; Bifröst guarda el texto
    cifrado, que el usuario admin tampoco puede leer.

    Cada escritura renueva la caducidad de su clave: el bucket olvida una conversación 24 horas después de su último
    mensaje. Se escribe solo si la conversación sigue en la revisión que se leyó (compare-and-swap).

    La supresión de un contacto, un bot o un cliente purga sus claves por sujeto, en una sola petición.
"""
import asyncio
import json
from datetime import datetime
from uuid import UUID

import nats.errors
import nats.js.errors
from pydantic import ValidationError

from shared.engine_key_store import KeyStoreUnavailable
from src.domain.models.sobre import Vuelta
from src.domain.ports.contexto import Apunte, Choque, Contexto, ContextoNoDisponible, Conversacion

BUCKET = "contexto"
CLAVE_DE_CIFRADO = "contexto"
VERSION = 1
FALLOS_DE_BIFROST = (nats.errors.Error, OSError, TimeoutError)


def clave(client_id: UUID, bot_id: UUID, contact_id: UUID) -> str:
    """La clave de una conversación en el bucket: sus tres identificadores, sin ningún dato personal."""
    return f"{client_id}.{bot_id}.{contact_id}"


class ContextoEnBifrost(Contexto):
    """El bucket `contexto` con la conexión del coordinador, y Andvari para cifrar."""

    def __init__(self, jetstream, cifrado, bucket: str = BUCKET, clave_de_cifrado: str = CLAVE_DE_CIFRADO):
        self._jetstream = jetstream
        # Lo que cifra con Transit: EngineKeyStore (shared/engine_key_store.py), con el rol del coordinador.
        self._cifrado = cifrado
        self._bucket = bucket
        self._clave_de_cifrado = clave_de_cifrado
        self._kv = None

    async def _abrir(self):
        if self._kv is None:
            try:
                self._kv = await self._jetstream.key_value(self._bucket)
            except FALLOS_DE_BIFROST as e:
                raise ContextoNoDisponible(type(e).__name__) from None
        return self._kv

    async def leer(self, client_id: UUID, bot_id: UUID, contact_id: UUID) -> Conversacion | None:
        kv = await self._abrir()
        try:
            entrada = await kv.get(clave(client_id, bot_id, contact_id))
        except nats.js.errors.KeyNotFoundError:
            # También cubre KeyDeletedError, su subclase.
            return None
        except FALLOS_DE_BIFROST as e:
            raise ContextoNoDisponible(type(e).__name__) from None
        try:
            claro = await asyncio.to_thread(self._cifrado.decrypt, self._clave_de_cifrado, client_id,
                                            entrada.value.decode("ascii"))
        except KeyStoreUnavailable as e:
            raise ContextoNoDisponible(f"Andvari: {e}") from None
        except UnicodeDecodeError:
            return Conversacion(apuntes=[], revision=entrada.revision)
        try:
            datos = json.loads(claro)
            if datos.get("v") != VERSION:
                raise ValueError("otra versión")
            apuntes = [Apunte(vuelta=Vuelta(de=a["de"], texto=a["texto"], adjuntos=a.get("adjuntos") or [],
                                            enviadaEn=datetime.fromisoformat(a["enviadaEn"])),
                              mensaje=a["mensaje"]) for a in datos["apuntes"]]
        except (ValueError, KeyError, TypeError, AttributeError, ValidationError):
            # Lo que no se entiende no es contexto: vacío, con su revisión para que se pueda escribir encima.
            return Conversacion(apuntes=[], revision=entrada.revision)
        return Conversacion(apuntes=apuntes, revision=entrada.revision)

    async def guardar(self, client_id: UUID, bot_id: UUID, contact_id: UUID, apuntes: list[Apunte],
                      revision: int) -> int:
        datos = {"v": VERSION, "apuntes": [{"de": a.vuelta.de, "texto": a.vuelta.texto, "adjuntos": a.vuelta.adjuntos,
                                            "enviadaEn": a.vuelta.enviadaEn.isoformat(), "mensaje": a.mensaje}
                                           for a in apuntes]}
        try:
            cifrado = await asyncio.to_thread(self._cifrado.encrypt, self._clave_de_cifrado, client_id,
                                              json.dumps(datos, ensure_ascii=False).encode("utf-8"))
        except KeyStoreUnavailable as e:
            raise ContextoNoDisponible(f"Andvari: {e}") from None
        kv = await self._abrir()
        nombre = clave(client_id, bot_id, contact_id)
        try:
            if revision:
                return await kv.update(nombre, cifrado.encode("ascii"), last=revision)
            return await kv.create(nombre, cifrado.encode("ascii"))
        except nats.js.errors.KeyWrongLastSequenceError:
            raise Choque() from None
        except FALLOS_DE_BIFROST as e:
            raise ContextoNoDisponible(type(e).__name__) from None

    async def olvidar(self, client_id: UUID, bot_id: UUID | None = None, contact_id: UUID | None = None) -> None:
        if contact_id is not None and bot_id is not None:
            sujeto = f"$KV.{self._bucket}.{clave(client_id, bot_id, contact_id)}"
        elif bot_id is not None:
            sujeto = f"$KV.{self._bucket}.{client_id}.{bot_id}.>"
        else:
            sujeto = f"$KV.{self._bucket}.{client_id}.>"
        try:
            await self._jetstream.purge_stream(f"KV_{self._bucket}", subject=sujeto)
        except FALLOS_DE_BIFROST as e:
            raise ContextoNoDisponible(type(e).__name__) from None
