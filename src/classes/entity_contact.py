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
