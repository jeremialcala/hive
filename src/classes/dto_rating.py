from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_COMMENT = 500


class RatingRequest(BaseModel):
    """
        What a person of the client thinks of an answer of the bot (RF28, HIG-111): good (`up`) or bad (`down`), and
        why if they want. The answer is said by Meta's id of what was sent, which the conversation gives.
    """
    model_config = ConfigDict(extra="forbid")

    messageId: str = Field(min_length=1, max_length=200)
    score: Literal["up", "down"]
    comment: str | None = Field(default=None, max_length=MAX_COMMENT)

    @field_validator("comment")
    @classmethod
    def _plain(cls, comment: str | None) -> str | None:
        if comment is None:
            return None
        comment = comment.strip()
        if any(ord(each) < 32 and each not in "\n\t" for each in comment):
            raise ValueError("without control characters")
        return comment or None
