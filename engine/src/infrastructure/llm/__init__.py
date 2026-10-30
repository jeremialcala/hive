"""
    El LLM de Hive: llama.cpp en la LAN, por su API compatible con la de OpenAI (ADR-0010).
"""
from .cliente import ClienteLlm, LlmNoDisponible
from .clasificador import LlmClasificador
from .redactor import LlmRedactor

__all__ = ["ClienteLlm", "LlmNoDisponible", "LlmClasificador", "LlmRedactor"]
