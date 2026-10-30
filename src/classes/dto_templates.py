# -*- coding: utf-8 -*-
"""
    A WhatsApp template as a client asks Hive to create or edit it (ADR-0031, HIG-85), in Meta's own shape so that it
    is handed over as it is: a body, and maybe a header, a footer and buttons. Checked before it goes to Meta, so that a
    template Meta would turn away does not cost one of the 100 an hour it allows:

    - the name, in lower case, digits and _;
    - the body's values numbered {{1}}, {{2}}... from 1 with none missing, and an example for each one;
    - no values in the header or the footer: a send only fills the body's (the send route, HIG-83);
    - a button that opens a link, only to an https address.
"""
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .entity_outbound import CATEGORIES

VALUE = re.compile(r"\{\{\s*(\d+)\s*\}\}")


def values_in(text: str) -> list[int]:
    return [int(number) for number in VALUE.findall(text)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BodyExample(_Strict):
    body_text: list[list[str]] = Field(min_length=1, max_length=1)


class Body(_Strict):
    type: Literal["BODY"]
    text: str = Field(min_length=1, max_length=1024)
    example: BodyExample | None = None

    @model_validator(mode="after")
    def _numbered_with_examples(self):
        numbers = values_in(self.text)
        if sorted(set(numbers)) != list(range(1, len(set(numbers)) + 1)):
            raise ValueError("the values of the body are {{1}}, {{2}}... from 1, with none missing")
        wanted = len(set(numbers))
        given = self.example.body_text[0] if self.example else []
        if len(given) != wanted or any(not value.strip() for value in given):
            raise ValueError(f"the body takes {wanted} values: an example for each one, in example.body_text")
        return self


class Header(_Strict):
    type: Literal["HEADER"]
    format: Literal["TEXT"]
    text: str = Field(min_length=1, max_length=60)

    @field_validator("text")
    @classmethod
    def _no_values(cls, text: str) -> str:
        if values_in(text):
            raise ValueError("a header without values: only the body's are filled when sending")
        return text


class Footer(_Strict):
    type: Literal["FOOTER"]
    text: str = Field(min_length=1, max_length=60)

    @field_validator("text")
    @classmethod
    def _no_values(cls, text: str) -> str:
        if values_in(text):
            raise ValueError("a footer has no values")
        return text


class QuickReply(_Strict):
    type: Literal["QUICK_REPLY"]
    text: str = Field(min_length=1, max_length=25)


class UrlButton(_Strict):
    type: Literal["URL"]
    text: str = Field(min_length=1, max_length=25)
    url: str = Field(min_length=9, max_length=2000, pattern=r"^https://[^\s]+$")


class PhoneButton(_Strict):
    type: Literal["PHONE_NUMBER"]
    text: str = Field(min_length=1, max_length=25)
    phone_number: str = Field(pattern=r"^\+?[0-9]{6,20}$")


Button = Annotated[QuickReply | UrlButton | PhoneButton, Field(discriminator="type")]


class Buttons(_Strict):
    type: Literal["BUTTONS"]
    buttons: list[Button] = Field(min_length=1, max_length=10)


Component = Annotated[Body | Header | Footer | Buttons, Field(discriminator="type")]


class _WithComponents(_Strict):
    components: list[Component] = Field(min_length=1, max_length=4)

    @field_validator("components")
    @classmethod
    def _one_body_at_most_one_of_the_rest(cls, components: list) -> list:
        kinds = [component.type for component in components]
        if kinds.count("BODY") != 1 or any(kinds.count(kind) > 1 for kind in ("HEADER", "FOOTER", "BUTTONS")):
            raise ValueError("one BODY, and at most one HEADER, one FOOTER and one BUTTONS")
        return components

    def values(self) -> int:
        """How many values the body takes: a send has to bring exactly that many."""
        body = next(component for component in self.components if component.type == "BODY")
        return len(set(values_in(body.text)))

    def components_for_meta(self) -> list[dict]:
        return [component.model_dump(exclude_none=True) for component in self.components]


class TemplateCreateRequest(_WithComponents):
    name: str = Field(min_length=1, max_length=512, pattern=r"^[a-z0-9_]+$")
    language: str = Field(min_length=2, max_length=15, pattern=r"^[a-z]{2,3}(_[A-Za-z0-9]{2,8})?$")
    category: Literal[CATEGORIES]

    def for_meta(self) -> dict:
        return {"name": self.name, "language": self.language, "category": self.category,
                "components": self.components_for_meta()}


class TemplateEditRequest(_WithComponents):
    """Meta replaces every component of the template, and an approved one does not change its category."""
