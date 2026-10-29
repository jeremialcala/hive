# -*- coding: utf-8 -*-

__all__ = ["TITLE", "SUMMARY", "DESCRIPTION", "WEBHOOK_DESCRIPTION", "TAGS_METADATA"]

TITLE = "Hive management API"

SUMMARY = "The chatbots of a Hive client: their Meta credentials, numbers, rules, intents and contacts"

DESCRIPTION = """
The management API of Hive, the gateway between Meta (Messenger and WhatsApp) and the response engine of each
chatbot. Each client manages its own chatbots through it and reaches nothing of anybody else's. What Meta calls is
another service, the webhook, which runs apart from this one.

### Getting a token

Every route but the health check and /oauth/token asks for an access token in `Authorization: DPoP`, and beside
it a DPoP proof (RFC 9449) in the `DPoP` header: the token is bound to the key of the client.

* /oauth/token: @POST, as a form (OAuth 2.0 client credentials, RFC 6749 section 4.4). The client proves who it is
  with an assertion (RFC 7523): a JWT it signs with a key it has registered (`ES256`, with the key's `kid`), with
  its own identifier as `iss` and `sub` and the audience of /gateway/keys as `aud`, encrypted to a public key of
  /gateway/keys (`RSA-OAEP-256` with `A256CBC-HS512`). An assertion is good once and for 60 seconds at most.
* Every request, the one to /oauth/token too, carries a new DPoP proof: a JWT signed with the same key as the
  assertion (`typ` `dpop+jwt`, `ES256`, the public key as `jwk` in its header), with a new `jti`, the method as
  `htm`, the URL without query as `htu`, `iat` and, beside an access token, its SHA-256 as `ath`. A proof is good
  once and for 60 seconds.
* The access token lasts 10 minutes and is good for every request in them. Ask for a new one shortly before it
  expires, not for every request: each key gets a bounded number of tokens an hour, and an address that asks too
  often, or keeps sending assertions that are not good, gets a 429 with `Retry-After`.

### A client and its chatbots

* /bot: @POST creates a chatbot.
* /bot/{bot_id}: @DELETE deletes it with everything stored for it.
* /chat/{bot_id}/info: @GET what is stored of the chatbot, and which Meta credentials are loaded.
* /chat/{bot_id}/credentials: @PUT loads or rotates its Meta credentials. They are never returned.
* /chat/{bot_id}/phones: @POST adds a WhatsApp number, @GET lists them. `?environment=` gives the last one of it.
* /chat/{bot_id}/rules: @POST adds rules, @GET the persona of the chatbot and its rules, as data.
* /chat/{bot_id}/intents: @POST and @GET; /chat/{bot_id}/intents/{intent_id} takes @GET, @PUT and @DELETE.
* /chat/{bot_id}/tools: @GET the actions of its intents as tool definitions for the model.
* /chat/{bot_id}/contacts: @GET who wrote to it, a page at a time; /chat/{bot_id}/contacts/{contact_id} exports
  one with @GET and deletes it with @DELETE.
* /contacts/search: @POST finds a person in every chatbot of the client, by number or by Messenger id.
* /keys: @GET the keys of the caller, @POST adds one to rotate; /keys/{kid} removes one with @DELETE.

### The response engine

Each worker of the engine holds its own key for each client, and the key says which routes it reaches: the
notifier, the Meta tokens; the coordinator, the rules, the tools and the last turns of a conversation; the
classifier, the responder and the evaluator, the rules; the link, the tools. All of them read /gateway/keys.

* /chat/{bot_id}/tokens: @GET the Meta tokens of a chatbot. Only for the notifier, with the key it holds for the
  client that owns the chatbot.
* /chat/{bot_id}/contacts/{contact_id}/turns: @GET the last 10 turns of a conversation, for the coordinator.

### The operator

* /clients: @POST registers a client, @GET lists them; /clients/{client_id} takes one off with @DELETE.
* /clients/{client_id}/keys: the keys a client signs with; /clients/{client_id}/service-keys, the keys each
  worker of the response engine signs with for that client, with the worker that signs with each.

### Any caller

* /gateway/keys: @GET the audience and the public keys tokens are encrypted to.
* /health: @GET liveness, without a token.
"""

WEBHOOK_DESCRIPTION = """
What Meta calls for each chatbot. It checks that a notification comes from Meta, finds or stores who wrote, and
queues each message for the response engine. It runs apart from the management API and serves nothing else.

* /chat/{bot_id}: what Meta calls for a chatbot.
    * @GET: the verification challenge, checked against the verification token of the chatbot.
    * @POST: a notification, taken only with a valid `X-Hub-Signature-256`. Each message in it is queued.
* /health: @GET liveness.
"""

TAGS_METADATA = [
    {
        "name": "Clients",
        "description": "The registry of clients and of the public keys they sign their tokens with."
    },
    {
        "name": "OAuth",
        "description": "The access token every other route asks for (ADR-0025)."
    },
    {
        "name": "Bot",
        "description": "Creating a chatbot and deleting it with everything stored for it.",
    },
    {
        "name": "Chat",
        "description": "What a chatbot is made of: its Meta credentials, numbers and rules, and what the response "
                       "engine reads of it."
    },
    {
        "name": "Contacts",
        "description": "The people who wrote to a chatbot: listing them, finding one, exporting what is stored "
                       "about it and deleting it."
    },
    {
        "name": "Templates",
        "description": "The WhatsApp templates of a chatbot, created, edited and deleted in Meta, with the copy "
                       "of each that Hive keeps with its state: only an approved one is sent."
    },
    {
        "name": "Intents",
        "description": "Configure intents and their actions (API calls) as LLM tool definitions, "
                       "stored per chatbot and consumed by the downstream worker."
    }
]
