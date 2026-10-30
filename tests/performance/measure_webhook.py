# -*- coding: utf-8 -*-
"""
    How long the webhook takes to answer Meta, measured on the server with synthetic WhatsApp notifications signed
    like Meta signs them (RNF01, increment I1). Not a pytest test: it runs against a deployed gateway, inside a
    container of the service's own image on the host network, and keeps everything in /m.

        python /m/measure_webhook.py key       # the operator's key pair, and the gateway's public keys
        python /m/measure_webhook.py prepare   # a synthetic chatbot with a random App Secret
        python /m/measure_webhook.py measure   # the scenarios; results in /m/results-<date>.json

    Between key and prepare the operator's public key is registered with ops.operator_key. Nothing secret is
    printed: the operator's private key and the App Secret go to owner-only files. The steps are in
    docs/04-testing/medicion-webhook-i1.md.
"""
import hashlib
import hmac
import json
import os
import secrets
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests

DIR = "/m"
MANAGEMENT = "http://127.0.0.1:5002"
WEBHOOK = "http://127.0.0.1:5003"
AUDIENCE = os.environ.get("AUDIENCE", "")
PHONE_NUMBER_ID = "100000000000001"


def path(name: str) -> str:
    return os.path.join(DIR, name)


def read(name: str):
    with open(path(name), encoding="utf-8") as file:
        return json.load(file)


def write_private(name: str, text: str) -> None:
    descriptor = os.open(path(name), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.write(text)


def token() -> str:
    from ops.client import token as sign
    return sign(path("operator.json"), read("operator-client.json")["client"], AUDIENCE, path("gateway-public.json"))


def management(method: str, route: str, body: dict) -> dict:
    response = requests.request(method, MANAGEMENT + route, json=body, timeout=30,
                                headers={"Authorization": f"Bearer {token()}"})
    if response.status_code >= 300:
        raise SystemExit(f"{method} {route}: {response.status_code}")
    return response.json()


def key() -> None:
    from shared.auth.gateway_keys import GatewayKeys
    from ops.client import new_key
    if not os.path.exists(path("operator.json")):
        with open(path("operator-public.json"), "w", encoding="utf-8") as file:
            json.dump(new_key(path("operator.json")), file)
    with open(path("gateway-public.json"), "w", encoding="utf-8") as file:
        json.dump(GatewayKeys.load("/k/gateway-keys.json").public_set(), file)
    print("operator key ready; register operator-public.json with ops.operator_key")


def prepare() -> None:
    if not os.path.exists(path("bot.json")):
        data = management("POST", "/bot", {"name": "Measurement", "role": "Tech Support", "tone": "helpful",
                                           "description": "Synthetic chatbot to measure the webhook"}).get("data") or {}
        bot = data.get("uuid") or data.get("_uuid") or data.get("id")
        if not bot:
            raise SystemExit(f"POST /bot answered without a uuid; keys of data: {sorted(data)}")
        with open(path("bot.json"), "w", encoding="utf-8") as file:
            json.dump({"bot": str(bot)}, file)
    if not os.path.exists(path("app-secret")):
        write_private("app-secret", secrets.token_hex(32))
        with open(path("app-secret"), encoding="utf-8") as file:
            management("PUT", f"/chat/{read('bot.json')['bot']}/credentials", {"appSecret": file.read()})
    print("chatbot ready:", read("bot.json")["bot"])


def notification(wa_id: str, n: int) -> bytes:
    """One text message from one contact, the shape Meta sends for WhatsApp."""
    now = int(time.time())
    body = {"object": "whatsapp_business_account", "entry": [{"id": "200000000000002", "changes": [{
        "field": "messages", "value": {
            "messaging_product": "whatsapp",
            "metadata": {"display_phone_number": "15550000001", "phone_number_id": PHONE_NUMBER_ID},
            "contacts": [{"profile": {"name": "Measurement"}, "wa_id": wa_id}],
            "messages": [{"from": wa_id, "id": f"wamid.M{now}{n:06d}{secrets.token_hex(4)}",
                          "timestamp": str(now), "type": "text", "text": {"body": "hello, this is a measurement"}}],
        }}]}]}
    return json.dumps(body, separators=(",", ":")).encode()


def send(session: requests.Session, url: str, secret: bytes, wa_id: str, n: int) -> tuple[float, int]:
    body = notification(wa_id, n)
    signature = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    start = time.perf_counter()
    try:
        status = session.post(url, data=body, timeout=15, headers={
            "Content-Type": "application/json", "X-Hub-Signature-256": signature}).status_code
    except requests.RequestException:
        status = 0
    return (time.perf_counter() - start) * 1000, status


def summary(name: str, samples: list[tuple[float, int]]) -> dict:
    ms = sorted(t for t, _ in samples)

    def quantile(q: float) -> float:
        return ms[min(len(ms) - 1, int(q * len(ms)))]

    statuses = {}
    for _, status in samples:
        statuses[str(status)] = statuses.get(str(status), 0) + 1
    result = {"scenario": name, "n": len(ms), "median_ms": round(statistics.median(ms), 1),
              "p95_ms": round(quantile(0.95), 1), "p99_ms": round(quantile(0.99), 1), "max_ms": round(ms[-1], 1),
              "min_ms": round(ms[0], 1), "over_1s_pct": round(100 * sum(t > 1000 for t in ms) / len(ms), 2),
              "statuses": statuses}
    print(json.dumps(result))
    return result


def measure() -> None:
    url = f"{WEBHOOK}/chat/{read('bot.json')['bot']}"
    with open(path("app-secret"), encoding="utf-8") as file:
        secret = file.read().encode()
    base = f"58412{secrets.randbelow(10 ** 6):06d}"
    known, session, results = base + "0", requests.Session(), []
    count = 0

    def sequential(wa_ids) -> list[tuple[float, int]]:
        nonlocal count
        samples = []
        for wa_id in wa_ids:
            count += 1
            samples.append(send(session, url, secret, wa_id, count))
            time.sleep(0.2)
        return samples

    # Warm-up: the contact gets created and the client's data key cached.
    sequential([known] * 10)
    results.append(summary("known contact, one at a time", sequential([known] * 300)))
    results.append(summary("new contact, one at a time", sequential([f"{base}{i:03d}" for i in range(1, 101)])))

    def thread(k: int) -> list[tuple[float, int]]:
        own = requests.Session()
        return [send(own, url, secret, known, 10000 * (k + 1) + i) for i in range(50)]

    with ThreadPoolExecutor(4) as pool:
        results.append(summary("known contact, 4 at a time", [s for batch in pool.map(thread, range(4)) for s in batch]))

    date = datetime.now(timezone.utc).strftime("%Y%m%dT%H%MZ")
    with open(path(f"results-{date}.json"), "w", encoding="utf-8") as file:
        json.dump({"date": date, "image": os.environ.get("IMAGE"), "results": results}, file, indent=2)
    print("saved in", f"results-{date}.json")


if __name__ == "__main__":
    sys.path.insert(0, "/usr/src/app")
    commands = {"key": key, "prepare": prepare, "measure": measure}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        raise SystemExit(__doc__)
    commands[sys.argv[1]]()
