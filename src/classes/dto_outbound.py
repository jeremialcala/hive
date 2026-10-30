# -*- coding: utf-8 -*-
"""
    What a client sends to write to a contact (ADR-0031): a text, or a template with its values; and what it sends to
    record that a contact agreed to get its messages.
"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .entity_outbound import CATEGORIES

# WhatsApp's own limits: a text of 4 096 characters, and a template's values short enough to fit its body.
MAX_TEXT = 4096
MAX_PARAMETER = 1024
MAX_PARAMETERS = 20


class TemplateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=512, pattern=r"^[a-z0-9_]+$")
    language: str = Field(min_length=2, max_length=15, pattern=r"^[a-z]{2,3}(_[A-Za-z0-9]{2,8})?$")
    parameters: list[str] = Field(default_factory=list, max_length=MAX_PARAMETERS)

    @field_validator("parameters")
    @classmethod
    def _values(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > MAX_PARAMETER for value in values):
            raise ValueError(f"each value has 1 to {MAX_PARAMETER} characters")
        return values


class OutboundMessageRequest(BaseModel):
    """A text inside the window of 24 hours, or an approved template, inside or outside it."""
    model_config = ConfigDict(extra="forbid")

    type: Literal["text", "template"]
    text: str | None = Field(default=None, min_length=1, max_length=MAX_TEXT)
    template: TemplateRequest | None = None

    @model_validator(mode="after")
    def _one_of_them(self):
        if (self.type == "text") != (self.text is not None) or (self.type == "template") != (self.template is not None):
            raise ValueError("a text brings `text` and nothing else; a template, `template` and nothing else")
        if self.text is not None and not self.text.strip():
            raise ValueError("an empty text is not sent")
        return self


class ConsentRequest(BaseModel):
    """That a contact agreed to get the client's messages of these categories, when and where (RF31)."""
    model_config = ConfigDict(extra="forbid")

    categories: list[Literal[CATEGORIES]] = Field(min_length=1, max_length=len(CATEGORIES))
    source: str = Field(min_length=1, max_length=200)
    grantedAt: datetime | None = None

    @field_validator("categories")
    @classmethod
    def _once_each(cls, categories: list[str]) -> list[str]:
        if len(set(categories)) != len(categories):
            raise ValueError("each category once")
        return categories

    @field_validator("grantedAt")
    @classmethod
    def _with_zone(cls, granted: datetime | None) -> datetime | None:
        if granted is not None and granted.tzinfo is None:
            raise ValueError("with its time zone")
        return granted
