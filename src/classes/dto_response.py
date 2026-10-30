# -*- coding: utf-8 -*-
from datetime import datetime

from pydantic import BaseModel, Field
from .dto_chatbot import ChatbotRequest
from .tool_clock import utc_now


class ResponseData(BaseModel):
    code: int = Field(default=200, examples=[201, 204, 400, 401, 403])
    message: str = Field(default="PROCESS COMPLETED SUCCESSFULLY")
    data: dict | list | object | None = None
    timestamp: datetime = Field(default_factory=utc_now)


class BotResponse(BaseModel):
    code: int = Field(default=200, examples=[201, 204, 400, 401, 403])
    message: str = Field(default="PROCESS COMPLETED SUCCESSFULLY")
    data: dict | list | object | None = Field(default=ChatbotRequest())
    timestamp: datetime = Field(default_factory=utc_now)
