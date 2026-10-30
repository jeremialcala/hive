# -*- coding: utf-8 -*-
"""
    The only way to a client's data. A repository is bound to one client when it is built, and every query it
    sends carries that client's id, so the isolation between clients does not depend on each caller remembering
    to check who owns what.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from mongoengine import Q
from mongoengine.errors import NotUniqueError, SaveConditionError

from classes import (BotVersion, ChatBot, ChatbotPhone, Client, ClientUser, Contact, ConversationMessage, Intent,
                     MessageTemplate, OutboundRequest, Rule)
from classes.entity_contact import Consent
from classes.entity_conversation_message import INCOMING
from classes.entity_outbound import FAILED, SENT
from crypto.fields import ClientCipher, normalize_email
from enums import PersonRole, PersonStatus, Status

from . import erasures, kept_bots
from .data_keys import cipher_for
from .errors import Conflict, NotFound

# What is stored encrypted with the client's data key. The documents always hold the ciphertext: whoever
# needs the value asks the repository for it, so a document saved again never writes it in clear.
BOT_SECRETS = ("verifyToken", "facebookToken", "whatsappToken", "appSecret")
BOT_CREDENTIALS = ("facebookToken", "whatsappToken", "appSecret")
PHONE_FIELDS = ("phoneNumber", "phoneId")

# How much older than what Hive did in Meta a notice of Meta may be and still count: Meta says when in whole
# seconds, and may approve a template before its creation is answered (HIG-86).
NOTICE_MARGIN = timedelta(seconds=60)

# How long a message of the history is kept at most, from when it is stored: 12 months (RNF05, ADR-0017).
HISTORY_RETENTION = timedelta(days=365)

# What is stored for a chatbot, and the field that says which one: all of it goes when the chatbot does.
OF_A_BOT = (("contacts", Contact, "botId"), ("phones", ChatbotPhone, "bot_id"), ("rules", Rule, "botId"),
            ("intents", Intent, "botId"), ("messages", ConversationMessage, "botId"),
            ("templates", MessageTemplate, "botId"), ("outbound", OutboundRequest, "botId"),
            ("versions", BotVersion, "botId"))


@dataclass(frozen=True)
class BotSecrets:
    """The secrets of a chatbot, opened. It is never stored and never goes into a response as a whole."""
    verify_token: str | None
    facebook_token: str | None
    whatsapp_token: str | None
    app_secret: str | None


def as_uuid(value) -> UUID:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except ValueError:
        # An id that cannot exist is answered like one that does not.
        raise NotFound(value) from None


class TenantRepository:

    def __init__(self, client_id: UUID):
        if not isinstance(client_id, UUID):
            raise TypeError("A repository cannot be built without the client it works for")
        self.client_id = client_id

    @property
    def _cipher(self) -> ClientCipher:
        return cipher_for(self.client_id)

    def _own(self, document, **filters):
        return document.objects(clientId=self.client_id, **filters)

    def _save_own(self, document):
        """Saves the changes to a document that is already stored."""
        if document.clientId != self.client_id:
            raise NotFound(type(document).__name__)
        try:
            # Without the condition, saving a document that was deleted in the meantime would bring part of it back.
            document.save(save_condition={"clientId": self.client_id})
        except SaveConditionError:
            raise NotFound(type(document).__name__) from None
        return document

    # --- Chatbots ---------------------------------------------------------------------------------------------------

    def add_bot(self, bot: ChatBot) -> ChatBot:
        """A new chatbot. Whatever secrets it carries are in clear, and they are encrypted here."""
        bot.clientId = self.client_id
        for field in BOT_SECRETS:
            setattr(bot, field, self._cipher.encrypt(f"ChatBot.{field}", getattr(bot, field)))
        bot.save()
        if not Client.objects(uuid=self.client_id, status=Status.ACT.value).count():
            # The client was deleted while this was on its way: nothing is left behind in its name.
            bot.delete()
            raise NotFound(self.client_id)
        return bot

    def bot_secrets(self, bot: ChatBot) -> BotSecrets:
        if bot.clientId != self.client_id:
            raise NotFound(bot.uuid)
        opened = {field: self._cipher.decrypt(f"ChatBot.{field}", getattr(bot, field)) for field in BOT_SECRETS}
        return BotSecrets(verify_token=opened["verifyToken"], facebook_token=opened["facebookToken"],
                          whatsapp_token=opened["whatsappToken"], app_secret=opened["appSecret"])

    def set_bot_credentials(self, bot_id, wabaId: str | None = None, **credentials) -> ChatBot:
        """Loads or rotates the Meta credentials of a chatbot. Only the ones given change."""
        bot = self.get_bot(bot_id)
        if wabaId is not None:
            # An id of Meta, not a secret: it is stored as it is.
            bot.wabaId = wabaId
        for field, value in credentials.items():
            if field not in BOT_CREDENTIALS:
                raise ValueError(field)
            if value is not None:
                setattr(bot, field, self._cipher.encrypt(f"ChatBot.{field}", value))
        saved = self._save_own(bot)
        # The webhook checks signatures against the secrets it was kept with.
        kept_bots.forget(saved.uuid)
        return saved

    def move_bot(self, bot_uuid: UUID, start: tuple[Status, ...], to: Status) -> bool:
        """
            Moves a chatbot to `to`, but only from one of `start`, in one update that says whether it matched: two
            processes that see the same bot do not undo each other, and nothing takes it out of LOK or DIS. Says
            whether it moved.
        """
        moved = self._own(ChatBot, uuid=bot_uuid, status__in=[each.value for each in start]).update(
            set__status=to.value, set__statusDate=datetime.now(timezone.utc))
        if moved:
            # The webhook keeps the chatbot it found: this process sees the new state at once, the others in KEEP_FOR.
            kept_bots.forget(bot_uuid)
        return bool(moved)

    def list_bots(self) -> list:
        """The chatbots of the client, oldest first."""
        return list(self._own(ChatBot).order_by("createdAt"))

    def get_bot(self, bot_id) -> ChatBot:
        bot = self._own(ChatBot, uuid=as_uuid(bot_id)).first()
        if bot is None:
            raise NotFound(bot_id)
        return bot

    def delete_bot(self, bot_id) -> dict:
        """
            Deletes a chatbot and everything stored for it, and says how many of each. What a call that failed
            halfway left behind is deleted by calling again, so it is only not found when nothing of it is left.
        """
        bot_uuid = as_uuid(bot_id)
        if not self.stored_for_bot(bot_uuid):
            raise NotFound(bot_id)
        erasures.record(erasures.CHATBOT, self.client_id, bot_id=bot_uuid)
        deleted = self.erase_bot(bot_uuid)
        if not any(deleted.values()):
            # Somebody else deleted it in the meantime.
            raise NotFound(bot_id)
        return deleted

    def stored_for_bot(self, bot_uuid: UUID) -> int:
        """How many documents there are of a chatbot, itself included."""
        return self._own(ChatBot, uuid=bot_uuid).count() + sum(
            self._own(document, **{field: bot_uuid}).count() for _, document, field in OF_A_BOT)

    def stored(self) -> int:
        """How many documents there are of the client, its chatbots and its people included."""
        return (self._own(ChatBot).count() + self._own(ClientUser).count()
                + sum(self._own(document).count() for _, document, _ in OF_A_BOT))

    # The erase_ methods delete without writing it down in the register of erasures. They are for the delete_
    # methods, which write it down first, and for making again the deletions the register holds.

    def erase_bot(self, bot_uuid: UUID) -> dict:
        """
            The chatbot goes first: from then on the webhook does not find it and nothing more is stored for it.
        """
        deleted = {"chatbots": self._own(ChatBot, uuid=bot_uuid).delete()}
        kept_bots.forget(bot_uuid)
        for name, document, field in OF_A_BOT:
            deleted[name] = self._own(document, **{field: bot_uuid}).delete()
        return deleted

    def erase_all(self) -> dict:
        """Every chatbot of the client and everything stored for them. Chatbots first, as in erase_bot."""
        deleted = {"chatbots": self._own(ChatBot).delete()}
        # Which chatbots they were is not asked for: deleting a client is rare, and the rest are found again.
        kept_bots.forget()
        for name, document, _ in OF_A_BOT:
            deleted[name] = self._own(document).delete()
        # The people of the client: nobody gets in as one of them any more.
        deleted["people"] = self._own(ClientUser).delete()
        return deleted

    def erase_contacts(self, bot_uuid: UUID, contacts: list) -> dict:
        """The contacts first, then their history: from then on no new message is stored for them."""
        return {"contacts": self._own(Contact, botId=bot_uuid, _uuid__in=list(contacts)).delete(),
                "messages": self._own(ConversationMessage, botId=bot_uuid, contactId__in=list(contacts)).delete(),
                "outbound": self._own(OutboundRequest, botId=bot_uuid, contactId__in=list(contacts)).delete()}

    def _add_to_bot(self, bot_id, document, field: str, seal=None):
        bot_uuid = self.get_bot(bot_id).uuid
        setattr(document, field, bot_uuid)
        document.clientId = self.client_id
        if seal is not None:
            # Only once the chatbot is known to be the client's: nothing is encrypted for a stranger.
            seal(document)
        document.save()
        if not self._own(ChatBot, uuid=bot_uuid).count():
            # The chatbot was deleted between the lookup and the save: this would be left behind, out of reach.
            document.delete()
            raise NotFound(bot_id)
        return document

    # --- People (ADR-0029) -------------------------------------------------------------------------------------------

    def add_person(self, email: str, issuer: str, subject: str, role: PersonRole, invited_by: str,
                   status: PersonStatus = PersonStatus.PENDING) -> ClientUser:
        """
            A person of the client, with their identity in the IdP. Their email is stored encrypted and searched by
            its blind index. Raises Conflict if the same person of the IdP is already a person of the client.
        """
        person = ClientUser(key=ClientUser.key_for(self.client_id, issuer, subject), clientId=self.client_id,
                            issuer=issuer, subject=subject, role=role.value, status=status.value, invitedBy=invited_by,
                            email=self._cipher.encrypt("ClientUser.email", normalize_email(email)),
                            emailIndex=self._cipher.blind_index("ClientUser.email", email, normalize_email))
        try:
            person.save(force_insert=True)
        except NotUniqueError:
            person = self._invite_again(person)
        if not Client.objects(uuid=self.client_id, status=Status.ACT.value).count():
            # The client was deleted while this was on its way: nobody is left behind in its name.
            person.delete()
            raise NotFound(self.client_id)
        return person

    def _invite_again(self, person: ClientUser) -> ClientUser:
        """The same person of the IdP is already in the client: only one that was removed is invited again."""
        again = self._own(ClientUser, key=person.key, status=PersonStatus.REMOVED.value).modify(
            new=True, set__email=person.email, set__emailIndex=person.emailIndex, set__role=person.role,
            set__status=person.status, set__invitedBy=person.invitedBy, set__invitedAt=datetime.now(timezone.utc),
            set__statusDate=datetime.now(timezone.utc))
        if again is None:
            raise Conflict("person")
        return again

    def list_people(self) -> list:
        """The people of the client, the removed ones too: what is left of them says who did what."""
        return list(self._own(ClientUser).order_by("invitedAt"))

    def active_admins(self) -> int:
        return self._own(ClientUser, role=PersonRole.ADMIN.value, status=PersonStatus.ACTIVE.value).count()

    def set_person_role(self, person: ClientUser, role: PersonRole) -> ClientUser:
        """Another role, from their next exchange of a token on."""
        person.role = role.value
        return self._save_own(person)

    def remove_person(self, person: ClientUser) -> ClientUser:
        """
            The person gets no token any more, ever, and their email is erased: what is left, the uuid and who they
            are in the IdP, is what the client's audit says who did what by. Inviting them again brings them back.
        """
        person.status, person.statusDate = PersonStatus.REMOVED.value, datetime.now(timezone.utc)
        person.email, person.emailIndex = None, None
        return self._save_own(person)

    def get_person(self, person_id) -> ClientUser:
        person = self._own(ClientUser, uuid=as_uuid(person_id)).first()
        if person is None:
            raise NotFound(person_id)
        return person

    def find_person_by_email(self, email: str) -> ClientUser | None:
        return self._own(ClientUser, emailIndex=self._cipher.blind_index("ClientUser.email", email,
                                                                         normalize_email)).first()

    def person_email(self, person: ClientUser) -> str | None:
        """The email, opened; None for a person who was removed."""
        if person.clientId != self.client_id:
            raise NotFound(person.uuid)
        return self._cipher.decrypt("ClientUser.email", person.email) if person.email else None

    def set_person_status(self, person: ClientUser, status: PersonStatus) -> ClientUser:
        """Lets a person in, blocks them or removes them: from their next exchange of a token on (ADR-0029, point 8)."""
        person.status, person.statusDate = status.value, datetime.now(timezone.utc)
        return self._save_own(person)

    # --- Phones -----------------------------------------------------------------------------------------------------

    def add_phone(self, bot_id, phone: ChatbotPhone) -> ChatbotPhone:
        def seal(document):
            for field in PHONE_FIELDS:
                setattr(document, field, self._cipher.encrypt(f"ChatbotPhone.{field}", getattr(document, field)))

        return self._add_to_bot(bot_id, phone, "bot_id", seal)

    def phone_numbers(self, phone: ChatbotPhone) -> dict:
        """The number and the Meta id of a phone, opened."""
        if phone.clientId != self.client_id:
            raise NotFound(phone.uuid)
        return {field: self._cipher.decrypt(f"ChatbotPhone.{field}", getattr(phone, field)) for field in PHONE_FIELDS}

    def list_phones(self, bot_id, environment: str | None = None) -> list:
        filters = {"bot_id": self.get_bot(bot_id).uuid}
        if environment is not None:
            filters["environment"] = environment
        return list(self._own(ChatbotPhone, **filters))

    # --- Rules ------------------------------------------------------------------------------------------------------

    def add_rules(self, bot_id, rules: list) -> list:
        return [self._add_to_bot(bot_id, Rule(rule=rule), "botId") for rule in rules]

    def list_rules(self, bot_id) -> list:
        return list(self._own(Rule, botId=self.get_bot(bot_id).uuid))

    def count_rules(self, bot_id) -> int:
        return self._own(Rule, botId=self.get_bot(bot_id).uuid).count()

    def get_rule(self, bot_id, rule_id) -> Rule:
        rule = self._own(Rule, botId=self.get_bot(bot_id).uuid, uuid=as_uuid(rule_id)).first()
        if rule is None:
            raise NotFound(rule_id)
        return rule

    def set_rule(self, rule: Rule, text: str) -> Rule:
        rule.rule = text
        return self._save_own(rule)

    def delete_rule(self, rule: Rule) -> None:
        if not self._own(Rule, uuid=rule.uuid).delete():
            # Somebody else deleted it in the meantime.
            raise NotFound(rule.uuid)

    def set_persona(self, bot_id, name: str, role: str, tone: str, description: str) -> ChatBot:
        """The persona of the chatbot (RF23): its name, role, tone and description, the rest as it was."""
        bot_uuid = self.get_bot(bot_id).uuid
        if not self._own(ChatBot, uuid=bot_uuid).update(set__name=name, set__role=role, set__tone=tone,
                                                        set__description=description):
            raise NotFound(bot_id)
        return self.get_bot(bot_uuid)

    # --- Intents ----------------------------------------------------------------------------------------------------

    def add_intent(self, bot_id, intent: Intent) -> Intent:
        return self._add_to_bot(bot_id, intent, "botId")

    def list_intents(self, bot_id) -> list:
        return [intent for intent in self._own(Intent, botId=self.get_bot(bot_id).uuid)
                if intent.status != Status.DIS.value]

    def get_intent(self, bot_id, intent_id) -> Intent:
        """A deleted intent is not found, as it is not listed: it is kept, disabled, but nothing reaches it."""
        intent = self._own(Intent, botId=self.get_bot(bot_id).uuid, uuid=as_uuid(intent_id)).first()
        if intent is None or intent.status == Status.DIS.value:
            raise NotFound(intent_id)
        return intent

    def save_intent(self, intent: Intent) -> Intent:
        return self._save_own(intent)

    # --- Versions of the configuration (RF26, ADR-0035) -------------------------------------------------------------

    def bot_config(self, bot_id) -> dict:
        """What a version keeps: the persona, the rules and the intents of the chatbot as they are now."""
        bot = self.get_bot(bot_id)
        intents = [intent for intent in self._own(Intent, botId=bot.uuid).order_by("createdAt")
                   if intent.status != Status.DIS.value]
        return {"persona": {"name": bot.name, "role": bot.role, "tone": bot.tone, "description": bot.description},
                "rules": [{"uuid": str(rule.uuid), "rule": rule.rule} for rule in self.list_rules(bot.uuid)],
                "intents": [{key: value for key, value in intent.intent_info().items() if key != "botId"}
                            for intent in intents]}

    def add_version(self, bot_id, author: str, change: str, restored_from: int | None = None) -> BotVersion:
        """
            Writes the configuration the chatbot has now as its next version. The number comes from the chatbot, in one
            update: two changes at once get two numbers.
        """
        bot_uuid = self.get_bot(bot_id).uuid
        config = self.bot_config(bot_uuid)
        bot = self._own(ChatBot, uuid=bot_uuid).modify(inc__configVersion=1, new=True)
        if bot is None:
            raise NotFound(bot_id)
        version = BotVersion(id=f"{bot_uuid}:{bot.configVersion}", clientId=self.client_id, botId=bot_uuid,
                             version=bot.configVersion, author=author, change=change, restoredFrom=restored_from,
                             config=config)
        version.save(force_insert=True)
        if not self._own(ChatBot, uuid=bot_uuid).count():
            # The chatbot was deleted in the meantime: its versions go with it.
            version.delete()
            raise NotFound(bot_id)
        return version

    def list_versions(self, bot_id, offset: int = 0, limit: int = 100) -> tuple:
        """How many versions the chatbot has, and a page of them, the newest first."""
        found = self._own(BotVersion, botId=self.get_bot(bot_id).uuid)
        return found.count(), list(found.order_by("-version").skip(offset).limit(limit))

    def get_version(self, bot_id, number: int) -> BotVersion:
        version = self._own(BotVersion, botId=self.get_bot(bot_id).uuid, version=number).first()
        if version is None:
            raise NotFound(number)
        return version

    def apply_config(self, bot_id, config: dict) -> None:
        """
            Gives the chatbot the persona, rules and intents of `config`, a version's. Not in one transaction: what a
            call that failed halfway left is put right by calling again. An intent left out is deleted, as DELETE does.
        """
        bot_uuid = self.get_bot(bot_id).uuid
        persona = config["persona"]
        self.set_persona(bot_uuid, persona["name"], persona["role"], persona["tone"], persona["description"])
        self._own(Rule, botId=bot_uuid).delete()
        for rule in config["rules"]:
            Rule(uuid=UUID(rule["uuid"]), clientId=self.client_id, botId=bot_uuid, rule=rule["rule"]).save(
                force_insert=True)
        kept = {UUID(intent["uuid"]) for intent in config["intents"]}
        now = datetime.now(timezone.utc)
        for intent in self._own(Intent, botId=bot_uuid, status__ne=Status.DIS.value):
            if intent.uuid not in kept:
                self._own(Intent, uuid=intent.uuid).update(set__status=Status.DIS.value, set__statusDate=now)
        for kept_intent in config["intents"]:
            intent_uuid = UUID(kept_intent["uuid"])
            intent = (self._own(Intent, botId=bot_uuid, uuid=intent_uuid).first()
                      or Intent(uuid=intent_uuid, clientId=self.client_id, botId=bot_uuid))
            intent.name, intent.description = kept_intent["name"], kept_intent["description"]
            intent.samplePhrases = list(kept_intent["samplePhrases"])
            intent.actions = [Intent.action_from(action) for action in kept_intent["actions"]]
            intent.status, intent.statusDate = Status[kept_intent["status"]].value, now
            intent.save()
        if not self._own(ChatBot, uuid=bot_uuid).count():
            # Deleted while this was written: nothing is left behind out of reach.
            self.erase_bot(bot_uuid)
            raise NotFound(bot_id)

    # --- Contacts ---------------------------------------------------------------------------------------------------

    def find_contact_by_wa_id(self, bot_id, wa_id: str) -> Contact | None:
        # The number is not stored in clear, so it is looked up by its blind index.
        bot = self.get_bot(bot_id)
        return self._last_contact(bot.uuid, waIdIndex=self._cipher.blind_index("Contact.waId", wa_id))

    def find_contact_by_fb_id(self, bot_id, fb_id: str) -> Contact | None:
        return self._last_contact(self.get_bot(bot_id).uuid, fbId=fb_id)

    def _last_contact(self, bot_uuid: UUID, **identity) -> Contact | None:
        # Only for a chatbot already found to be the client's: asking again would cost another round trip.
        contacts = list(self._own(Contact, botId=bot_uuid, **identity))
        return contacts[-1] if contacts else None

    def add_contact(self, bot_id, contact: Contact) -> Contact:
        def seal(document):
            if document.waId is not None:
                document.waIdIndex = self._cipher.blind_index("Contact.waId", document.waId)
                document.waId = self._cipher.encrypt("Contact.waId", document.waId)

        return self._add_to_bot(bot_id, contact, "botId", seal)

    def contact_number(self, contact: Contact) -> str | None:
        if contact.clientId != self.client_id:
            raise NotFound(contact._uuid)
        return self._cipher.decrypt("Contact.waId", contact.waId)

    def list_contacts(self, bot_id, offset: int = 0, limit: int = 100) -> tuple:
        """How many contacts the chatbot has, and one page of them, oldest first."""
        contacts = self._own(Contact, botId=self.get_bot(bot_id).uuid)
        return contacts.count(), list(contacts.order_by("id").skip(offset).limit(limit))

    def find_contacts(self, wa_id: str | None = None, fb_id: str | None = None) -> list:
        """
            One person in every chatbot of the client, by number or by Messenger id: whoever asks for their data
            asks the client, not one of its chatbots.
        """
        if (wa_id is None) == (fb_id is None):
            raise ValueError("Either a number or a Messenger id")
        if wa_id is not None:
            identity = {"waIdIndex": self._cipher.blind_index("Contact.waId", wa_id)}
        else:
            identity = {"fbId": fb_id}
        return list(self._own(Contact, **identity).order_by("id"))

    def get_contact(self, bot_id, contact_id) -> Contact:
        contact = self._own(Contact, botId=self.get_bot(bot_id).uuid, _uuid=as_uuid(contact_id)).first()
        if contact is None:
            raise NotFound(contact_id)
        return contact

    def contact_copies(self, bot_id, contact_id) -> tuple[UUID, list[UUID]]:
        """
            The chatbot of a contact and every contact of that same person in it. Two first messages arriving at once
            can store the same person twice: deleting one would leave the other.
        """
        contact = self.get_contact(bot_id, contact_id)
        same = Q(_uuid=contact._uuid)
        if contact.waIdIndex:
            same |= Q(waIdIndex=contact.waIdIndex)
        if contact.fbId:
            same |= Q(fbId=contact.fbId)
        return contact.botId, [copy._uuid for copy in self._own(Contact, botId=contact.botId).filter(same)]

    def delete_contact(self, bot_id, contact_id) -> dict:
        """Deletes a contact of a chatbot: every document of that person for that chatbot goes."""
        bot_uuid, copies = self.contact_copies(bot_id, contact_id)
        erasures.record(erasures.CONTACT, self.client_id, bot_id=bot_uuid, contacts=copies)
        return self.erase_contacts(bot_uuid, copies)

    def set_consent(self, contact: Contact, categories: list[str], source: str, granted_at: datetime) -> Contact:
        """Records that the contact agreed to the client's messages of those categories (RF31). It replaces any before."""
        contact.consent = Consent(categories=list(categories), source=source, grantedAt=granted_at)
        return self._save_own(contact)

    def withdraw_consent(self, contact: Contact, when: datetime) -> Contact:
        """The contact withdrew it: it is kept, with when, and nothing outside the window is sent any more."""
        if contact.consent is None:
            # There was nothing to withdraw: nothing changes, and nothing is sent outside the window either way.
            return contact
        if contact.consent.withdrawnAt is None:
            contact.consent.withdrawnAt = when
        return self._save_own(contact)

    # --- What the client sends to its contacts (ADR-0031) ------------------------------------------------------------

    def last_phone_id(self, contact: Contact) -> str | None:
        """The phone_number_id of the bot's number the contact last wrote to, opened; None if not known yet."""
        if contact.clientId != self.client_id:
            raise NotFound(contact._uuid)
        return self._cipher.decrypt("Contact.lastPhoneId", contact.lastPhoneId) if contact.lastPhoneId else None

    def remember_phone(self, contact: Contact, phone_id: str) -> None:
        """The bot's number the contact wrote to now, encrypted, if it is another: only then is anything written."""
        if contact.clientId != self.client_id or not phone_id or self.last_phone_id(contact) == phone_id:
            return
        sealed = self._cipher.encrypt("Contact.lastPhoneId", phone_id)
        self._own(Contact, _uuid=contact._uuid).update_one(set__lastPhoneId=sealed)
        contact.lastPhoneId = sealed

    def first_phone_id(self, bot_uuid: UUID) -> str | None:
        """The phone_number_id of the first number registered for the bot, opened; None if it has none."""
        phone = self._own(ChatbotPhone, bot_id=bot_uuid).order_by("id").first()
        return self.phone_numbers(phone)["phoneId"] if phone is not None else None

    def find_template(self, bot_uuid: UUID, name: str, language: str) -> MessageTemplate | None:
        return self._own(MessageTemplate, key=MessageTemplate.key_for(bot_uuid, name, language)).first()

    def list_templates(self, bot_uuid: UUID) -> list:
        return list(self._own(MessageTemplate, botId=bot_uuid).order_by("name", "language"))

    def template_by_meta_id(self, bot_uuid: UUID, meta_id: str) -> MessageTemplate | None:
        return self._own(MessageTemplate, botId=bot_uuid, metaId=meta_id).first()

    def save_template(self, bot: ChatBot, template: MessageTemplate) -> MessageTemplate:
        """Hive's copy of a template of one of the client's chatbots, over the one it had of it."""
        if bot.clientId != self.client_id:
            raise NotFound(bot.uuid)
        template.clientId, template.botId = self.client_id, bot.uuid
        template.key = MessageTemplate.key_for(bot.uuid, template.name, template.language)
        template.updatedAt = datetime.now(timezone.utc)
        # What Hive did or saw in Meta now: a notice of Meta from a little before still counts (HIG-86).
        template.statusAt = template.updatedAt - NOTICE_MARGIN
        template.save()
        if not self._own(ChatBot, uuid=bot.uuid).count():
            # The chatbot was deleted between the lookup and the save: this would be left behind, out of reach.
            template.delete()
            raise NotFound(bot.uuid)
        return template

    def apply_template_notice(self, bot_uuid: UUID, meta_id: str, status: str | None, reason: str | None,
                              at: datetime) -> str:
        """
            Applies a notice of Meta to the copy of a template, in one write that only goes through if the copy is
            not newer than the notice: a notice delivered late does not undo one that came after it. With status
            None, it only says as of when. Says whether it was applied, older, or of a template Hive does not have.
        """
        if not self._own(MessageTemplate, botId=bot_uuid, metaId=meta_id).count():
            return "unknown"
        changes = {"set__statusAt": at, "set__updatedAt": datetime.now(timezone.utc)}
        if status is not None:
            changes.update(set__status=status, set__statusReason=reason)
        applied = self._own(MessageTemplate, botId=bot_uuid, metaId=meta_id).filter(
            Q(statusAt=None) | Q(statusAt__lte=at)).update_one(**changes)
        return "applied" if applied else "older"

    def forget_templates(self, bot_uuid: UUID, keep: set) -> int:
        """Removes the copies of the templates Meta does not have any more: how many."""
        return self._own(MessageTemplate, botId=bot_uuid, key__nin=sorted(keep)).delete()

    def delete_template(self, template: MessageTemplate) -> None:
        self._own(MessageTemplate, key=template.key).delete()

    def outbound_request(self, idempotency_key: str) -> OutboundRequest | None:
        return self._own(OutboundRequest, key=OutboundRequest.key_for(self.client_id, idempotency_key)).first()

    def remember_outbound(self, request: OutboundRequest) -> bool:
        """Stores a request under its idempotency key. False if another one with the same key got there first."""
        if request.clientId != self.client_id:
            raise NotFound(request.uuid)
        try:
            request.save(force_insert=True)
        except NotUniqueError:
            return False
        return True

    def outbound_of(self, contact: Contact, request_id) -> OutboundRequest | None:
        """A request to send to that contact, by its uuid, while it is remembered (24 hours)."""
        if contact.clientId != self.client_id:
            raise NotFound(contact._uuid)
        return self._own(OutboundRequest, uuid=as_uuid(request_id), botId=contact.botId,
                         contactId=contact._uuid).first()

    def outbound_sent(self, request_id: UUID, message_id: str, sent_at: datetime) -> bool:
        """Meta took the request: its message id. Only a request not failed meanwhile; says whether it changed."""
        return bool(self._own(OutboundRequest, uuid=request_id, status__ne=FAILED).update_one(
            set__status=SENT, set__messageId=message_id, set__sentAt=sent_at))

    def outbound_failed(self, request_id: UUID, code: int | None, reason: str, failed_at: datetime) -> bool:
        """The request will not be sent: why. Not one already sent; says whether it changed."""
        return bool(self._own(OutboundRequest, uuid=request_id, status__ne=SENT).update_one(
            set__status=FAILED, set__failureCode=code, set__failureReason=reason, set__failedAt=failed_at))

    def forget_outbound(self, request: OutboundRequest) -> None:
        """A request the bus did not take: the same key can be tried again."""
        self._own(OutboundRequest, key=request.key).delete()

    # --- History ----------------------------------------------------------------------------------------------------

    def last_incoming_at(self, contact: Contact) -> datetime | None:
        """When the contact last wrote, which opens Meta's window of 24 hours; None if it never did."""
        if contact.clientId != self.client_id:
            raise NotFound(contact._uuid)
        last = (self._own(ConversationMessage, botId=contact.botId, contactId=contact._uuid, direction=INCOMING)
                .order_by("-sentAt").only("sentAt").first())
        return last.sentAt if last else None

    def has_message(self, bot_uuid: UUID, direction: str, message_id: str) -> bool:
        """Whether a message is already in the history: one Meta sends again is."""
        return bool(self._own(ConversationMessage, key=ConversationMessage.key_for(bot_uuid, direction,
                                                                                  message_id)).count())

    def record_message(self, contact: Contact, direction: str, message_id: str, channel: str, sent_at: datetime,
                       text: str | None = None, attachments: tuple = (), intent: str | None = None,
                       in_reply_to: str | None = None, origin: str | None = None,
                       sent_by: str | None = None) -> bool:
        """
            Stores one message of the contact's conversation, with its text encrypted. False if it was already
            stored: storing it twice changes nothing, and does not tell whoever sent it again apart.
        """
        if contact.clientId != self.client_id:
            raise NotFound(contact._uuid)
        now = datetime.now(timezone.utc)
        message = ConversationMessage(
            key=ConversationMessage.key_for(contact.botId, direction, message_id), clientId=self.client_id,
            botId=contact.botId, contactId=contact._uuid, direction=direction, messageId=message_id,
            channel=channel, sentAt=sent_at, text=self._cipher.encrypt("Message.text", text),
            attachments=list(attachments), intent=intent, inReplyTo=in_reply_to, origin=origin, sentBy=sent_by,
            createdAt=now,
            expiresAt=now + HISTORY_RETENTION)
        try:
            message.save(force_insert=True)
        except NotUniqueError:
            return False
        if not self._own(Contact, _uuid=contact._uuid).count():
            # The contact was deleted between finding it and storing this: nothing is left behind in its name.
            message.delete()
            raise NotFound(contact._uuid)
        return True

    def recent_history(self, contact: Contact, limit: int = 50) -> list[dict]:
        """The last `limit` messages of the contact's conversation, oldest first, with each text opened."""
        if contact.clientId != self.client_id:
            raise NotFound(contact._uuid)
        last = list(self._own(ConversationMessage, botId=contact.botId, contactId=contact._uuid)
                    .order_by("-sentAt").limit(limit))
        return self._opened(reversed(last))

    def history(self, contact: Contact) -> list[dict]:
        """The contact's conversation, oldest first, with each text opened."""
        if contact.clientId != self.client_id:
            raise NotFound(contact._uuid)
        return self._opened(self._own(ConversationMessage, botId=contact.botId,
                                      contactId=contact._uuid).order_by("sentAt"))

    def last_turns(self, contact: Contact, turns: int = 10) -> list[dict]:
        """
            The last turns of the contact's conversation, oldest first: from the `turns`-th last message the contact
            sent, everything that came after, both ways (RF15). What the engine's coordinator rebuilds its context
            with when it has none.
        """
        if contact.clientId != self.client_id:
            raise NotFound(contact._uuid)
        conversation = self._own(ConversationMessage, botId=contact.botId, contactId=contact._uuid)
        starts = list(conversation.filter(direction=INCOMING).order_by("-sentAt").limit(turns).only("sentAt"))
        if not starts:
            return []
        return self._opened(conversation.filter(sentAt__gte=starts[-1].sentAt).order_by("sentAt"))

    def _opened(self, messages) -> list[dict]:
        return [{"direction": message.direction, "messageId": message.messageId, "channel": message.channel,
                 "sentAt": message.sentAt.replace(tzinfo=timezone.utc), "intent": message.intent,
                 "inReplyTo": message.inReplyTo, "origin": message.origin, "sentBy": message.sentBy,
                 "text": self._cipher.decrypt("Message.text", message.text),
                 "attachments": list(message.attachments)} for message in messages]
