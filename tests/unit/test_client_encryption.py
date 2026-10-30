# -*- coding: utf-8 -*-
"""
    Encryption of a client's data at rest: the numbers of contacts and chatbots, the Meta credentials and the
    verification token. The tests go through the application with real tokens and then read the database the way
    somebody with a copy of it would: nothing of that may be there in clear.
"""
import hashlib
import hmac
import json
import os
from uuid import UUID, uuid4

import mongoengine
import pytest
from fastapi.testclient import TestClient

import app as app_module
import webhook as webhook_module
import controller.ingestion as ingestion
from classes import ChatBot, ChatbotPhone, Client, Contact
from crypto.fields import ClientCipher, NotEncrypted, is_encrypted, normalize_email, normalize_number
from migrations import assign_operator_client, encrypt_client_data
from repository import NotFound, TenantRepository, data_keys, operator_client

# What Meta calls is its own service, apart from the management API.
META = TestClient(webhook_module.app)

BOT = {"name": "Mine", "role": "Sales Assistant", "description": "A test chatbot", "tone": "helpful"}
SECRETS = {"facebookToken": "EAAG-page-token-0123456789", "whatsappToken": "EAAG-whatsapp-token-9876543210",
           "appSecret": "app-secret-of-this-test"}
PHONE = {"phoneNumber": "12025550101", "phoneId": "100000000000001"}
WA_ID = "12025550143"
WHATSAPP = {"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{"field": "messages", "value": {
    "messaging_product": "whatsapp",
    "metadata": {"display_phone_number": "12025550101", "phone_number_id": "100000000000001"},
    "contacts": [{"profile": {"name": "Ana"}, "wa_id": WA_ID}],
    "messages": [{"from": WA_ID, "id": "wamid.TEST", "timestamp": "1760000000", "type": "text",
                  "text": {"body": "hola"}}]}}]}]}


@pytest.fixture
def http():
    return TestClient(app_module.app)


@pytest.fixture
def queue(monkeypatch):
    sent = []

    async def capture(queue, message):
        sent.append(json.loads(message.to_json()))

    monkeypatch.setattr(ingestion, "send_message_to_queue", capture)
    return sent


@pytest.fixture
def caller(register):
    return register("acme")


@pytest.fixture
def bot_id(http, caller):
    """A chatbot made through the API, with its credentials and a phone loaded."""
    created = http.post("/bot", headers=caller.auth, json=BOT).json()["data"]
    http.put(f"/chat/{created['uuid']}/credentials", headers=caller.auth, json=SECRETS)
    http.post(f"/chat/{created['uuid']}/phones", headers=caller.auth, json=PHONE)
    return created["uuid"]


def notify(http, bot_id, payload, secret=SECRETS["appSecret"]):
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return META.post(f"/chat/{bot_id}", content=body,
                     headers={"content-type": "application/json", "X-Hub-Signature-256": signature})


def dump() -> str:
    """Everything in the database, as text: what a copy of it shows."""
    database = mongoengine.get_db()
    return json.dumps({name: list(database[name].find({})) for name in database.list_collection_names()},
                      default=str)


# --- The cipher ----------------------------------------------------------------------------------------------------

@pytest.fixture
def cipher():
    return ClientCipher(uuid4(), os.urandom(32))


def test_what_is_encrypted_comes_back(cipher):
    stored = cipher.encrypt("Contact.waId", WA_ID)

    assert is_encrypted(stored) and WA_ID not in stored
    assert cipher.decrypt("Contact.waId", stored) == WA_ID


def test_the_same_value_never_encrypts_to_the_same_text(cipher):
    assert len({cipher.encrypt("Contact.waId", WA_ID) for _ in range(20)}) == 20


def test_nothing_is_nothing(cipher):
    assert cipher.encrypt("ChatBot.appSecret", None) is None
    assert cipher.decrypt("ChatBot.appSecret", None) is None


def test_a_value_does_not_open_in_another_field_nor_for_another_client(cipher):
    stored = cipher.encrypt("ChatBot.facebookToken", "a-token")
    same_key_other_client = ClientCipher(uuid4(), os.urandom(32))

    for wrong in (lambda: cipher.decrypt("ChatBot.appSecret", stored),
                  lambda: same_key_other_client.decrypt("ChatBot.facebookToken", stored)):
        with pytest.raises(NotEncrypted):
            wrong()


def test_the_client_is_part_of_what_is_authenticated():
    """Two clients that somehow had the same key still could not read each other's values."""
    key = os.urandom(32)
    stored = ClientCipher(uuid4(), key).encrypt("ChatBot.appSecret", "a-secret")

    with pytest.raises(NotEncrypted):
        ClientCipher(uuid4(), key).decrypt("ChatBot.appSecret", stored)


@pytest.mark.parametrize("stored", ["a-token-in-clear", "", "v1:", "v1:not base64 !!", "v1:AAAA", 7],
                         ids=["in clear", "empty", "only the prefix", "not base64", "too short", "not text"])
def test_what_is_not_a_ciphertext_is_never_returned_as_is(cipher, stored):
    with pytest.raises(NotEncrypted):
        cipher.decrypt("ChatBot.appSecret", stored)


def test_a_tampered_ciphertext_does_not_open(cipher):
    stored = cipher.encrypt("ChatBot.appSecret", "a-secret")
    flipped = stored[:-4] + ("AAAA" if stored[-4:] != "AAAA" else "BBBB")

    with pytest.raises(NotEncrypted):
        cipher.decrypt("ChatBot.appSecret", flipped)


def test_a_data_key_has_to_be_32_bytes():
    with pytest.raises(ValueError, match="^A data key is 32 bytes$"):
        ClientCipher(uuid4(), os.urandom(16))


def test_the_blind_index_is_the_same_for_the_same_number_however_it_is_written(cipher):
    assert normalize_number("+1 (202) 555-0143") == WA_ID
    assert cipher.blind_index("Contact.waId", "+1 (202) 555-0143") == cipher.blind_index("Contact.waId", WA_ID)
    assert cipher.blind_index("Contact.waId", WA_ID) != cipher.blind_index("Contact.waId", "12025550144")


def test_the_blind_index_of_a_number_differs_between_clients_and_says_nothing_of_it(cipher):
    other = ClientCipher(uuid4(), os.urandom(32))
    index = cipher.blind_index("Contact.waId", WA_ID)

    assert index != other.blind_index("Contact.waId", WA_ID)
    assert WA_ID not in index
    # It is not a plain hash of the number, which anybody could compute for every number there is.
    assert index != hashlib.sha256(WA_ID.encode()).hexdigest()


def test_the_blind_index_of_a_value_differs_between_fields(cipher):
    """The index of a number is no use to find the same digits in another field (HIG-137, a surviving mutant)."""
    assert cipher.blind_index("Contact.waId", WA_ID) != cipher.blind_index("ClientUser.email", WA_ID)


def test_the_blind_index_is_the_same_from_one_version_to_the_next():
    """
        The indexes are stored: a contact is found by the index written when it came in. Any change to how one is made
        (the key derived for it, what is bound to it, the normalization) loses every contact already stored, without
        an error. These values were made by the version of 2026-10-30 (HIG-137).
    """
    cipher = ClientCipher(UUID("00000000-0000-4000-8000-000000000001"), bytes(range(32)))

    assert cipher.blind_index("Contact.waId", "+1 (202) 555-0143") == "7hbCQWh_eiTvpdVeh8HctRWuPdJFY1m2bXcs_u7raGA="
    assert cipher.blind_index("ClientUser.email", " Ana@Example.com ", normalize_email) \
        == "a4HfuTWExg52cr0WqaDT3453L_vigh_5a-3FySSxLW8="


@pytest.mark.parametrize("stored", ["a-token-in-clear", "v1:AAAA"], ids=["in clear", "does not open"])
def test_what_does_not_open_says_only_which_field(cipher, stored):
    """The log says what could not be read, by its field, and nothing of the value (HIG-137, a surviving mutant)."""
    with pytest.raises(NotEncrypted) as refused:
        cipher.decrypt("ChatBot.appSecret", stored)

    assert refused.value.args == ("ChatBot.appSecret",)


# --- The data key of a client ------------------------------------------------------------------------------------

def test_a_client_gets_its_data_key_the_first_time_it_is_needed(caller, key_service):
    assert Client.objects(uuid=caller.client_id).first().wrappedDataKey is None

    data_keys.cipher_for(caller.client_id)

    assert Client.objects(uuid=caller.client_id).first().wrappedDataKey.startswith("fake:v1:")
    assert key_service.calls == ["new_data_key"]


def test_only_the_wrapped_key_is_stored(caller, key_service, monkeypatch):
    made = []
    original = key_service.new_data_key
    monkeypatch.setattr(key_service, "new_data_key", lambda client_id: made.append(original(client_id)) or made[-1])

    data_keys.cipher_for(caller.client_id)
    key, wrapped = made[0]

    assert Client.objects(uuid=caller.client_id).first().wrappedDataKey == wrapped
    assert key.hex() not in dump() and __import__("base64").b64encode(key).decode() not in dump()


def test_the_open_key_is_kept_for_a_while_and_then_asked_for_again(caller, key_service, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(data_keys.time, "monotonic", lambda: clock[0])

    data_keys.cipher_for(caller.client_id)
    data_keys.cipher_for(caller.client_id)
    clock[0] += data_keys.OPEN_FOR - 1
    data_keys.cipher_for(caller.client_id)
    assert key_service.calls == ["new_data_key"]

    clock[0] += 2
    data_keys.cipher_for(caller.client_id)
    assert key_service.calls == ["new_data_key", "unwrap"]


def test_the_key_is_the_same_after_it_is_forgotten(caller):
    stored = data_keys.cipher_for(caller.client_id).encrypt("ChatBot.appSecret", "a-secret")

    data_keys.forget(caller.client_id)

    assert data_keys.cipher_for(caller.client_id).decrypt("ChatBot.appSecret", stored) == "a-secret"


def test_two_workers_starting_at_once_end_up_with_the_same_key(caller, key_service, monkeypatch):
    """The second one to store its key must drop it and use the first: otherwise each would encrypt alone."""
    original = key_service.new_data_key
    first = {}

    def new_data_key_while_another_worker_stores_its_own(client_id):
        if not first:
            first["key"], wrapped = original(client_id)
            Client.objects(uuid=client_id).update_one(set__wrappedDataKey=wrapped)
        return original(client_id)

    monkeypatch.setattr(key_service, "new_data_key", new_data_key_while_another_worker_stores_its_own)

    late = data_keys.cipher_for(caller.client_id)

    assert late.decrypt("x", ClientCipher(caller.client_id, first["key"]).encrypt("x", "same key")) == "same key"


def test_each_client_has_its_own_key(caller, register):
    other = register("globex")
    mine, theirs = data_keys.cipher_for(caller.client_id), data_keys.cipher_for(other.client_id)
    stored = mine.encrypt("ChatBot.appSecret", "a-secret")

    wrapped = [Client.objects(uuid=client_id).first().wrappedDataKey for client_id in (caller.client_id,
                                                                                         other.client_id)]
    assert all(wrapped) and wrapped[0] != wrapped[1]
    with pytest.raises(NotEncrypted):
        theirs.decrypt("ChatBot.appSecret", stored)


def test_the_wrapped_key_of_a_client_does_not_open_for_another(caller, register, key_service):
    """A wrapped key copied onto another client's entry is useless: the key service derives per client."""
    from crypto.key_service import KeyServiceUnavailable

    other = register("globex")
    data_keys.cipher_for(caller.client_id)
    Client.objects(uuid=other.client_id).update_one(
        set__wrappedDataKey=Client.objects(uuid=caller.client_id).first().wrappedDataKey)

    with pytest.raises(KeyServiceUnavailable):
        data_keys.cipher_for(other.client_id)


def test_somebody_who_is_not_a_client_has_no_key():
    with pytest.raises(NotFound):
        data_keys.cipher_for(uuid4())


def test_nothing_is_encrypted_without_a_key_service(caller):
    data_keys.configure(None)

    with pytest.raises(RuntimeError):
        data_keys.cipher_for(caller.client_id)


# --- What a copy of the database shows -----------------------------------------------------------------------------

def test_a_copy_of_the_database_shows_no_number_and_no_credential(http, caller, bot_id, queue):
    created_token = http.get(f"/chat/{bot_id}/info", headers=caller.auth).json()["data"]["verifyToken"]
    assert notify(http, bot_id, WHATSAPP).status_code == 200

    copy = dump()

    for secret in (*SECRETS.values(), *PHONE.values(), WA_ID, created_token):
        assert secret not in copy
    # And they are there, encrypted: the test would also pass if nothing had been stored.
    bot, phone, contact = ChatBot.objects.get(), ChatbotPhone.objects.get(), Contact.objects.get()
    stored = [bot.verifyToken, bot.facebookToken, bot.whatsappToken, bot.appSecret, phone.phoneNumber,
              phone.phoneId, contact.waId]
    assert all(is_encrypted(value) for value in stored) and len(set(stored)) == 7
    assert contact.waIdIndex and WA_ID not in contact.waIdIndex


def test_the_message_for_the_engine_still_carries_the_number(http, bot_id, queue):
    """The engine has to answer the contact. What is not in clear is what is stored, not what is in transit."""
    notify(http, bot_id, WHATSAPP)

    assert queue[0]["jwe_body"]["contact"] == {"name": "Ana", "waId": WA_ID}


def test_a_contact_is_found_again_by_its_number(http, bot_id, queue, another):
    notify(http, bot_id, WHATSAPP)
    notify(http, bot_id, another(WHATSAPP))

    assert Contact.objects.count() == 1 and len(queue) == 2


def test_the_stored_values_open_with_the_repository_of_their_client(http, caller, bot_id, queue):
    notify(http, bot_id, WHATSAPP)
    repo = TenantRepository(caller.client_id)
    bot = repo.get_bot(bot_id)

    secrets = repo.bot_secrets(bot)

    assert (secrets.facebook_token, secrets.whatsapp_token, secrets.app_secret) == tuple(SECRETS.values())
    assert repo.phone_numbers(repo.list_phones(bot_id)[0]) == PHONE
    assert repo.contact_number(repo.find_contact_by_wa_id(bot_id, "+1 202 555 0143")) == WA_ID


def test_the_repository_of_another_client_does_not_open_them(http, caller, bot_id, queue, register):
    notify(http, bot_id, WHATSAPP)
    stranger = TenantRepository(register("globex").client_id)
    bot, phone, contact = ChatBot.objects.get(), ChatbotPhone.objects.get(), Contact.objects.get()

    for attempt in (lambda: stranger.bot_secrets(bot), lambda: stranger.phone_numbers(phone),
                    lambda: stranger.contact_number(contact)):
        with pytest.raises(NotFound):
            attempt()


def test_a_ciphertext_copied_to_the_chatbot_of_another_client_does_not_open(http, caller, bot_id, register):
    """Somebody who can write to the database cannot give a chatbot the App Secret of another client's."""
    other = register("globex")
    theirs = http.post("/bot", headers=other.auth, json=BOT).json()["data"]["uuid"]
    ChatBot.objects(uuid=theirs).update_one(set__appSecret=ChatBot.objects(uuid=bot_id).first().appSecret)

    assert notify(http, theirs, WHATSAPP).status_code == 404


# --- The API -----------------------------------------------------------------------------------------------------

def test_the_meta_credentials_go_in_and_do_not_come_out(http, caller, bot_id):
    info = http.get(f"/chat/{bot_id}/info", headers=caller.auth)

    assert info.status_code == 200
    assert info.json()["data"]["credentials"] == {"facebookToken": True, "whatsappToken": True, "appSecret": True,
                                                "wabaId": False}
    assert all(secret not in info.text for secret in SECRETS.values())


def test_the_answer_to_loading_credentials_does_not_repeat_them(http, caller):
    bot = http.post("/bot", headers=caller.auth, json=BOT).json()["data"]["uuid"]

    response = http.put(f"/chat/{bot}/credentials", headers=caller.auth, json=SECRETS)

    assert response.status_code == 200
    assert response.json()["data"] == {"uuid": bot, "credentials": {"facebookToken": True, "whatsappToken": True,
                                                                     "appSecret": True, "wabaId": False}}
    assert all(secret not in response.text for secret in SECRETS.values())


def test_the_verification_token_is_given_to_its_client_and_is_the_one_that_works(http, caller, bot_id):
    created = http.post("/bot", headers=caller.auth, json=BOT).json()["data"]
    read = http.get(f"/chat/{created['uuid']}/info", headers=caller.auth).json()["data"]

    assert created["verifyToken"] == read["verifyToken"] and not is_encrypted(read["verifyToken"])
    accepted = META.get(f"/chat/{created['uuid']}", params={"hub.mode": "subscribe", "hub.challenge": "42",
                                                            "hub.verify_token": read["verifyToken"]})
    rejected = META.get(f"/chat/{created['uuid']}", params={"hub.mode": "subscribe", "hub.challenge": "42",
                                                            "hub.verify_token": ChatBot.objects(
                                                                uuid=created["uuid"]).first().verifyToken})
    assert (accepted.status_code, accepted.text, rejected.status_code) == (200, "42", 403)


def test_a_new_chatbot_has_no_credentials(http, caller):
    created = http.post("/bot", headers=caller.auth, json=BOT).json()["data"]

    assert created["credentials"] == {"facebookToken": False, "whatsappToken": False, "appSecret": False,
                                      "wabaId": False}
    assert "facebookToken" not in created and "whatsappToken" not in created


def test_only_the_credentials_sent_change(http, caller, bot_id):
    before = ChatBot.objects.get()

    http.put(f"/chat/{bot_id}/credentials", headers=caller.auth, json={"appSecret": "the-new-app-secret"})
    after, secrets = ChatBot.objects.get(), TenantRepository(caller.client_id).bot_secrets(ChatBot.objects.get())

    assert (after.facebookToken, after.whatsappToken) == (before.facebookToken, before.whatsappToken)
    assert (secrets.app_secret, secrets.facebook_token) == ("the-new-app-secret", SECRETS["facebookToken"])


def test_the_webhook_is_verified_with_the_rotated_app_secret(http, caller, bot_id, queue):
    http.put(f"/chat/{bot_id}/credentials", headers=caller.auth, json={"appSecret": "the-new-app-secret"})

    old = notify(http, bot_id, WHATSAPP)
    new = notify(http, bot_id, WHATSAPP, secret="the-new-app-secret")

    assert (old.status_code, new.status_code, len(queue)) == (403, 200, 1)


@pytest.mark.parametrize("body", [{}, {"appSecret": ""}, {"appSecret": None}, {"appSecret": "x" * 257},
                                  {"appSecret": "an-app-secret", "verifyToken": "mine"}, {"facebookToken": 7}],
                         ids=["nothing", "empty", "null", "too long", "a field that is not a credential", "not text"])
def test_credentials_that_are_not_well_formed_are_rejected(http, caller, bot_id, body):
    before = ChatBot.objects.get().appSecret

    response = http.put(f"/chat/{bot_id}/credentials", headers=caller.auth, json=body)

    assert response.status_code == 422
    assert ChatBot.objects.get().appSecret == before


@pytest.mark.parametrize("body", [{"appSecret": "s3cret-that-must-not-come-back", "other": 1},
                                  {"appSecret": "s3cret-that-must-not-come-back" * 9},
                                  {"appSecret": ["s3cret-that-must-not-come-back"]},
                                  {"facebookToken": {"value": "s3cret-that-must-not-come-back"}}],
                         ids=["next to a field that is not a credential", "too long", "in a list", "in an object"])
def test_a_rejected_credential_is_not_repeated_in_the_answer(http, caller, bot_id, body):
    response = http.put(f"/chat/{bot_id}/credentials", headers=caller.auth, json=body)

    assert response.status_code == 422
    assert "s3cret-that-must-not-come-back" not in response.text
    # It still says where the request is wrong and why.
    assert all(set(error) == {"type", "loc", "msg"} and error["loc"][0] == "body"
               for error in response.json()["detail"])


def test_the_phones_of_a_chatbot_are_listed_with_their_numbers(http, caller, bot_id):
    listed = http.get(f"/chat/{bot_id}/phones", headers=caller.auth).json()["data"]

    assert [(phone["phoneNumber"], phone["phoneId"]) for phone in listed] == [tuple(PHONE.values())]


# --- Without the key service -----------------------------------------------------------------------------------------

@pytest.fixture
def key_service_down(key_service):
    """The key service stops answering and no data key is left open in memory."""
    def go_down():
        key_service.down = True
        data_keys.forget()

    return go_down


def test_without_the_key_service_the_webhook_asks_meta_to_send_again(http, bot_id, queue, key_service_down):
    key_service_down()

    response = notify(http, bot_id, WHATSAPP)

    # Not a 200, which would lose the message, and not a 403, which would tell Meta the signature is wrong.
    assert (response.status_code, queue) == (503, [])


def test_without_the_key_service_the_webhook_is_not_verified(http, caller, bot_id, key_service_down):
    token = http.get(f"/chat/{bot_id}/info", headers=caller.auth).json()["data"]["verifyToken"]
    key_service_down()

    response = META.get(f"/chat/{bot_id}", params={"hub.mode": "subscribe", "hub.challenge": "42",
                                                   "hub.verify_token": token})

    assert response.status_code == 503


@pytest.mark.parametrize("method,path,body", [
    ("POST", "/bot", BOT),
    ("GET", "/chat/{bot_id}/info", None),
    ("PUT", "/chat/{bot_id}/credentials", {"appSecret": "another"}),
    ("POST", "/chat/{bot_id}/phones", PHONE),
    ("GET", "/chat/{bot_id}/phones", None),
])
def test_without_the_key_service_nothing_encrypted_is_read_or_written(http, caller, bot_id, key_service_down,
                                                                     method, path, body):
    before = (ChatBot.objects.count(), ChatbotPhone.objects.count(), ChatBot.objects.get().appSecret)
    key_service_down()

    response = http.request(method, path.format(bot_id=bot_id), headers=caller.auth, json=body)

    assert response.status_code == 503
    assert all(secret not in response.text for secret in (*SECRETS.values(), *PHONE.values()))
    assert (ChatBot.objects.count(), ChatbotPhone.objects.count(), ChatBot.objects.get().appSecret) == before


def test_what_is_not_encrypted_does_not_need_the_key_service(http, caller, bot_id, key_service_down):
    key_service_down()

    assert http.get(f"/chat/{bot_id}/rules", headers=caller.auth).status_code == 200
    assert http.get(f"/chat/{bot_id}/intents", headers=caller.auth).status_code == 200
    assert http.get("/health").status_code == 200


def test_a_key_already_open_keeps_the_webhook_working_while_the_service_is_down(http, bot_id, queue, key_service,
                                                                               another):
    notify(http, bot_id, WHATSAPP)
    key_service.down = True

    assert (notify(http, bot_id, another(WHATSAPP)).status_code, len(queue)) == (200, 2)


def test_a_message_is_not_lost_when_the_key_goes_between_the_signature_and_the_contact(http, bot_id, queue,
                                                                                    key_service, monkeypatch):
    """The signature is checked with the key open; if it is gone by the time the contact is stored, Meta retries."""
    import controller.facebook as facebook
    original = facebook.TenantRepository

    def repository_after_the_key_is_gone(client_id):
        key_service.down = True
        data_keys.forget()
        return original(client_id)

    monkeypatch.setattr(facebook, "TenantRepository", repository_after_the_key_is_gone)

    assert (notify(http, bot_id, WHATSAPP).status_code, queue) == (503, [])


# --- What was stored in clear before -------------------------------------------------------------------------------

@pytest.fixture
def legacy(caller):
    """A chatbot, a phone and a contact as they were stored before anything was encrypted."""
    bot_id = uuid4()
    ChatBot._get_collection().insert_one({
        "uuid": bot_id, "clientId": caller.client_id, "name": "Old", "role": "r", "description": "d", "tone": "t",
        "verifyToken": "old-verify-token", "facebookToken": SECRETS["facebookToken"],
        "appSecret": SECRETS["appSecret"]})
    ChatbotPhone._get_collection().insert_one({"uuid": uuid4(), "clientId": caller.client_id, "bot_id": bot_id,
                                               **PHONE})
    Contact._get_collection().insert_one({"_uuid": uuid4(), "clientId": caller.client_id, "botId": bot_id,
                                          "firstName": "Ana", "waId": WA_ID})
    return str(bot_id)


def test_a_chatbot_with_secrets_in_clear_is_not_served(http, legacy, queue):
    assert (notify(http, legacy, WHATSAPP).status_code, queue) == (404, [])


def test_the_migration_only_reports_unless_told_to_apply(legacy):
    report = encrypt_client_data.run(apply=False)

    assert {name: counts["in clear"] for name, counts in report.items()} == {
        "ChatBot": 1, "ChatbotPhone": 1, "Contact": 1}
    assert SECRETS["appSecret"] in dump() and WA_ID in dump()


def test_the_migration_encrypts_what_was_in_clear_and_everything_keeps_working(http, caller, legacy, queue):
    encrypt_client_data.run(apply=True)

    copy = dump()
    assert all(secret not in copy for secret in (SECRETS["facebookToken"], SECRETS["appSecret"], "old-verify-token",
                                                 *PHONE.values(), WA_ID))
    # The contact from before is found by its number, not stored again.
    assert (notify(http, legacy, WHATSAPP).status_code, Contact.objects.count(), len(queue)) == (200, 1, 1)
    assert http.get(f"/chat/{legacy}/info", headers=caller.auth).json()["data"]["verifyToken"] == "old-verify-token"
    assert http.get(f"/chat/{legacy}/phones", headers=caller.auth).json()["data"][0]["phoneNumber"] == "12025550101"
    assert {name: counts["in clear"] for name, counts in encrypt_client_data.run(apply=True).items()} == {
        "ChatBot": 0, "ChatbotPhone": 0, "Contact": 0}


def test_the_migration_does_not_bring_back_what_is_deleted_while_it_runs(legacy, monkeypatch):
    """Found in the dual review of Gate 2: it saved without a condition, and saving brought a deleted one back."""
    cipher_for = data_keys.cipher_for

    def deleted_meanwhile(client_id):
        for collection in (ChatBot, ChatbotPhone, Contact):
            collection.objects.delete()
        return cipher_for(client_id)

    monkeypatch.setattr(data_keys, "cipher_for", deleted_meanwhile)

    encrypt_client_data.run(apply=True)

    assert (ChatBot.objects.count(), ChatbotPhone.objects.count(), Contact.objects.count()) == (0, 0, 0)


def test_the_migration_leaves_alone_what_has_no_client_and_says_so():
    ChatBot._get_collection().insert_one({"uuid": uuid4(), "name": "Orphan", "role": "r", "description": "d",
                                          "tone": "t", "appSecret": "orphan-secret"})

    report = encrypt_client_data.run(apply=True)

    assert report["ChatBot"] == {"in clear": 0, "without a client": 1}
    assert "orphan-secret" in dump()
    # The two migrations in order leave nothing in clear.
    assign_operator_client.run(apply=True)
    encrypt_client_data.run(apply=True)
    assert "orphan-secret" not in dump()
    assert TenantRepository(operator_client().uuid).bot_secrets(ChatBot.objects.get()).app_secret == "orphan-secret"
