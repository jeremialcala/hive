# -*- coding: utf-8 -*-
"""
    The WhatsApp templates of a chatbot (ADR-0031, RF30, HIG-85), against a local HTTP server that stands for Meta's
    Graph API: it checks the address, the token and the body, and answers what Meta would, with the error codes of
    its documentation. A template is checked before it goes to Meta, its copy keeps Meta's state, and neither its
    text nor the token reaches the log.
"""
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

import app as app_module
from classes import ChatBot, MessageTemplate
from controller import meta_templates, templates
from controller.meta_templates import MetaTemplates
from repository import TenantRepository

WABA, TOKEN, META_ID = "102290129340398", "EAAG-whatsapp-token-of-the-templates", "594425479261596"
TEXT = "Your order {{1}} is on its way, text-marker"
BODY = {"type": "BODY", "text": TEXT, "example": {"body_text": [["A-1"]]}}
TEMPLATE = {"name": "order_update", "language": "es", "category": "UTILITY", "components": [BODY]}


class Meta(BaseHTTPRequestHandler):
    answers: list = []
    seen: list = []

    def log_message(self, *_args):
        pass

    def _answer(self):
        parts = urlsplit(self.path)
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else None
        Meta.seen.append((self.command, parts.path, parse_qs(parts.query), self.headers.get("Authorization"), body))
        code, content = Meta.answers.pop(0) if Meta.answers else (200, {"success": True})
        data = json.dumps(content).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_DELETE = _answer  # pylint: disable=invalid-name


@pytest.fixture(scope="module")
def graph():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Meta)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture(autouse=True)
def meta(graph, monkeypatch):
    Meta.answers, Meta.seen = [], []
    monkeypatch.setattr(templates, "meta", MetaTemplates(graph, "v26.0", 2))
    return Meta


def error(code, http=400):
    return http, {"error": {"message": f"(#{code}) about text-marker", "type": "OAuthException", "code": code,
                            "fbtrace_id": "A1"}}


@pytest.fixture
def http():
    return TestClient(app_module.app)


@pytest.fixture
def caller(register):
    return register("acme")


@pytest.fixture
def repo(caller):
    return TenantRepository(caller.client_id)


@pytest.fixture
def bot(repo):
    made = repo.add_bot(ChatBot(name="Bot", role="Sales Assistant", description="A test chatbot", tone="helpful"))
    return repo.set_bot_credentials(made.uuid, wabaId=WABA, whatsappToken=TOKEN)


@pytest.fixture
def logged(caplog):
    caplog.set_level(logging.DEBUG)
    return lambda: "\n".join(record.getMessage() for record in caplog.records
                             if not record.name.startswith(("httpx", "httpcore", "urllib3", "pymongo")))


def create(http, caller, bot, body=None):
    return http.post(f"/chat/{bot.uuid}/templates", headers=caller.auth, json=body or TEMPLATE)


def stored(repo, bot, status="APPROVED", meta_id=META_ID, name="order_update", parameters=1):
    return repo.save_template(bot, MessageTemplate(name=name, language="es", category="UTILITY", status=status,
                                                   metaId=meta_id, components=[BODY], parameters=parameters))


# --- The account ------------------------------------------------------------------------------------------------------

def test_the_whatsapp_business_account_is_loaded_with_the_credentials_and_shown_as_loaded(http, caller, repo):
    made = repo.add_bot(ChatBot(name="Bot", role="r", description="d", tone="t"))

    response = http.put(f"/chat/{made.uuid}/credentials", headers=caller.auth, json={"wabaId": WABA})

    assert response.status_code == 200
    assert response.json()["data"]["credentials"]["wabaId"] is True
    assert ChatBot.objects(uuid=made.uuid).first().wabaId == WABA


@pytest.mark.parametrize("waba", ["", "12a45", "1234", "1" * 26, "123 456"])
def test_a_whatsapp_business_account_that_is_not_an_id_of_meta_is_not_taken(http, caller, repo, waba):
    made = repo.add_bot(ChatBot(name="Bot", role="r", description="d", tone="t"))

    assert http.put(f"/chat/{made.uuid}/credentials", headers=caller.auth, json={"wabaId": waba}).status_code == 422


def test_without_the_whatsapp_business_account_nothing_goes_to_meta(http, caller, repo, meta):
    made = repo.add_bot(ChatBot(name="Bot", role="r", description="d", tone="t"))
    repo.set_bot_credentials(made.uuid, whatsappToken=TOKEN)

    response = create(http, caller, made)

    assert (response.status_code, response.json()["message"].split(":")[0]) == (422, "NO_WABA")
    assert meta.seen == []


def test_without_the_whatsapp_token_nothing_goes_to_meta(http, caller, repo, meta):
    made = repo.add_bot(ChatBot(name="Bot", role="r", description="d", tone="t"))
    repo.set_bot_credentials(made.uuid, wabaId=WABA)

    response = create(http, caller, made)

    assert (response.status_code, response.json()["message"].split(":")[0]) == (422, "NO_TOKEN")
    assert meta.seen == []


# --- Creating -----------------------------------------------------------------------------------------------------

def test_a_template_is_created_in_meta_and_its_copy_stays_in_review(http, caller, repo, bot, meta, logged):
    meta.answers = [(200, {"id": META_ID, "status": "PENDING", "category": "UTILITY"})]

    response = create(http, caller, bot)

    assert response.status_code == 201
    ((method, path, query, authorization, body),) = meta.seen
    assert (method, path, query, authorization) == ("POST", f"/v26.0/{WABA}/message_templates", {}, f"Bearer {TOKEN}")
    assert body == TEMPLATE
    data = response.json()["data"]
    assert (data["id"], data["status"], data["parameters"], data["components"]) == (META_ID, "PENDING", 1, [BODY])
    copy = repo.find_template(bot.uuid, "order_update", "es")
    assert (copy.metaId, copy.status, copy.category, copy.parameters) == (META_ID, "PENDING", "UTILITY", 1)
    assert META_ID in logged()
    assert "text-marker" not in logged() and TOKEN not in logged()


def test_the_category_meta_gives_is_the_one_kept(http, caller, repo, bot, meta):
    meta.answers = [(200, {"id": META_ID, "status": "PENDING", "category": "MARKETING"})]

    assert create(http, caller, bot).status_code == 201
    assert repo.find_template(bot.uuid, "order_update", "es").category == "MARKETING"


def test_a_template_with_a_header_a_footer_and_buttons_goes_as_it_is(http, caller, bot, meta):
    meta.answers = [(200, {"id": META_ID, "status": "PENDING", "category": "MARKETING"})]
    full = {**TEMPLATE, "category": "MARKETING", "components": [
        {"type": "HEADER", "format": "TEXT", "text": "News"}, BODY, {"type": "FOOTER", "text": "Acme"},
        {"type": "BUTTONS", "buttons": [{"type": "QUICK_REPLY", "text": "Stop"},
                                        {"type": "URL", "text": "See", "url": "https://acme.example/orders"},
                                        {"type": "PHONE_NUMBER", "text": "Call", "phone_number": "+12025550101"}]}]}

    assert create(http, caller, bot, full).status_code == 201
    assert meta.seen[0][4] == full


def components(*parts):
    return {**TEMPLATE, "components": list(parts)}


@pytest.mark.parametrize("body", [
    {**TEMPLATE, "name": "Order_Update"},
    {**TEMPLATE, "name": "order update"},
    {**TEMPLATE, "language": "spanish"},
    {**TEMPLATE, "category": "AUTHENTICATION"},
    components({"type": "BODY", "text": "Order {{2}}", "example": {"body_text": [["A-1"]]}}),
    components({"type": "BODY", "text": "Order {{1}} and {{3}}", "example": {"body_text": [["A-1", "B"]]}}),
    components({"type": "BODY", "text": "Order {{1}}"}),
    components({"type": "BODY", "text": "Order {{1}}", "example": {"body_text": [["A-1", "extra"]]}}),
    components({"type": "BODY", "text": "Order {{1}}", "example": {"body_text": [[" "]]}}),
    components({"type": "HEADER", "format": "TEXT", "text": "For {{1}}"}, BODY),
    components({"type": "HEADER", "format": "IMAGE", "text": "x"}, BODY),
    components(BODY, {"type": "FOOTER", "text": "Bye {{1}}"}),
    components(BODY, BODY),
    components({"type": "FOOTER", "text": "Acme"}),
    components(BODY, {"type": "BUTTONS", "buttons": [{"type": "URL", "text": "See", "url": "http://acme.example"}]}),
    components(BODY, {"type": "BUTTONS", "buttons": [{"type": "QUICK_REPLY", "text": "Ok"}] * 11}),
    components(BODY, {"type": "BUTTONS", "buttons": [{"type": "OTP", "text": "Code"}]}),
    components({**BODY, "unknown": True}),
], ids=["upper case", "space", "language", "authentication", "from 2", "missing one", "no example", "more examples",
        "empty example", "value in header", "image header", "value in footer", "two bodies", "no body", "http link",
        "eleven buttons", "other button", "unknown field"])
def test_a_template_meta_would_turn_away_is_turned_away_before_going(http, caller, bot, meta, body):
    response = create(http, caller, bot, body)

    assert response.status_code == 422
    assert meta.seen == []


# --- What Meta says no to -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("answer", [error(200, 403), error(10, 403), error(190, 401), (403, {})],
                         ids=["permission", "no permission", "token", "no code"])
def test_a_token_that_cannot_manage_templates_says_which_permission_it_needs(http, caller, repo, bot, meta, logged,
                                                                            answer):
    meta.answers = [answer]

    response = create(http, caller, bot)

    assert response.status_code == 502
    assert "whatsapp_business_management" in response.json()["message"]
    assert repo.list_templates(bot.uuid) == []
    assert "text-marker" not in response.text + logged()


def test_what_meta_says_no_to_gives_only_its_code(http, caller, repo, bot, meta, logged):
    meta.answers = [error(100)]

    response = create(http, caller, bot)

    assert response.status_code == 422
    assert response.json()["message"].startswith("META_REFUSED") and "100" in response.json()["message"]
    assert "text-marker" not in response.text + logged()
    assert repo.list_templates(bot.uuid) == []


def test_an_account_the_token_does_not_reach_says_what_to_check(http, caller, repo, bot, meta, logged):
    code, content = error(100)
    content["error"]["error_subcode"] = 33
    meta.answers = [(code, content)]

    response = http.get(f"/chat/{bot.uuid}/templates", headers=caller.auth, params={"sync": "true"})

    assert response.status_code == 422
    message = response.json()["message"]
    assert message.startswith("META_REFUSED") and "100/33" in message
    assert "wabaId" in message and "whatsapp_business_management" in message
    assert "100/33" in logged() and "text-marker" not in response.text + logged()


@pytest.mark.parametrize("subcode", ["33", True, None, [33]])
def test_a_subcode_that_is_not_a_number_is_not_kept(http, caller, bot, meta, subcode):
    code, content = error(100)
    content["error"]["error_subcode"] = subcode
    meta.answers = [(code, content)]

    response = create(http, caller, bot)

    assert response.status_code == 422
    assert response.json()["message"] == "META_REFUSED: Meta did not take it, with its code 100"


@pytest.mark.parametrize("answer", [(500, {}), (503, {"error": {"code": 2}}), error(4), error(80008), (429, {})])
def test_when_meta_cannot_answer_now_the_client_can_try_again_later(http, caller, repo, bot, meta, answer):
    meta.answers = [answer]

    assert create(http, caller, bot).status_code == 503
    assert repo.list_templates(bot.uuid) == []


@pytest.mark.parametrize("made", [{}, {"id": 594425479261596}, {"id": "../me"}, {"id": "1/../2"}])
def test_a_template_meta_made_without_an_id_of_its_own_is_not_stored(http, caller, repo, bot, meta, made):
    meta.answers = [(200, made)]

    assert create(http, caller, bot).status_code == 503
    assert repo.list_templates(bot.uuid) == []


# --- Listing and bringing Meta's list -------------------------------------------------------------------------------

def of_meta(name, status="APPROVED", category="UTILITY", meta_id=META_ID, text=TEXT):
    return {"id": meta_id, "name": name, "language": "es", "status": status, "category": category,
            "components": [{"type": "BODY", "text": text}]}


def test_a_list_without_sync_does_not_go_to_meta(http, caller, repo, bot, meta):
    stored(repo, bot)

    response = http.get(f"/chat/{bot.uuid}/templates", headers=caller.auth)

    assert response.status_code == 200
    assert [template["id"] for template in response.json()["data"]["templates"]] == [META_ID]
    assert meta.seen == []


def test_a_sync_brings_meta_s_list_over_the_copy(http, caller, repo, bot, meta):
    stored(repo, bot, status="PENDING", parameters=1)
    stored(repo, bot, status="APPROVED", meta_id="111111111", name="deleted_in_meta")
    meta.answers = [
        (200, {"data": [of_meta("order_update", text="Order {{1}} for {{2}}")],
               "paging": {"cursors": {"after": "PAGE2"}, "next": "https://graph.facebook.com/next"}}),
        (200, {"data": [of_meta("welcome", meta_id="222222222", text="Hello"),
                        of_meta("login_code", category="AUTHENTICATION", meta_id="333333333"),
                        of_meta("Bad Name", meta_id="444444444"), "not a template"],
               "paging": {"cursors": {"after": "PAGE3"}}}),
    ]

    response = http.get(f"/chat/{bot.uuid}/templates", headers=caller.auth, params={"sync": "true"})

    assert response.status_code == 200
    assert [(m, p, q.get("after")) for m, p, q, _, _ in meta.seen] == [
        ("GET", f"/v26.0/{WABA}/message_templates", None), ("GET", f"/v26.0/{WABA}/message_templates", ["PAGE2"])]
    listed = {t["name"]: (t["status"], t["parameters"]) for t in response.json()["data"]["templates"]}
    assert listed == {"order_update": ("APPROVED", 2), "welcome": ("APPROVED", 0)}
    assert repo.find_template(bot.uuid, "order_update", "es").status == "APPROVED"


def test_a_list_cut_short_does_not_take_what_is_missing_for_deleted(http, caller, repo, bot, meta, monkeypatch):
    monkeypatch.setattr(meta_templates, "MAX_PAGES", 1)
    stored(repo, bot, meta_id="111111111", name="on_a_later_page")
    meta.answers = [(200, {"data": [of_meta("order_update")],
                           "paging": {"cursors": {"after": "PAGE2"}, "next": "https://graph.facebook.com/next"}})]

    response = http.get(f"/chat/{bot.uuid}/templates", headers=caller.auth, params={"sync": "true"})

    assert response.status_code == 200
    assert {t["name"] for t in response.json()["data"]["templates"]} == {"order_update", "on_a_later_page"}


def test_a_sync_that_meta_turns_away_leaves_the_copy_as_it_was(http, caller, repo, bot, meta):
    stored(repo, bot)
    meta.answers = [error(190, 401)]

    assert http.get(f"/chat/{bot.uuid}/templates", headers=caller.auth, params={"sync": "true"}).status_code == 502
    assert [t.metaId for t in repo.list_templates(bot.uuid)] == [META_ID]


# --- Editing ------------------------------------------------------------------------------------------------------

EDIT = {"components": [{"type": "BODY", "text": "Order {{1}} left on {{2}}", "example": {"body_text": [["A", "B"]]}}]}


@pytest.mark.parametrize("status", ["APPROVED", "REJECTED", "PAUSED"])
def test_an_edited_template_goes_back_to_review(http, caller, repo, bot, meta, status):
    stored(repo, bot, status=status)

    response = http.put(f"/chat/{bot.uuid}/templates/{META_ID}", headers=caller.auth, json=EDIT)

    assert response.status_code == 200
    ((method, path, _, authorization, body),) = meta.seen
    assert (method, path, authorization, body) == ("POST", f"/v26.0/{META_ID}", f"Bearer {TOKEN}", EDIT)
    copy = repo.template_by_meta_id(bot.uuid, META_ID)
    assert (copy.status, copy.parameters, copy.components) == ("PENDING", 2, EDIT["components"])


@pytest.mark.parametrize("status", ["PENDING", "DISABLED", "IN_APPEAL"])
def test_a_template_in_review_or_disabled_is_not_edited(http, caller, repo, bot, meta, status):
    stored(repo, bot, status=status)

    assert http.put(f"/chat/{bot.uuid}/templates/{META_ID}", headers=caller.auth, json=EDIT).status_code == 409
    assert meta.seen == []


def test_an_edit_meta_turns_away_leaves_the_copy_as_it_was(http, caller, repo, bot, meta):
    stored(repo, bot)
    meta.answers = [error(100)]

    assert http.put(f"/chat/{bot.uuid}/templates/{META_ID}", headers=caller.auth, json=EDIT).status_code == 422
    assert repo.template_by_meta_id(bot.uuid, META_ID).status == "APPROVED"


# --- Deleting -----------------------------------------------------------------------------------------------------

def test_a_template_is_deleted_in_meta_by_its_id_and_name_and_its_copy_goes(http, caller, repo, bot, meta):
    stored(repo, bot)

    response = http.delete(f"/chat/{bot.uuid}/templates/{META_ID}", headers=caller.auth)

    assert response.status_code == 200
    ((method, path, query, authorization, _),) = meta.seen
    assert (method, path, authorization) == ("DELETE", f"/v26.0/{WABA}/message_templates", f"Bearer {TOKEN}")
    assert query == {"hsm_id": [META_ID], "name": ["order_update"]}
    assert repo.list_templates(bot.uuid) == []


def test_a_disabled_template_is_not_deleted(http, caller, repo, bot, meta):
    stored(repo, bot, status="DISABLED")

    response = http.delete(f"/chat/{bot.uuid}/templates/{META_ID}", headers=caller.auth)

    assert response.status_code == 409
    assert meta.seen == []
    assert [t.metaId for t in repo.list_templates(bot.uuid)] == [META_ID]


def test_a_deletion_meta_turns_away_keeps_the_copy(http, caller, repo, bot, meta):
    stored(repo, bot)
    meta.answers = [(500, {})]

    response = http.delete(f"/chat/{bot.uuid}/templates/{META_ID}", headers=caller.auth)

    assert response.status_code == 503
    assert [t.metaId for t in repo.list_templates(bot.uuid)] == [META_ID]


# --- Which template --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("method", ["PUT", "DELETE"])
@pytest.mark.parametrize("template_id", ["999999999", "..%2Fme", "1%0AFORGED", "x" * 40])
def test_a_template_that_is_not_there_is_not_found_and_nothing_goes_to_meta(http, caller, repo, bot, meta, logged,
                                                                           method, template_id):
    stored(repo, bot)

    response = http.request(method, f"/chat/{bot.uuid}/templates/{template_id}", headers=caller.auth, json=EDIT)

    assert response.status_code == 404
    assert meta.seen == []
    assert "FORGED" not in logged()


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
def test_a_template_is_not_reachable_through_another_chatbot(http, caller, repo, bot, meta, method):
    stored(repo, bot)
    other = repo.set_bot_credentials(repo.add_bot(ChatBot(name="Other", role="r", description="d", tone="t")).uuid,
                                     wabaId=WABA, whatsappToken=TOKEN)

    response = http.request(method, f"/chat/{other.uuid}/templates/{META_ID}", headers=caller.auth, json=EDIT)

    assert response.status_code == 404
    assert meta.seen == []
    assert repo.template_by_meta_id(bot.uuid, META_ID).status == "APPROVED"


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
def test_a_template_of_another_client_is_not_reachable(http, caller, repo, bot, meta, register, method):
    stored(repo, bot)
    stranger = register("globex")
    theirs = TenantRepository(stranger.client_id)
    their_bot = theirs.set_bot_credentials(
        theirs.add_bot(ChatBot(name="Theirs", role="r", description="d", tone="t")).uuid, wabaId=WABA,
        whatsappToken="EAAG-their-token")

    response = http.request(method, f"/chat/{their_bot.uuid}/templates/{META_ID}", headers=stranger.auth, json=EDIT)

    assert response.status_code == 404
    assert meta.seen == []
    assert theirs.list_templates(their_bot.uuid) == []
