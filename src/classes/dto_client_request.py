# -*- coding: utf-8 -*-
from pydantic import BaseModel, ConfigDict, Field

from enums import Worker

PUBLIC_KEY_EXAMPLE = {
    "kty": "EC",
    "crv": "P-256",
    "x": "f83OJ3D2xF1Bg8vub9tLe1gHMzV76e8Tus9uPHvRVEU",
    "y": "x_FEzRu9m36HLN_tue659LNpXW6pCyStikYjKIWI5a0"
}


class SigningKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The public part only: a P-256 key as a JWK. The private part never leaves the client.
    signingKey: dict = Field(examples=[PUBLIC_KEY_EXAMPLE])


class OwnedKeyRequest(SigningKeyRequest):
    # A JWT signed by the new key for the token of this same request (auth.client.key_proof): whoever adds a key
    # proves they hold its private part.
    proof: str = Field(min_length=1, max_length=8192)


class ServiceKeyRequest(SigningKeyRequest):
    # Which worker of the response engine signs with it: that is what decides the routes it reaches (ADR-0022).
    worker: Worker = Field(examples=[Worker.NOTIFIER])


class ClientRequest(SigningKeyRequest):
    name: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._-]*$", examples=["acme"])
