# -*- coding: utf-8 -*-
"""
    The rules of a chatbot, as the client adds them or changes one (RF02, RF24). Each has its limit, and a chatbot has
    RULES_PER_BOT at most (RS11). The engine puts them in the `system` of the model, so their text is checked by
    classes.bot_text.
"""
import logging
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from . import bot_text
from .tool_settings import Settings

settings = Settings()
log = logging.getLogger(settings.environment)

RuleText = Annotated[str, Field(min_length=1, max_length=bot_text.RULE_LENGTH), AfterValidator(bot_text.rule)]


class RulesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules: list[RuleText] = Field(min_length=1, max_length=bot_text.RULES_PER_BOT, examples=[[
        "If a question does not make any sense, or is not factually coherent, say so instead of answering",
        "If you don't know the answer to a question, please don't share false information."]])


class RuleRequest(BaseModel):
    """One rule, changed."""
    model_config = ConfigDict(extra="forbid")

    rule: RuleText = Field(examples=["Never promise delivery dates."])
