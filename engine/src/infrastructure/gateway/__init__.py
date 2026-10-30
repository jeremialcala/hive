"""
    La API de gestión del gateway, como la ve el motor: cada worker la llama con su clave para el cliente dueño del
    bot (ADR-0022 del gateway).
"""
from .api import ApiDelGateway

__all__ = ["ApiDelGateway"]
