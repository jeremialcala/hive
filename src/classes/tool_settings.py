from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # The key pair clients encrypt their tokens to: the path of a JWK Set file kept outside the repository and the
    # image. The management API does not start without it (app.py); the webhook does not read it, and its
    # container does not mount it.
    gateway_keys_file: str | None = None
    # What clients put in the aud of their tokens, so a token issued for another service is not valid here.
    # Required by the management API, like the key pair.
    gateway_audience: str | None = None
    # The addresses a DPoP proof may say it was made for (ADR-0033), comma-separated and without a trailing slash:
    # the public one, which is the audience, and the one the engine uses inside midgard. Without it, only the
    # audience.
    gateway_urls: str | None = None
    # The IdP of the people of a client (ADR-0029): Zitadel. Its issuer, the client id the BFF has there, which its ID
    # tokens are for, and where its public keys are, by its public name and with TLS. Without the issuer and the
    # audience, the exchange of an ID token for an access token is turned away as a grant this service does not take.
    idp_issuer: str | None = None
    idp_audience: str | None = None
    idp_jwks_url: str | None = None
    # Hive's service account in Zitadel, which invites the people (HIG-94): its API, by default the issuer; the
    # organization of Hive's people there; and the file its token is read from, which goes nowhere else.
    idp_api_url: str | None = None
    idp_organization_id: str | None = None
    idp_token_file: str | None = None
    # The key service that holds the master key: an OpenBao. The app starts without reaching it, but nothing
    # encrypted can be read or written until it answers.
    andvari_addr: str = Field(min_length=8)
    andvari_role_id: str = Field(min_length=1)
    # Path of the file with the AppRole secret id. A file, not a variable: it does not show in the environment
    # of the container, and it can be rotated without a restart.
    andvari_secret_id_file: str = Field(min_length=1)
    # The CA that signed the key service's certificate. Without it, the system's are used.
    andvari_ca_file: str | None = None
    # Where the register of erasures is kept: a directory that outlives the container and is not the database.
    # Nothing is deleted while it cannot be written.
    erasures_dir: str = Field(default="erasures", min_length=1)
    # Meta App Secret used to verify webhook signatures; a bot's own appSecret overrides it.
    facebook_app_secret: str | None = None

    client_id: str
    national_id_url: str

    db_name: str
    db_host: str
    db_username: str
    db_password: str
    # How long a query, a connection or the choice of a server may take before it fails (classes/tool_database.py).
    # Under the 10 seconds the engine's coordinator waits for each call, so it gets an answer, a 503, and not a
    # timeout of its own.
    db_timeout_ms: int = Field(default=5000, ge=500, le=30000)
    # A connection idle longer than this is closed rather than reused.
    db_max_idle_ms: int = Field(default=60000, ge=1000, le=600000)

    sender_email: str
    sender_password: str

    gmail_server_url: str
    gmail_server_port: int

    expiration_time: int
    activation_code_length: int
    password_length: int

    qms_server: str
    qms_user: str
    qms_password: str

    # The event bus, Bifröst (ADR-0019): the same names as the file Bifröst writes for each service in
    # /srv/infra/bifrost/credenciales/<service>.env, so that file can be handed to the container as it is. Without
    # NATS_URL there is no publisher. Outside development the address must be tls://.
    nats_url: str | None = None
    nats_user: str | None = None
    nats_password: str | None = None
    # The CA that signed Bifröst's certificate.
    nats_ca: str | None = None
    # Each service has its own prefix of reply subjects, and may only read those. Without one, NATS's own.
    nats_inbox_prefix: str | None = None

    key_size: int
    private_key_filename: str
    public_key_filename: str

    # Which logger is used (logging_config.yaml) and whether development shortcuts apply. Anything else stops
    # the start: with a typo, production would log at another level and skip the HTTPS check of the key service.
    environment: Literal["development", "staging", "production"]
    version: str

    verify_token: str

    facebook_graph_url: str
    facebook_whatsapp_message: str
    facebook_graph_version: str

    # The Graph API that manages the WhatsApp templates of a chatbot (HIG-85), at the version the engine sends with.
    meta_graph_url: str = "https://graph.facebook.com"
    meta_graph_version: str = "v26.0"

    # hide_input_in_errors: a rejected setting must not be echoed into the startup traceback.
    # extra="ignore": a key left over in config.env from a setting that no longer exists must not stop the start.
    model_config = SettingsConfigDict(env_file="config.env", hide_input_in_errors=True, extra="ignore")
