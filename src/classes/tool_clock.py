# -*- coding: utf-8 -*-
"""
    The time of the documents, always in UTC (H10). MongoDB keeps every date as UTC, so a date without a time zone
    would be read as UTC whatever the clock of the machine that wrote it: written with datetime.now() on a machine
    four hours behind, it lands four hours early. What is written carries its time zone; what is read back comes
    without one (pymongo hands UTC dates over naive), and as_utc gives it back before it is compared or shown.
"""
from datetime import datetime, timezone


def utc_now() -> datetime:
    """Now, in UTC and saying so: the default of every date of the documents."""
    return datetime.now(timezone.utc)


def as_utc(moment: datetime | None) -> datetime | None:
    """A date read from the database, which is UTC without saying so, in UTC and saying so. None stays None."""
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)
