# -*- coding: utf-8 -*-
"""
    What a client needs to answer somebody who asks for their data, and what happens when a chatbot or a client
    leaves: listing and finding contacts, exporting one, deleting one, deleting a chatbot with everything stored
    for it, and the operator deleting a client.

    The tests go through the application with real tokens and signed notifications, and then look at the
    database the way somebody with a copy of it would.
"""
import hashlib
import hmac
import json
import logging
from types import SimpleNamespace
from uuid import uuid4

import mongoengine
import pytest
from fastapi.testclient import TestClient

import app as app_module
import webhook as webhook_module
import controller.facebook as facebook
import controller.ingestion as ingestion
import repository.clients as clients
from classes import ChatBot, ChatbotPhone, Client, Contact, Intent, Rule, SeenToken
from conftest import request_token
from enums import Status
from repository import NotFound, TenantRepository, data_keys, delete_client, operator_client

# What Meta calls is its own service, apart from the management API.
META = TestClient(webhook_module.app)

BOT = {"name": "Mine", "role": "Sales Assistant", "description": "A test chatbot", "tone": "helpful"}
APP_SECRET = "app-secret-of-this-test"
SECRETS = {"facebookToken": "EAAG-page-token-0123456789", "appSecret": APP_SECRET}
PHONE = {"phoneNumber": "12025550101", "phoneId": "100000000000001"}
INTENT = {"name": "check_order", "description": "Order status"}
ANA, ANA_NUMBER = "Ana", "12025550143"
LUIS, LUIS_NUMBER = "Luis", "12025550177"
PSID = "123456789"
PROFILE = {"id": PSID, "first_name": "Carla", "last_name": "Diaz", "profile_pic": "https://example.com/c.jpg"}
PERSONAL = (ANA_NUMBER, LUIS_NUMBER, PSID, ANA, LUIS, "Carla", "Diaz")
STORED = (ChatBot, ChatbotPhone, Rule, Intent, Contact)


def whatsapp(number: str, name: str) -> dict:
    return {"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "12025550101", "phone_number_id": "100000000000001"},
        "contacts": [{"profile": {"name": name}, "wa_id": number}],
        "messages": [{"from": number, "id": f"wamid.{uuid4()}", "timestamp": "1760000000", "type": "text",
                      "text": {"body": "hola"}}]}}]}]}


def messenger(psid: str = PSID) -> dict:
    return {"object": "page", "entry": [{"id": "1", "time": 1760000000000, "messaging": [{
        "sender": {"id": psid}, "recipient": {"id": "987654321"}, "timestamp": 1760000000000,
        "message": {"mid": f"m_{uuid4()}", "text": "hola"}}]}]}


@pytest.fixture
def http():
    return TestClient(app_module.app)


@pytest.fixture(autouse=True)
def queue(monkeypatch):
    sent = []

    async def capture(queue, message):
        sent.append(json.loads(message.to_json()))

    monkeypatch.setattr(ingestion, "send_message_to_queue", capture)
    return sent


@pytest.fixture(autouse=True)
def graph_api(monkeypatch):
    get = lambda url, timeout: SimpleNamespace(status_code=200, text="{}", json=lambda: PROFILE)  # noqa: E731
    monkeypatch.setattr(facebook.requests, "get", get)


@pytest.fixture
def caller(register):
    return register("acme")


@pytest.fixture
def stranger(register):
    return register("globex")


def new_bot(http, caller) -> str:
    """A chatbot made through the API, with one of everything a client can load into it."""
    bot_id = http.post("/bot", headers=caller.auth, json=BOT).json()["data"]["uuid"]
    for method, path, body in (("PUT", "credentials", SECRETS), ("POST", "phones", PHONE),
                               ("POST", "rules", {"rules": ["Be kind"]}), ("POST", "intents", INTENT)):
        assert http.request(method, f"/chat/{bot_id}/{path}", headers=caller.auth, json=body).status_code == 200
    return bot_id


@pytest.fixture
def bot_id(http, caller):
    return new_bot(http, caller)


def notify(http, bot_id, payload):
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return META.post(f"/chat/{bot_id}", content=body,
                     headers={"content-type": "application/json", "X-Hub-Signature-256": signature})


@pytest.fixture
def people(http, bot_id):
    """Three people wrote to the chatbot: two by WhatsApp and one by Messenger."""
    for payload in (whatsapp(ANA_NUMBER, ANA), whatsapp(LUIS_NUMBER, LUIS), messenger()):
        assert notify(http, bot_id, payload).status_code == 200
    return bot_id


def contacts_of(http, caller, bot_id, **page) -> dict:
    response = http.get(f"/chat/{bot_id}/contacts", headers=caller.auth, params=page)
    assert response.status_code == 200
    return response.json()["data"]


def search(http, caller, **who):
    return http.post("/contacts/search", headers=caller.auth, json=who)


def erase(http, caller, path):
    return http.delete(path, headers=caller.auth)


def dump() -> str:
    """Everything in the database, as text: what a copy of it shows."""
    database = mongoengine.get_db()
    return json.dumps({name: list(database[name].find({})) for name in database.list_collection_names()},
                      default=str)


def counts(**filters) -> dict:
    return {document.__name__: document.objects(**filters).count() for document in STORED}


# --- Listing and finding contacts --------------------------------------------------------------------------------

def test_the_contacts_of_a_chatbot_are_listed_with_what_is_stored_about_them(http, caller, people):
    page = contacts_of(http, caller, people)

    assert (page["total"], page["offset"], page["limit"]) == (3, 0, 100)
    assert [(c["firstName"], c["lastName"], c["waId"], c["fbId"]) for c in page["contacts"]] == [
        (ANA, None, ANA_NUMBER, None), (LUIS, None, LUIS_NUMBER, None), ("Carla", "Diaz", None, PSID)]
    assert {c["botId"] for c in page["contacts"]} == {people}


def test_a_chatbot_nobody_wrote_to_has_no_contacts(http, caller, bot_id):
    assert contacts_of(http, caller, bot_id) == {"total": 0, "offset": 0, "limit": 100, "contacts": []}


def test_the_list_comes_a_page_at_a_time_and_misses_nobody(http, caller, bot_id):
    numbers = [f"1202555{n:04d}" for n in range(7)]
    for number in numbers:
        notify(http, bot_id, whatsapp(number, "Somebody"))

    pages = [contacts_of(http, caller, bot_id, offset=offset, limit=3) for offset in (0, 3, 6, 9)]

    assert [page["total"] for page in pages] == [7, 7, 7, 7]
    assert [len(page["contacts"]) for page in pages] == [3, 3, 1, 0]
    assert [c["waId"] for page in pages for c in page["contacts"]] == numbers


@pytest.mark.parametrize("page", [{"limit": 0}, {"limit": 501}, {"offset": -1}, {"limit": "many"}])
def test_a_page_that_makes_no_sense_is_rejected(http, caller, bot_id, page):
    assert http.get(f"/chat/{bot_id}/contacts", headers=caller.auth, params=page).status_code == 422


def test_a_person_is_found_in_every_chatbot_of_the_client(http, caller, people):
    other = new_bot(http, caller)
    notify(http, other, whatsapp(ANA_NUMBER, ANA))
    notify(http, other, messenger())

    by_number = search(http, caller, waId=ANA_NUMBER)
    by_psid = search(http, caller, fbId=PSID)

    assert by_number.status_code == by_psid.status_code == 200
    assert [(c["waId"], c["botId"]) for c in by_number.json()["data"]] == [(ANA_NUMBER, people), (ANA_NUMBER, other)]
    assert [(c["fbId"], c["botId"]) for c in by_psid.json()["data"]] == [(PSID, people), (PSID, other)]


@pytest.mark.parametrize("written", ["+1 (202) 555-0143", "1 202 555 0143", "1.202.555.0143"])
def test_a_number_is_found_however_it_is_written(http, caller, people, written):
    assert [c["firstName"] for c in search(http, caller, waId=written).json()["data"]] == [ANA]


def test_somebody_who_never_wrote_is_not_found(http, caller, people):
    for who in ({"waId": "12025550999"}, {"fbId": "000"}, {"fbId": ANA_NUMBER}, {"waId": PSID}):
        found = search(http, caller, **who)

        assert (found.status_code, found.json()["data"]) == (200, [])


def test_a_search_only_finds_the_contacts_of_whoever_asks(http, caller, stranger, people):
    theirs = new_bot(http, stranger)
    notify(http, theirs, whatsapp(ANA_NUMBER, ANA))

    assert [c["botId"] for c in search(http, stranger, waId=ANA_NUMBER).json()["data"]] == [theirs]
    assert [c["botId"] for c in search(http, caller, waId=ANA_NUMBER).json()["data"]] == [people]
    assert search(http, stranger, waId=LUIS_NUMBER).json()["data"] == []
    assert search(http, stranger, fbId=PSID).json()["data"] == []


@pytest.mark.parametrize("who", [
    {}, {"waId": ANA_NUMBER, "fbId": PSID}, {"waId": ""}, {"waId": "no digits"}, {"waId": "1" * 33},
    {"fbId": ""}, {"fbId": "9" * 65}, {"waId": None}, {"waId": ANA_NUMBER, "name": ANA}, {"waId": 12025550143},
])
def test_a_search_that_is_not_well_formed_is_rejected_without_repeating_it(http, caller, people, who):
    response = search(http, caller, **who)

    assert response.status_code == 422
    assert ANA_NUMBER not in response.text and PSID not in response.text


def test_the_repository_looks_for_one_identity_at_a_time(caller):
    repo = TenantRepository(caller.client_id)

    for who in ({}, {"wa_id": ANA_NUMBER, "fb_id": PSID}):
        with pytest.raises(ValueError):
            repo.find_contacts(**who)


def test_the_number_never_travels_in_an_address():
    """An address is written down by every proxy on the way: whoever is looked for goes in the body."""
    routes = [route for route in app_module.app.routes if "contacts" in getattr(route, "path", "")]

    assert len(routes) == 5
    for route in routes:
        assert {parameter.name for parameter in route.dependant.query_params} <= {"offset", "limit"}
        assert {parameter.name for parameter in route.dependant.path_params} <= {"bot_id", "contact_id"}


# --- Exporting a contact -----------------------------------------------------------------------------------------

def test_the_export_is_everything_stored_about_the_contact(http, caller, people):
    """If a field is added to the contact and left out of the export, this fails."""
    internal = {"id", "clientId", "waIdIndex"}
    contact = contacts_of(http, caller, people)["contacts"][0]

    exported = http.get(f"/chat/{people}/contacts/{contact['uuid']}", headers=caller.auth)

    assert exported.status_code == 200
    # The export adds the conversation: the one message Ana sent. What it holds is in test_history.py.
    assert exported.json()["data"] == {**contact, "messages": exported.json()["data"]["messages"]}
    assert [m["direction"] for m in exported.json()["data"]["messages"]] == ["in"]
    assert set(contact) == {name.lstrip("_") for name in Contact._fields if name not in internal} | {"botId"}
    assert (contact["firstName"], contact["waId"], contact["isAdmin"], contact["status"]) == (
        ANA, ANA_NUMBER, False, Status.REG.name)


def test_a_contact_from_messenger_is_exported_with_its_profile(http, caller, people):
    contact = search(http, caller, fbId=PSID).json()["data"][0]

    assert (contact["firstName"], contact["lastName"], contact["profilePic"], contact["fbId"], contact["waId"]) == (
        "Carla", "Diaz", PROFILE["profile_pic"], PSID, None)


@pytest.mark.parametrize("contact_id", [str(uuid4()), "not-a-uuid"])
def test_a_contact_that_does_not_exist_is_not_found(http, caller, people, contact_id):
    for method in ("GET", "DELETE"):
        response = http.request(method, f"/chat/{people}/contacts/{contact_id}", headers=caller.auth)

        assert (response.status_code, response.json()["code"]) == (404, 404)
    assert Contact.objects.count() == 3


# --- Deleting a contact ------------------------------------------------------------------------------------------

def test_a_deleted_contact_is_gone_and_the_others_stay(http, caller, people):
    ana = search(http, caller, waId=ANA_NUMBER).json()["data"][0]
    assert ana["uuid"] in dump()

    deleted = erase(http, caller, f"/chat/{people}/contacts/{ana['uuid']}")

    assert deleted.status_code == 200
    assert deleted.json()["data"] == {"uuid": ana["uuid"], "deleted": {"contacts": 1, "messages": 1}}
    assert ana["uuid"] not in dump()
    assert search(http, caller, waId=ANA_NUMBER).json()["data"] == []
    assert http.get(f"/chat/{people}/contacts/{ana['uuid']}", headers=caller.auth).status_code == 404
    assert erase(http, caller, f"/chat/{people}/contacts/{ana['uuid']}").status_code == 404
    assert [c["firstName"] for c in contacts_of(http, caller, people)["contacts"]] == [LUIS, "Carla"]


@pytest.mark.parametrize("who,again", [({"waId": ANA_NUMBER}, lambda: Contact(firstName=ANA, waId=ANA_NUMBER)),
                                       ({"fbId": PSID}, lambda: Contact(firstName="Carla", fbId=PSID))],
                         ids=["whatsapp", "messenger"])
def test_deleting_a_contact_takes_every_copy_of_that_person_in_the_chatbot(http, caller, people, who, again):
    """Two first messages arriving at once at two workers store the same person twice."""
    repo = TenantRepository(caller.client_id)
    repo.add_contact(people, again())
    other = new_bot(http, caller)
    repo.add_contact(other, again())
    copies = [c for c in search(http, caller, **who).json()["data"] if c["botId"] == people]
    assert len(copies) == 2 and Contact.objects.count() == 5

    deleted = erase(http, caller, f"/chat/{people}/contacts/{copies[0]['uuid']}")

    assert deleted.json()["data"]["deleted"] == {"contacts": 2, "messages": 1}
    # What is left: the other two people of this chatbot, and the same person in the other one.
    assert [c["botId"] for c in search(http, caller, **who).json()["data"]] == [other]
    assert contacts_of(http, caller, people)["total"] == 2


def test_somebody_deleted_who_writes_again_is_a_new_contact(http, caller, people):
    before = search(http, caller, waId=ANA_NUMBER).json()["data"][0]
    erase(http, caller, f"/chat/{people}/contacts/{before['uuid']}")

    assert notify(http, people, whatsapp(ANA_NUMBER, ANA)).status_code == 200

    after = search(http, caller, waId=ANA_NUMBER).json()["data"]
    assert len(after) == 1 and after[0]["uuid"] != before["uuid"]


# --- Without the key service, and with numbers from before they were encrypted --------------------------------------

@pytest.fixture
def key_service_down(key_service):
    def down():
        key_service.down = True
        data_keys.forget()

    return down


def test_without_the_key_service_no_number_is_read(http, caller, people, key_service_down):
    contact = contacts_of(http, caller, people)["contacts"][0]
    key_service_down()

    for response in (http.get(f"/chat/{people}/contacts", headers=caller.auth),
                     http.get(f"/chat/{people}/contacts/{contact['uuid']}", headers=caller.auth),
                     search(http, caller, waId=ANA_NUMBER)):
        assert (response.status_code, response.json()["code"]) == (503, 503)
        assert "data" not in response.json()


def test_deleting_does_not_need_the_key_service(http, caller, operator, people, key_service_down):
    contact = contacts_of(http, caller, people)["contacts"][0]
    new_bot(http, caller)
    key_service_down()

    assert erase(http, caller, f"/chat/{people}/contacts/{contact['uuid']}").status_code == 200
    assert erase(http, caller, f"/bot/{people}").status_code == 200
    assert erase(http, operator, f"/clients/{caller.client_id}").status_code == 200
    assert counts() == dict.fromkeys(counts(), 0)


@pytest.fixture
def in_clear(caller, people):
    """A contact stored before numbers were encrypted, which the migration has not reached."""
    contact = Contact(_uuid=uuid4(), clientId=caller.client_id, botId=people, firstName="Old", waId="12025550188")
    contact.save()
    return contact


def test_a_number_that_is_not_encrypted_is_not_handed_over_as_it_is(http, caller, people, in_clear):
    for response in (http.get(f"/chat/{people}/contacts", headers=caller.auth),
                     http.get(f"/chat/{people}/contacts/{in_clear._uuid}", headers=caller.auth)):
        assert (response.status_code, response.json()["code"]) == (500, 500)
        assert "12025550188" not in response.text and "data" not in response.json()


def test_a_contact_with_a_number_that_is_not_encrypted_can_still_be_deleted(http, caller, people, in_clear):
    assert erase(http, caller, f"/chat/{people}/contacts/{in_clear._uuid}").status_code == 200
    assert "12025550188" not in dump()
    assert contacts_of(http, caller, people)["total"] == 3


# --- Deleting a chatbot ------------------------------------------------------------------------------------------

def test_deleting_a_chatbot_deletes_everything_stored_for_it(http, caller, stranger, people):
    mine, theirs = new_bot(http, caller), new_bot(http, stranger)
    notify(http, mine, whatsapp(ANA_NUMBER, ANA))
    notify(http, theirs, whatsapp(ANA_NUMBER, ANA))
    kept = {bot: dump().count(bot) for bot in (mine, theirs)}
    assert people in dump()

    deleted = erase(http, caller, f"/bot/{people}")

    assert deleted.status_code == 200
    assert deleted.json()["data"] == {"uuid": people, "deleted": {"chatbots": 1, "contacts": 3, "phones": 1,
                                                                 "rules": 1, "intents": 1, "messages": 3}}
    assert people not in dump()
    assert {bot: dump().count(bot) for bot in kept} == kept
    assert counts() == {"ChatBot": 2, "ChatbotPhone": 2, "Rule": 2, "Intent": 2, "Contact": 2}


def test_a_deleted_chatbot_is_not_found_by_anybody(http, caller, people, queue):
    erase(http, caller, f"/bot/{people}")
    queue.clear()

    assert http.get(f"/chat/{people}/info", headers=caller.auth).status_code == 404
    assert http.get(f"/chat/{people}/contacts", headers=caller.auth).status_code == 404
    assert erase(http, caller, f"/bot/{people}").status_code == 404
    assert notify(http, people, whatsapp(ANA_NUMBER, ANA)).status_code == 404
    assert META.get(f"/chat/{people}", params={"hub.mode": "subscribe", "hub.challenge": "1",
                                               "hub.verify_token": "x"}).status_code == 404
    assert queue == [] and Contact.objects.count() == 0
    assert search(http, caller, waId=ANA_NUMBER).json()["data"] == []


@pytest.mark.parametrize("bot", [str(uuid4()), "not-a-uuid"])
def test_a_chatbot_that_does_not_exist_cannot_be_deleted(http, caller, people, bot):
    response = erase(http, caller, f"/bot/{bot}")

    assert (response.status_code, response.json()["code"]) == (404, 404)
    assert counts() == {"ChatBot": 1, "ChatbotPhone": 1, "Rule": 1, "Intent": 1, "Contact": 3}


def test_what_a_deletion_cut_short_left_behind_is_deleted_by_asking_again(http, caller, people):
    """The chatbot goes first. If the rest failed, a second call must not answer that there is nothing there."""
    ChatBot.objects(uuid=people).delete()

    again = erase(http, caller, f"/bot/{people}")

    assert again.status_code == 200
    assert again.json()["data"]["deleted"] == {"chatbots": 0, "contacts": 3, "phones": 1, "rules": 1, "intents": 1,
                                               "messages": 3}
    assert people not in dump()
    assert erase(http, caller, f"/bot/{people}").status_code == 404


def test_the_leftovers_of_a_chatbot_are_only_deleted_by_their_client(http, caller, stranger, people):
    ChatBot.objects(uuid=people).delete()

    assert erase(http, stranger, f"/bot/{people}").status_code == 404
    assert counts() == {"ChatBot": 0, "ChatbotPhone": 1, "Rule": 1, "Intent": 1, "Contact": 3}


def deleted_meanwhile(monkeypatch, document, delete):
    """Makes `delete` happen just before the next save of a `document`: after the lookup, before the write."""
    save = document.save

    def late_save(self, *args, **kwargs):
        monkeypatch.setattr(document, "save", save)
        delete()
        return save(self, *args, **kwargs)

    monkeypatch.setattr(document, "save", late_save)


ADDITIONS = {
    "contact": (Contact, lambda repo, bot: repo.add_contact(bot, Contact(firstName=ANA, waId=ANA_NUMBER))),
    "phone": (ChatbotPhone, lambda repo, bot: repo.add_phone(bot, ChatbotPhone(phoneNumber="12025550102"))),
    "rule": (Rule, lambda repo, bot: repo.add_rules(bot, ["Be brief"])),
    "intent": (Intent, lambda repo, bot: repo.add_intent(bot, Intent(name="x", description="x"))),
}


@pytest.mark.parametrize("what", sorted(ADDITIONS))
def test_nothing_is_stored_for_a_chatbot_deleted_meanwhile(caller, bot_id, monkeypatch, what):
    """A message can be on its way while the chatbot is deleted: its contact must not be left behind."""
    document, add = ADDITIONS[what]
    repo = TenantRepository(caller.client_id)
    deleted_meanwhile(monkeypatch, document, lambda: repo.delete_bot(bot_id))

    with pytest.raises(NotFound):
        add(repo, bot_id)

    assert counts() == dict.fromkeys(counts(), 0)


def test_a_message_on_its_way_to_a_deleted_chatbot_leaves_no_contact(http, caller, bot_id, queue, monkeypatch):
    deleted_meanwhile(monkeypatch, Contact, lambda: TenantRepository(caller.client_id).delete_bot(bot_id))

    notify(http, bot_id, whatsapp(ANA_NUMBER, ANA))

    assert queue == [] and counts() == dict.fromkeys(counts(), 0)
    assert bot_id not in dump()


def test_changing_a_chatbot_deleted_meanwhile_does_not_bring_it_back(caller, bot_id, monkeypatch):
    repo = TenantRepository(caller.client_id)
    deleted_meanwhile(monkeypatch, ChatBot, lambda: repo.delete_bot(bot_id))

    with pytest.raises(NotFound):
        repo.set_bot_credentials(bot_id, appSecret="another-secret")

    assert mongoengine.get_db()["chat_bot"].count_documents({}) == 0


def test_changing_an_intent_deleted_meanwhile_does_not_bring_it_back(caller, bot_id, monkeypatch):
    repo = TenantRepository(caller.client_id)
    intent = repo.list_intents(bot_id)[0]
    intent.status = Status.DIS.value
    deleted_meanwhile(monkeypatch, Intent, lambda: repo.delete_bot(bot_id))

    with pytest.raises(NotFound):
        repo.save_intent(intent)

    assert mongoengine.get_db()["intent"].count_documents({}) == 0


# --- The operator deletes a client -----------------------------------------------------------------------------------

@pytest.fixture
def leaving(http, caller, people):
    """A client with two chatbots, people who wrote to both, and tokens it has already used."""
    other = new_bot(http, caller)
    notify(http, other, whatsapp(ANA_NUMBER, ANA))
    return caller


def test_the_operator_deletes_a_client_with_everything_stored_for_it(http, operator, stranger, leaving):
    theirs = new_bot(http, stranger)
    notify(http, theirs, whatsapp(ANA_NUMBER, ANA))
    kept = counts(clientId=stranger.client_id)
    assert str(leaving.client_id) in dump() and SeenToken.objects(clientId=leaving.client_id).count() > 0

    deleted = erase(http, operator, f"/clients/{leaving.client_id}")

    assert deleted.status_code == 200
    assert deleted.json()["data"] == {"uuid": str(leaving.client_id), "deleted": {
        "chatbots": 2, "contacts": 4, "phones": 2, "rules": 2, "intents": 2, "messages": 4}}
    # Not a document of any kind names the client any more: its registry entry, its tokens, its data.
    assert str(leaving.client_id) not in dump()
    assert counts(clientId=stranger.client_id) == kept == counts()
    assert [c["botId"] for c in search(http, stranger, waId=ANA_NUMBER).json()["data"]] == [theirs]
    assert [client["name"] for client in http.get("/clients", headers=operator.auth).json()["data"]] == [
        "operator", "globex"]


def test_a_deleted_client_no_longer_gets_in(http, operator, leaving):
    made_before = leaving.assertion()
    token_before = leaving.access_token()

    erase(http, operator, f"/clients/{leaving.client_id}")

    for assertion in (made_before, leaving.assertion()):
        assert request_token(assertion).status_code == 401
    # The access token it already had lasts until it expires (ADR-0025), and there is nothing left to reach.
    assert http.get("/keys", headers={"Authorization": f"Bearer {token_before}"}).status_code != 200


def test_the_webhooks_of_a_deleted_client_are_not_found(http, operator, leaving, people, queue):
    erase(http, operator, f"/clients/{leaving.client_id}")
    queue.clear()

    assert notify(http, people, whatsapp(ANA_NUMBER, ANA)).status_code == 404
    assert queue == [] and Contact.objects.count() == 0


def test_the_data_key_of_a_deleted_client_is_gone(http, operator, leaving, key_service):
    wrapped = Client.objects(uuid=leaving.client_id).first().wrappedDataKey
    assert wrapped and wrapped in dump()
    data_keys.cipher_for(leaving.client_id)

    erase(http, operator, f"/clients/{leaving.client_id}")

    assert wrapped not in dump()
    asked = len(key_service.calls)
    with pytest.raises(NotFound):
        # Neither stored nor still open in memory: asking for it does not even reach the key service.
        data_keys.cipher_for(leaving.client_id)
    assert len(key_service.calls) == asked


def test_the_name_of_a_deleted_client_is_free_and_starts_from_nothing(http, operator, leaving, register):
    erase(http, operator, f"/clients/{leaving.client_id}")

    again = register("acme")

    assert again.client_id != leaving.client_id
    assert http.get("/keys", headers=again.auth).status_code == 200
    assert search(http, again, waId=ANA_NUMBER).json()["data"] == []


def test_only_the_operator_deletes_a_client(http, stranger, leaving):
    registry, stored = Client.objects.to_json(), counts()

    for caller, target in ((leaving, leaving), (stranger, leaving), (leaving, stranger)):
        response = erase(http, caller, f"/clients/{target.client_id}")

        assert response.status_code == 403
    assert Client.objects.to_json() == registry
    assert counts() == stored == {"ChatBot": 2, "ChatbotPhone": 2, "Rule": 2, "Intent": 2, "Contact": 4}


def test_the_operator_cannot_be_deleted(http, operator, leaving):
    response = erase(http, operator, f"/clients/{operator.client_id}")

    assert (response.status_code, response.json()["detail"]) == (409, "OPERATOR_CLIENT")
    assert operator_client().signingKeys and operator_client().status == Status.ACT.value
    assert http.get("/clients", headers=operator.auth).status_code == 200


@pytest.mark.parametrize("client_id", [str(uuid4()), "not-a-uuid"])
def test_a_client_that_does_not_exist_cannot_be_deleted(http, operator, leaving, client_id):
    assert erase(http, operator, f"/clients/{client_id}").status_code == 404
    assert Client.objects.count() == 2


def test_a_deletion_cut_short_has_already_shut_the_client_out_and_is_finished_by_asking_again(
        http, operator, leaving, monkeypatch):
    def fails(client_id):
        raise ConnectionError("the database went away")

    forget_tokens = clients.forget_tokens
    monkeypatch.setattr(clients, "forget_tokens", fails)
    made_before = leaving.assertion()

    cut_short = erase(http, operator, f"/clients/{leaving.client_id}")

    assert cut_short.status_code == 500
    client = Client.objects(uuid=leaving.client_id).first()
    assert (client.signingKeys, client.status) == ([], Status.DIS.value)
    assert request_token(made_before).status_code == 401

    monkeypatch.setattr(clients, "forget_tokens", forget_tokens)
    again = erase(http, operator, f"/clients/{leaving.client_id}")

    assert again.status_code == 200
    assert str(leaving.client_id) not in dump()


def test_a_chatbot_is_not_left_behind_for_a_client_deleted_meanwhile(leaving, monkeypatch):
    repo = TenantRepository(leaving.client_id)
    deleted_meanwhile(monkeypatch, ChatBot, lambda: delete_client(leaving.client_id))

    with pytest.raises(NotFound):
        repo.add_bot(ChatBot(name="Late", role="r", description="d", tone="t"))

    assert str(leaving.client_id) not in dump() and ChatBot.objects.count() == 0


def test_a_chatbot_is_not_left_behind_for_a_client_whose_deletion_is_under_way(leaving, monkeypatch):
    """Its data is gone and its registry entry is not yet: the client is still there, shut out."""
    repo = TenantRepository(leaving.client_id)

    def halfway():
        Client.objects(uuid=leaving.client_id).update_one(set__signingKeys=[], set__status=Status.DIS.value)
        repo.erase_all()

    deleted_meanwhile(monkeypatch, ChatBot, halfway)

    with pytest.raises(NotFound):
        repo.add_bot(ChatBot(name="Late", role="r", description="d", tone="t"))

    assert counts() == dict.fromkeys(counts(), 0)


def test_a_chatbot_whose_client_is_gone_is_not_served(http, leaving, people, queue):
    """Not something the service does: a client deleted by hand, or a copy restored halfway."""
    Client.objects(uuid=leaving.client_id).delete()
    data_keys.forget()
    queue.clear()

    assert notify(http, people, whatsapp(ANA_NUMBER, ANA)).status_code == 404
    assert queue == []


def test_the_repository_of_another_client_deletes_nothing_of_this_one(caller, stranger, people):
    before = counts()

    assert TenantRepository(stranger.client_id).erase_all() == {
        "chatbots": 0, "contacts": 0, "phones": 0, "rules": 0, "intents": 0, "messages": 0}
    assert counts() == before == counts(clientId=caller.client_id)


# --- What deleting does not reach ----------------------------------------------------------------------------------

def test_a_copy_of_the_database_from_before_the_deletion_still_opens(http, operator, leaving, people):
    """
        H11 in the threat model, stated here so that it is not taken for covered: the wrapped data key is
        stored with the data, and the key service is not asked to destroy anything. Whoever keeps a copy of
        the database from before the deletion, and can use the key service, still reads the numbers.

        Accepted in ADR-0014: in backups, what was deleted is gone when the backup expires, 365 days later at
        most. If the key of a client can one day be destroyed in the key service, this test is turned around.
    """
    database = mongoengine.get_db()
    copy = {name: [document for document in database[name].find({})
                   if leaving.client_id in (document.get("uuid"), document.get("clientId"))]
            for name in ("client", "contact")}

    assert erase(http, operator, f"/clients/{leaving.client_id}").status_code == 200
    assert str(leaving.client_id) not in dump()

    for name, documents in copy.items():
        database[name].insert_many(documents)
    restored = TenantRepository(leaving.client_id)
    assert sorted(restored.contact_number(c) for c in Contact.objects(waId__ne=None)) == sorted(
        [ANA_NUMBER, ANA_NUMBER, LUIS_NUMBER])


# --- What is written to the log ------------------------------------------------------------------------------------

def test_nothing_personal_is_logged_when_listing_exporting_or_deleting(http, caller, operator, people, caplog):
    # Everything the service itself writes, down to its debug lines. Not what the database driver would.
    caplog.set_level(logging.DEBUG, logger=app_module.settings.environment)
    ana = search(http, caller, waId=ANA_NUMBER).json()["data"][0]
    caplog.clear()

    contacts_of(http, caller, people)
    search(http, caller, waId=LUIS_NUMBER)
    search(http, caller, fbId=PSID)
    search(http, caller, waId=LUIS_NUMBER, fbId=PSID)
    http.get(f"/chat/{people}/contacts/{ana['uuid']}", headers=caller.auth)
    erase(http, caller, f"/chat/{people}/contacts/{ana['uuid']}")
    erase(http, caller, f"/bot/{people}")
    erase(http, operator, f"/clients/{caller.client_id}")

    assert caplog.text
    for value in PERSONAL:
        assert value not in caplog.text


def test_every_deletion_leaves_a_trace_at_the_level_production_logs_at(http, caller, operator, people, caplog):
    ana = search(http, caller, waId=ANA_NUMBER).json()["data"][0]
    other = new_bot(http, caller)
    caplog.clear()

    erase(http, caller, f"/chat/{people}/contacts/{ana['uuid']}")
    erase(http, caller, f"/bot/{people}")
    erase(http, operator, f"/clients/{caller.client_id}")

    kept = [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING]
    assert len(kept) == 3
    assert ana["uuid"] in kept[0] and people in kept[0] and str(caller.client_id) in kept[0]
    assert people in kept[1] and "'contacts': 2" in kept[1] and str(caller.client_id) in kept[1]
    assert str(caller.client_id) in kept[2] and "'chatbots': 1" in kept[2] and other not in kept[2]
