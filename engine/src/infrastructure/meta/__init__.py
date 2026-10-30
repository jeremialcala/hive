"""
    Los canales de Meta por los que el motor responde. En el esqueleto, la Cloud API de WhatsApp; Messenger e
    Instagram llegan en el E10.
"""
from .whatsapp import WhatsAppCloud

__all__ = ["WhatsAppCloud"]
