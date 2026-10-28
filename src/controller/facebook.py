# -*- coding: utf8 -*-
"""
    Who wrote: the contact behind each message the ingestion worker takes. These are functions and not coroutines:
    they wait on MongoDB, the key service and the Graph API, and the ingestion runs them in a thread (H14), so that
    a slow answer holds only the message that asked for it.
"""
import logging
from inspect import currentframe
from uuid import uuid4

import requests
from fastapi import status

from classes import ChatBot, Contact, ProfileInfo
from classes import Settings
from crypto.key_service import KeyServiceUnavailable
from repository import TenantRepository, database_failed
from utils import describe

__all__ = ["ctr_get_contact_from_facebook_id", "ctr_identify_fb_contact", "ctr_identify_contact_by_wa_id"]

settings = Settings()
log = logging.getLogger(settings.environment)


def ctr_get_contact_from_facebook_id(fb_id: str, _bot: ChatBot) -> Contact:
    """
        A new Messenger contact, with the profile the Graph API gives for it. Without the profile, the contact is
        stored all the same, known only by its identifier.
    """
    log.info(f"Starting: {currentframe().f_code.co_name}")
    try:
        _token = TenantRepository(_bot.clientId).bot_secrets(_bot).facebook_token
        url = settings.facebook_graph_url.format(fb_id=fb_id, ACCESSTOKEN=_token)
        try:
            resp = requests.get(url, timeout=5)
        except requests.RequestException as e:
            # The exception text carries the URL, and the URL carries the page access token.
            raise Exception(f"Graph API request failed: {type(e).__name__}") from None
        if resp.status_code != status.HTTP_200_OK:
            # Its body is not logged nor kept in the error: it can carry the profile that was asked for.
            log.error(f"The Graph API answered {resp.status_code}")
            raise Exception(f"The Graph API answered {resp.status_code}")

        _profile = ProfileInfo(**resp.json())
        log.info(f"Ending: {currentframe().f_code.co_name}")
        return Contact.generate_contact_from_fb(_bot.uuid, fb_id, _profile)

    except KeyServiceUnavailable:
        raise

    except Exception as e:
        if database_failed(e):
            # The token of the page could not be read. Not the same as the Graph API not giving the profile.
            raise
        log.error(f"No profile from the Graph API, the contact is stored without it: {describe(e)}")
        # The message does not depend on the name: whoever wrote is known by its identifier. The Graph API
        # refuses profiles for reasons that asking again does not change, such as a permission the page lacks.
        return Contact(_uuid=uuid4(), fbId=fb_id)


def ctr_identify_fb_contact(fbId: str, _bot: ChatBot) -> Contact:
    """The Messenger contact who wrote, found or stored under the client that owns the chatbot."""
    log.info(f"Starting: {currentframe().f_code.co_name}")
    try:
        repo = TenantRepository(_bot.clientId)
        contact = repo.find_contact_by_fb_id(_bot.uuid, fbId)
        if contact is None:
            contact = ctr_get_contact_from_facebook_id(fb_id=fbId, _bot=_bot)
            repo.add_contact(_bot.uuid, contact)
        log.info(f"Contact {contact._uuid} identified")
    except KeyServiceUnavailable:
        # Not a failure of this message: Meta has to be told to send it again.
        raise
    except Exception as e:
        log.error(describe(e))
        raise
    finally:
        log.info(f"Ending: {currentframe().f_code.co_name}")
    return contact


def ctr_identify_contact_by_wa_id(wa_id: str, name: str, _bot: ChatBot) -> Contact:
    """The WhatsApp contact who wrote, found by its number or stored under the client that owns the chatbot."""
    log.info(f"Starting: {currentframe().f_code.co_name}")
    try:
        repo = TenantRepository(_bot.clientId)
        contact = repo.find_contact_by_wa_id(_bot.uuid, wa_id)
        if contact is None:
            contact = repo.add_contact(_bot.uuid, Contact(_uuid=uuid4(), firstName=name, waId=wa_id))
        log.info(f"Contact {contact._uuid} identified")
    except KeyServiceUnavailable:
        # Not a failure of this message: Meta has to be told to send it again.
        raise
    except Exception as e:
        log.error(describe(e))
        raise
    finally:
        log.info(f"Ending: {currentframe().f_code.co_name}")
    return contact
