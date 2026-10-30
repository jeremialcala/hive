# -*- coding: utf-8 -*-
"""
    Sanitización de la entrada (OWASP). Limpia el texto de la persona antes de que llegue al LLM.
"""
import re
import unicodedata

# Longitud máxima razonable para un mensaje de chat; recorta entradas abusivas.
MAX_TEXTO = 4096

# Caracteres de control (excepto tab/newline) que no deben llegar al LLM.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitizar_texto(texto: object, max_len: int = MAX_TEXTO) -> str:
    """
        Normaliza Unicode (NFKC), elimina caracteres de control, recorta espacios
        y limita la longitud. Mitiga inyección de control y entradas desbordadas.

    :param texto: valor crudo (puede no ser str).
    :param max_len: longitud máxima permitida.
    :return: texto sanitizado.
    """
    if texto is None:
        return ""
    valor = unicodedata.normalize("NFKC", str(texto))
    valor = _CONTROL.sub("", valor)
    valor = valor.strip()
    return valor[:max_len]
