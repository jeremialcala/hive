# -*- coding: utf-8 -*-
"""
    The management API: what clients, the operator and the response engine call, each with its own token. It runs
    apart from the webhook Meta calls (webhook.py), so that a slow request here never holds a notification up.

        uvicorn app:app
"""
from inspect import currentframe
from urllib.parse import parse_qs

from fastapi import Depends, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from fastapi.openapi.models import OAuthFlowClientCredentials, OAuthFlows
from fastapi.security import OAuth2
from fastapi.security.utils import get_authorization_scheme_param

from shared.auth.client import ASSERTION_TYPE, GRANT_TYPE
from shared.auth.gateway_keys import GatewayKeys
from auth.principal import Principal, authenticate, exchange
from shared.auth.tokens import InvalidToken
from classes import (BotCredentialsRequest, BotResponse, ChatbotPhoneRequest, ChatbotRequest, ClientRequest,
                     ContactSearchRequest, IntentRequest, OwnedKeyRequest, ResponseData, RulesRequest,
                     ServiceKeyRequest, SigningKeyRequest)
from constants import DESCRIPTION, SUMMARY, TAGS_METADATA, TITLE
from controller import (ctr_add_own_signing_key, ctr_add_phone_chatbot, ctr_add_service_key, ctr_add_signing_key,
                        ctr_create_chatbot, ctr_create_chatbot_rules, ctr_create_intent, ctr_delete_chatbot,
                        ctr_delete_client, ctr_delete_contact, ctr_delete_intent, ctr_export_contact,
                        ctr_gateway_keys, ctr_get_bot_tokens, ctr_get_bot_tools, ctr_get_chatbot_from_uuid,
                        ctr_get_chatbot_phones, ctr_get_chatbot_rules_from_uuid, ctr_get_client, ctr_get_intent,
                        ctr_get_intents, ctr_last_turns, ctr_list_clients, ctr_list_contacts, ctr_register_client,
                        ctr_remove_service_key, ctr_remove_signing_key, ctr_search_contacts, ctr_set_bot_credentials,
                        ctr_update_intent)
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

# Every deletion is written down here before it is made. It is only reached when needed.
erasures.configure(settings.erasures_dir)

# The contract says how to get the token: OAuth 2.0 client_credentials at /oauth/token. The header is read here and
# checked in get_principal; nothing here answers on its own (auto_error=False).
oauth2 = OAuth2(flows=OAuthFlows(clientCredentials=OAuthFlowClientCredentials(tokenUrl="/oauth/token")),
                scheme_name="OAuth2", auto_error=False,
                description="An access token from POST /oauth/token (ADR-0025): a JWT the gateway signs (ES256, typ "
                            "at+jwt), sent as `Authorization: Bearer <token>`. It lasts 10 minutes and is good for "
                            "every request in them. The client authenticates at /oauth/token with a signed assertion "
                            "(RFC 7523), not with a secret.")

# POST /oauth/token (ADR-0025): OAuth 2.0 client_credentials, the client authenticated by a signed assertion
# (RFC 7523, section 2.2). Its body is a form, never bigger than this.
MAX_TOKEN_REQUEST = 16 * 1024
FORM = "application/x-www-form-urlencoded"
TOKEN_PARAMETERS = ("grant_type", "client_assertion_type", "client_assertion")
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
        principal = authenticate(token if scheme.lower() == "bearer" else None, GATEWAY_KEYS,
                                 settings.gateway_audience)
    except InvalidToken as e:
        log.warning(f"Rejected a request: {e}")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="HTTP_401_UNAUTHORIZED",
                            headers={"WWW-Authenticate": "Bearer"}) from None
    if principal.role is Role.SERVICE:
        route = (METHODS.get(request.method), getattr(request.scope.get("route"), "path", None))
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


app = build(title=TITLE, summary=SUMMARY, description=DESCRIPTION, openapi_tags=TAGS_METADATA)


def _oauth_error(code: int, error: str) -> JSONResponse:
    """An error of RFC 6749, section 5.2: the code and nothing else, whatever the reason."""
    headers = {**NO_STORE, **({"WWW-Authenticate": "Bearer"} if code == status.HTTP_401_UNAUTHORIZED else {})}
    return JSONResponse(status_code=code, content={"error": error}, headers=headers)


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
    "requestBody": {"required": True, "content": {FORM: {"schema": {
        "type": "object", "required": list(TOKEN_PARAMETERS),
        "properties": {
            "grant_type": {"type": "string", "enum": [GRANT_TYPE]},
            "client_assertion_type": {"type": "string", "enum": [ASSERTION_TYPE]},
            "client_assertion": {"type": "string", "description": (
                "A JWT the client signs (ES256, with the kid of its registered key; iss and sub, the client; aud, "
                "the gateway; 60 seconds at most; good once), inside a JWE for a key of GET /gateway/keys "
                "(RSA-OAEP-256, A256CBC-HS512, cty JWT).")},
        }}}}},
    "responses": {
        "200": _oauth_answer("An access token, good for 10 minutes.", {
            "type": "object", "required": ["access_token", "token_type", "expires_in"],
            "properties": {"access_token": {"type": "string"}, "token_type": {"type": "string", "enum": ["Bearer"]},
                           "expires_in": {"type": "integer"}}}),
        "400": _oauth_answer("invalid_request or unsupported_grant_type.", OAUTH_ERROR),
        "401": _oauth_answer("invalid_client: the assertion is not good, whatever the reason.", OAUTH_ERROR),
    },
}


@app.post(path="/oauth/token", tags=["OAuth"], openapi_extra=TOKEN_CONTRACT,
          summary="An access token for a client that proves who it is (OAuth 2.0 client_credentials, RFC 7523)",
          description="The client authenticates with an assertion its key signed, as in RFC 7523, section 2.2 "
                      "(ADR-0025), and gets an access token the gateway signs, for every other route.")
async def oauth_token(request: Request):
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != FORM:
        return _oauth_error(status.HTTP_400_BAD_REQUEST, "invalid_request")
    body = await request.body()
    form = _form(body) if len(body) <= MAX_TOKEN_REQUEST else None
    if form is None or any(name not in form for name in TOKEN_PARAMETERS):
        return _oauth_error(status.HTTP_400_BAD_REQUEST, "invalid_request")
    if form["grant_type"] != GRANT_TYPE:
        return _oauth_error(status.HTTP_400_BAD_REQUEST, "unsupported_grant_type")
    if form["client_assertion_type"] != ASSERTION_TYPE:
        return _oauth_error(status.HTTP_401_UNAUTHORIZED, "invalid_client")
    try:
        # It reads the client's key and remembers the assertion's jti in MongoDB: in a thread, not in the event loop.
        token, lifetime, caller = await run_in_threadpool(exchange, form["client_assertion"], GATEWAY_KEYS,
                                                          settings.gateway_audience)
    except InvalidToken as e:
        log.warning(f"Rejected a request for an access token: {e}")
        return _oauth_error(status.HTTP_401_UNAUTHORIZED, "invalid_client")
    log.info(f"Access token for client {caller.client_id}, key {caller.kid}, worker {caller.worker_name}")
    return JSONResponse(status_code=status.HTTP_200_OK, headers=NO_STORE,
                        content={"access_token": token, "token_type": "Bearer", "expires_in": lifetime})


@app.post(path="/bot", tags=["Bot"], response_model=BotResponse)
async def create_chatbot(chatbot: ChatbotRequest, request: Request,
                         repo: TenantRepository = Depends(get_repository)):
    log.info(f"Starting: {currentframe().f_code.co_name}")
    try:
        response = await ctr_create_chatbot(repo, request=chatbot, eventId=request.state.event_id)
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
async def add_whatsapp_phone(bot_id: str, chatbot: ChatbotPhoneRequest, request: Request,
                             repo: TenantRepository = Depends(get_repository)):
    log.info(f"Starting: {currentframe().f_code.co_name}")
    try:
        response = await ctr_add_phone_chatbot(repo, bot_uuid=bot_id, req=chatbot, eventId=request.state.event_id)

        log.info(f"Ending: {currentframe().f_code.co_name}")
        return response
    except Exception as e:
        log.error(describe(e))
        raise Exception(e.args)


@app.get(path="/chat/{bot_id}/phones", tags=["Chat"])
async def get_chatbot_phones(bot_id, req: Request, repo: TenantRepository = Depends(get_repository)):
    log.info(f"Starting: {currentframe().f_code.co_name}")
    try:
        response = await ctr_get_chatbot_phones(repo, bot_uuid=bot_id,
                                                environment=req.query_params.get("environment"))

        log.info(f"Ending: {currentframe().f_code.co_name}")
        return response
    except Exception as e:
        raise Exception(e.args)


@app.get(path="/chat/{bot_id}/info", tags=["Chat"])
async def get_chatbot_info(bot_id, req: Request, repo: TenantRepository = Depends(get_repository)):
    log.info(f"Starting: {currentframe().f_code.co_name}")
    try:
        response = await ctr_get_chatbot_from_uuid(repo, bot_id, eventId=req.state.event_id)
        log.info(f"Ending: {currentframe().f_code.co_name}")
        return response
    except Exception as e:
        raise Exception(e.args)


@app.put(path="/chat/{bot_id}/credentials", tags=["Chat"], response_model=ResponseData)
async def set_chatbot_credentials(bot_id: str, credentials: BotCredentialsRequest,
                                  repo: TenantRepository = Depends(get_repository)):
    """
        Loads or rotates the Meta credentials of a chatbot: the page token, the WhatsApp token and the App
        Secret. The ones sent replace the stored ones. The answer says which are loaded, never their values.
    """
    return await ctr_set_bot_credentials(repo, bot_id, credentials)


@app.get(path="/chat/{bot_id}/contacts", tags=["Contacts"], response_model=ResponseData)
async def list_contacts(bot_id: str, offset: int = Query(default=0, ge=0),
                        limit: int = Query(default=100, ge=1, le=500),
                        repo: TenantRepository = Depends(get_repository)):
    """The contacts of a chatbot, oldest first, a page at a time, with everything stored about each."""
    return await ctr_list_contacts(repo, bot_id, offset, limit)


@app.get(path="/chat/{bot_id}/contacts/{contact_id}", tags=["Contacts"], response_model=ResponseData)
async def export_contact(bot_id: str, contact_id: str, repo: TenantRepository = Depends(get_repository)):
    """Everything the service stores about a contact, the history of its messages included."""
    return await ctr_export_contact(repo, bot_id, contact_id)


@app.delete(path="/chat/{bot_id}/contacts/{contact_id}", tags=["Contacts"], response_model=ResponseData)
async def delete_contact(bot_id: str, contact_id: str, repo: TenantRepository = Depends(get_repository)):
    """
        Deletes a contact of a chatbot. It cannot be undone. If the same person writes again, it is stored
        again as a new contact.
    """
    return await ctr_delete_contact(repo, bot_id, contact_id)


@app.post(path="/contacts/search", tags=["Contacts"], response_model=ResponseData)
async def search_contacts(search: ContactSearchRequest, repo: TenantRepository = Depends(get_repository)):
    """
        Finds a person among the contacts of all the chatbots of the client, by WhatsApp number or by
        Messenger id. It is how a request from somebody for their data starts.
    """
    return await ctr_search_contacts(repo, search)


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
                           repo: TenantRepository = Depends(get_repository)):
    log.info(f"Starting: {currentframe().f_code.co_name}")
    try:
        response = await ctr_create_chatbot_rules(repo, request=rules, bot_id=bot_id, eventId=req.state.event_id)
        log.info(f"Ending: {currentframe().f_code.co_name}")
        return response
    except Exception as e:
        raise Exception(e.args)


@app.post(path="/chat/{bot_id}/intents", tags=["Intents"])
async def add_chatbot_intent(bot_id, req: Request, intent: IntentRequest,
                             repo: TenantRepository = Depends(get_repository)):
    log.info(f"Starting: {currentframe().f_code.co_name}")
    try:
        response = await ctr_create_intent(repo, request=intent, bot_id=bot_id, eventId=req.state.event_id)
        log.info(f"Ending: {currentframe().f_code.co_name}")
        return response
    except Exception as e:
        raise Exception(e.args)


@app.get(path="/chat/{bot_id}/intents", tags=["Intents"])
async def list_chatbot_intents(bot_id, req: Request, repo: TenantRepository = Depends(get_repository)):
    log.info(f"Starting: {currentframe().f_code.co_name}")
    try:
        response = await ctr_get_intents(repo, bot_id=bot_id, eventId=req.state.event_id)
        log.info(f"Ending: {currentframe().f_code.co_name}")
        return response
    except Exception as e:
        raise Exception(e.args)


@app.get(path="/chat/{bot_id}/intents/{intent_id}", tags=["Intents"])
async def get_chatbot_intent(bot_id, intent_id, req: Request, repo: TenantRepository = Depends(get_repository)):
    log.info(f"Starting: {currentframe().f_code.co_name}")
    try:
        response = await ctr_get_intent(repo, bot_id=bot_id, intent_id=intent_id, eventId=req.state.event_id)
        log.info(f"Ending: {currentframe().f_code.co_name}")
        return response
    except Exception as e:
        raise Exception(e.args)


@app.put(path="/chat/{bot_id}/intents/{intent_id}", tags=["Intents"])
async def update_chatbot_intent(bot_id, intent_id, req: Request, intent: IntentRequest,
                                repo: TenantRepository = Depends(get_repository)):
    log.info(f"Starting: {currentframe().f_code.co_name}")
    try:
        response = await ctr_update_intent(repo, request=intent, bot_id=bot_id, intent_id=intent_id,
                                           eventId=req.state.event_id)
        log.info(f"Ending: {currentframe().f_code.co_name}")
        return response
    except Exception as e:
        raise Exception(e.args)


@app.delete(path="/chat/{bot_id}/intents/{intent_id}", tags=["Intents"])
async def delete_chatbot_intent(bot_id, intent_id, req: Request, repo: TenantRepository = Depends(get_repository)):
    log.info(f"Starting: {currentframe().f_code.co_name}")
    try:
        response = await ctr_delete_intent(repo, bot_id=bot_id, intent_id=intent_id, eventId=req.state.event_id)
        log.info(f"Ending: {currentframe().f_code.co_name}")
        return response
    except Exception as e:
        raise Exception(e.args)


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
async def gateway_keys(_principal: Principal = Depends(get_principal)):
    """
        The audience and the public keys a client encrypts its tokens to. A client that is already in asks here
        for the new key when the gateway rotates its own.
    """
    return await ctr_gateway_keys(GATEWAY_KEYS)


@app.get(path="/keys", tags=["Clients"], response_model=ResponseData)
async def list_my_keys(principal: Principal = Depends(get_principal)):
    return await ctr_get_client(principal.client_id)


@app.post(path="/keys", tags=["Clients"], response_model=ResponseData)
async def add_my_key(key: OwnedKeyRequest, principal: Principal = Depends(get_principal)):
    """
        A client registers a second public key to rotate: both work until it removes the old one. The new key
        signs a proof for the token of this same request, so nobody registers a key they cannot sign with.
    """
    return await ctr_add_own_signing_key(principal, key)


@app.delete(path="/keys/{kid}", tags=["Clients"], response_model=ResponseData)
async def remove_my_key(kid: str, principal: Principal = Depends(get_principal)):
    """A client revokes one of its keys. It cannot remove the last one: only the operator can let it back in."""
    return await ctr_remove_signing_key(principal.client_id, kid, keep_one=True)


@app.post(path="/clients", tags=["Clients"], response_model=ResponseData)
async def register_client(client: ClientRequest, _operator: Principal = Depends(require_operator)):
    """
        The operator registers a client with its first public key. The answer carries what the client needs to
        call the service: its identifier, the audience and the gateway's public keys.
    """
    return await ctr_register_client(client, GATEWAY_KEYS)


@app.get(path="/clients", tags=["Clients"], response_model=ResponseData)
async def list_clients(_operator: Principal = Depends(require_operator)):
    return await ctr_list_clients()


@app.delete(path="/clients/{client_id}", tags=["Clients"], response_model=ResponseData)
async def delete_client(client_id: str, _operator: Principal = Depends(require_operator)):
    """
        The operator takes a client off the service: its keys stop working at once, and its chatbots, with
        everything stored for them, and its data key are deleted. It cannot be undone.
    """
    return await ctr_delete_client(client_id)


@app.post(path="/clients/{client_id}/keys", tags=["Clients"], response_model=ResponseData)
async def add_client_key(client_id: str, key: SigningKeyRequest, _operator: Principal = Depends(require_operator)):
    return await ctr_add_signing_key(client_id, key)


@app.post(path="/clients/{client_id}/service-keys", tags=["Clients"], response_model=ResponseData)
async def add_service_key(client_id: str, key: ServiceKeyRequest, _operator: Principal = Depends(require_operator)):
    """
        The operator lets a worker of the response engine act for a client: it registers the public key that
        worker signs with for that client. With it the worker reaches the chatbots of that client and of nobody
        else, and only the routes of that worker: the notifier, the Meta tokens; the coordinator, the rules,
        the tools and the last turns; the classifier, the responder and the evaluator, the rules; the link,
        the tools. Two keys at most per worker and client, to rotate without a gap.
    """
    return await ctr_add_service_key(client_id, key)


@app.delete(path="/clients/{client_id}/service-keys/{kid}", tags=["Clients"], response_model=ResponseData)
async def remove_service_key(client_id: str, kid: str, _operator: Principal = Depends(require_operator)):
    """Takes the response engine's key for a client away: it stops reaching that client's chatbots at once."""
    return await ctr_remove_service_key(client_id, kid)


@app.delete(path="/clients/{client_id}/keys/{kid}", tags=["Clients"], response_model=ResponseData)
async def remove_client_key(client_id: str, kid: str, _operator: Principal = Depends(require_operator)):
    """Revoking a key is deleting it: the tokens signed with it stop being accepted at once."""
    return await ctr_remove_signing_key(client_id, kid, keep_one=False)
