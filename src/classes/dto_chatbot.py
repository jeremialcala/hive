# -*- coding: utf-8 -*-
"""
    The persona of a chatbot, as the client writes it when it creates the chatbot or changes it (RF02, RF23). Every
    field is required and has its limit (RS11): nothing is filled in with an example. The engine puts it in the
    `system` of the model, so its text is checked by classes.bot_text.
"""
import logging
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from . import bot_text
from .tool_settings import Settings

settings = Settings()
log = logging.getLogger(settings.environment)

OneLine = Annotated[str, Field(min_length=1, max_length=bot_text.NAME_LENGTH), AfterValidator(bot_text.one_line)]


class PersonaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: OneLine = Field(examples=["Ana"])
    role: OneLine = Field(examples=["Sales Assistant", "Office Assistant", "Tech Support"])
    description: Annotated[str, Field(max_length=bot_text.DESCRIPTION_LENGTH), AfterValidator(bot_text.lines)] = Field(
        examples=["Answers the questions of the customers of a shoe shop. Never promises delivery dates."])
    tone: OneLine = Field(examples=["helpful", "respectful", "honest"])


class ChatbotRequest(PersonaRequest):
    """A new chatbot: for now, its persona. Its numbers, rules and intents are added afterwards."""
