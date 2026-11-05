# -*- coding: utf-8 -*-
"""El llama-server falso de la suite (llama.py), uno por módulo, y el cliente del motor contra él."""
import threading
from http.server import ThreadingHTTPServer

import pytest

from src.infrastructure.llm import ClienteLlm

from .llama import CLAVE, MODELO, Llama


@pytest.fixture(scope="module")
def servidor():
    http = ThreadingHTTPServer(("127.0.0.1", 0), Llama)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{http.server_address[1]}"
    http.shutdown()


@pytest.fixture
def llm(servidor):
    """El cliente del motor contra el servidor falso, que empieza sin peticiones."""
    Llama.peticiones = []
    return ClienteLlm(servidor, CLAVE, MODELO, timeout=5)
