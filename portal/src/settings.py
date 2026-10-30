# -*- coding: utf-8 -*-
"""
    What hive-portal is told by its environment. The secrets are files: the client secret of the BFF in Zitadel and
    the key the sessions are sealed with. They are read, never printed, and nothing of them goes to the log.
"""
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """The settings of the portal, from its environment and portal.env."""
    environment: Literal["development", "staging", "production"] = "production"
    # Where the browser reaches the portal: its origin, and the base of the redirect URIs Zitadel knows.
    portal_url: str = Field(min_length=8)
    # Zitadel (ADR-0029), by its public name: the issuer of its ID tokens, and the BFF as its confidential client.
    idp_issuer: str = Field(min_length=8)
    oidc_client_id: str = Field(min_length=1)
    oidc_client_secret_file: str = Field(min_length=1)
    # hive-api, inside midgard. It is also where the DPoP proofs say they are sent to (GATEWAY_URLS of hive-api).
    hive_api_url: str = "http://hive-api:5002"
    # 32 random bytes, base64: what the sessions are sealed with before they go to Bifröst.
    session_key_file: str = Field(min_length=1)
    # Bifröst, as it wrote credenciales/portal.env: only the bucket `sesiones`. Without NATS_URL, in development
    # only, the sessions live in memory.
    nats_url: str | None = None
    nats_user: str | None = None
    nats_password: str | None = None
    nats_ca: str | None = None
    nats_inbox_prefix: str | None = None

    # hide_input_in_errors: a rejected setting must not be echoed into the startup traceback.
    model_config = SettingsConfigDict(env_file="portal.env", hide_input_in_errors=True, extra="ignore")
