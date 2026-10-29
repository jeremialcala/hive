# -*- coding: utf-8 -*-
"""
    What a client of the service does on its side, as a reference implementation (ADR-0025). It needs neither the
    service's settings nor its database: copy it, or do the same with any JOSE and OAuth library.

        python -m ops.client new-key <file>
            Writes a new private signing key to the file, owner-only, and prints its public part: that is what
            gets registered. The private key is never printed.

        python -m ops.client access-token <key file> <client id> <audience> <gateway keys file> <gateway url>
            Gets an access token from POST /oauth/token and prints it. It lasts 10 minutes, for every request in them,
            and it is bound to the key (ADR-0033): each request needs a new proof, which `proof` signs.

                TOKEN=$(python -m ops.client access-token ...)
                PROOF=$(python -m ops.client proof key.json GET https://.../bot "$TOKEN")
                curl -H "Authorization: DPoP $TOKEN" -H "DPoP: $PROOF" https://.../bot

        python -m ops.client proof <key file> <method> <url> <access token>
            Prints the DPoP header of one request with that access token: good once, and for 60 seconds.

            The gateway keys file is what GET /gateway/keys or the registration answer gave under "keys",
            saved as {"keys": [...]}.

        python -m ops.client assertion <key file> <client id> <audience> <gateway keys file>
            Prints only the assertion, to send to POST /oauth/token with another tool. It is good once and for 60
            seconds.

        python -m ops.client add-key <new key file> <client id> <audience> <access token>
            Prints the body POST /keys takes to add the new key, to send with that access token: the public part of
            the new key and the proof it signs for that access token.
"""
import json
import os
import sys

import requests

from shared.auth.client import (TOKEN_PATH, access_token_jti, build_assertion, dpop_proof, key_proof,
                                new_signing_key, public_part, token_request)

TIMEOUT = 10  # seconds


def new_key(path: str) -> dict:
    key = new_signing_key()
    # O_EXCL: an existing key is never overwritten, losing it would lock the client out.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        json.dump(key, file)
    return public_part(key)


def _read(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def assertion(key_path: str, client_id: str, audience: str, gateway_keys_path: str) -> str:
    # The last gateway key is the newest: while the gateway rotates, that is the one to move to.
    return build_assertion(_read(key_path), client_id, audience, _read(gateway_keys_path)["keys"][-1])


def access_token(key_path: str, client_id: str, audience: str, gateway_keys_path: str, gateway_url: str) -> str:
    url = f"{gateway_url.rstrip('/')}{TOKEN_PATH}"
    response = requests.post(url, timeout=TIMEOUT, headers={"DPoP": dpop_proof(_read(key_path), "POST", url)},
                             data=token_request(assertion(key_path, client_id, audience, gateway_keys_path)))
    if response.status_code != 200:
        raise SystemExit(f"The gateway gave no access token: {response.status_code} {response.text[:200]}")
    return response.json()["access_token"]


def proof(key_path: str, method: str, url: str, token: str) -> str:
    return dpop_proof(_read(key_path), method, url, token)


def add_key(new_key_path: str, client_id: str, audience: str, token: str) -> dict:
    new_key = _read(new_key_path)
    return {"signingKey": public_part(new_key), "proof": key_proof(new_key, client_id, audience,
                                                                     access_token_jti(token))}


if __name__ == "__main__":
    commands = {"new-key": (new_key, 1, True), "access-token": (access_token, 5, False),
                "assertion": (assertion, 4, False), "proof": (proof, 4, False), "add-key": (add_key, 4, True)}
    arguments = sys.argv[1:]
    if not arguments or arguments[0] not in commands or len(arguments) - 1 != commands[arguments[0]][1]:
        raise SystemExit(__doc__)
    command, _, as_json = commands[arguments[0]]
    result = command(*arguments[1:])
    print(json.dumps(result, indent=2) if as_json else result)
