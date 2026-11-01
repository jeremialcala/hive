# -*- coding: utf-8 -*-
"""
    A topic a client keeps a chatbot away from (RF27, HIG-103), as it is added or changed. The engine puts the topic and
    its description in the `system` of its classifier, after the base of the platform, so their text is checked as a
    rule's is (RS11, classes.bot_text). The reply is what the contact reads, and goes out as written.
"""
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from . import bot_text
from .entity_guardrail import BLOCK

TOPIC_LENGTH = 100
GUARDRAIL_DESCRIPTION_LENGTH = 500
REPLY_LENGTH = 500
GUARDRAILS_PER_BOT = 20

Topic = Annotated[str, Field(min_length=1, max_length=TOPIC_LENGTH), AfterValidator(bot_text.one_line)]
Description = Annotated[str, Field(min_length=1, max_length=GUARDRAIL_DESCRIPTION_LENGTH),
                        AfterValidator(bot_text.rule)]
Reply = Annotated[str, Field(min_length=1, max_length=REPLY_LENGTH), AfterValidator(bot_text.rule)]


class GuardrailRequest(BaseModel):
    """One topic: what it covers, and what happens to a message on it. A topic that blocks says what to reply."""
    model_config = ConfigDict(extra="forbid")

    topic: Topic = Field(examples=["Competidores"])
    description: Description = Field(examples=["Preguntas o comparaciones sobre otras tiendas o sus precios."])
    action: Literal["block", "log"] = Field(examples=["block"])
    reply: Reply | None = Field(default=None, examples=["De eso no puedo hablar, pero con gusto te ayudo con lo nuestro."])

    @model_validator(mode="after")
    def _reply_when_it_blocks(self) -> "GuardrailRequest":
        if self.action == BLOCK and self.reply is None:
            raise ValueError("A topic that blocks says what to reply")
        if self.action != BLOCK:
            # Only a topic that blocks replies: the message on one that logs is answered as any other.
            self.reply = None
        return self
