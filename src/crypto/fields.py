# -*- coding: utf-8 -*-
"""
    What is done with a client's data key: encrypting single fields, and the blind index that lets a phone number
    be searched for without storing it in clear. Nothing here knows where the key comes from.
"""
import base64
import hashlib
import hmac
import os
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# What a stored ciphertext starts with. The number is the format, so it can change without guessing.
PREFIX = "v1:"
KEY_BYTES = 32
NONCE_BYTES = 12


class NotEncrypted(ValueError):
    """
        The stored value is not a ciphertext of this client and field: it is still in clear, it was written by
        hand, it was moved from another field or client, or it was tampered with. It is never returned as is.
    """


def normalize_number(number: str) -> str:
    """The same number written two ways must give the same index: only its digits count."""
    return "".join(character for character in str(number) if character.isdigit())


def normalize_email(email: str) -> str:
    """The same address written two ways must give the same index: without the spaces around it, and in lower case."""
    return str(email).strip().casefold()


class ClientCipher:

    def __init__(self, client_id: UUID, data_key: bytes):
        if len(data_key) != KEY_BYTES:
            raise ValueError("A data key is 32 bytes")
        self._client_id = client_id
        self._cipher = AESGCM(data_key)
        # The index never uses the data key itself: knowing an index key must not open what is encrypted.
        self._index_key = HKDF(algorithm=hashes.SHA256(), length=KEY_BYTES, salt=None,
                               info=b"blind-index").derive(data_key)

    def _bound_to(self, field: str) -> bytes:
        # Authenticated with the ciphertext, not stored: a value copied to another field or client does not open.
        return f"{self._client_id}:{field}".encode()

    def encrypt(self, field: str, value: str | None) -> str | None:
        if value is None:
            return None
        nonce = os.urandom(NONCE_BYTES)
        sealed = self._cipher.encrypt(nonce, str(value).encode(), self._bound_to(field))
        return PREFIX + base64.urlsafe_b64encode(nonce + sealed).decode()

    def decrypt(self, field: str, stored: str | None) -> str | None:
        if stored is None:
            return None
        if not isinstance(stored, str) or not stored.startswith(PREFIX):
            raise NotEncrypted(field)
        try:
            raw = base64.urlsafe_b64decode(stored[len(PREFIX):])
            return self._cipher.decrypt(raw[:NONCE_BYTES], raw[NONCE_BYTES:], self._bound_to(field)).decode()
        except (InvalidTag, ValueError):
            raise NotEncrypted(field) from None

    def blind_index(self, field: str, number: str, normalize=normalize_number) -> str:
        """
            Equal numbers of one client give equal indexes; the same number under another client gives another.
            `normalize` says when two values are the same one: a number by its digits, an email by normalize_email.
        """
        message = self._bound_to(field) + b":" + normalize(number).encode()
        return base64.urlsafe_b64encode(hmac.new(self._index_key, message, hashlib.sha256).digest()).decode()


def is_encrypted(stored) -> bool:
    return isinstance(stored, str) and stored.startswith(PREFIX)
