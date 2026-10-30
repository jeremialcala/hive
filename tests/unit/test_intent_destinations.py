# -*- coding: utf-8 -*-
"""
    Where the API call of an intent may go (I7: R6, RS16, T11). The worker of the engine makes these calls with
    the platform's network, so a client must not be able to point one at the platform: https only, a public host
    written out, no user nor password, no Host header. The rule is checked when an intent is saved, and an action
    saved before it is left out of the tools the engine reads.
"""
import logging

import pytest
from fastapi.testclient import TestClient

import app as app_module
from classes import Intent
from shared.destinations import check_destination, is_valid_destination
from classes.entity_intent import Action, ApiCall

BOT = {"name": "Mine", "role": "Sales Assistant", "description": "A test chatbot", "tone": "helpful"}

ALLOWED = {
    "a public name with a placeholder in the path": "https://api.example.com/orders/{order_id}",
    "a placeholder in the query": "https://api.example.com/orders?id={order_id}",
    "another port": "https://api.example.com:8443/orders",
    "a public IPv4 address": "https://8.8.8.8/dns",
    "a public IPv6 address": "https://[2001:4860:4860::8888]/dns",
    "an international name as xn--": "https://xn--bcher-kva.example/books",
    "a name ending in a dot": "https://api.example.com./orders",
    "the scheme in capitals": "HTTPS://api.example.com/orders",
}
REFUSED = {
    "http": "http://api.example.com/orders",
    "another scheme": "ftp://api.example.com/orders",
    "no scheme": "api.example.com/orders",
    "no host": "https:///orders",
    "localhost": "https://localhost/admin",
    "a name under localhost": "https://api.localhost/admin",
    "loopback": "https://127.0.0.1/admin",
    "loopback in short form": "https://127.1/admin",
    "loopback as one number": "https://2130706433/admin",
    "loopback in hexadecimal": "https://0x7f.1/admin",
    "the address of no host": "https://0.0.0.0/admin",
    "a private network": "https://10.0.0.5/admin",
    "the appliance": "https://192.168.10.1/admin",
    "the cloud metadata service": "https://169.254.169.254/latest/meta-data",
    "shared address space": "https://100.64.0.1/admin",
    "multicast": "https://224.0.0.1/admin",
    "IPv6 loopback": "https://[::1]/admin",
    "loopback mapped into IPv6": "https://[::ffff:127.0.0.1]/admin",
    "IPv6 unique local": "https://[fd00::1]/admin",
    "IPv6 link local": "https://[fe80::1]/admin",
    "a host of the platform's network": "https://andvari:8200/v1/sys/mounts",
    "the management API": "https://hive-api:5002/clients",
    "a name without a dot": "https://hermod/api",
    "mDNS": "https://printer.local/status",
    "an internal name": "https://api.internal/orders",
    "a home network": "https://router.home.arpa/admin",
    "a user and a password": "https://user:secret@api.example.com/orders",
    "only a user": "https://user@api.example.com/orders",
    "a placeholder for the host": "https://{host}/orders",
    "a placeholder in the host": "https://api.{domain}.com/orders",
    "a placeholder for the port": "https://api.example.com:{port}/orders",
    "a port out of range": "https://api.example.com:99999/orders",
    "a label starting with a dash": "https://-bad.example.com/orders",
    "an underscore in the host": "https://bad_name.example.com/orders",
    "an international name as is": "https://bücher.example/books",
    "a space": "https://api.example.com/or ders",
    "a line break": "https://api.example.com/orders\nX-Injected: 1",
    "too long": "https://api.example.com/" + "a" * 2048,
    "empty": "",
}


@pytest.mark.parametrize("url", ALLOWED.values(), ids=ALLOWED.keys())
def test_a_public_https_destination_is_allowed(url):
    check_destination(url)
    assert is_valid_destination(url)


@pytest.mark.parametrize("url", REFUSED.values(), ids=REFUSED.keys())
def test_a_destination_that_could_reach_the_platform_is_refused(url):
    with pytest.raises(ValueError) as refusal:
        check_destination(url)

    assert not is_valid_destination(url)
    # The message says why, never with what: what a client sends is not repeated.
    assert url not in str(refusal.value) or not url


def test_something_that_is_not_text_is_refused():
    assert not is_valid_destination(None)


# --- Saving an intent ---------------------------------------------------------------------------------------------

@pytest.fixture
def http():
    return TestClient(app_module.app)


@pytest.fixture
def acme(register):
    return register("acme")


@pytest.fixture
def bot(http, acme):
    return http.post("/bot", headers=acme.auth, json=BOT).json()["data"]["uuid"]


def intent(url="https://api.example.com/orders/{order_id}", **api_call) -> dict:
    return {"name": "check_order", "description": "Order status",
            "actions": [{"name": "get_order", "description": "Looks an order up",
                         "apiCall": {"url": url, **api_call}}]}


def stored_urls(bot) -> list:
    return [action.apiCall.url for item in Intent.objects(botId=bot) for action in item.actions]


@pytest.mark.parametrize("url", [REFUSED["the appliance"], REFUSED["a host of the platform's network"],
                                 REFUSED["http"], REFUSED["a placeholder for the host"]],
                         ids=["the appliance", "a host of the platform's network", "http", "a placeholder for the host"])
def test_an_intent_that_calls_where_it_may_not_is_not_saved(http, acme, bot, url):
    response = http.post(f"/chat/{bot}/intents", headers=acme.auth, json=intent(url))

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][-1] == "url"
    assert url not in response.text
    assert stored_urls(bot) == []


def test_an_intent_is_not_changed_to_call_where_it_may_not(http, acme, bot):
    created = http.post(f"/chat/{bot}/intents", headers=acme.auth, json=intent())
    assert created.status_code == 200
    intent_id = http.get(f"/chat/{bot}/intents", headers=acme.auth).json()["data"][0]["uuid"]

    changed = http.put(f"/chat/{bot}/intents/{intent_id}", headers=acme.auth,
                       json=intent("https://169.254.169.254/latest/meta-data"))

    assert changed.status_code == 422
    assert stored_urls(bot) == ["https://api.example.com/orders/{order_id}"]


@pytest.mark.parametrize("api_call", [{"headers": {"Host": "andvari"}}, {"headers": {" host ": "andvari"}},
                                      {"method": "CONNECT"}, {"method": "get"}, {"authType": "token"},
                                      {"timeout": 0}, {"timeout": 31}],
                         ids=["a Host header", "a Host header with spaces", "another method", "a method in lowercase",
                              "another kind of authentication", "no time", "more than 30 seconds"])
def test_the_rest_of_a_call_is_held_to_what_the_worker_does(http, acme, bot, api_call):
    response = http.post(f"/chat/{bot}/intents", headers=acme.auth, json=intent(**api_call))

    assert response.status_code == 422
    assert stored_urls(bot) == []


def test_a_call_to_a_public_api_is_saved_as_it_was_written(http, acme, bot):
    response = http.post(f"/chat/{bot}/intents", headers=acme.auth,
                         json=intent("https://api.example.com:8443/orders?id={order_id}", method="POST",
                                     headers={"X-Api-Version": "2"}, authType="bearer", timeout=10))

    assert response.status_code == 200
    assert stored_urls(bot) == ["https://api.example.com:8443/orders?id={order_id}"]


# --- The tools the engine reads -------------------------------------------------------------------------------------

def test_an_action_saved_before_the_rule_is_left_out_of_the_tools(http, acme, bot, caplog):
    assert http.post(f"/chat/{bot}/intents", headers=acme.auth, json=intent()).status_code == 200
    # As it could have been saved before I7: straight in the database.
    saved = Intent.objects(botId=bot).first()
    saved.actions.append(Action(name="read_vault", description="Reads the vault",
                                apiCall=ApiCall(url="https://andvari:8200/v1/sys/mounts")))
    saved.actions.append(Action(name="hand_off", description="Hands the person to a human", type="handoff"))
    saved.save()
    caplog.set_level(logging.WARNING)

    tools = http.get(f"/chat/{bot}/tools", headers=acme.auth).json()["data"]

    assert [tool["name"] for tool in tools] == ["get_order", "hand_off"]
    assert f"Intent {saved.uuid}: 1 action(s) left out of the tools" in caplog.text
    assert "andvari:8200" not in caplog.text and "read_vault" not in caplog.text
