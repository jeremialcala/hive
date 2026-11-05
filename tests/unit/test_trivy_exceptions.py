# -*- coding: utf-8 -*-
"""
    The exceptions of the scan of the images (.trivyignore.yaml, HIG-157): each one says why, and until when. An
    exception without a date would let a vulnerability through for ever; one far in the future, almost the same.
"""
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

IGNORE = Path(__file__).resolve().parents[2] / ".trivyignore.yaml"
EXCEPTIONS = yaml.safe_load(IGNORE.read_text(encoding="utf-8"))["vulnerabilities"] or []
# Looked at again within six months at the latest: what has no fix today may have one by then.
AT_MOST = timedelta(days=183)


def test_the_file_is_the_one_the_workflow_reads():
    workflow = (IGNORE.parent / ".github" / "workflows" / "seguridad.yml").read_text(encoding="utf-8")

    assert workflow.count("--ignorefile .trivyignore.yaml") == 3


@pytest.mark.parametrize("exception", EXCEPTIONS, ids=[str(each.get("id")) for each in EXCEPTIONS])
def test_every_exception_says_why_and_until_when(exception):
    assert str(exception.get("id", "")).startswith(("CVE-", "GHSA-")), exception
    assert len(str(exception.get("statement", "")).strip()) >= 20, exception
    until = exception.get("expired_at")
    assert isinstance(until, date), exception
    assert until <= date.today() + AT_MOST, exception
