# -*- coding: utf-8 -*-
import logging
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from shared.destinations import MAX_LENGTH, check_destination
from .tool_settings import Settings

settings = Settings()
log = logging.getLogger(settings.environment)


class ActionParameterRequest(BaseModel):
    name: str = Field(examples=["order_id"])
    type: str = Field(default="string", examples=["string", "number", "integer", "boolean"])
    description: str = Field(default="", examples=["The unique identifier of the order"])
    required: bool = Field(default=False)
    enum: list = Field(default=[])


class ApiCallRequest(BaseModel):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = Field(default="GET")
    # Where the worker of the engine calls: https, a public host written out, never the platform's own network
    # (shared.destinations, T11). Placeholders like {order_id} only go in the path or the query.
    url: str = Field(max_length=MAX_LENGTH, examples=["https://api.example.com/orders/{order_id}"])
    headers: dict = Field(default={})
    query: dict = Field(default={})
    body: dict = Field(default={})
    authType: Literal["none", "bearer", "api_key", "basic"] = Field(default="none")
    authRef: str = Field(default="", examples=["example_api_token"])
    # Seconds the worker waits for the answer: the engine answers a person who is waiting.
    timeout: int = Field(default=30, ge=1, le=30)

    @field_validator("url")
    @classmethod
    def _destination(cls, url: str) -> str:
        check_destination(url)
        return url

    @field_validator("headers")
    @classmethod
    def _no_host(cls, headers: dict) -> dict:
        # With its own Host, a call to a public address could be served as a name of the platform's network.
        if any(str(name).strip().lower() == "host" for name in headers):
            raise ValueError("The headers of a call cannot set Host")
        return headers


class ActionRequest(BaseModel):
    name: str = Field(examples=["get_order"])
    description: str = Field(examples=["Fetch the status and details of a customer order"])
    type: str = Field(default="api_call", examples=["api_call", "handoff"])
    parameters: list[ActionParameterRequest] = Field(default=[
        ActionParameterRequest(name="order_id", type="string",
                               description="The unique identifier of the order", required=True)
    ])
    apiCall: ApiCallRequest = Field(default=ApiCallRequest(
        method="GET", url="https://api.example.com/orders/{order_id}"))


class IntentRequest(BaseModel):
    name: str = Field(examples=["check_order_status"])
    description: str = Field(examples=["The user wants to know the status of an existing order"])
    samplePhrases: list = Field(default=[
        "Where is my order?",
        "What's the status of order 12345?",
        "Has my package shipped yet?"
    ])
    actions: list[ActionRequest] = Field(default=[ActionRequest(
        name="get_order",
        description="Fetch the status and details of a customer order"
    )])
