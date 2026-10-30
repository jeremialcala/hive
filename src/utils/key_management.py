# -*- coding: utf-8 -*-

from jwcrypto import jwk
from jwcrypto.common import json_decode

from classes import Settings

__all__ = ["get_private_key_pem", "get_private_key", "get_public_key"]

settings = Settings()


def get_private_key_pem(_uuid: str):
    with open(settings.private_key_filename.format(UUID=_uuid), "r") as key:
        pem_data = key.read()
        key.close()

    return pem_data


def get_private_key(_uuid):
    return jwk.JWK.from_pem(data=get_private_key_pem(_uuid).encode("utf-8"))


def get_public_key(_uuid):
    public_key = jwk.JWK()
    public_key.import_key(**json_decode(get_private_key(_uuid).export_public()))
    return public_key
