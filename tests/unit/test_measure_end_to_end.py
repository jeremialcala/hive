# -*- coding: utf-8 -*-
"""
    The measurement of the end-to-end wait (tests/performance/measure_end_to_end.py, RNF10, HIG-136): the steps of each
    message from what Bifröst keeps, the percentiles, the verdict against the objective, and a report with times only.
"""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "performance" / "measure_end_to_end.py"
spec = importlib.util.spec_from_file_location("measure_end_to_end", SCRIPT)
measure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(measure)

START = datetime(2026, 10, 30, 9, 20, 16, tzinfo=timezone.utc)
BOT = "a2feaa48-7c67-4b85-a029-340df729da7f"


def message(number: int, steps: list[float], answered: bool = True, received: datetime = START):
    """The events of one message, each step `steps[i]` seconds after the one before, and its sentAt if answered."""
    data = {"botId": BOT, "messageId": f"wamid.SECRET{number}", "receivedAt": received.isoformat(),
            "text": "hola, soy Ana y mi número es 12025550143"}
    events, at = [], received
    for step, took in zip(["ingesta", "clasificado", "pedido", "respuesta"], steps):
        at += timedelta(seconds=took)
        stream = "MENSAJES" if step == "ingesta" else "MOTOR"
        events.append((stream, f"motor.{step}.{BOT}" if stream == "MOTOR" else f"gateway.message.3.{BOT}.c", data, at))
    sent = {measure.key_of(BOT, data["messageId"]): at + timedelta(seconds=steps[4])} if answered else {}
    return events, sent


def measured(*messages):
    events, sent = [], {}
    for each_events, each_sent in messages:
        events += each_events
        sent.update(each_sent)
    return measure.rows_of(events, sent)


def test_each_step_is_measured_from_the_one_before():
    rows = measured(message(1, [1.77, 1.52, 2.01, 3.06, 1.12]))

    result = measure.summary(rows)
    assert result["total"]["p50"] == pytest.approx(9.48)
    assert result["steps"]["coordinator"]["p50"] == pytest.approx(2.01)
    assert result["steps"]["Meta"]["p50"] == pytest.approx(1.12)


@pytest.mark.parametrize("values,p50,p95", [([7.7, 10.0, 9.5], 9.5, 10.0), ([1.0], 1.0, 1.0),
                                            (list(range(1, 101)), 50, 95)])
def test_the_percentiles_are_nearest_rank(values, p50, p95):
    assert (measure.percentile(values, 0.5), measure.percentile(values, 0.95)) == (p50, p95)


def test_the_objective_is_met_or_not_by_the_median_and_the_p95():
    quick = [message(n, [1, 1, 1, 1, 1], received=START + timedelta(minutes=n)) for n in range(25)]
    assert measure.summary(measured(*quick))["met"] is True and measure.summary(measured(*quick))["enough"] is True

    slow_tail = quick[:23] + [message(90 + n, [1, 1, 20, 1, 1], received=START + timedelta(hours=1, minutes=n))
                              for n in range(2)]
    result = measure.summary(measured(*slow_tail))
    assert (result["total"]["p50"], result["met"]) == (5.0, False)


def test_with_few_answers_the_figures_are_kept_but_do_not_count():
    result = measure.summary(measured(message(1, [1, 1, 1, 1, 1]), message(2, [1, 1, 1, 1, 1], answered=False)))

    assert (result["answered"], result["unanswered"], result["met"], result["enough"]) == (1, 1, True, False)
    assert "does not count" in measure.report(measured(message(1, [1, 1, 1, 1, 1])), result)


def test_nothing_to_measure_is_neither_met_nor_failed():
    assert measure.summary([])["met"] is None


def test_the_report_has_times_and_nothing_of_the_message():
    rows = measured(message(1, [1.77, 1.52, 2.01, 3.06, 1.12]))

    text = measure.report(rows, measure.summary(rows))

    for private in ("wamid", "SECRET", "Ana", "12025550143", BOT, "hola"):
        assert private not in text
    assert "09:20:16" in text and "9.5 s" in text
