# -*- coding: utf-8 -*-
import logging
from uuid import uuid4

from mongoengine import *

from enums import Status
from .dto_facebook_info import ProfileInfo
from .tool_settings import Settings
from .tool_database import connect_database
from .tool_clock import utc_now

settings = Settings()
log = logging.getLogger(settings.environment)
connect_database(settings)


class Consent(EmbeddedDocument):
    """
        That the contact agreed to get the client's messages (RF31, ADR-0031): which categories, when, and where the
        client says it got it. Withdrawing it keeps the record, with when: the consent was there until then.
    """
    categories = ListField(StringField(), required=True)
    source = StringField(required=True, max_length=200)
    grantedAt = DateTimeField(required=True)
    withdrawnAt = DateTimeField()


class Contact(Document):
    _uuid = UUIDField(required=True, unique=True, default=uuid4)
    clientId = UUIDField(required=True)
    botId = UUIDField(required=True)
    firstName = StringField()
    lastName = StringField()
    profilePic = URLField()
    fbId = StringField()
    waId = StringField()
    # The number is stored encrypted. This is what it is searched by: a keyed hash of it, different per client.
    waIdIndex = StringField()
    isAdmin = BooleanField(default=False)
    createdAt = DateTimeField(required=True, default=utc_now)
    status = IntField(required=True, default=Status.REG.value)
    statusDate = DateTimeField(required=True, default=utc_now)
    # It goes with the contact: exported and deleted with it.
    consent = EmbeddedDocumentField(Consent)
    # The phone_number_id of the bot's number the contact last wrote to: a client's message goes from there (ADR-0031).
    # Encrypted with the client's data key, like the bot's numbers themselves (RS05).
    lastPhoneId = StringField()

    @staticmethod
    def generate_contact_from_fb(botId: str, fbId: str, fb_info: ProfileInfo):
        """`fbId` is the sender of the message: who the contact is does not depend on what the Graph API says."""
        return Contact(
            _uuid=uuid4(),
            botId=botId,
            firstName=fb_info.first_name,
            lastName=fb_info.last_name,
            profilePic=fb_info.profile_pic,
            fbId=fbId
        )
