# -*- coding: utf-8 -*-
"""
    Lo que el notificador ya envió, en el bucket KV `enviados` de Bifröst (decisión de Jeremi del 2026-10-20). Así,
    si Meta aceptó una respuesta y algo falla antes de confirmar el mensaje (publicar en RESPUESTAS, un reinicio),
    Bifröst lo entrega otra vez y no se envía dos veces: se publica lo que ya se envió.

        clave   <botId>.<SHA-256 del id de Meta del mensaje que se responde>
        valor   {"messageId": <el id que Meta dio a la respuesta>, "sentAt": <cuándo>}

    La clave no lleva el id de Meta tal cual, que en WhatsApp lleva el número dentro. El bucket caduca a las 24 horas,
    como el stream MOTOR: pasado ese tiempo Bifröst ya no entrega otra vez ese mensaje. Lo crea el aprovisionamiento
    de Bifröst, y el usuario del notificador solo lee y escribe sus claves.
"""
import hashlib
import json
from datetime import datetime
from uuid import UUID

import nats.errors
import nats.js.errors

from src.domain.ports.envio import Envio, Enviados, RegistroNoDisponible

BUCKET = "enviados"


def clave(bot_id: UUID, message_id: str) -> str:
    """La clave de una respuesta en el bucket: el bot y el SHA-256 del id del mensaje que responde."""
    return f"{bot_id}.{hashlib.sha256(message_id.encode('utf-8')).hexdigest()}"


class EnviadosEnBifrost(Enviados):
    """El bucket `enviados`, con la conexión del worker."""

    def __init__(self, jetstream, bucket: str = BUCKET):
        self._jetstream = jetstream
        self._bucket = bucket
        self._kv = None

    async def _abrir(self):
        if self._kv is None:
            try:
                self._kv = await self._jetstream.key_value(self._bucket)
            except (nats.errors.Error, OSError, TimeoutError) as e:
                raise RegistroNoDisponible(type(e).__name__) from None
        return self._kv

    async def enviado(self, bot_id: UUID, message_id: str) -> Envio | None:
        kv = await self._abrir()
        try:
            entrada = await kv.get(clave(bot_id, message_id))
        except nats.js.errors.KeyNotFoundError:
            # También cubre KeyDeletedError, su subclase.
            return None
        except (nats.errors.Error, OSError, TimeoutError) as e:
            raise RegistroNoDisponible(type(e).__name__) from None
        try:
            datos = json.loads(entrada.value)
            return Envio(message_id=datos["messageId"], sent_at=datetime.fromisoformat(datos["sentAt"]))
        except (ValueError, KeyError, TypeError):
            raise RegistroNoDisponible("valor inesperado") from None

    async def apuntar(self, bot_id: UUID, message_id: str, envio: Envio) -> None:
        kv = await self._abrir()
        valor = json.dumps({"messageId": envio.message_id, "sentAt": envio.sent_at.isoformat()}).encode("utf-8")
        try:
            await kv.put(clave(bot_id, message_id), valor)
        except (nats.errors.Error, OSError, TimeoutError) as e:
            raise RegistroNoDisponible(type(e).__name__) from None
