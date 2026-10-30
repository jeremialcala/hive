# -*- coding: utf-8 -*-
"""
    How long a person waits for the answer (RNF10, HIG-136): from the moment the webhook got the message
    (`receivedAt`) to the moment Meta took the answer (`sentAt` of the bucket `enviados`), and how long each step took
    on the way. Passive: it reads what Bifröst keeps of the real traffic, 24 hours, and changes nothing.

        python /m/measure_end_to_end.py [--since HH:MM] [--json /m/result.json]

    It runs on midgard, inside the engine's image, with Bifröst's admin credentials (docs/04-testing/
    medicion-punta-a-punta.md). It prints times and nothing else: no text, no number, no id.

    The objective, decided by Jeremi on 2026-10-30: a median of 10 s or less, and a 95th percentile of 20 s or less,
    over every message answered in the window. With fewer than MIN_SAMPLES messages it says so: the figures are kept,
    but they do not count for the gate.
"""
import argparse
import hashlib
import json
import math
import re
import sys
from datetime import datetime, timezone

STEPS = ["ingesta", "clasificado", "pedido", "respuesta", "enviado"]
NAMES = {"ingesta": "ingestion", "clasificado": "classifier", "pedido": "coordinator", "respuesta": "responder",
         "enviado": "Meta"}
OBJECTIVE = {"p50": 10.0, "p95": 20.0}  # seconds
MIN_SAMPLES = 20


def moment(value) -> datetime:
    """A date of NATS or of an envelope, always in UTC. NATS gives nanoseconds: they are cut to microseconds."""
    if isinstance(value, datetime):
        found = value
    else:
        parts = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:[.](\d+))?(Z|[+-]\d\d:?\d\d)?", str(value))
        fraction = (parts.group(2) or "0")[:6].ljust(6, "0")
        zone = parts.group(3) or "Z"
        found = datetime.fromisoformat(f"{parts.group(1)}.{fraction}" + ("+00:00" if zone == "Z" else zone))
    return found if found.tzinfo else found.replace(tzinfo=timezone.utc)


def key_of(bot_id: str, message_id: str) -> str:
    """The key the notifier keeps a sent answer under in `enviados` (engine/src/infrastructure/bifrost/enviados.py)."""
    return f"{bot_id}.{hashlib.sha256(message_id.encode('utf-8')).hexdigest()}"


def rows_of(events: list[tuple[str, str, dict, datetime]], sent: dict[str, datetime]) -> list[dict]:
    """
        One row per message, from the events of MENSAJES and MOTOR as (stream, subject, data, stored at), and the
        sentAt of each key of `enviados`. A row has `received` and the moment of each step it reached.
    """
    rows = {}
    for stream, subject, data, stored in events:
        key = key_of(data["botId"], data["messageId"])
        step = "ingesta" if stream == "MENSAJES" else subject.split(".")[1]
        rows.setdefault(key, {"received": moment(data["receivedAt"])})[step] = stored
    for key, row in rows.items():
        if key in sent:
            row["enviado"] = sent[key]
    return sorted(rows.values(), key=lambda row: row["received"])


def percentile(values: list[float], fraction: float) -> float | None:
    """The nearest-rank percentile: of 3 values, the p95 is the largest. None without values."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def summary(rows: list[dict]) -> dict:
    """The median and the 95th percentile of the whole wait and of each step, and whether the objective is met."""
    answered = [row for row in rows if "enviado" in row]
    totals = [(row["enviado"] - row["received"]).total_seconds() for row in answered]
    steps = {}
    for step in STEPS:
        took = []
        for row in answered:
            before = row["received"]
            for earlier in STEPS[:STEPS.index(step)]:
                before = row.get(earlier, before)
            if step in row:
                took.append((row[step] - before).total_seconds())
        steps[NAMES[step]] = {"p50": percentile(took, 0.5), "p95": percentile(took, 0.95)}
    p50, p95 = percentile(totals, 0.5), percentile(totals, 0.95)
    return {
        "messages": len(rows), "answered": len(answered), "unanswered": len(rows) - len(answered),
        "total": {"p50": p50, "p95": p95}, "steps": steps, "objective": OBJECTIVE,
        "met": None if not totals else p50 <= OBJECTIVE["p50"] and p95 <= OBJECTIVE["p95"],
        "enough": len(answered) >= MIN_SAMPLES,
    }


def report(rows: list[dict], result: dict) -> str:
    def seconds(value):
        return "—" if value is None else f"{value:.1f} s"

    lines = [f"{'received':8}  " + "  ".join(f"{NAMES[step]:>11}" for step in STEPS) + f"  {'total':>7}"]
    for row in rows:
        cells, before = [], row["received"]
        for step in STEPS:
            if step in row:
                cells.append(f"{(row[step] - before).total_seconds():+10.2f}s")
                before = row[step]
            else:
                cells.append(f"{'—':>11}")
        total = f"{(row['enviado'] - row['received']).total_seconds():6.1f}s" if "enviado" in row else f"{'—':>7}"
        lines.append(f"{row['received'].strftime('%H:%M:%S'):8}  " + "  ".join(cells) + f"  {total}")
    lines.append("")
    lines.append(f"{result['answered']} answered of {result['messages']}: median {seconds(result['total']['p50'])}, "
                 f"p95 {seconds(result['total']['p95'])} (objective {OBJECTIVE['p50']:.0f} s and "
                 f"{OBJECTIVE['p95']:.0f} s)")
    for name, figures in result["steps"].items():
        lines.append(f"  {name:12} median {seconds(figures['p50']):>7}   p95 {seconds(figures['p95']):>7}")
    verdict = {True: "met", False: "NOT met", None: "nothing to measure"}[result["met"]]
    lines.append(f"Objective: {verdict}" + ("" if result["enough"] or result["met"] is None else
                                           f" — but with fewer than {MIN_SAMPLES} answers, it does not count"))
    return "\n".join(lines)


async def read_bifrost() -> tuple[list, dict]:
    """What Bifröst keeps: MENSAJES, MOTOR and the bucket `enviados`, with the admin credentials of the environment."""
    import os  # pylint: disable=import-outside-toplevel
    import ssl  # pylint: disable=import-outside-toplevel

    import nats  # pylint: disable=import-outside-toplevel

    connection = await nats.connect(os.environ["NATS_URL"], user=os.environ["NATS_USER"],
                                    password=os.environ["NATS_PASSWORD"],
                                    tls=ssl.create_default_context(cafile=os.environ["NATS_CA"]))
    js = connection.jetstream()

    async def every(stream):
        info = await js.stream_info(stream)
        for sequence in range(info.state.first_seq, info.state.last_seq + 1):
            try:
                yield await js.get_msg(stream, sequence)
            except Exception:  # pylint: disable=broad-except
                # Deleted, or gone with its age while this reads: there is nothing to measure in it.
                continue

    events = []
    for stream in ("MENSAJES", "MOTOR"):
        async for message in every(stream):
            events.append((stream, message.subject, json.loads(message.data), moment(message.time)))
    sent = {}
    async for message in every("KV_enviados"):
        if message.data:
            sent[message.subject.removeprefix("$KV.enviados.")] = moment(json.loads(message.data)["sentAt"])
    await connection.close()
    return events, sent


def main(arguments: list[str]) -> int:
    import asyncio  # pylint: disable=import-outside-toplevel

    parser = argparse.ArgumentParser(description="How long a person waits for the answer (RNF10).")
    parser.add_argument("--since", default="00:00", help="only the messages received from this UTC time, HH:MM")
    parser.add_argument("--json", help="also write the summary, as JSON, to this file")
    options = parser.parse_args(arguments)
    events, sent = asyncio.run(read_bifrost())
    rows = [row for row in rows_of(events, sent) if row["received"].strftime("%H:%M") >= options.since]
    result = summary(rows)
    print(report(rows, result))
    if options.json:
        with open(options.json, "w", encoding="utf-8") as file:
            json.dump({**result, "measuredAt": datetime.now(timezone.utc).isoformat()}, file, indent=2)
    return 0 if result["met"] is not False else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
