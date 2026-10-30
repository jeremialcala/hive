# -*- coding: utf-8 -*-
"""
    The management API: what clients, the operator and the response engine call, each with its own token. It runs
    apart from the webhook Meta calls (webhook.py), so that a slow request here never holds a notification up.

        uvicorn app:app
"""
import time
from inspect import currentframe
from uuid import UUID
from urllib.parse import parse_qs

from fastapi import Depends, Header, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from fastapi.openapi.models import OAuthFlowClientCredentials, OAuthFlows
from fastapi.security import OAuth2
from fastapi.security.utils import get_authorization_scheme_param

from shared.auth.client import ASSERTION_TYPE, GRANT_TYPE
from shared.auth.gateway_keys import GatewayKeys
from auth.principal import ClientNotChosen, Principal, authenticate, exchange, exchange_id_token
from auth import dpop, request_limits, token_limits
from auth.idp import IdentityProvider, IdpUnavailable
from shared.auth.tokens import InvalidToken
from classes import (BotCredentialsRequest, BotResponse, ChatbotPhoneRequest, ChatbotRequest, ClientRequest,
                     ConsentRequest, ContactSearchRequest, IntentRequest, OutboundMessageRequest, OwnedKeyRequest,
                     PersonaRequest, PersonChangeRequest, PersonInviteRequest, ResponseData, RuleRequest,
                     RulesRequest, ServiceKeyRequest,
                     SigningKeyRequest, TemplateCreateRequest, TemplateEditRequest)
from constants import DESCRIPTION, SUMMARY, TAGS_METADATA, TITLE
from controller import (ctr_add_own_signing_key, ctr_add_phone_chatbot, ctr_add_service_key, ctr_add_signing_key,
                        ctr_change_person, ctr_conversation, ctr_create_chatbot, ctr_create_chatbot_rules,
                        ctr_create_intent, ctr_create_template, ctr_invite_person, ctr_list_people, ctr_remove_person,
                        ctr_delete_chatbot, ctr_delete_client, ctr_delete_contact, ctr_delete_intent,
                        ctr_delete_template, ctr_edit_template, ctr_export_contact, ctr_list_templates,
                        ctr_gateway_keys, ctr_get_bot_tokens, ctr_get_bot_tools, ctr_get_chatbot_from_uuid,
                        ctr_get_chatbot_phones, ctr_get_chatbot_rules_from_uuid, ctr_get_client, ctr_get_intent,
                        ctr_get_intents, ctr_last_turns, ctr_list_bots, ctr_list_clients, ctr_list_contacts,
                        ctr_register_client, ctr_send_status,
                        ctr_remove_service_key, ctr_remove_signing_key, ctr_search_contacts, ctr_send_message,
                        ctr_set_bot_credentials, ctr_set_consent, ctr_update_intent, ctr_withdraw_consent,
                        announce, ctr_get_version, ctr_list_versions, ctr_restore_version, ctr_change_rule,
                        ctr_delete_rule, ctr_set_persona)
from enums import Role, Worker
from repository import TenantRepository, erasures
from service import METHODS, build, log, settings
from utils import describe

# Only this service authenticates callers and deletes: the webhook runs without either setting.
if not settings.gateway_keys_file:
    raise ValueError("GATEWAY_KEYS_FILE is required")
if not settings.gateway_audience or len(settings.gateway_audience) < 8:
    raise ValueError("GATEWAY_AUDIENCE is required, eight characters at least")

# Read once, at start: without the gateway's key pair no client could authenticate, so the app does not start.
GATEWAY_KEYS = GatewayKeys.load(settings.gateway_keys_file)

# Where a DPoP proof may say a request was made to (ADR-0033): the public address and the engine's, inside midgard.
# The request's own Host is never trusted for it: behind the tunnel it is whatever the proxy says.
GATEWAY_URLS = tuple(sorted({url.strip().rstrip("/") for url in (settings.gateway_urls or "").split(",")
                             if url.strip()} | {settings.gateway_audience.rstrip("/")}))
# RFC 9449, section 7.1: how a request without a good token or proof is told what this service takes.
DPOP_CHALLENGE = f'DPoP algs="{dpop.ALGORITHM}"'

# The IdP of the people of a client (ADR-0029). Without it configured, nobody exchanges an ID token: only machines
# get in.
IDP = (IdentityProvider(settings.idp_issuer, settings.idp_audience, settings.idp_jwks_url)
       if settings.idp_issuer and settings.idp_audience else None)

# Every deletion is written down here before it is made. It is only reached when needed.
erasures.configure(settings.erasures_dir)

# The contract says how to get the token: OAuth 2.0 client_credentials at /oauth/token. The header is read here and
# checked in get_principal; nothing here answers on its own (auto_error=False).
oauth2 = OAuth2(flows=OAuthFlows(clientCredentials=OAuthFlowClientCredentials(tokenUrl="/oauth/token")),
                scheme_name="OAuth2", auto_error=False,
                description="An access token from POST /oauth/token (ADR-0025): a JWT the gateway signs (ES256, typ "
                            "at+jwt), sent as `Authorization: DPoP <token>`. It lasts 10 minutes and is good for "
                            "every request in them. The client authenticates at /oauth/token with a signed assertion "
                            "(RFC 7523), not with a secret. The token is bound to the client's key (RFC 9449, "
                            "ADR-0033): every request, POST /oauth/token too, carries a `DPoP` header, a proof that "
                            "key signs for that method and URL, good once and for 60 seconds, with the hash of the "
                            "token it goes with.")

# POST /oauth/token (ADR-0025): OAuth 2.0 client_credentials, the client authenticated by a signed assertion
# (RFC 7523, section 2.2). Its body is a form, never bigger than this.
MAX_TOKEN_REQUEST = 16 * 1024
FORM = "application/x-www-form-urlencoded"
TOKEN_PARAMETERS = ("grant_type", "client_assertion_type", "client_assertion")
# RFC 8693: the BFF exchanges the ID token of a person for an access token of Hive (ADR-0029).
TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
ID_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:id_token"
ACCESS_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"
EXCHANGE_PARAMETERS = ("grant_type", "subject_token", "subject_token_type")
# What else the exchange may say: which token it wants, and which client, for a person of more than one.
EXCHANGE_OPTIONS = ("requested_token_type", "hive_client")
# RFC 6749, section 5.1: neither the token nor an error about it is kept by a cache.
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


GATEWAY_KEYS_ROUTE = ("GET", "/gateway/keys")
RULES = ("GET", "/chat/{bot_id}/rules")
TOOLS = ("GET", "/chat/{bot_id}/tools")
TOKENS = ("GET", "/chat/{bot_id}/tokens")
# The coordinator's, when its context is gone (RF15).
TURNS = ("GET", "/chat/{bot_id}/contacts/{contact_id}/turns")

# What each worker of the response engine may call with the key it holds for a client (ADR-0022). A route that
# is not here is closed to every worker, and so is any route added later until it is written down here.
WORKER_ROUTES = {
    Worker.CLASSIFIER: {GATEWAY_KEYS_ROUTE, RULES},
    Worker.COORDINATOR: {GATEWAY_KEYS_ROUTE, RULES, TOOLS, TURNS},
    Worker.LINK: {GATEWAY_KEYS_ROUTE, TOOLS},
    Worker.RESPONDER: {GATEWAY_KEYS_ROUTE, RULES},
    Worker.EVALUATOR: {GATEWAY_KEYS_ROUTE, RULES},
    Worker.NOTIFIER: {GATEWAY_KEYS_ROUTE, TOKENS},
}
# What a cliente-analista may call (ADR-0029, the role × action matrix: D1, D2, D6, D10): it reads what its client
# has, and sends approved templates. Any other route answers 403 to them, and so does any route added later until it
# is written down here. A machine credential and a cliente-admin reach everything of their client (D3).
ANALYST_ROUTES = {
    ("GET", "/chat/{bot_id}/info"), ("GET", "/chat/{bot_id}/phones"), ("GET", "/chat/{bot_id}/rules"),
    ("GET", "/chat/{bot_id}/intents"), ("GET", "/chat/{bot_id}/intents/{intent_id}"),
    ("GET", "/chat/{bot_id}/tools"), ("GET", "/chat/{bot_id}/contacts"), ("POST", "/contacts/search"),
    ("POST", "/chat/{bot_id}/contacts/{contact_id}/messages"), ("GET", "/chat/{bot_id}/templates"),
    # What the PWA reads (HIG-87): the chatbots, a contact's conversation without its number, and what came of a send.
    ("GET", "/bots"), ("GET", "/chat/{bot_id}/contacts/{contact_id}/conversation"),
    ("GET", "/chat/{bot_id}/contacts/{contact_id}/messages/{request_id}"),
    # The versions of a chatbot and what changed in each (RF26): seen, never gone back to.
    ("GET", "/chat/{bot_id}/versions"), ("GET", "/chat/{bot_id}/versions/{version}"),
}

# Everything the engine reaches with some key. The keys registered before keys had a worker (ADR-0016) reach all
# of it until the engine moves to one key per worker; then they are taken out.
SERVICE_ROUTES = set().union(*WORKER_ROUTES.values())


def get_principal(request: Request,
                  authorization: str | None = Depends(oauth2)) -> Principal:
    """
        Everything in this service but the health check and POST /oauth/token depends on this. The answer to a
        token that is not accepted is always the same 401: the reason only goes to the log.

        Checking an access token does not touch the database (ADR-0025). It stays a function, not a coroutine, like
        get_repository and the routes of the response engine: FastAPI runs them in its pool of threads, so what
        blocks holds up only its own request.
    """
    try:
        scheme, token = get_authorization_scheme_param(authorization)
        principal = authenticate(token if scheme.lower() == "dpop" else None, request.headers.getlist("dpop"),
                                 request.method, _urls_of(request), GATEWAY_KEYS, settings.gateway_audience)
    except InvalidToken as e:
        log.warning(f"Rejected a request: {e}")
        error = "invalid_dpop_proof" if isinstance(e, dpop.InvalidProof) else "invalid_token"
        challenge = DPOP_CHALLENGE if not authorization else f'{DPOP_CHALLENGE}, error="{error}"'
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="HTTP_401_UNAUTHORIZED",
                            headers={"WWW-Authenticate": challenge}) from None
    # HIG-139: a credential that already made its requests this minute waits, whatever it asks for.
    refusal = request_limits.LIMITS.admit(principal.client_id, principal.limited_as)
    if refusal is not None:
        log.warning(f"Turned away a request of client {principal.client_id}, {principal.actor}: too many requests "
                    f"this minute")
        raise request_limits.TooManyRequests(refusal.retry_after)
    route = (METHODS.get(request.method), getattr(request.scope.get("route"), "path", None))
    if principal.is_analyst and route not in ANALYST_ROUTES:
        log.warning(f"A cliente-analista was denied a route: client {principal.client_id}, {principal.actor}")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="HTTP_403_FORBIDDEN")
    if principal.role is Role.SERVICE:
        if route not in WORKER_ROUTES.get(principal.worker, SERVICE_ROUTES):
            log.warning(f"The response engine was denied a route with its key for client {principal.client_id}, "
                        f"worker {principal.worker_name}")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="HTTP_403_FORBIDDEN")
        if principal.worker is None:
            # A warning, so that it is kept at the level production logs at: it is how the keys still to be
            # taken out are found.
            log.warning(f"The response engine used key {principal.kid} of client {principal.client_id}, which has "
                        f"no worker: register one per worker (ADR-0022)")
    return principal


def _urls_of(request: Request) -> set[str]:
    """The URLs a proof for this request may name: each address of the service, with the path as it was sent."""
    path = request.scope.get("raw_path", b"").decode("latin-1") or request.url.path
    return {f"{base}{path}" for base in GATEWAY_URLS}


async def require_service(principal: Principal = Depends(get_principal)) -> Principal:
    """What only the response engine gets. A client does not get it with its own key, nor does the operator."""
    if principal.role is not Role.SERVICE:
        log.warning(f"Client {principal.client_id} was denied a route of the response engine")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="HTTP_403_FORBIDDEN")
    return principal


def get_repository(principal: Principal = Depends(get_principal)) -> TenantRepository:
    """
        What the endpoints of a client get: the repository of whoever is authenticated, the only way they have
        to reach data. The operator is a client too, and reaches its own.
    """
    return TenantRepository(principal.client_id)


async def require_operator(principal: Principal = Depends(get_principal)) -> Principal:
    """The registry of clients is the operator's. What a caller may do comes from its entry, not from the token."""
    if principal.role is not Role.OPERATOR:
        log.warning(f"Client {principal.client_id} was denied an operator endpoint")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="HTTP_403_FORBIDDEN")
    return principal


# An origin that keeps getting 401 or 403 waits, before its body is read (HIG-139).
app = build(limit=request_limits.API, title=TITLE, summary=SUMMARY, description=DESCRIPTION,
            openapi_tags=TAGS_METADATA)


def _oauth_error(code: int, error: str) -> JSONResponse:
    """An error of RFC 6749, section 5.2: the code and nothing else, whatever the reason."""
    headers = {**NO_STORE, **({"WWW-Authenticate": DPOP_CHALLENGE} if code == status.HTTP_401_UNAUTHORIZED else {})}
    return JSONResponse(status_code=code, content={"error": error}, headers=headers)


def _too_many(retry_after: int) -> JSONResponse:
    """RFC 6585: 429, and when to ask again."""
    return request_limits.too_many(retry_after)


async def _credential_refused(_request: Request, refused: request_limits.TooManyRequests) -> JSONResponse:
    return _too_many(refused.retry_after)


app.add_exception_handler(request_limits.TooManyRequests, _credential_refused)


def _form(body: bytes) -> dict | None:
    """The parameters of the token request, each once; None if the body is not a form of them."""
    try:
        parsed = parse_qs(body.decode("ascii"), keep_blank_values=False, strict_parsing=True, max_num_fields=10)
    except (UnicodeDecodeError, ValueError):
        return None
    # RFC 6749, section 3.2: a parameter sent more than once is not taken.
    if any(len(values) != 1 for values in parsed.values()):
        return None
    return {name: values[0] for name, values in parsed.items()}


def _oauth_answer(description: str, schema: dict) -> dict:
    return {"description": description, "content": {"application/json": {"schema": schema}}}


OAUTH_ERROR = {"type": "object", "required": ["error"], "properties": {"error": {"type": "string"}}}
# What the contract says of /oauth/token: the route reads its form by hand, so FastAPI cannot infer it.
TOKEN_CONTRACT = {
    "parameters": [{"name": "DPoP", "in": "header", "required": True, "schema": {"type": "string"},
                    "description": "A DPoP proof (RFC 9449) for POST and this URL, without ath: signed with the same "
                                   "key as the assertion, or, in an exchange, with the BFF's own key."}],
    "requestBody": {"required": True, "content": {FORM: {"schema": {
        "type": "object", "required": ["grant_type"],
        "description": "client_credentials takes client_assertion_type and client_assertion; token-exchange "
                       "(RFC 8693, ADR-0029), subject_token and subject_token_type.",
        "properties": {
            "grant_type": {"type": "string", "enum": [GRANT_TYPE, TOKEN_EXCHANGE]},
            "subject_token": {"type": "string", "description": (
                "token-exchange: the ID token Zitadel gave the person, for the BFF, with mfa in its amr.")},
            "subject_token_type": {"type": "string", "enum": [ID_TOKEN_TYPE]},
            "requested_token_type": {"type": "string", "enum": [ACCESS_TOKEN_TYPE]},
            "hive_client": {"type": "string", "format": "uuid", "description": (
                "token-exchange: the client the person enters, only needed for a person of more than one.")},
            "client_assertion_type": {"type": "string", "enum": [ASSERTION_TYPE]},
            "client_assertion": {"type": "string", "description": (
                "A JWT the client signs (ES256, with the kid of its registered key; iss and sub, the client; aud, "
                "the gateway; 60 seconds at most; good once), inside a JWE for a key of GET /gateway/keys "
                "(RSA-OAEP-256, A256CBC-HS512, cty JWT).")},
        }}}}},
    "responses": {
        "200": _oauth_answer("An access token, good for 10 minutes. An exchange also says issued_token_type.", {
            "type": "object", "required": ["access_token", "token_type", "expires_in"],
            "properties": {"access_token": {"type": "string"}, "token_type": {"type": "string", "enum": ["DPoP"]},
                           "expires_in": {"type": "integer"},
                           "issued_token_type": {"type": "string", "enum": [ACCESS_TOKEN_TYPE]}}}),
        "400": _oauth_answer("invalid_request, unsupported_grant_type, or invalid_dpop_proof: the DPoP header is "
                             "missing, is not good, or is not signed by the key that signed the assertion. In an "
                             "exchange, invalid_grant: the ID token is not good, has no second factor, or its person "
                             "is not in Hive, is blocked or removed, or their client is not active, whatever the "
                             "reason; and invalid_target: the person is of more than one client, and hive_client "
                             "does not say which.", OAUTH_ERROR),
        "401": _oauth_answer("invalid_client: the assertion is not good, whatever the reason.", OAUTH_ERROR),
        "429": {**_oauth_answer("temporarily_unavailable: too many requests from this origin, too many rejected "
                                "assertions from it, or too many tokens for this key in the last hour (ADR-0028). "
                                "Retry-After says in how many seconds to ask again.", OAUTH_ERROR),
                "headers": {"Retry-After": {"description": "Seconds to wait.", "schema": {"type": "integer"}}}},
        "503": _oauth_answer("temporarily_unavailable: in an exchange, the keys of the IdP could not be read.",
                             OAUTH_ERROR),
    },
}


@app.post(path="/oauth/token", tags=["OAuth"], openapi_extra=TOKEN_CONTRACT,
          summary="An access token, for a client that proves who it is or for a person of a client",
          description="A machine credential authenticates with an assertion its key signed (client_credentials, "
                      "RFC 7523, section 2.2, ADR-0025). The BFF of the PWA exchanges the ID token of a person "
                      "(token-exchange, RFC 8693, ADR-0029), and the token says the person and their role in their "
                      "client. Either way, an access token the gateway signs, for every other route.")
async def oauth_token(request: Request):
    # Before anything of the request is read (ADR-0028): an origin that asks too often, or failed too often, waits.
    origin = token_limits.origin_of(request.headers, request.client.host if request.client else None)
    refusal = token_limits.LIMITS.admit(origin)
    if refusal is not None:
        log.warning(f"Turned away a request for an access token: too many {refusal.reason} from its origin")
        return _too_many(refusal.retry_after)
    response = await _token_answer(request)
    if response.status_code in (status.HTTP_400_BAD_REQUEST, status.HTTP_401_UNAUTHORIZED):
        token_limits.LIMITS.failed(origin)
    return response


async def _token_answer(request: Request) -> JSONResponse:
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != FORM:
        return _oauth_error(status.HTTP_400_BAD_REQUEST, "invalid_request")
    body = await request.body()
    form = _form(body) if len(body) <= MAX_TOKEN_REQUEST else None
    if form is None or "grant_type" not in form:
        return _oauth_error(status.HTTP_400_BAD_REQUEST, "invalid_request")
    if form["grant_type"] == TOKEN_EXCHANGE:
        # Without an IdP configured, this service does not take the grant.
        if IDP is None:
            return _oauth_error(status.HTTP_400_BAD_REQUEST, "unsupported_grant_type")
        return await _exchange_answer(request, form)
    if any(name not in form for name in TOKEN_PARAMETERS):
        return _oauth_error(status.HTTP_400_BAD_REQUEST, "invalid_request")
    if form["grant_type"] != GRANT_TYPE:
        return _oauth_error(status.HTTP_400_BAD_REQUEST, "unsupported_grant_type")
    if form["client_assertion_type"] != ASSERTION_TYPE:
        return _oauth_error(status.HTTP_401_UNAUTHORIZED, "invalid_client")
    # RFC 9449, section 5: the proof first, and its key has to be the one that signed the assertion.
    try:
        jkt = dpop.check(request.headers.getlist("dpop"), request.method, _urls_of(request), None, time.time())
    except dpop.InvalidProof as e:
        log.warning(f"Rejected a request for an access token: {e}")
        return _oauth_error(status.HTTP_400_BAD_REQUEST, "invalid_dpop_proof")
    try:
        # It reads the client's key and remembers the assertion's jti in MongoDB: in a thread, not in the event loop.
        token, lifetime, caller = await run_in_threadpool(exchange, form["client_assertion"], GATEWAY_KEYS,
                                                          settings.gateway_audience, jkt)
    except dpop.InvalidProof as e:
        log.warning(f"Rejected a request for an access token: {e}")
        return _oauth_error(status.HTTP_400_BAD_REQUEST, "invalid_dpop_proof")
    except InvalidToken as e:
        log.warning(f"Rejected a request for an access token: {e}")
        return _oauth_error(status.HTTP_401_UNAUTHORIZED, "invalid_client")
    # The assertion is good and spent; the token it was exchanged for is only handed over if the key has room.
    refusal = token_limits.LIMITS.issue(caller.client_id, caller.kid)
    if refusal is not None:
        log.warning(f"Turned away a request for an access token: client {caller.client_id}, key {caller.kid} got "
                    f"its share of tokens this hour")
        return _too_many(refusal.retry_after)
    log.info(f"Access token for client {caller.client_id}, key {caller.kid}, worker {caller.worker_name}")
    return JSONResponse(status_code=status.HTTP_200_OK, headers=NO_STORE,
                        content={"access_token": token, "token_type": "DPoP", "expires_in": lifetime})


def _is_uuid(value: str) -> bool:
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


async def _exchange_answer(request: Request, form: dict) -> JSONResponse:
    """
        RFC 8693, for the BFF (ADR-0029): an ID token of Zitadel for an access token of Hive, bound to the key of the
        request's proof, the BFF's own. Whatever is wrong with the ID token or the person is the same invalid_grant.
    """
    if (any(name not in form for name in EXCHANGE_PARAMETERS)
            or any(name not in EXCHANGE_PARAMETERS + EXCHANGE_OPTIONS for name in form)
            or form["subject_token_type"] != ID_TOKEN_TYPE
            or form.get("requested_token_type", ACCESS_TOKEN_TYPE) != ACCESS_TOKEN_TYPE
            or not _is_uuid(form.get("hive_client", str(UUID(int=0))))):
        return _oauth_error(status.HTTP_400_BAD_REQUEST, "invalid_request")
    try:
        jkt = dpop.check(request.headers.getlist("dpop"), request.method, _urls_of(request), None, time.time())
    except dpop.InvalidProof as e:
        log.warning(f"Rejected an exchange for an access token: {e}")
        return _oauth_error(status.HTTP_400_BAD_REQUEST, "invalid_dpop_proof")
    try:
        # It may read Zitadel's keys, and reads the people of the IdP's person: in a thread, not in the event loop.
        token, lifetime, caller = await run_in_threadpool(exchange_id_token, form["subject_token"],
                                                          form.get("hive_client"), IDP, GATEWAY_KEYS,
                                                          settings.gateway_audience, jkt)
    except IdpUnavailable as e:
        log.error(f"The keys of the IdP could not be read: {e}")
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, headers=NO_STORE,
                            content={"error": "temporarily_unavailable"})
    except ClientNotChosen as e:
        log.warning(f"Rejected an exchange for an access token: {e}")
        return _oauth_error(status.HTTP_400_BAD_REQUEST, "invalid_target")
    except InvalidToken as e:
        log.warning(f"Rejected an exchange for an access token: {e}")
        return _oauth_error(status.HTTP_400_BAD_REQUEST, "invalid_grant")
    refusal = token_limits.LIMITS.issue(caller.client_id, caller.limited_as)
    if refusal is not None:
        log.warning(f"Turned away an exchange for an access token: client {caller.client_id}, {caller.actor} got "
                    f"their share of tokens this hour")
        return _too_many(refusal.retry_after)
    log.info(f"Access token for client {caller.client_id}, {caller.actor}, {caller.user_role.value}")
    return JSONResponse(status_code=status.HTTP_200_OK, headers=NO_STORE,
                        content={"access_token": token, "issued_token_type": ACCESS_TOKEN_TYPE, "token_type": "DPoP",
                                 "expires_in": lifetime})


@app.post(path="/bot", tags=["Bot"], response_model=BotResponse)
def create_chatbot(chatbot: ChatbotRequest, request: Request,
                         repo: TenantRepository = Depends(get_repository),
                         principal: Principal = Depends(get_principal)):
    log.info(f"Starting: {currentframe().f_code.co_name}")
    try:
        response = ctr_create_chatbot(repo, request=chatbot, author=principal.actor, eventId=request.state.event_id)
        log.info(f"Ending: {currentframe().f_code.co_name}")
        return response
    except Exception as e:
        raise Exception(e.args)


@app.delete(path="/bot/{bot_id}", tags=["Bot"], response_model=ResponseData)
async def delete_chatbot(bot_id: str, repo: TenantRepository = Depends(get_repository)):
    """
        Deletes a chatbot and everything stored for it: its contacts, phones, rules and intents. It cannot be
        undone. From then on its webhook answers 404: the subscription in Meta has to be removed as well.
    """
    return await ctr_delete_chatbot(repo, bot_id)


@app.post(path="/chat/{bot_id}/phones", tags=["Chat"])
def add_whatsapp_phone(bot_id: str, chatbot: ChatbotPhoneRequest, request: Request,
                             repo: TenantRepository = Depends(get_repository)):
    log.info(f"Starting: {currentframe().f_code.co_name}")
    try:
        response = ctr_add_phone_chatbot(repo, bot_uuid=bot_id, req=chatbot, eventId=request.state.event_id)

        log.info(f"Ending: {currentframe().f_code.co_name}")
        return response
    except Exception as e:
        log.error(describe(e))
        raise Exception(e.args)


@app.get(path="/chat/{bot_id}/phones", tags=["Chat"])
def get_chatbot_phones(bot_id, req: Request, repo: TenantRepository = Depends(get_repository)):
    log.info(f"Starting: {currentframe().f_code.co_name}")
    try:
        response = ctr_get_chatbot_phones(repo, bot_uuid=bot_id,
                                                environment=req.query_params.get("environment"))

        log.info(f"Ending: {currentframe().f_code.co_name}")
        return response
    except Exception as e:
        raise Exception(e.args)


@app.get(path="/chat/{bot_id}/info", tags=["Chat"])
def get_chatbot_info(bot_id, req: Request, principal: Principal = Depends(get_principal)):
    """The card of a chatbot, with its verifyToken; a cliente-analista gets it without it (D6)."""
    log.info(f"Starting: {currentframe().f_code.co_name}")
    try:
        response = ctr_get_chatbot_from_uuid(TenantRepository(principal.client_id), bot_id, eventId=req.state.event_id,
                                             with_verify_token=not principal.is_analyst)
        log.info(f"Ending: {currentframe().f_code.co_name}")
        return response
    except Exception as e:
        raise Exception(e.args)


@app.put(path="/chat/{bot_id}/credentials", tags=["Chat"], response_model=ResponseData)
def set_chatbot_credentials(bot_id: str, credentials: BotCredentialsRequest,
                                  repo: TenantRepository = Depends(get_repository)):
    """
        Loads or rotates the Meta credentials of a chatbot: the page token, the WhatsApp token and the App
        Secret. The ones sent replace the stored ones. The answer says which are loaded, never their values.
    """
    return ctr_set_bot_credentials(repo, bot_id, credentials)


@app.get(path="/chat/{bot_id}/contacts", tags=["Contacts"], response_model=ResponseData)
def list_contacts(bot_id: str, offset: int = Query(default=0, ge=0),
                        limit: int = Query(default=100, ge=1, le=500),
                        repo: TenantRepository = Depends(get_repository)):
    """The contacts of a chatbot, oldest first, a page at a time, with everything stored about each."""
    return ctr_list_contacts(repo, bot_id, offset, limit)


@app.get(path="/chat/{bot_id}/contacts/{contact_id}", tags=["Contacts"], response_model=ResponseData)
def export_contact(bot_id: str, contact_id: str, repo: TenantRepository = Depends(get_repository)):
    """Everything the service stores about a contact, the history of its messages included."""
    return ctr_export_contact(repo, bot_id, contact_id)


@app.delete(path="/chat/{bot_id}/contacts/{contact_id}", tags=["Contacts"], response_model=ResponseData)
async def delete_contact(bot_id: str, contact_id: str, repo: TenantRepository = Depends(get_repository)):
    """
        Deletes a contact of a chatbot. It cannot be undone. If the same person writes again, it is stored
        again as a new contact.
    """
    return await ctr_delete_contact(repo, bot_id, contact_id)


@app.post(path="/chat/{bot_id}/contacts/{contact_id}/messages", tags=["Contacts"], response_model=ResponseData,
          status_code=status.HTTP_202_ACCEPTED)
async def send_message(bot_id: str, contact_id: str, message: OutboundMessageRequest,
                       principal: Principal = Depends(get_principal),
                       idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    """
        Sends a WhatsApp message to a contact who already wrote to the chatbot (ADR-0031): a text, within the 24
        hours since the contact last wrote, or an approved template of the chatbot, within or outside them; outside
        them, only to a contact who agreed to messages of the template's category. The engine's notifier sends it:
        202 says the request was taken, and the message shows in the history once Meta accepted it.

        The Idempotency-Key header is required: the same key within 24 hours gives back the same request and sends
        nothing more. 60 sends a minute for each chatbot and 1 000 a day for each client: more get 429 with
        Retry-After.

        A cliente-analista only sends approved templates (D10): a text from them gets 403.
    """
    if principal.is_analyst and message.type != "template":
        log.warning(f"A cliente-analista was denied sending a text: client {principal.client_id}, {principal.actor}")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="HTTP_403_FORBIDDEN")
    return await ctr_send_message(TenantRepository(principal.client_id), bot_id, contact_id, message,
                                  idempotency_key, sent_by=principal.actor)


@app.get(path="/bots", tags=["Bot"], response_model=ResponseData)
def list_bots(principal: Principal = Depends(get_principal)):
    """The chatbots of the client, with which Meta credentials each has loaded, never their values."""
    return ctr_list_bots(TenantRepository(principal.client_id))


@app.get(path="/chat/{bot_id}/contacts/{contact_id}/conversation", tags=["Contacts"], response_model=ResponseData)
def get_conversation(bot_id: str, contact_id: str, principal: Principal = Depends(get_principal)):
    """
        A contact's last 50 messages, each with its origin (the engine or the client, and who), and Meta's window of 24
        hours: open or not, and until when. Without the contact's number. When a person reads it, the log says who.
    """
    return ctr_conversation(TenantRepository(principal.client_id), bot_id, contact_id, principal.actor,
                            principal.is_person)


@app.get(path="/chat/{bot_id}/contacts/{contact_id}/messages/{request_id}", tags=["Contacts"],
         response_model=ResponseData)
def get_send_status(bot_id: str, contact_id: str, request_id: str, principal: Principal = Depends(get_principal)):
    """
        What came of a request to send, for 24 hours: accepted (the bus holds it), sent (Meta took it) or failed, with
        Meta's code and why (window_closed, template_paused, template_disabled, template_parameters, marketing_limit,
        no_token, gave_up...).
    """
    return ctr_send_status(TenantRepository(principal.client_id), bot_id, contact_id, request_id)


@app.put(path="/chat/{bot_id}/contacts/{contact_id}/consent", tags=["Contacts"], response_model=ResponseData)
def set_consent(bot_id: str, contact_id: str, consent: ConsentRequest,
                repo: TenantRepository = Depends(get_repository)):
    """
        Records that a contact agreed to get the client's messages of these categories (RF31), and where the client
        got that consent. Outside the window of 24 hours, a template is only sent with it. It replaces any before.
    """
    return ctr_set_consent(repo, bot_id, contact_id, consent)


@app.delete(path="/chat/{bot_id}/contacts/{contact_id}/consent", tags=["Contacts"], response_model=ResponseData)
def withdraw_consent(bot_id: str, contact_id: str, repo: TenantRepository = Depends(get_repository)):
    """The contact withdrew its consent: it is kept with when, and nothing is sent to it outside the window any more."""
    return ctr_withdraw_consent(repo, bot_id, contact_id)


@app.post(path="/people", tags=["People"], response_model=ResponseData, status_code=status.HTTP_201_CREATED)
def invite_person(person: PersonInviteRequest, principal: Principal = Depends(get_principal)):
    """
        Invites a person to the client by their email, with a role: cliente-admin or cliente-analista (RF16). Hive
        creates their user in Zitadel, which sends them the invitation to set up their account and the MFA; a person
        who already is a user there is linked. They are invited until their first sign-in. 409 if they already are of
        the client.
    """
    return ctr_invite_person(TenantRepository(principal.client_id), person, principal.actor)


@app.get(path="/people", tags=["People"], response_model=ResponseData)
def list_people(principal: Principal = Depends(get_principal)):
    """The people of the client, with their role and state. Of one removed, only who they were: their email is gone."""
    return ctr_list_people(TenantRepository(principal.client_id))


@app.put(path="/people/{person_id}", tags=["People"], response_model=ResponseData)
def change_person(person_id: str, change: PersonChangeRequest, principal: Principal = Depends(get_principal)):
    """
        Another role for a person, or blocks them or lets them in again. It is felt at their next exchange of a token,
        within 10 minutes. The last active cliente-admin cannot be demoted nor blocked: 409.
    """
    return ctr_change_person(TenantRepository(principal.client_id), person_id, change, principal.actor)


@app.delete(path="/people/{person_id}", tags=["People"], response_model=ResponseData)
def remove_person(person_id: str, principal: Principal = Depends(get_principal)):
    """
        Removes a person from the client: no more tokens, and their email is erased. Their user in Zitadel stays: they
        may be of another client. Not the last active cliente-admin: 409. Inviting them again brings them back.
    """
    return ctr_remove_person(TenantRepository(principal.client_id), person_id, principal.actor)


@app.post(path="/chat/{bot_id}/templates", tags=["Templates"], response_model=ResponseData,
          status_code=status.HTTP_201_CREATED)
def create_template(bot_id: str, template: TemplateCreateRequest, repo: TenantRepository = Depends(get_repository)):
    """
        Creates a WhatsApp template of the chatbot in Meta, with the chatbot's WhatsApp token and WhatsApp Business
        Account (wabaId, loaded with PUT /chat/{bot_id}/credentials). Meta reviews it: it is sent once it says
        APPROVED, which a list with ?sync=true brings. Only MARKETING and UTILITY; the body's values, {{1}}, {{2}}...,
        with an example each, and none in the header or the footer.
    """
    return ctr_create_template(repo, bot_id, template)


@app.get(path="/chat/{bot_id}/templates", tags=["Templates"], response_model=ResponseData)
def list_templates(bot_id: str, sync: bool = Query(default=False), repo: TenantRepository = Depends(get_repository)):
    """
        The templates of the chatbot, as Hive last saw them in Meta. With ?sync=true, Meta's list is brought over
        the copy first: states, categories, and the templates made or deleted in Meta's own tools.
    """
    return ctr_list_templates(repo, bot_id, sync)


@app.put(path="/chat/{bot_id}/templates/{template_id}", tags=["Templates"], response_model=ResponseData)
def edit_template(bot_id: str, template_id: str, template: TemplateEditRequest,
                  repo: TenantRepository = Depends(get_repository)):
    """
        Replaces every component of a template, by Meta's id: one APPROVED, REJECTED or PAUSED, else 409. Meta
        reviews it again, and it is not sent until it is approved again.
    """
    return ctr_edit_template(repo, bot_id, template_id, template)


@app.delete(path="/chat/{bot_id}/templates/{template_id}", tags=["Templates"], response_model=ResponseData)
def delete_template(bot_id: str, template_id: str, repo: TenantRepository = Depends(get_repository)):
    """
        Deletes a template in Meta, in its language only, and Hive's copy: not one DISABLED (409). Meta keeps its
        name taken for 30 days.
    """
    return ctr_delete_template(repo, bot_id, template_id)


@app.post(path="/contacts/search", tags=["Contacts"], response_model=ResponseData)
def search_contacts(search: ContactSearchRequest, repo: TenantRepository = Depends(get_repository)):
    """
        Finds a person among the contacts of all the chatbots of the client, by WhatsApp number or by
        Messenger id. It is how a request from somebody for their data starts.
    """
    return ctr_search_contacts(repo, search)


@app.get(path="/chat/{bot_id}/tokens", tags=["Chat"], response_model=ResponseData)
def get_chatbot_tokens(bot_id: str, engine: Principal = Depends(require_service)):
    """
        The Meta tokens of a chatbot, for the response engine to send its answers with. Only the engine gets
        them, with the key it holds for the client that owns the chatbot, and every time it does is logged.
    """
    return ctr_get_bot_tokens(TenantRepository(engine.client_id), bot_id, engine)


@app.get(path="/chat/{bot_id}/contacts/{contact_id}/turns", tags=["Contacts"], response_model=ResponseData)
def get_last_turns(bot_id: str, contact_id: str, engine: Principal = Depends(require_service)):
    """
        The last 10 turns of a conversation, both ways and oldest first: from the tenth last message the contact
        sent, everything after it. For the engine's coordinator, when its context of 24 hours is gone; only the
        engine gets them, with the key it holds for the client that owns the chatbot, and every time it does is
        logged.
    """
    return ctr_last_turns(TenantRepository(engine.client_id), bot_id, contact_id, engine)


@app.get(path="/chat/{bot_id}/rules", tags=["Chat"])
def get_chatbot_rules(bot_id, req: Request, repo: TenantRepository = Depends(get_repository)):
    log.info(f"Starting: {currentframe().f_code.co_name}")

    try:
        response = ctr_get_chatbot_rules_from_uuid(repo, bot_id, eventId=req.state.event_id)
        log.info(f"Ending: {currentframe().f_code.co_name}")
        return response
    except Exception as e:
        raise Exception(e.args)


@app.post(path="/chat/{bot_id}/rules", tags=["Chat"])
async def add_chatbot_rule(bot_id, req: Request, rules: RulesRequest,
                           repo: TenantRepository = Depends(get_repository),
                           principal: Principal = Depends(get_principal)):
    log.info(f"Starting: {currentframe().f_code.co_name}")
    try:
        response = await run_in_threadpool(ctr_create_chatbot_rules, repo, request=rules, bot_id=bot_id,
                                           author=principal.actor, eventId=req.state.event_id)
        await _configured(repo, bot_id, response)
        log.info(f"Ending: {currentframe().f_code.co_name}")
        return response
    except Exception as e:
        raise Exception(e.args)


@app.put(path="/chat/{bot_id}/rules/{rule_id}", tags=["Chat"], response_model=ResponseData)
async def change_chatbot_rule(bot_id: str, rule_id: str, rule: RuleRequest,
                              repo: TenantRepository = Depends(get_repository),
                              principal: Principal = Depends(get_principal)):
    """
        Changes one rule of the chatbot (RF24), with the same limits as when it is added. The ids of its rules come
        in `ruleList` of GET /chat/{bot_id}/rules. Each change is a version (RF26). 409 if the rule already says it.
    """
    response = await run_in_threadpool(ctr_change_rule, repo, bot_id, rule_id, rule, principal.actor)
    await _configured(repo, bot_id, response)
    return response


@app.delete(path="/chat/{bot_id}/rules/{rule_id}", tags=["Chat"], response_model=ResponseData)
async def delete_chatbot_rule(bot_id: str, rule_id: str, repo: TenantRepository = Depends(get_repository),
                              principal: Principal = Depends(get_principal)):
    """
        Deletes one rule of the chatbot (RF24), and the engine stops using it. It is a version (RF26): going back to the
        one before brings the rule back.
    """
    response = await run_in_threadpool(ctr_delete_rule, repo, bot_id, rule_id, principal.actor)
    await _configured(repo, bot_id, response)
    return response


@app.put(path="/chat/{bot_id}/persona", tags=["Bot"], response_model=ResponseData)
async def set_chatbot_persona(bot_id: str, persona: PersonaRequest, repo: TenantRepository = Depends(get_repository),
                              principal: Principal = Depends(get_principal)):
    """
        Changes the persona of the chatbot (RF23): its name, role, tone and description, all four, with the same limits
        as when it is created (RS11). Each change is a version (RF26). 409 if it is the persona it already has.
    """
    response = await run_in_threadpool(ctr_set_persona, repo, bot_id, persona, principal.actor)
    await _configured(repo, bot_id, response)
    return response


@app.post(path="/chat/{bot_id}/intents", tags=["Intents"])
async def add_chatbot_intent(bot_id, req: Request, intent: IntentRequest,
                             repo: TenantRepository = Depends(get_repository),
                             principal: Principal = Depends(get_principal)):
    log.info(f"Starting: {currentframe().f_code.co_name}")
    try:
        response = await run_in_threadpool(ctr_create_intent, repo, request=intent, bot_id=bot_id,
                                           author=principal.actor, eventId=req.state.event_id)
        await _configured(repo, bot_id, response)
        log.info(f"Ending: {currentframe().f_code.co_name}")
        return response
    except Exception as e:
        raise Exception(e.args)


@app.get(path="/chat/{bot_id}/intents", tags=["Intents"])
def list_chatbot_intents(bot_id, req: Request, repo: TenantRepository = Depends(get_repository)):
    log.info(f"Starting: {currentframe().f_code.co_name}")
    try:
        response = ctr_get_intents(repo, bot_id=bot_id, eventId=req.state.event_id)
        log.info(f"Ending: {currentframe().f_code.co_name}")
        return response
    except Exception as e:
        raise Exception(e.args)


@app.get(path="/chat/{bot_id}/intents/{intent_id}", tags=["Intents"])
def get_chatbot_intent(bot_id, intent_id, req: Request, repo: TenantRepository = Depends(get_repository)):
    log.info(f"Starting: {currentframe().f_code.co_name}")
    try:
        response = ctr_get_intent(repo, bot_id=bot_id, intent_id=intent_id, eventId=req.state.event_id)
        log.info(f"Ending: {currentframe().f_code.co_name}")
        return response
    except Exception as e:
        raise Exception(e.args)


@app.put(path="/chat/{bot_id}/intents/{intent_id}", tags=["Intents"])
async def update_chatbot_intent(bot_id, intent_id, req: Request, intent: IntentRequest,
                                repo: TenantRepository = Depends(get_repository),
                                principal: Principal = Depends(get_principal)):
    log.info(f"Starting: {currentframe().f_code.co_name}")
    try:
        response = await run_in_threadpool(ctr_update_intent, repo, request=intent, bot_id=bot_id,
                                           intent_id=intent_id, author=principal.actor, eventId=req.state.event_id)
        await _configured(repo, bot_id, response)
        log.info(f"Ending: {currentframe().f_code.co_name}")
        return response
    except Exception as e:
        raise Exception(e.args)


@app.delete(path="/chat/{bot_id}/intents/{intent_id}", tags=["Intents"])
async def delete_chatbot_intent(bot_id, intent_id, req: Request, repo: TenantRepository = Depends(get_repository),
                                principal: Principal = Depends(get_principal)):
    log.info(f"Starting: {currentframe().f_code.co_name}")
    try:
        response = await run_in_threadpool(ctr_delete_intent, repo, bot_id=bot_id, intent_id=intent_id,
                                           author=principal.actor, eventId=req.state.event_id)
        await _configured(repo, bot_id, response)
        log.info(f"Ending: {currentframe().f_code.co_name}")
        return response
    except Exception as e:
        raise Exception(e.args)


@app.get(path="/chat/{bot_id}/versions", tags=["Versions"], response_model=ResponseData)
def list_versions(bot_id: str, offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=100),
                  repo: TenantRepository = Depends(get_repository)):
    """
        The versions of the chatbot's configuration, the newest first (RF26): who changed it, how and when. Each
        change of its persona, rules or intents writes one, and they are kept for as long as the chatbot is.
    """
    return ctr_list_versions(repo, bot_id, offset, limit)


@app.get(path="/chat/{bot_id}/versions/{version}", tags=["Versions"], response_model=ResponseData)
def get_version(bot_id: str, version: str, against: int | None = Query(default=None, ge=0),
                repo: TenantRepository = Depends(get_repository)):
    """
        A version: the persona, rules and intents it holds, and what changed from the version `against`, by default
        the one before it. Never the Meta credentials nor the verifyToken: they are not versioned.
    """
    return ctr_get_version(repo, bot_id, version, against)


@app.post(path="/chat/{bot_id}/versions/{version}/restore", tags=["Versions"], response_model=ResponseData)
async def restore_version(bot_id: str, version: str, repo: TenantRepository = Depends(get_repository),
                          principal: Principal = Depends(get_principal)):
    """
        Goes back to a version: the chatbot gets its persona, rules and intents, and that is written as a new version,
        so nothing is lost. Its intents are checked again: 422, and nothing changes, if one of them is not allowed any
        more (RS16). 409 if the chatbot already has that configuration. Only a cliente-admin or the machine credential.
    """
    response = await run_in_threadpool(ctr_restore_version, repo, bot_id, version, principal.actor)
    await _configured(repo, bot_id, response)
    return response


async def _configured(repo: TenantRepository, bot_id, response) -> None:
    """A change of the configuration that went through is told to the engine, so that it reads it again (ADR-0035)."""
    if response.status_code == status.HTTP_200_OK:
        await announce(repo, bot_id)


@app.get(path="/chat/{bot_id}/tools", tags=["Intents"])
def get_chatbot_tools(bot_id, req: Request, repo: TenantRepository = Depends(get_repository)):
    log.info(f"Starting: {currentframe().f_code.co_name}")
    try:
        response = ctr_get_bot_tools(repo, bot_id=bot_id, eventId=req.state.event_id)
        log.info(f"Ending: {currentframe().f_code.co_name}")
        return response
    except Exception as e:
        raise Exception(e.args)


@app.get(path="/gateway/keys", tags=["Clients"], response_model=ResponseData)
def gateway_keys(_principal: Principal = Depends(get_principal)):
    """
        The audience and the public keys a client encrypts its tokens to. A client that is already in asks here
        for the new key when the gateway rotates its own.
    """
    return ctr_gateway_keys(GATEWAY_KEYS)


@app.get(path="/keys", tags=["Clients"], response_model=ResponseData)
def list_my_keys(principal: Principal = Depends(get_principal)):
    return ctr_get_client(principal.client_id)


@app.post(path="/keys", tags=["Clients"], response_model=ResponseData)
def add_my_key(key: OwnedKeyRequest, principal: Principal = Depends(get_principal)):
    """
        A client registers a second public key to rotate: both work until it removes the old one. The new key
        signs a proof for the token of this same request, so nobody registers a key they cannot sign with.
    """
    return ctr_add_own_signing_key(principal, key)


@app.delete(path="/keys/{kid}", tags=["Clients"], response_model=ResponseData)
def remove_my_key(kid: str, principal: Principal = Depends(get_principal)):
    """A client revokes one of its keys. It cannot remove the last one: only the operator can let it back in."""
    return ctr_remove_signing_key(principal.client_id, kid, keep_one=True)


@app.post(path="/clients", tags=["Clients"], response_model=ResponseData)
def register_client(client: ClientRequest, _operator: Principal = Depends(require_operator)):
    """
        The operator registers a client with its first public key. The answer carries what the client needs to
        call the service: its identifier, the audience and the gateway's public keys.
    """
    return ctr_register_client(client, GATEWAY_KEYS)


@app.get(path="/clients", tags=["Clients"], response_model=ResponseData)
def list_clients(_operator: Principal = Depends(require_operator)):
    return ctr_list_clients()


@app.delete(path="/clients/{client_id}", tags=["Clients"], response_model=ResponseData)
async def delete_client(client_id: str, _operator: Principal = Depends(require_operator)):
    """
        The operator takes a client off the service: its keys stop working at once, and its chatbots, with
        everything stored for them, and its data key are deleted. It cannot be undone.
    """
    return await ctr_delete_client(client_id)


@app.post(path="/clients/{client_id}/keys", tags=["Clients"], response_model=ResponseData)
def add_client_key(client_id: str, key: SigningKeyRequest, _operator: Principal = Depends(require_operator)):
    return ctr_add_signing_key(client_id, key)


@app.post(path="/clients/{client_id}/service-keys", tags=["Clients"], response_model=ResponseData)
def add_service_key(client_id: str, key: ServiceKeyRequest, _operator: Principal = Depends(require_operator)):
    """
        The operator lets a worker of the response engine act for a client: it registers the public key that
        worker signs with for that client. With it the worker reaches the chatbots of that client and of nobody
        else, and only the routes of that worker: the notifier, the Meta tokens; the coordinator, the rules,
        the tools and the last turns; the classifier, the responder and the evaluator, the rules; the link,
        the tools. Two keys at most per worker and client, to rotate without a gap.
    """
    return ctr_add_service_key(client_id, key)


@app.delete(path="/clients/{client_id}/service-keys/{kid}", tags=["Clients"], response_model=ResponseData)
def remove_service_key(client_id: str, kid: str, _operator: Principal = Depends(require_operator)):
    """Takes the response engine's key for a client away: it stops reaching that client's chatbots at once."""
    return ctr_remove_service_key(client_id, kid)


@app.delete(path="/clients/{client_id}/keys/{kid}", tags=["Clients"], response_model=ResponseData)
def remove_client_key(client_id: str, kid: str, _operator: Principal = Depends(require_operator)):
    """Revoking a key is deleting it: the tokens signed with it stop being accepted at once."""
    return ctr_remove_signing_key(client_id, kid, keep_one=False)
