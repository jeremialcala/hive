# -*- coding: utf-8 -*-
"""
    What the gateway and the workers of the response engine both use (ADR-0023): the contract of the bus, the bus,
    the traces, the tokens of the management API, the store of the engine's keys in Andvari, the rule of the
    destinations of the intents and the names of the workers.

    Nothing here reads the settings of the gateway or of the engine, nor their database: whoever uses it passes what
    it needs. That is what lets both import it. The submodules are imported one by one.
"""
