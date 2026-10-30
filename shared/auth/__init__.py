# -*- coding: utf-8 -*-
"""
    The tokens of the management API: a JWT signed by the caller, inside a JWE encrypted to the gateway. Here is what
    both sides need: building a token (`client`), its format and its verification (`tokens`, `key_proof`) and the
    gateway's key pair (`gateway_keys`). Who the caller is, from the registry, stays in the gateway (`auth.principal`).
"""
