# -*- coding: utf-8 -*-
"""
    The ingestion of a notification (ADR-0021, I4b): from the event the webhook publishes on WEBHOOK to the contact,
    the history, the message identified on MENSAJES (validated against asyncapi-v2.yaml) and the envelope of version
    1 for the engine that still reads Hermod. The bus and the broker are replaced; the database is the in-memory one.
"""
import asyncio
import hashlib
import json
import pathlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import nats.errors
import pytest
import yaml
from jsonschema import Draft202012Validator
from pymongo.errors import ServerSelectionTimeoutError

from shared import bus
import controller.facebook as facebook
import controller.ingestion as ingestion
import ingest
from classes import ChatBot, Contact, ConversationMessage
from controller.incoming import IncomingMessage
from controller.webhook_event import webhook_event
from repository import NotFound, TenantRepository
from shared.contract import IdentifiedMessage, SentReply, identified_subject

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTRACT = yaml.safe_load((ROOT / "docs" / "02-design" / "contracts" / "asyncapi-v2.yaml").read_text(encoding="utf-8"))
IDENTIFIED = Draft202012Validator({"$ref": "#/components/schemas/MensajeIdentificado",
                                   "components": CONTRACT["components"]})
METADATA = {"display_phone_number": "12025550101", "phone_number_id": "100000000000001"}
NUMBER = "12025550143"


class Bus:
    """Stands for Bifröst: keeps what it is handed, or refuses it."""

    def __init__(self):
        self.events, self.refuse = [], False

    async def publish(self, subject, payload, message_id, headers=None):
        if self.refuse:
            raise bus.BusUnavailable("TimeoutError")
        self.events.append(SimpleNamespace(subject=subject, payload=json.loads(payload), message_id=message_id,
                                           headers=headers or {}))
        return bus.Published(stream="MENSAJES", sequence=len(self.events), duplicate=False)

    async def close(self):
        pass


@pytest.fixture
def bifrost():
    kept = Bus()
    bus.configure(kept)
    yield kept
    bus.configure(None)


@pytest.fixture
def queue(monkeypatch):
    sent = []

    async def publish(queue, message):
        sent.append(json.loads(message.to_json()))
    monkeypatch.setattr(ingestion, "send_message_to_queue", publish)
    return sent


@pytest.fixture
def graph_api(monkeypatch):
    profile = {"id": "123456789", "first_name": "Carla", "last_name": "Diaz"}
    monkeypatch.setattr(facebook.requests, "get",
                        lambda url, timeout: SimpleNamespace(status_code=200, text="{}", json=lambda: profile))


@pytest.fixture
def repo(register):
    return TenantRepository(register("acme").client_id)


@pytest.fixture
def bot(repo):
    return repo.add_bot(ChatBot(name="Bot", role="Sales Assistant", description="A test chatbot", tone="helpful",
                                facebookToken="page-token"))


def whatsapp_message(message_id, kind="text", **body):
    message = {"from": NUMBER, "id": message_id, "timestamp": "1760000000", "type": kind,
               kind: body or {"body": "hola, quiero una cita"}}
    return IncomingMessage(message_id=message_id, sender=NUMBER, name="Ana", content={
        "messaging_product": "whatsapp", "metadata": METADATA,
        "contacts": [{"profile": {"name": "Ana"}, "wa_id": NUMBER}], "messages": [message]})


def messenger_message(mid, **message):
    return IncomingMessage(message_id=mid, sender="123456789", name=None, content={
        "sender": {"id": "123456789"}, "recipient": {"id": "987654321"}, "timestamp": 1760000000000,
        "message": {"mid": mid, "text": "hola", **message}})


def event_of(bot, *messages_, channel="whatsapp"):
    # Through JSON, as it comes out of the bus.
    return json.loads(json.dumps(webhook_event(channel, bot, list(messages_), datetime.now(timezone.utc))))


def ingest_now(event, trace=None):
    return asyncio.run(ingestion.ingest_notification(event, trace))


# --- One message -----------------------------------------------------------------------------------------------------

def test_a_message_is_identified_stored_and_published_for_the_engine(bot, bifrost, queue):
    assert ingest_now(event_of(bot, whatsapp_message("wamid.A"))) == {"ingested": 1, "seen": 0, "failed": 0}

    contact = Contact.objects.get()
    identified, = bifrost.events
    where = ingestion.partition(bot.uuid, contact._uuid)
    assert identified.subject == f"gateway.message.{where}.{bot.uuid}.{contact._uuid}"
    assert identified.message_id == f"{bot.uuid}:wamid.A"
    assert identified.payload["contact"] == {"uuid": str(contact._uuid), "name": "Ana", "waId": NUMBER}
    assert identified.payload["orderKey"] == f"{bot.uuid}.{contact._uuid}"
    assert [error.message for error in IDENTIFIED.iter_errors(identified.payload)] == []
    # The engine's classifier reads it with the shared model (ADR-0023), from where the shared subject says.
    read = IdentifiedMessage.model_validate(identified.payload)
    assert (read.botId, read.contact.uuid, read.contact.waId) == (bot.uuid, contact._uuid, NUMBER)
    assert identified.subject == identified_subject(where, bot.uuid, contact._uuid)
    assert [envelope["messageId"] for envelope in queue] == ["wamid.A"]
    assert ConversationMessage.objects.get().contactId == contact._uuid


def test_the_trace_of_the_event_goes_on_with_the_message(bot, bifrost, queue):
    trace = "00-" + "a" * 32 + "-" + "b" * 16 + "-01"

    ingest_now(event_of(bot, whatsapp_message("wamid.A")), trace)

    # The same trace, from a span of the ingestion: the message identified is its child.
    version, trace_id, span_id, flags = bifrost.events[0].headers["traceparent"].split("-")
    assert (version, trace_id, flags) == ("00", "a" * 32, "01")
    assert span_id != "b" * 16


def test_a_messenger_message_is_identified_through_the_graph_api(bot, bifrost, queue, graph_api):
    ingest_now(event_of(bot, messenger_message("m_A"), channel="messenger"))

    identified, = bifrost.events
    assert identified.payload["contact"]["fbId"] == "123456789"
    assert identified.payload["contact"]["name"] == "Carla"
    assert queue[0]["jwe_body"]["contact"] == {"name": "Carla", "fbId": "123456789"}


def test_without_a_bus_only_the_envelope_goes_out(bot, queue):
    ingest_now(event_of(bot, whatsapp_message("wamid.A")))

    assert len(queue) == 1 and ConversationMessage.objects.count() == 1


# --- Several messages, in order -------------------------------------------------------------------------------------

def test_the_messages_of_a_notification_are_ingested_in_order_for_the_same_contact(bot, bifrost, queue):
    ingest_now(event_of(bot, whatsapp_message("wamid.A"), whatsapp_message("wamid.B")))

    assert [event.payload["messageId"] for event in bifrost.events] == ["wamid.A", "wamid.B"]
    assert Contact.objects.count() == 1
    assert len({event.subject for event in bifrost.events}) == 1


def test_a_message_already_in_the_history_is_skipped(bot, bifrost, queue):
    ingest_now(event_of(bot, whatsapp_message("wamid.A")))

    again = ingest_now(event_of(bot, whatsapp_message("wamid.A"), whatsapp_message("wamid.B")))

    assert again == {"ingested": 1, "seen": 1, "failed": 0}
    assert [event.payload["messageId"] for event in bifrost.events] == ["wamid.A", "wamid.B"]


def test_a_message_that_fails_by_itself_does_not_take_the_others_with_it(bot, bifrost, queue):
    broken = IncomingMessage(message_id="wamid.BROKEN", sender=NUMBER, name=None, content={"contacts": []})

    outcome = ingest_now(event_of(bot, whatsapp_message("wamid.A"), broken, whatsapp_message("wamid.B")))

    assert outcome == {"ingested": 2, "seen": 0, "failed": 1}


# --- When it has to wait --------------------------------------------------------------------------------------------

def test_a_bus_that_does_not_keep_the_message_makes_the_event_wait_and_nothing_is_stored(bot, bifrost, queue):
    bifrost.refuse = True

    with pytest.raises(bus.BusUnavailable):
        ingest_now(event_of(bot, whatsapp_message("wamid.A")))

    # Not in the history: delivered again, it is published then.
    assert ConversationMessage.objects.count() == 0 and queue == []


# Meta delivers a message again for as long as it did not get its 200: 7 days in WhatsApp, 36 hours in Instagram
# (HIG-114, RNF04). The history, which holds every message for 12 months, is what says it was already seen.
META_RETRIES = timedelta(days=7)


@pytest.mark.parametrize("later", [timedelta(hours=6), timedelta(days=6, hours=23)], ids=["hours", "days"])
def test_a_message_meta_delivers_again_later_is_seen_and_not_published_again(bot, bifrost, queue, later):
    first = event_of(bot, whatsapp_message("wamid.A"))
    ingest_now(first)
    again = dict(first, receivedAt=(datetime.fromisoformat(first["receivedAt"]) + later).isoformat())

    assert ingest_now(again) == {"ingested": 0, "seen": 1, "failed": 0}
    assert [event.payload["messageId"] for event in bifrost.events] == ["wamid.A"]
    assert ConversationMessage.objects.count() == 1


def test_the_history_remembers_a_message_longer_than_meta_delivers_it_again(bot, bifrost, queue):
    ingest_now(event_of(bot, whatsapp_message("wamid.A")))

    (stored,) = ConversationMessage.objects
    assert stored.expiresAt - stored.createdAt > META_RETRIES


def test_a_message_published_twice_carries_the_same_id_for_the_bus_to_drop_the_second(bot, bifrost, queue,
                                                                                       monkeypatch):
    """
        Stopping between publishing and storing publishes it again on the next delivery, with the same Nats-Msg-Id:
        MENSAJES drops the copy for 24 hours, as long as it keeps a message (higerotech/bifrost#10).
    """
    record = TenantRepository.record_message
    monkeypatch.setattr(TenantRepository, "record_message", lambda *args, **kwargs: (_ for _ in ()).throw(
        ingestion.DatabaseUnavailable("ServerSelectionTimeoutError")))
    with pytest.raises(ingestion.DatabaseUnavailable):
        ingest_now(event_of(bot, whatsapp_message("wamid.A")))
    monkeypatch.setattr(TenantRepository, "record_message", record)

    later = event_of(bot, whatsapp_message("wamid.A"))
    later["receivedAt"] = (datetime.fromisoformat(later["receivedAt"]) + timedelta(hours=3)).isoformat()
    ingest_now(later)

    first, second = bifrost.events
    assert first.message_id == second.message_id == f"{bot.uuid}:wamid.A"


def test_a_message_published_but_not_stored_is_published_again_and_stored_once(bot, bifrost, queue, monkeypatch):
    """Publishing goes before storing: stopping in between repeats the publication, never loses it."""
    record = TenantRepository.record_message
    monkeypatch.setattr(TenantRepository, "record_message", lambda *args, **kwargs: (_ for _ in ()).throw(
        ingestion.DatabaseUnavailable("ServerSelectionTimeoutError")))
    with pytest.raises(ingestion.DatabaseUnavailable):
        ingest_now(event_of(bot, whatsapp_message("wamid.A")))
    monkeypatch.setattr(TenantRepository, "record_message", record)

    ingest_now(event_of(bot, whatsapp_message("wamid.A")))

    assert [event.message_id for event in bifrost.events] == [f"{bot.uuid}:wamid.A"] * 2
    assert ConversationMessage.objects.count() == 1


# --- What can never be ingested -------------------------------------------------------------------------------------

@pytest.mark.parametrize("change", [
    lambda event: event.update(schemaVersion=1),
    lambda event: event.update(botId="00000000-0000-0000-0000-000000000000"),
    lambda event: event.update(clientId="not-a-uuid"),
], ids=["another version", "a chatbot that does not exist", "no client"])
def test_an_event_that_can_never_be_ingested_is_unprocessable(bot, bifrost, queue, change):
    event = event_of(bot, whatsapp_message("wamid.A"))
    change(event)

    with pytest.raises(ingestion.Unprocessable):
        ingest_now(event)
    assert bifrost.events == [] and queue == []


def test_the_chatbot_of_another_client_is_not_found(bot, bifrost, queue, register):
    event = event_of(bot, whatsapp_message("wamid.A"))
    event["clientId"] = str(register("globex").client_id)

    with pytest.raises(ingestion.Unprocessable):
        ingest_now(event)


# --- What goes to the history ----------------------------------------------------------------------------------------

def test_the_text_of_each_kind_of_message(bot):
    whatsapp = lambda message: ingestion.what_was_said("whatsapp", message.content)[:2]  # noqa: E731

    assert whatsapp(whatsapp_message("1")) == ("hola, quiero una cita", ())
    assert whatsapp(whatsapp_message("2", "image", id="media", caption="mira")) == ("mira", ("image",))
    assert whatsapp(whatsapp_message("3", "audio", id="media")) == (None, ("audio",))
    assert whatsapp(whatsapp_message("4", "interactive", type="button_reply",
                                     button_reply={"id": "b1", "title": "Sí"})) == ("Sí", ())
    assert ingestion.what_was_said("messenger", messenger_message("m_1").content)[:2] == ("hola", ())
    assert ingestion.what_was_said("messenger", messenger_message(
        "m_2", text=None, attachments=[{"type": "image", "payload": {"url": "https://lookaside.example/x"}}]
    ).content)[:2] == (None, ("image",))


def test_the_address_of_an_attachment_is_not_stored(bot, bifrost, queue):
    message = whatsapp_message("wamid.A", "image", id="media-id", link="https://lookaside.example/secret")

    ingest_now(event_of(bot, message))

    stored = ConversationMessage.objects.get()
    assert stored.attachments == ["image"]
    assert "lookaside" not in json.dumps(stored.to_mongo().to_dict(), default=str)


def test_the_time_is_the_one_meta_says_it_was_sent(bot, bifrost, queue):
    ingest_now(event_of(bot, whatsapp_message("wamid.A")))

    assert ConversationMessage.objects.get().sentAt.replace(tzinfo=timezone.utc) == datetime.fromtimestamp(
        1760000000, timezone.utc)


# --- The partition of a conversation --------------------------------------------------------------------------------

def test_the_partition_is_the_one_the_contract_says():
    bot_id, contact_id = "6f0c2c1e-0000-4000-8000-000000000001", "6f0c2c1e-0000-4000-8000-000000000002"
    digest = hashlib.sha256(f"{bot_id}.{contact_id}".encode()).digest()

    assert ingestion.partition(bot_id, contact_id) == int.from_bytes(digest[:4], "big") % 16
    assert ingestion.PARTITIONS == 16
    assert {ingestion.partition(bot_id, f"6f0c2c1e-0000-4000-8000-{i:012d}") for i in range(200)} == set(range(16))


# --- The worker, event by event -------------------------------------------------------------------------------------

class Delivered:
    """An event as the consumer hands it: it says what was done with it."""

    def __init__(self, data, headers=None):
        self.data, self.headers, self.done = data, headers, None

    async def ack(self):
        self.done = "ack"

    async def term(self):
        self.done = "term"


def delivered(event) -> Delivered:
    return Delivered(json.dumps(event).encode())


def test_an_event_ingested_is_acknowledged(bot, bifrost, queue):
    message = delivered(event_of(bot, whatsapp_message("wamid.A")))

    assert asyncio.run(ingest.handle(message)) == "acknowledged"
    assert message.done == "ack"


def test_an_event_that_has_to_wait_is_left_for_bifrost_to_deliver_again(bot, bifrost, queue):
    bifrost.refuse = True
    message = delivered(event_of(bot, whatsapp_message("wamid.A")))

    assert asyncio.run(ingest.handle(message)) == "waiting"
    assert message.done is None


@pytest.mark.parametrize("data", [b"not json", json.dumps({"schemaVersion": 1}).encode()],
                         ids=["not JSON", "another version"])
def test_an_event_that_can_never_be_ingested_is_dropped(bot, bifrost, queue, data):
    message = Delivered(data)

    assert asyncio.run(ingest.handle(message)) == "dropped"
    assert message.done == "term"


def test_an_unexpected_failure_is_tried_again_rather_than_lost(bot, bifrost, queue, monkeypatch):
    async def fails(*args, **kwargs):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(ingest, "ingest_notification", fails)
    message = delivered(event_of(bot, whatsapp_message("wamid.A")))

    assert asyncio.run(ingest.handle(message)) == "waiting"
    assert message.done is None


class Subscription:
    """Stands for the consumer: hands what the test says, one batch per request, and stops the worker after."""

    def __init__(self, stop, *batches):
        self.stop, self.batches, self.asked = stop, list(batches), 0

    async def fetch(self, batch, timeout):
        self.asked += 1
        if not self.batches:
            self.stop.set()
            raise nats.errors.TimeoutError()
        answer = self.batches.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_the_worker_ingests_what_it_is_handed_and_keeps_asking(bot, bifrost, queue, monkeypatch, tmp_path):
    monkeypatch.setattr(ingest, "ALIVE_FILE", tmp_path / "alive")
    no_wait = asyncio.sleep
    monkeypatch.setattr(ingest.asyncio, "sleep", lambda seconds: no_wait(0))
    first, second = delivered(event_of(bot, whatsapp_message("wamid.A"))), Delivered(b"not json")

    async def run():
        stop = asyncio.Event()
        subscription = Subscription(stop, nats.errors.TimeoutError(), [first], nats.errors.ConnectionClosedError(),
                                    [second])
        await ingest.consume(subscription, stop)
        return subscription.asked

    # A request with nothing, one with an event, the connection lost for a moment, a broken event, nothing.
    assert asyncio.run(run()) == 5
    assert (first.done, second.done) == ("ack", "term")
    assert (tmp_path / "alive").exists()


def test_a_plain_timeout_of_the_client_is_nothing_to_fetch_too(bot, bifrost, queue, monkeypatch, tmp_path, caplog):
    """
        nats-py also says "nothing in time" with asyncio's own TimeoutError, not only with its subclass: when the
        time runs out between its two requests (nats/js/client.py, _fetch_n). It used to stop the worker.
    """
    monkeypatch.setattr(ingest, "ALIVE_FILE", tmp_path / "alive")
    first = delivered(event_of(bot, whatsapp_message("wamid.A")))

    async def run():
        stop = asyncio.Event()
        subscription = Subscription(stop, asyncio.TimeoutError(), [first])
        await ingest.consume(subscription, stop)
        return subscription.asked

    assert asyncio.run(run()) == 3
    assert first.done == "ack"
    assert "Waiting for" not in caplog.text


def test_the_worker_does_not_start_without_the_bus(monkeypatch):
    monkeypatch.setattr(ingest.settings, "nats_url", None)

    with pytest.raises(SystemExit):
        asyncio.run(ingest.main())


# --- The answers the engine sent (RESPUESTAS) ------------------------------------------------------------------------

ANSWER = Draft202012Validator({"$ref": "#/components/schemas/RespuestaEnviada", "components": CONTRACT["components"]})


def answer_to(contact, message_id="wamid.ANSWER", **changes) -> dict:
    """A RespuestaEnviada as the engine's notifier publishes it, through JSON."""
    answer = {"schemaVersion": 2, "botId": str(contact.botId), "clientId": str(contact.clientId),
              "contactId": str(contact._uuid), "channel": "whatsapp", "inReplyTo": "wamid.A",
              "messageId": message_id, "sentAt": "2026-10-19T12:00:05+00:00", "text": "claro, ¿a qué hora?"}
    answer.update(changes)
    return json.loads(json.dumps(answer))


@pytest.fixture
def ana(bot, bifrost, queue):
    ingest_now(event_of(bot, whatsapp_message("wamid.A")))
    return Contact.objects.get()


def reply_now(event):
    return asyncio.run(ingestion.ingest_reply(event))


def test_the_answer_used_here_is_the_one_of_the_contract(ana):
    assert [error.message for error in ANSWER.iter_errors(answer_to(ana))] == []


def test_an_answer_built_with_the_shared_model_is_the_one_of_the_contract_and_is_stored(ana, repo):
    """What the engine's notifier will build (shared.contract.SentReply), through the gateway: ADR-0023."""
    sent = SentReply(schemaVersion=2, botId=ana.botId, clientId=ana.clientId, contactId=ana._uuid, channel="whatsapp",
                     inReplyTo="wamid.A", messageId="wamid.SHARED",
                     sentAt=datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc), text="hola", attachments=[{"type": "image"}])
    published = json.loads(sent.model_dump_json())

    assert [error.message for error in ANSWER.iter_errors(published)] == []
    assert reply_now(published) == {"stored": True}
    assert repo.history(ana)[1]["messageId"] == "wamid.SHARED"
    assert SentReply.model_validate(answer_to(ana)).inReplyTo == "wamid.A"


def test_an_answer_is_stored_as_the_contacts_conversation_going_out(ana, repo):
    assert reply_now(answer_to(ana)) == {"stored": True}

    incoming, answer = repo.history(ana)
    assert (incoming["direction"], answer["direction"]) == ("in", "out")
    assert (answer["messageId"], answer["inReplyTo"], answer["text"]) == ("wamid.ANSWER", "wamid.A",
                                                                          "claro, ¿a qué hora?")
    assert answer["sentAt"] == datetime(2026, 10, 19, 12, 0, 5, tzinfo=timezone.utc)
    assert "a qué hora" not in json.dumps(ConversationMessage.objects(direction="out").get().to_mongo().to_dict(),
                                          default=str)


def test_of_the_attachments_of_an_answer_only_the_type_is_stored(ana, repo):
    reply_now(answer_to(ana, text=None, attachments=[{"type": "image"}, {"url": "not a type"}]))

    assert repo.history(ana)[1]["attachments"] == ["image"]


def test_an_answer_published_again_is_stored_once(ana):
    reply_now(answer_to(ana))

    assert reply_now(answer_to(ana)) == {"stored": False}
    assert ConversationMessage.objects(direction="out").count() == 1


@pytest.mark.parametrize("change", [
    lambda answer: answer.update(schemaVersion=1),
    lambda answer: answer.update(contactId=str(uuid4())),
    lambda answer: answer.update(botId=str(uuid4())),
    lambda answer: answer.update(clientId="not-a-uuid"),
    lambda answer: answer.pop("messageId"),
    lambda answer: answer.update(sentAt="yesterday"),
], ids=["another version", "a contact that does not exist", "a chatbot that does not exist", "no client",
        "no identifier", "no time"])
def test_an_answer_that_can_never_be_stored_is_unprocessable(ana, change):
    answer = answer_to(ana)
    change(answer)

    with pytest.raises(ingestion.Unprocessable):
        reply_now(answer)
    assert ConversationMessage.objects(direction="out").count() == 0


def test_the_answer_to_a_contact_of_another_client_is_not_stored(ana, register):
    with pytest.raises(ingestion.Unprocessable):
        reply_now(answer_to(ana, clientId=str(register("globex").client_id)))


def test_an_answer_for_a_contact_deleted_meanwhile_is_unprocessable(ana, monkeypatch):
    def deleted_meanwhile(self, contact, *args, **kwargs):
        raise NotFound(contact._uuid)
    monkeypatch.setattr(TenantRepository, "record_message", deleted_meanwhile)

    with pytest.raises(ingestion.Unprocessable):
        reply_now(answer_to(ana))


def test_an_answer_waits_while_the_database_is_not_there(ana, monkeypatch):
    def down(*args, **kwargs):
        raise ServerSelectionTimeoutError("no servers")
    monkeypatch.setattr(TenantRepository, "record_message", down)

    with pytest.raises(ingestion.DatabaseUnavailable):
        reply_now(answer_to(ana))


def test_the_worker_acknowledges_an_answer_and_drops_one_it_cannot_store(ana):
    stored, unknown = delivered(answer_to(ana)), delivered(answer_to(ana, contactId=str(uuid4())))

    assert asyncio.run(ingest.handle(stored, ingest.ANSWERS)) == "acknowledged"
    assert asyncio.run(ingest.handle(unknown, ingest.ANSWERS)) == "dropped"
    assert (stored.done, unknown.done) == ("ack", "term")


# --- What a client's message needs (ADR-0031) -------------------------------------------------------------------------

def test_the_number_of_the_bot_the_contact_wrote_to_is_remembered_encrypted(bot, repo, bifrost, queue):
    ingest_now(event_of(bot, whatsapp_message("wamid.A")))

    contact = Contact.objects.get()
    assert repo.last_phone_id(contact) == METADATA["phone_number_id"]
    # Stored like the bot's numbers themselves: not in clear (RS05).
    assert METADATA["phone_number_id"] not in contact.lastPhoneId


def test_a_message_to_the_same_number_writes_nothing_more(bot, repo, bifrost, queue, another):
    ingest_now(event_of(bot, whatsapp_message("wamid.A")))
    sealed = Contact.objects.get().lastPhoneId

    ingest_now(event_of(bot, whatsapp_message("wamid.B")))

    assert Contact.objects.get().lastPhoneId == sealed


def test_what_the_client_sent_goes_to_the_history_with_its_origin_and_who_asked(bot, repo, bifrost, queue):
    ingest_now(event_of(bot, whatsapp_message("wamid.A")))
    contact = Contact.objects.get()
    sent = {"schemaVersion": 2, "botId": str(bot.uuid), "clientId": str(repo.client_id),
            "contactId": str(contact._uuid), "channel": "whatsapp", "messageId": "wamid.CLIENTE",
            "sentAt": "2026-10-29T19:00:00+00:00", "text": "Su cita es mañana", "origin": "client",
            "sentBy": "key abc", "requestId": str(uuid4())}

    assert asyncio.run(ingestion.ingest_reply(sent)) == {"stored": True}

    stored = [message for message in repo.history(contact) if message["direction"] == "out"]
    assert [(m["origin"], m["sentBy"], m["inReplyTo"], m["text"]) for m in stored] == \
        [("client", "key abc", None, "Su cita es mañana")]
