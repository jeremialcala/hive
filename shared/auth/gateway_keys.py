# -*- coding: utf-8 -*-
"""
    The gateway's own key pairs, in a JWK Set file outside the repository and the image:

    * **to decrypt** (RSA, `use: enc`): clients encrypt their assertions to the public half, so only the gateway reads
      them. Its public half is what GET /gateway/keys hands out;
    * **to sign** (P-256, `use: sig`): the gateway signs the access tokens it issues with it (ADR-0025). Only the
      gateway checks them, so the public half is not handed out.

    One key of each normally, two while rotating. The newest signing key signs; every one of them verifies.
"""
import json
import os

from jwcrypto import jwk
from jwcrypto.common import base64url_decode

MIN_BITS = 2048
NEW_KEY_BITS = 3072
ALGORITHM = "RSA-OAEP-256"
SIGNING_ALGORITHM = "ES256"


def new_key() -> dict:
    """A new private key to decrypt, as a JWK. Its kid is its thumbprint, so two keys never share one."""
    key = jwk.JWK.generate(kty="RSA", size=NEW_KEY_BITS)
    return {**json.loads(key.export_private()), "kid": key.thumbprint(), "use": "enc", "alg": ALGORITHM}


def new_signing_key() -> dict:
    """A new private P-256 key to sign the access tokens, as a JWK, with its thumbprint as kid."""
    key = jwk.JWK.generate(kty="EC", crv="P-256")
    return {**json.loads(key.export_private()), "kid": key.thumbprint(), "use": "sig", "alg": SIGNING_ALGORITHM}


def public_part(private: dict) -> dict:
    return {name: private[name] for name in ("kty", "n", "e", "crv", "x", "y", "kid", "use", "alg")
            if name in private}


def read_file(path: str) -> list:
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)["keys"]


def write_file(path: str, keys: list) -> None:
    """Owner-only from the first byte, and replaced in one step so a reader never sees half a file."""
    temporary = f"{path}.new"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        json.dump({"keys": keys}, file)
    os.replace(temporary, path)


def is_signing(entry: dict) -> bool:
    return entry.get("use") == "sig"


class GatewayKeys:

    def __init__(self, keys: list):
        self._private, self._signing, encrypting = {}, {}, []
        for entry in keys:
            if not entry.get("kid") or "d" not in entry:
                raise ValueError("Every gateway key must be a private JWK with a kid")
            if is_signing(entry):
                if entry.get("kty") != "EC" or entry.get("crv") != "P-256":
                    raise ValueError("A gateway signing key must be an EC key on the P-256 curve")
                self._signing[entry["kid"]] = jwk.JWK(**entry)
            else:
                if entry.get("kty") != "RSA" or len(base64url_decode(entry["n"])) * 8 < MIN_BITS:
                    raise ValueError("A gateway key to decrypt must be RSA, of at least 2048 bits")
                self._private[entry["kid"]] = jwk.JWK(**entry)
                encrypting.append(entry)
        if not self._private:
            raise ValueError("The gateway has no key to decrypt: clients could not encrypt their assertions to it")
        if not self._signing:
            raise ValueError("The gateway has no signing key: it could not issue access tokens "
                             "(python -m ops.gateway_key generate-signing <file>)")
        self._public = [public_part(entry) for entry in encrypting]
        # The last one in the file is the newest.
        self._signer = list(self._signing)[-1]

    @classmethod
    def load(cls, path: str) -> "GatewayKeys":
        return cls(read_file(path))

    def private(self, kid) -> jwk.JWK | None:
        """The key that decrypts what was encrypted to `kid`, if it is one of the gateway's."""
        return self._private.get(kid) if isinstance(kid, str) else None

    def signer(self) -> tuple[str, jwk.JWK]:
        """The kid and the key the gateway signs its access tokens with: the newest signing key."""
        return self._signer, self._signing[self._signer]

    def verifier(self, kid) -> jwk.JWK | None:
        """The signing key with that kid, to check an access token; None if the gateway has no such key."""
        return self._signing.get(kid) if isinstance(kid, str) else None

    def public_set(self) -> dict:
        """What a client needs to encrypt an assertion: safe to hand out. The signing keys are not in it."""
        return {"keys": self._public}
