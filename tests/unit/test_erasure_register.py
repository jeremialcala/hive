# -*- coding: utf-8 -*-
"""
    The register of erasures: what is written down before each deletion, so that the deletions can be made again
    after a backup of the database is restored (ADR-0014).

    The tests delete through the application, then put the database back the way a restored backup would, and
    make the recorded deletions again.
"""
import hashlib
import hmac
import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timezone
from uuid import uuid4

import mongoengine
import pytest
from fastapi.testclient import TestClient

import app as app_module
import webhook as webhook_module
import controller.ingestion as ingestion
from classes import ChatBot, ChatbotPhone, Client, Contact, Intent, Rule
from migrations import replay_erasures
from repository import RegisterUnavailable, TenantRepository, erasures
from repository import clients as client_registry

# What Meta calls is its own service, apart from the management API.
META = TestClient(webhook_module.app)

BOT = {"name": "Mine", "role": "Sales Assistant", "description": "A test chatbot", "tone": "helpful"}
APP_SECRET = "app-secret-of-this-test"
ANA, ANA_NUMBER = "Ana", "12025550143"
LUIS, LUIS_NUMBER = "Luis", "12025550177"
STORED = (ChatBot, ChatbotPhone, Rule, Intent, Contact)


def whatsapp(number: str, name: str) -> dict:
    return {"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "12025550101", "phone_number_id": "100000000000001"},
        "contacts": [{"profile": {"name": name}, "wa_id": number}],
        "messages": [{"from": number, "id": f"wamid.{uuid4()}", "timestamp": "1760000000", "type": "text",
                      "text": {"body": "hola"}}]}}]}]}


@pytest.fixture
def http():
    return TestClient(app_module.app)


@pytest.fixture(autouse=True)
def queue(monkeypatch):
    async def discard(queue, message):
        pass

    monkeypatch.setattr(ingestion, "send_message_to_queue", discard)


def new_bot(http, caller) -> str:
    bot_id = http.post("/bot", headers=caller.auth, json=BOT).json()["data"]["uuid"]
    for method, path, body in (("PUT", "credentials", {"appSecret": APP_SECRET}),
                               ("POST", "phones", {"phoneNumber": "12025550101", "phoneId": "100000000000001"}),
                               ("POST", "rules", {"rules": ["Be kind"]}),
                               ("POST", "intents", {"name": "check_order", "description": "Order status"})):
        assert http.request(method, f"/chat/{bot_id}/{path}", headers=caller.auth, json=body).status_code == 200
    return bot_id


def notify(http, bot_id, payload):
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return META.post(f"/chat/{bot_id}", content=body,
                     headers={"content-type": "application/json", "X-Hub-Signature-256": signature})


def contact_of(http, caller, number: str) -> dict:
    return http.post("/contacts/search", headers=caller.auth, json={"waId": number}).json()["data"][0]


def erase(http, caller, path):
    return http.delete(path, headers=caller.auth)


@pytest.fixture
def acme(register):
    return register("acme")


@pytest.fixture
def globex(register):
    return register("globex")


@pytest.fixture
def bot_id(http, acme):
    """A chatbot of acme that Ana and Luis wrote to."""
    bot = new_bot(http, acme)
    for number, name in ((ANA_NUMBER, ANA), (LUIS_NUMBER, LUIS)):
        assert notify(http, bot, whatsapp(number, name)).status_code == 200
    return bot


def written(directory) -> str:
    """Everything in the files of the register, as text."""
    if not directory.exists():
        return ""
    return "".join(path.read_text(encoding="utf-8") for path in sorted(directory.iterdir()))


def recorded() -> list:
    return erasures.entries()[0]


def counts(**filters) -> dict:
    return {document.__name__: document.objects(**filters).count() for document in STORED}


def copy_of_the_database() -> dict:
    database = mongoengine.get_db()
    return {name: list(database[name].find({})) for name in database.list_collection_names()}


def restore(copy: dict) -> None:
    """What restoring a backup does: the database goes back to what it held when the copy was taken."""
    database = mongoengine.get_db()
    for name in database.list_collection_names():
        database[name].delete_many({})
    for name, documents in copy.items():
        if documents:
            database[name].insert_many(documents)


# --- What is written down ----------------------------------------------------------------------------------------

def test_deleting_a_contact_is_written_down_with_its_identifiers(http, acme, bot_id, register_of_erasures):
    ana = contact_of(http, acme, ANA_NUMBER)

    assert erase(http, acme, f"/chat/{bot_id}/contacts/{ana['uuid']}").status_code == 200

    (entry,) = recorded()
    assert (entry["kind"], str(entry["clientId"]), str(entry["botId"]), [str(c) for c in entry["contacts"]]) == (
        "contact", str(acme.client_id), bot_id, [ana["uuid"]])
    assert [path.name for path in register_of_erasures.iterdir()] == [
        f"erasures-{datetime.now(timezone.utc):%Y-%m}.jsonl"]


def test_every_copy_of_a_deleted_person_is_written_down(http, acme, bot_id):
    twin = TenantRepository(acme.client_id).add_contact(bot_id, Contact(firstName=ANA, waId=ANA_NUMBER))
    ana = contact_of(http, acme, ANA_NUMBER)

    erase(http, acme, f"/chat/{bot_id}/contacts/{ana['uuid']}")

    assert sorted(str(c) for c in recorded()[0]["contacts"]) == sorted([ana["uuid"], str(twin._uuid)])


def test_deleting_a_chatbot_and_a_client_are_written_down(http, acme, operator, bot_id):
    other = new_bot(http, acme)

    assert erase(http, acme, f"/bot/{bot_id}").status_code == 200
    assert erase(http, operator, f"/clients/{acme.client_id}").status_code == 200

    first, second = recorded()
    assert (first["kind"], str(first["clientId"]), str(first["botId"]), first["contacts"]) == (
        "chatbot", str(acme.client_id), bot_id, [])
    assert (second["kind"], second["clientId"], second["botId"], second["contacts"]) == (
        "client", acme.client_id, None, [])
    assert other not in json.dumps([str(entry) for entry in recorded()])


def test_the_register_holds_nothing_of_what_was_deleted(http, acme, operator, bot_id, register_of_erasures):
    """Identifiers and dates. Not a name, not a number, not a key: it is kept for longer than the data was."""
    ana = contact_of(http, acme, ANA_NUMBER)
    wrapped = Client.objects(uuid=acme.client_id).first().wrappedDataKey
    stored = [Contact.objects(_uuid=ana["uuid"]).first().waId, Contact.objects(_uuid=ana["uuid"]).first().waIdIndex]

    erase(http, acme, f"/chat/{bot_id}/contacts/{ana['uuid']}")
    erase(http, acme, f"/bot/{bot_id}")
    erase(http, operator, f"/clients/{acme.client_id}")

    text = written(register_of_erasures)
    assert len(text.splitlines()) == 3
    for value in (ANA, ANA_NUMBER, LUIS, LUIS_NUMBER, wrapped, APP_SECRET, "acme", *stored):
        assert value not in text
    for line in text.splitlines():
        assert set(json.loads(line)) <= {"at", "kind", "clientId", "botId", "contacts"}


def test_an_entry_says_when_in_utc(http, acme, bot_id):
    before = datetime.now(timezone.utc).replace(microsecond=0)

    erase(http, acme, f"/bot/{bot_id}")

    at = datetime.fromisoformat(recorded()[0]["at"])
    assert at.utcoffset().total_seconds() == 0
    assert before <= at <= datetime.now(timezone.utc)


def test_what_is_not_there_to_delete_is_not_written_down(http, acme, globex, operator, bot_id, register_of_erasures):
    ana = contact_of(http, acme, ANA_NUMBER)

    for caller, path in ((acme, f"/chat/{bot_id}/contacts/{uuid4()}"), (acme, f"/bot/{uuid4()}"),
                         (globex, f"/chat/{bot_id}/contacts/{ana['uuid']}"), (globex, f"/bot/{bot_id}"),
                         (operator, f"/clients/{uuid4()}"), (acme, f"/clients/{globex.client_id}"),
                         (operator, f"/clients/{operator.client_id}")):
        assert erase(http, caller, path).status_code in (403, 404, 409)

    assert written(register_of_erasures) == ""


# --- Written down before it is deleted, or not deleted -------------------------------------------------------------

def test_a_deletion_is_on_disk_before_anything_is_deleted(http, acme, operator, bot_id, monkeypatch):
    seen = []

    def spy(name, target):
        original = getattr(target, name)

        def looking(*args, **kwargs):
            # What the register holds at the moment the deletion starts, and what the database still holds.
            seen.append((name, recorded()[-1]["kind"], Contact.objects.count()))
            return original(*args, **kwargs)

        monkeypatch.setattr(target, name, looking)

    spy("erase_contacts", TenantRepository)
    spy("erase_bot", TenantRepository)
    spy("erase_client", client_registry)
    ana = contact_of(http, acme, ANA_NUMBER)

    erase(http, acme, f"/chat/{bot_id}/contacts/{ana['uuid']}")
    erase(http, acme, f"/bot/{bot_id}")
    erase(http, operator, f"/clients/{acme.client_id}")

    assert seen == [("erase_contacts", "contact", 2), ("erase_bot", "chatbot", 1), ("erase_client", "client", 0)]


@pytest.fixture(params=["a file where the directory should be", "not configured"])
def register_down(request, tmp_path):
    """Returns a function that leaves the register of erasures unusable."""
    def down():
        if request.param == "not configured":
            erasures.configure(None)
        else:
            blocked = tmp_path / "blocked"
            blocked.write_text("not a directory", encoding="utf-8")
            erasures.configure(blocked / "erasures")

    return down


def test_nothing_is_deleted_while_the_register_cannot_be_written(http, acme, operator, bot_id, register_down):
    ana = contact_of(http, acme, ANA_NUMBER)
    before = counts()
    register_down()

    for caller, path in ((acme, f"/chat/{bot_id}/contacts/{ana['uuid']}"), (acme, f"/bot/{bot_id}"),
                         (operator, f"/clients/{acme.client_id}")):
        response = erase(http, caller, path)

        assert response.status_code == 503
        assert "erasures" not in response.text and "blocked" not in response.text
    assert counts() == before == {"ChatBot": 1, "ChatbotPhone": 1, "Rule": 1, "Intent": 1, "Contact": 2}
    # The client was not shut out either: the deletion did not start.
    assert http.get("/keys", headers=acme.auth).status_code == 200


def test_everything_else_works_while_the_register_cannot_be_written(http, acme, bot_id, register_down):
    register_down()

    assert http.get(f"/chat/{bot_id}/contacts", headers=acme.auth).status_code == 200
    assert notify(http, bot_id, whatsapp("12025550190", "Eva")).status_code == 200
    assert http.get("/health").status_code == 200


def test_the_reason_the_register_is_unavailable_does_not_carry_the_path(tmp_path):
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    erasures.configure(blocked / "erasures")

    with pytest.raises(RegisterUnavailable) as failure:
        erasures.record(erasures.CLIENT, uuid4())

    assert "blocked" not in str(failure.value) and str(tmp_path) not in str(failure.value)


# --- After a backup is restored --------------------------------------------------------------------------------------

@pytest.fixture
def restored(http, acme, globex, operator, bot_id):
    """
        A backup is taken, then a contact, a chatbot and a client are deleted, and then the backup is restored:
        the three are back. Returns what was deleted.
    """
    second = new_bot(http, acme)
    notify(http, second, whatsapp(ANA_NUMBER, ANA))
    theirs = new_bot(http, globex)
    notify(http, theirs, whatsapp(ANA_NUMBER, ANA))
    ana = contact_of(http, acme, ANA_NUMBER)
    backup = copy_of_the_database()

    assert erase(http, acme, f"/chat/{bot_id}/contacts/{ana['uuid']}").status_code == 200
    assert erase(http, acme, f"/bot/{second}").status_code == 200
    assert erase(http, operator, f"/clients/{globex.client_id}").status_code == 200
    restore(backup)
    return {"contact": ana["uuid"], "chatbot": second, "client": globex.client_id, "kept": bot_id}


def test_restoring_a_backup_brings_back_what_was_deleted(restored):
    assert Contact.objects(_uuid=restored["contact"]).count() == 1
    assert ChatBot.objects(uuid=restored["chatbot"]).count() == 1
    assert Client.objects(uuid=restored["client"]).count() == 1


def test_the_report_says_what_came_back_and_deletes_nothing(restored, register_of_erasures):
    before, register = copy_of_the_database(), written(register_of_erasures)

    report = replay_erasures.run(apply=False)

    # Each person wrote one message, and it came back with them: its history counts as a document too.
    assert report == {"contact": {"recorded": 1, "back": 1, "documents": 2},
                      "chatbot": {"recorded": 1, "back": 1, "documents": 6},
                      "client": {"recorded": 1, "back": 1, "documents": 7},
                      "unreadable lines": 0}
    assert copy_of_the_database() == before and written(register_of_erasures) == register


def test_the_recorded_deletions_are_made_again(http, acme, restored, register_of_erasures):
    register = written(register_of_erasures)

    replay_erasures.run(apply=True)

    assert Contact.objects(_uuid=restored["contact"]).count() == 0
    assert Contact.objects(botId=restored["chatbot"]).count() == ChatBot.objects(uuid=restored["chatbot"]).count() == 0
    assert str(restored["client"]) not in json.dumps(copy_of_the_database(), default=str)
    # What was never deleted is still there: the other chatbot of acme, with Luis.
    assert counts() == {"ChatBot": 1, "ChatbotPhone": 1, "Rule": 1, "Intent": 1, "Contact": 1}
    assert contact_of(http, acme, LUIS_NUMBER)["botId"] == restored["kept"]
    # And making them again is not a new deletion: nothing is added to the register.
    assert written(register_of_erasures) == register


def test_making_them_again_twice_changes_nothing(restored):
    replay_erasures.run(apply=True)
    after = copy_of_the_database()

    report = replay_erasures.run(apply=True)

    assert [report[kind]["back"] for kind in ("contact", "chatbot", "client")] == [0, 0, 0]
    assert copy_of_the_database() == after


def test_a_deleted_client_that_came_back_does_not_get_in(http, globex, restored):
    """Its registry entry is back, with its keys. Until the deletions are made again, it is a client again."""
    assert http.get("/keys", headers=globex.auth).status_code == 200

    replay_erasures.run(apply=True)

    assert http.get("/keys", headers=globex.auth).status_code == 401


def test_somebody_deleted_who_wrote_again_before_the_backup_is_kept(http, acme, bot_id):
    """The deletion names the contact that was deleted, not the person: the new contact is another one."""
    before = contact_of(http, acme, ANA_NUMBER)
    erase(http, acme, f"/chat/{bot_id}/contacts/{before['uuid']}")
    notify(http, bot_id, whatsapp(ANA_NUMBER, ANA))
    again = contact_of(http, acme, ANA_NUMBER)
    restore(copy_of_the_database())

    report = replay_erasures.run(apply=True)

    assert report["contact"] == {"recorded": 1, "back": 0, "documents": 0}
    assert contact_of(http, acme, ANA_NUMBER)["uuid"] == again["uuid"] != before["uuid"]


def test_a_database_that_was_not_restored_is_left_alone(http, acme, bot_id):
    ana = contact_of(http, acme, ANA_NUMBER)
    erase(http, acme, f"/chat/{bot_id}/contacts/{ana['uuid']}")
    before = copy_of_the_database()

    report = replay_erasures.run(apply=True)

    assert report["contact"] == {"recorded": 1, "back": 0, "documents": 0}
    assert copy_of_the_database() == before


# --- The files -------------------------------------------------------------------------------------------------------

def months_ago(months: int) -> str:
    now = datetime.now(timezone.utc)
    month = now.year * 12 + now.month - 1 - months
    return f"erasures-{month // 12:04d}-{month % 12 + 1:02d}.jsonl"


def test_what_is_older_than_any_backup_is_dropped_a_file_at_a_time(register_of_erasures):
    register_of_erasures.mkdir()
    line = json.dumps({"at": "2025-01-01T00:00:00+00:00", "kind": "client", "clientId": str(uuid4())}) + "\n"
    for name in (months_ago(30), months_ago(14), months_ago(13), months_ago(1), "notes.txt", "erasures-2020-01.bak"):
        (register_of_erasures / name).write_text(line, encoding="utf-8")

    erasures.record(erasures.CLIENT, uuid4())

    assert sorted(path.name for path in register_of_erasures.iterdir()) == sorted([
        months_ago(13), months_ago(1), months_ago(0), "notes.txt", "erasures-2020-01.bak"])
    assert len(recorded()) == 3


def test_a_line_that_cannot_be_read_is_counted_and_the_rest_is_used(http, acme, bot_id, register_of_erasures):
    ana = contact_of(http, acme, ANA_NUMBER)
    backup = copy_of_the_database()
    erase(http, acme, f"/chat/{bot_id}/contacts/{ana['uuid']}")
    (file,) = register_of_erasures.iterdir()
    with open(file, "a", encoding="utf-8") as register:
        register.write('{"at": "2026-10-13T00:00:00+00:00", "kind": "cont\n')
        register.write(json.dumps({"at": "x", "kind": "something else", "clientId": str(uuid4())}) + "\n")
        register.write(json.dumps({"at": "x", "kind": "chatbot", "clientId": "not-a-uuid"}) + "\n")
    restore(backup)

    report = replay_erasures.run(apply=True)

    assert report["unreadable lines"] == 3
    assert report["contact"] == {"recorded": 1, "back": 1, "documents": 2}
    assert Contact.objects(_uuid=ana["uuid"]).count() == 0


def test_a_register_that_is_not_there_is_not_an_empty_one(register_of_erasures):
    """Found in the dual review of Gate 2: replaying from the wrong directory said there was nothing to do."""
    assert not register_of_erasures.exists()

    with pytest.raises(RegisterUnavailable):
        replay_erasures.run(apply=True)


def test_an_empty_register_is_an_empty_report(register_of_erasures):
    register_of_erasures.mkdir()

    assert replay_erasures.run(apply=True) == {
        "contact": {"recorded": 0, "back": 0, "documents": 0}, "chatbot": {"recorded": 0, "back": 0, "documents": 0},
        "client": {"recorded": 0, "back": 0, "documents": 0}, "unreadable lines": 0}


def test_deletions_written_at_once_do_not_mix_their_lines():
    clients = [uuid4() for _ in range(8)]

    def write(client_id):
        for _ in range(40):
            erasures.record(erasures.CONTACT, client_id, bot_id=uuid4(), contacts=[uuid4(), uuid4()])

    threads = [threading.Thread(target=write, args=(client_id,)) for client_id in clients]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    read, unreadable = erasures.entries()
    assert (len(read), unreadable) == (320, 0)
    assert {entry["clientId"] for entry in read} == set(clients)


WRITER = """
import importlib.util, sys, uuid
spec = importlib.util.spec_from_file_location("erasures", sys.argv[1])
erasures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(erasures)
erasures.configure(sys.argv[2])
for _ in range(100):
    erasures.record(erasures.CONTACT, uuid.uuid4(), bot_id=uuid.uuid4(), contacts=[uuid.uuid4(), uuid.uuid4()])
"""


@pytest.mark.skipif(os.name == "nt", reason="Between processes, appending to a file is only atomic on POSIX")
def test_several_workers_writing_at_once_do_not_lose_a_line(register_of_erasures):
    """The service runs with more than one worker process, and each writes to the same files."""
    writers = [subprocess.Popen([sys.executable, "-c", WRITER, erasures.__file__, str(register_of_erasures)])
               for _ in range(4)]

    assert [writer.wait(timeout=60) for writer in writers] == [0, 0, 0, 0]
    read, unreadable = erasures.entries()
    assert (len(read), unreadable) == (400, 0)


@pytest.mark.skipif(os.name == "nt", reason="Windows has no owner-only file mode")
def test_the_files_of_the_register_are_owner_only(register_of_erasures):
    erasures.record(erasures.CLIENT, uuid4())

    (file,) = register_of_erasures.iterdir()
    assert file.stat().st_mode & 0o077 == 0
