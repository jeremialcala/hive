# -*- coding: utf-8 -*-
"""
    The connection to MongoDB: every wait has an end (classes/tool_database.py). On midgard a query to Atlas waited
    for minutes on a connection that had died without closing.
"""
import pytest
from pydantic import ValidationError

import classes.tool_database as tool_database
from classes.tool_settings import Settings


def test_every_wait_of_the_connection_has_an_end(monkeypatch):
    registered = {}
    monkeypatch.setattr(tool_database.mongoengine, "connect", lambda **kwargs: registered.update(kwargs))

    tool_database.connect_database(Settings())

    assert (registered["socketTimeoutMS"], registered["connectTimeoutMS"],
            registered["serverSelectionTimeoutMS"]) == (5000, 5000, 5000)
    assert registered["maxIdleTimeMS"] == 60000


def test_the_waits_can_be_set_but_not_taken_away(monkeypatch):
    monkeypatch.setenv("DB_TIMEOUT_MS", "8000")
    assert Settings().db_timeout_ms == 8000

    for forever in ("0", "-1", "600000"):
        monkeypatch.setenv("DB_TIMEOUT_MS", forever)
        with pytest.raises(ValidationError):
            Settings()
