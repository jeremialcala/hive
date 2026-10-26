# -*- coding: utf-8 -*-
"""
    The gateway's key file, the one GATEWAY_KEYS_FILE points to. Run it on the machine where the key will live:
    the private key is written to the file, owner-only, and never printed. It needs neither settings nor database.

        python -m ops.gateway_key generate <file>           # adds a key pair to decrypt; creates the file if needed
        python -m ops.gateway_key generate-signing <file>   # adds a key pair to sign the access tokens (ADR-0025)
        python -m ops.gateway_key show <file>               # the public keys to encrypt to, all a client is given
        python -m ops.gateway_key drop <file> <kid>         # removes a key pair once it is not needed

    The service needs one key of each kind, and reads the file when it starts.

    To rotate the key to decrypt: generate, restart the service, give the clients the new public key, and drop the
    old one when they have moved. To rotate the signing key: generate-signing and restart; the newest signs, and the
    old one keeps checking the tokens it signed until they expire, ten minutes later. Then drop it and restart.
    Dropping it at once ends every access token it signed: that is how all of them are cut off.
"""
import json
import os
import sys

from shared.auth.gateway_keys import GatewayKeys, is_signing, new_key, new_signing_key, public_part, read_file, \
    write_file


def _add(path: str, key: dict) -> dict:
    keys = read_file(path) if os.path.exists(path) else []
    write_file(path, keys + [key])
    return public_part(key)


def generate(path: str) -> dict:
    return _add(path, new_key())


def generate_signing(path: str) -> dict:
    return _add(path, new_signing_key())


def show(path: str) -> dict:
    return GatewayKeys.load(path).public_set()


def drop(path: str, kid: str) -> dict:
    keys = read_file(path)
    dropped = [key for key in keys if key.get("kid") == kid]
    if not dropped:
        raise SystemExit(f"There is no key {kid} in the file")
    remaining = [key for key in keys if key.get("kid") != kid]
    if not any(is_signing(key) == is_signing(dropped[0]) for key in remaining):
        raise SystemExit("That is the only key of its kind: generate the new one first")
    write_file(path, remaining)
    return {"keys": [public_part(key) for key in remaining]}


if __name__ == "__main__":
    commands = {"generate": (generate, 1), "generate-signing": (generate_signing, 1), "show": (show, 1),
                "drop": (drop, 2)}
    arguments = sys.argv[1:]
    if not arguments or arguments[0] not in commands or len(arguments) - 1 != commands[arguments[0]][1]:
        raise SystemExit(__doc__)
    print(json.dumps(commands[arguments[0]][0](*arguments[1:]), indent=2))
