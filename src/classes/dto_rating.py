from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_COMMENT = 500


class RatingRequest(BaseModel):
    """
        What a person of the client thinks of an answer of the bot (RF28, HIG-111): good (`up`) or bad (`down`), and
        why if they want. The answer is said by its reference, `ref` in the conversation: never Meta's id, which in
        WhatsApp carries the contact's number (HIG-149).
    """
    model_config = ConfigDict(extra="forbid")

    ref: str = Field(pattern=r"^[0-9a-f]{24}$")
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
