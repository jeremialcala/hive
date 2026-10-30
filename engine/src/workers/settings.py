# -*- coding: utf-8 -*-
"""
    La configuración de los workers, del entorno del contenedor. Ningún secreto va en ella: la contraseña de
    Bifröst llega en el fichero de credenciales que el despliegue monta (repositorio `bifrost`), la clave del LLM en un
    fichero de keys/, y las claves de cada cliente se leen de Andvari.
"""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    """Lo que necesita cualquier worker: Bifröst."""
    model_config = SettingsConfigDict(extra="ignore")

    # Bifröst: lo que deja credenciales/<worker>.env.
    nats_url: str = Field(min_length=7)
    nats_user: str | None = None
    nats_password: str | None = None
    nats_ca: str | None = None
    nats_inbox_prefix: str = "_INBOX"

    log_level: str = "INFO"


class AndvariSettings(WorkerSettings):
    """Los workers que firman con su clave para cada cliente (ADR-0022 del gateway): entran en Andvari con su rol."""
    andvari_addr: str = Field(min_length=8)
    andvari_ca_file: str | None = None
    engine_role_id: str = Field(min_length=1)
    engine_secret_id_file: str = Field(min_length=1)


class LlmSettings(WorkerSettings):
    """Los workers que llaman al LLM de Hive: llama.cpp en la LAN (ADR-0010)."""
    llm_url: str = Field(min_length=8)
    llm_api_key_file: str = Field(min_length=1)
    # El alias con el que llama-server sirve el modelo.
    llm_model: str = "gemma-4-26b-a4b"
    # Segundos sin usar el LLM tras los que el worker lo calienta con su `system` (src/infrastructure/llm/caliente.py).
    # 0, nunca. Tras horas sin uso, la primera consulta era cuatro veces más lenta (medido el 2026-10-22).
    llm_calentar_cada: int = Field(default=600, ge=0, le=3600)


class ClassifierSettings(LlmSettings):
    """El clasificador. Cambiar de modelo es cambiar el servidor y esto, no el código."""
    # Un JSON corto: unos 60 a 80 tokens medidos con Gemma 4 26B A4B, sin pensamiento.
    classifier_max_tokens: int = Field(default=256, ge=128, le=1024)
    # Por llamada, sin reintentos aquí: por debajo de los 30 s del ack_wait de MENSAJES/classifier. Medido: menos
    # de 3 s.
    classifier_timeout: float = Field(default=20.0, gt=0, le=25.0)


class CoordinatorSettings(AndvariSettings):
    """El coordinador: además, la API de gestión del gateway, con su clave para cada cliente."""
    # Lo mismo que necesita ops.engine_keys del gateway (docs/03-implementation/autenticacion.md).
    gateway_url: str = Field(min_length=8)
    gateway_audience: str = Field(min_length=8)
    gateway_public_keys_file: str = Field(min_length=1)
    gateway_ca_file: str | None = None
    # Por llamada. Son dos por mensaje: 20 s como mucho, por debajo de los 30 s del ack_wait de MOTOR/coordinator. Con 5
    # s, una lectura de 5,8 s medida en midgard se reintentaba 30 s después.
    coordinator_timeout: float = Field(default=10.0, gt=0, le=10.0)


class ResponderSettings(LlmSettings):
    """El de respuesta."""
    # Unas pocas frases de chat: con 1024 caben de sobra. Sin pensamiento, todo el tope es para la respuesta.
    responder_max_tokens: int = Field(default=1024, ge=256, le=4096)
    # Por llamada, sin reintentos aquí: por debajo de los 120 s del ack_wait de MOTOR/responder. Medido: unos 96
    # tokens por segundo.
    responder_timeout: float = Field(default=90.0, gt=0, le=100.0)


class NotifierSettings(AndvariSettings):
    """El notificador: la API del gateway, para los tokens de Meta, y la Graph API de Meta."""
    gateway_url: str = Field(min_length=8)
    gateway_audience: str = Field(min_length=8)
    gateway_public_keys_file: str = Field(min_length=1)
    gateway_ca_file: str | None = None
    graph_url: str = "https://graph.facebook.com"
    # La más nueva el 2026-10-20. Meta retira cada versión a los dos años: subirla es cambiar esto.
    graph_version: str = Field(default="v26.0", pattern=r"^v[0-9]{2}[.]0$")
    # Por llamada, a Meta y al gateway. Las tres de un mensaje en el peor caso (el gateway dos veces tras un 401, y
    # Meta) quedan por debajo de los 30 s del ack_wait de MOTOR/notifier.
    notifier_timeout: float = Field(default=8.0, gt=0, le=9.0)
