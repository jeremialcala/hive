# -*- coding: utf-8 -*-
"""
    Authentication of everything that is not the Meta webhook (ADR-0025): OAuth 2.0 client_credentials. A client
    proves who it is with an assertion, a JWT it signs inside a JWE for the gateway, and gets an access token the
    gateway signs. The submodules are imported one by one on purpose: `client` and `gateway_keys` need neither the
    settings nor the database, so they also run outside the service.
"""
