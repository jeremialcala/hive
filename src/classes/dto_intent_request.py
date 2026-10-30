# -*- coding: utf-8 -*-
"""
    An intent of a chatbot and its actions, as the client writes them (RF02, HIG-142). Nothing is filled in with an
    example: what is needed is asked for, and what may be left out has a neutral value ([], {}, "none", 30). A field
    that is not here is a 422 (RS11).

    The text goes to the model: the name and description of the intent and of each action, its phrases and the
    descriptions of the parameters are what the engine hands it as tools. It follows classes.bot_text. The name of an
    action is the name of a tool, and the call it makes goes only where shared.destinations allows (RS16), with headers
    that cannot carry a line break into the request the worker builds.
"""
import json
import logging
import re
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from shared.destinations import MAX_LENGTH, check_destination
from . import bot_text
from .tool_settings import Settings

settings = Settings()
log = logging.getLogger(settings.environment)

# How much an intent holds (RS11).
PHRASES_PER_INTENT = 50
ACTIONS_PER_INTENT = 10
PARAMETERS_PER_ACTION = 20
VALUES_PER_ENUM = 50
ENTRIES_PER_MAP = 20
BODY_LENGTH = 8192

# The name of a tool, as the models take it; of a parameter, as JSON Schema and the URL's placeholders write it.
TOOL_NAME = r"^[A-Za-z0-9_-]{1,64}$"
PARAMETER_NAME = r"^[A-Za-z_][A-Za-z0-9_]{0,63}$"
# The name of a header (RFC 9110, a token) and of a secret the worker looks up: never the secret itself.
HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,64}$")
SECRET_NAME = r"^[A-Za-z0-9_.-]{0,100}$"

Line = Annotated[str, Field(min_length=1, max_length=bot_text.NAME_LENGTH), AfterValidator(bot_text.one_line)]
Phrase = Annotated[str, Field(min_length=1, max_length=500), AfterValidator(bot_text.one_line)]
Text = Annotated[str, Field(min_length=1, max_length=bot_text.RULE_LENGTH), AfterValidator(bot_text.rule)]
OptionalText = Annotated[str, Field(max_length=bot_text.RULE_LENGTH), AfterValidator(bot_text.lines)]


def _plain_map(values: dict, what: str) -> dict:
    """Headers or query: a few names, each with one line of text. A line break here would split the request."""
    if len(values) > ENTRIES_PER_MAP:
        raise ValueError(f"{ENTRIES_PER_MAP} {what} at most")
    for name, value in values.items():
        if not isinstance(value, str) or len(value) > 1000:
            raise ValueError(f"Each of the {what} is a text of 1000 characters at most")
        if any(ord(character) < 32 or ord(character) == 127 or character in bot_text.HIDDEN for character in value):
            raise ValueError(f"The {what} cannot carry control characters nor line breaks")
        if bot_text.TEMPLATE_MARK.search(value):
            raise ValueError(f"The {what} cannot carry the marks a model separates roles with")
        if not name or len(name) > 64:
            raise ValueError(f"The name of each of the {what} has 64 characters at most")
    return values


class ActionParameterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=PARAMETER_NAME, examples=["order_id"])
    type: Literal["string", "number", "integer", "boolean", "object", "array"] = Field(default="string")
    description: OptionalText = Field(default="", examples=["The unique identifier of the order"])
    required: bool = Field(default=False)
    enum: list[Phrase] = Field(default=[], max_length=VALUES_PER_ENUM)


class ApiCallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = Field(default="GET")
    # Where the worker of the engine calls: https, a public host written out, never the platform's own network
    # (shared.destinations, T11). Placeholders like {order_id} only go in the path or the query.
    url: str = Field(max_length=MAX_LENGTH, examples=["https://api.example.com/orders/{order_id}"])
    headers: dict[str, str] = Field(default={})
    query: dict[str, str] = Field(default={})
    body: dict = Field(default={})
    authType: Literal["none", "bearer", "api_key", "basic"] = Field(default="none")
    authRef: str = Field(default="", pattern=SECRET_NAME, examples=["example_api_token"])
    # Seconds the worker waits for the answer: the engine answers a person who is waiting.
    timeout: int = Field(default=30, ge=1, le=30)

    @field_validator("url")
    @classmethod
    def _destination(cls, url: str) -> str:
        check_destination(url)
        return url

    @field_validator("headers")
    @classmethod
    def _headers(cls, headers: dict) -> dict:
        # With its own Host, a call to a public address could be served as a name of the platform's network.
        if any(str(name).strip().lower() == "host" for name in headers):
            raise ValueError("The headers of a call cannot set Host")
        if any(not HEADER_NAME.match(str(name)) for name in headers):
            raise ValueError("The name of a header is a token of 64 characters at most")
        return _plain_map(headers, "headers")

    @field_validator("query")
    @classmethod
    def _query(cls, query: dict) -> dict:
        return _plain_map(query, "values of the query")

    @field_validator("body")
    @classmethod
    def _body(cls, body: dict) -> dict:
        if len(json.dumps(body, ensure_ascii=False)) > BODY_LENGTH:
            raise ValueError(f"The body of a call has {BODY_LENGTH} characters at most, as JSON")
        return body


class ActionRequest(BaseModel):
    """
        What the bot can do for an intent: call an API (api_call, with its apiCall) or hand the conversation over to a
        person (handoff, without one).
    """
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=TOOL_NAME, examples=["get_order"])
    description: Text = Field(examples=["Fetch the status and details of a customer order"])
    type: Literal["api_call", "handoff"] = Field(default="api_call")
    parameters: list[ActionParameterRequest] = Field(default=[], max_length=PARAMETERS_PER_ACTION)
    apiCall: ApiCallRequest | None = Field(default=None)

    @model_validator(mode="after")
    def _call_when_it_calls(self):
        if self.type == "api_call" and self.apiCall is None:
            raise ValueError("An action of type api_call needs its apiCall")
        if self.type == "handoff" and self.apiCall is not None:
            raise ValueError("A handoff calls nothing: it has no apiCall")
        names = [parameter.name for parameter in self.parameters]
        if len(names) != len(set(names)):
            raise ValueError("Two parameters of an action cannot share a name")
        return self


class IntentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Line = Field(examples=["check_order_status"])
    description: Text = Field(examples=["The user wants to know the status of an existing order"])
    samplePhrases: list[Phrase] = Field(default=[], max_length=PHRASES_PER_INTENT,
                                        examples=[["Where is my order?", "Has my package shipped yet?"]])
    actions: list[ActionRequest] = Field(default=[], max_length=ACTIONS_PER_INTENT)

    @model_validator(mode="after")
    def _distinct_tools(self):
        names = [action.name for action in self.actions]
        if len(names) != len(set(names)):
            raise ValueError("Two actions of an intent cannot share a name: each is a tool of the model")
        return self
