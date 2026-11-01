# -*- coding: utf-8 -*-
"""
    The sessions of the people in the portal (ADR-0029, ADR-0034, HIG-93), and the logins on their way, server side.
    The browser only holds a random id in a cookie; what it opens is here: the ID token of the person and their token
    of Hive.

    - **Sealed before they are stored**, with AES-256-GCM and a key of the portal, bound to the name they are stored
      under: whoever reads the bucket of Bifröst reads nothing, and cannot move one session into another's place.
    - **Stored under a hash of the id**, not the id: the cookie cannot be rebuilt from the bucket either.
    - **A session ends** 30 minutes after its last use, and 12 hours after it began, whatever happens. The bucket
      `sesiones` forgets everything after 12 hours on its own.
    - **A login on its way** (state, nonce, PKCE verifier) lasts 10 minutes and is good once.

    The last use is written at most once a minute: each write is an fsync on Bifröst's disk.

    A session is written again only if it is still the one that was read (compare-and-swap on its revision, HIG-153):
    a renewal of its token that finishes after the logout cannot bring it back, and two renewals at once keep one.
"""
import base64
import hashlib
import json
import os
import secrets
import time
from typing import Callable, Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

__all__ = ["Sealer", "MemoryStore", "KvStore", "Sessions", "IDLE", "LIFETIME", "LOGIN_FOR", "BUCKET"]

BUCKET = "sesiones"
IDLE = 30 * 60           # seconds without use
LIFETIME = 12 * 3600     # seconds at most, from the login
LOGIN_FOR = 10 * 60      # seconds a login on its way waits for Zitadel's answer
TOUCH_AFTER = 60         # seconds between two writes of the last use
NONCE_BYTES = 12


class Store(Protocol):
    """Where the sealed values live: the bucket of Bifröst, or memory."""
    async def get(self, key: str) -> bytes | None:
        """The value under that name, or None."""
    async def put(self, key: str, value: bytes) -> None:
        """Stores the value under that name."""
    async def delete(self, key: str) -> None:
        """Forgets the value under that name, if there is one."""
    async def read(self, key: str) -> tuple[bytes | None, int]:
        """The value under that name, or None, and its revision: what `swap` compares."""
    async def swap(self, key: str, value: bytes, revision: int) -> bool:
        """Stores the value only if the name is still at that revision, and not deleted since. Says whether it did."""


class Sealer:
    """AES-256-GCM with the portal's key, each value bound to the name it is stored under."""

    def __init__(self, key: bytes):
        if len(key) != 32:
            raise ValueError("the key of the sessions is 32 bytes")
        self._aead = AESGCM(key)

    @classmethod
    def from_file(cls, path: str) -> "Sealer":
        """The sealer of the key in that file: 32 bytes, in base64."""
        with open(path, "rb") as file:
            return cls(base64.b64decode(file.read().strip(), validate=True))

    def seal(self, name: str, payload: dict) -> bytes:
        """The payload, sealed and bound to its name."""
        nonce = os.urandom(NONCE_BYTES)
        return nonce + self._aead.encrypt(nonce, json.dumps(payload).encode(), name.encode())

    def open(self, name: str, sealed: bytes) -> dict | None:
        """What was sealed under that name, or None: anything else, tampered with, moved or of another key."""
        try:
            opened = json.loads(self._aead.decrypt(sealed[:NONCE_BYTES], sealed[NONCE_BYTES:], name.encode()))
        except Exception:  # pylint: disable=broad-exception-caught
            return None
        return opened if isinstance(opened, dict) else None


class MemoryStore:
    """In this process: for the tests and for development, without Bifröst."""

    def __init__(self):
        self.values: dict[str, bytes] = {}
        # Each write and each deletion is a new revision, as in the bucket.
        self.revisions: dict[str, int] = {}
        self._last = 0

    def _next(self, key: str) -> None:
        self._last += 1
        self.revisions[key] = self._last

    async def get(self, key: str) -> bytes | None:
        """The value under that name, or None."""
        return self.values.get(key)

    async def put(self, key: str, value: bytes) -> None:
        """Stores the value under that name."""
        self.values[key] = value
        self._next(key)

    async def delete(self, key: str) -> None:
        """Forgets the value under that name, if there is one."""
        self.values.pop(key, None)
        self._next(key)

    async def read(self, key: str) -> tuple[bytes | None, int]:
        """The value under that name, or None, and its revision."""
        return self.values.get(key), self.revisions.get(key, 0)

    async def swap(self, key: str, value: bytes, revision: int) -> bool:
        """Stores the value only if the name is still at that revision, and not deleted since."""
        if key not in self.values or self.revisions.get(key) != revision:
            return False
        await self.put(key, value)
        return True


class KvStore:
    """The bucket `sesiones` of Bifröst, with the portal's own user, which reaches nothing else."""

    def __init__(self, url: str, user: str | None, password: str | None, ca: str | None,
                 inbox_prefix: str | None):
        self._options = {"servers": [url], "user": user, "password": password, "name": "hive-portal",
                         "inbox_prefix": inbox_prefix or "_INBOX", "max_reconnect_attempts": -1}
        self._ca = ca
        self._kv = None

    async def _bucket(self):
        if self._kv is None:
            import ssl  # pylint: disable=import-outside-toplevel
            import nats  # pylint: disable=import-outside-toplevel
            tls = ssl.create_default_context(cafile=self._ca or None) if self._options["servers"][0].startswith(
                "tls://") else None
            connection = await nats.connect(tls=tls, **self._options)
            self._kv = await connection.jetstream().key_value(BUCKET)
        return self._kv

    async def get(self, key: str) -> bytes | None:
        """The value under that name, or None."""
        from nats.js.errors import KeyNotFoundError  # pylint: disable=import-outside-toplevel
        try:
            entry = await (await self._bucket()).get(key)
        except KeyNotFoundError:
            return None
        return entry.value or None

    async def put(self, key: str, value: bytes) -> None:
        """Stores the value under that name."""
        await (await self._bucket()).put(key, value)

    async def delete(self, key: str) -> None:
        """Forgets the value under that name, if there is one."""
        from nats.js.errors import KeyNotFoundError  # pylint: disable=import-outside-toplevel
        try:
            await (await self._bucket()).delete(key)
        except KeyNotFoundError:
            # Already gone: a session that ended, or that the bucket forgot after its 12 hours.
            return

    async def read(self, key: str) -> tuple[bytes | None, int]:
        """The value under that name, or None, and its revision in the bucket."""
        from nats.js.errors import KeyNotFoundError  # pylint: disable=import-outside-toplevel
        try:
            entry = await (await self._bucket()).get(key)
        except KeyNotFoundError:
            return None, 0
        return entry.value or None, entry.revision or 0

    async def swap(self, key: str, value: bytes, revision: int) -> bool:
        """
            Stores the value only if the name is still at that revision. A deletion is a revision of its own in the
            bucket: a value deleted since is not stored again.
        """
        from nats.js.errors import KeyWrongLastSequenceError  # pylint: disable=import-outside-toplevel
        if not revision:
            return False
        try:
            await (await self._bucket()).update(key, value, last=revision)
        except KeyWrongLastSequenceError:
            return False
        return True


def _name(kind: str, raw: str) -> str:
    return f"{kind}.{hashlib.sha256(raw.encode()).hexdigest()}"


# The revision a session was read at, for the compare-and-swap of its next write: in memory, never sealed.
REVISION = "_revision"


def _stored(session: dict) -> dict:
    return {key: value for key, value in session.items() if key != REVISION}


class Sessions:
    """The logins on their way and the sessions, sealed in their store."""
    def __init__(self, store: Store, sealer: Sealer, clock: Callable[[], float] = time.time):
        self._store, self._sealer, self._clock = store, sealer, clock

    # --- Logins on their way --------------------------------------------------------------------------------------

    async def start_login(self, login: dict) -> tuple[str, str]:
        """The state for Zitadel and the value of the browser's own cookie for this login, which must come back."""
        state, browser = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        name = _name("login", state)
        login = {**login, "browser": hashlib.sha256(browser.encode()).hexdigest(), "at": self._clock()}
        await self._store.put(name, self._sealer.seal(name, login))
        return state, browser

    async def finish_login(self, state: str, browser: str | None) -> dict | None:
        """The login of that state, once, from the browser that began it and within its time; None otherwise."""
        if not state or not browser:
            return None
        name = _name("login", state)
        sealed = await self._store.get(name)
        if sealed is None:
            return None
        await self._store.delete(name)
        login = self._sealer.open(name, sealed)
        if (login is None or self._clock() - login.get("at", 0) > LOGIN_FOR
                or not secrets.compare_digest(login.get("browser", ""), hashlib.sha256(browser.encode()).hexdigest())):
            return None
        return login

    # --- Sessions ---------------------------------------------------------------------------------------------------

    async def create(self, session: dict) -> str:
        """A new session, and the id its cookie carries."""
        session_id = secrets.token_urlsafe(32)
        now = self._clock()
        await self.save(session_id, {**session, "created": now, "seen": now})
        return session_id

    async def save(self, session_id: str, session: dict) -> None:
        """Stores a new session, sealed under its name. One already stored is written with `renewed`."""
        name = _name("session", session_id)
        await self._store.put(name, self._sealer.seal(name, _stored(session)))

    async def _swap(self, session_id: str, session: dict) -> bool:
        """Writes the session again only if it is still the one `load` read (its `_revision`)."""
        name = _name("session", session_id)
        return await self._store.swap(name, self._sealer.seal(name, _stored(session)), session.get(REVISION, 0))

    async def renewed(self, session_id: str, session: dict) -> dict | None:
        """
            The session with its new token, written only if it is still the one that was read (HIG-153). If the
            person logged out meanwhile it stays ended: None. If another request renewed it at the same time, its
            renewal is the one kept, and the session as it is now is what comes back.
        """
        await self._swap(session_id, session)
        return await self.load(session_id)

    async def load(self, session_id: str | None) -> dict | None:
        """
            The session of that id while it lasts, with its last use updated; None if there is none, or it ended. It
            carries the revision it was read at (`_revision`), which is never stored.
        """
        if not session_id:
            return None
        name = _name("session", session_id)
        sealed, revision = await self._store.read(name)
        session = self._sealer.open(name, sealed) if sealed is not None else None
        if session is None:
            return None
        now = self._clock()
        if now - session.get("seen", 0) > IDLE or now - session.get("created", 0) > LIFETIME:
            await self._store.delete(name)
            return None
        session[REVISION] = revision
        if now - session["seen"] > TOUCH_AFTER:
            session["seen"] = now
            # Only if nobody ended or renewed it since it was read: a lost last use is written at the next request.
            if await self._swap(session_id, session):
                _, session[REVISION] = await self._store.read(name)
        return session

    async def end(self, session_id: str | None) -> None:
        """Ends the session of that id, if there is one."""
        if session_id:
            await self._store.delete(_name("session", session_id))
