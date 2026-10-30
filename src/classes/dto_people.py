# -*- coding: utf-8 -*-
"""What the cliente-admin sends to invite a person to the client, and to change one (RF16, ADR-0029, HIG-94)."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from enums import PersonRole, PersonStatus

ROLES = tuple(role.value for role in PersonRole)
# The two states a cliente-admin sets by hand: letting a person in, or blocking them. Removing is DELETE.
SETTABLE = (PersonStatus.ACTIVE.value, PersonStatus.BLOCKED.value)
# An address, as far as Hive needs to tell: one @, no spaces, a domain with a dot. Zitadel checks the rest.
EMAIL = r"^[^@\s]{1,64}@[^@\s]{1,253}\.[^@\s]{2,63}$"


class PersonInviteRequest(BaseModel):
    """The email and the role of the person. The names only go to Zitadel, for its invitation: Hive keeps none."""
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    email: str = Field(max_length=254, pattern=EMAIL)
    role: Literal[ROLES]
    givenName: str | None = Field(default=None, min_length=1, max_length=200)
    familyName: str | None = Field(default=None, min_length=1, max_length=200)


class PersonChangeRequest(BaseModel):
    """Another role, or another state: at least one of the two."""
    model_config = ConfigDict(extra="forbid")

    role: Literal[ROLES] | None = None
    status: Literal[SETTABLE] | None = None

    @model_validator(mode="after")
    def _something(self):
        if self.role is None and self.status is None:
            raise ValueError("send a role, a status, or both")
        return self
