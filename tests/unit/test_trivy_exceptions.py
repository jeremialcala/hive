# -*- coding: utf-8 -*-
"""
    The exceptions of the scans of Trivy (.trivyignore.yaml): of the images (HIG-157) and of the licenses (HIG-158).
    Each one says why, and until when: an exception without a date would let it through for ever; one far in the
    future, almost the same. And the policy of the licenses (.trivy-licencias.yaml) is the one Jeremi decided.
"""
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
IGNORE = yaml.safe_load((ROOT / ".trivyignore.yaml").read_text(encoding="utf-8"))
EXCEPTIONS = [("vulnerabilities", each) for each in IGNORE.get("vulnerabilities") or []] + \
             [("licenses", each) for each in IGNORE.get("licenses") or []]
POLICY = yaml.safe_load((ROOT / ".trivy-licencias.yaml").read_text(encoding="utf-8"))["license"]
# Looked at again within six months at the latest: what has no fix today may have one by then.
AT_MOST = timedelta(days=183)


def test_the_files_are_the_ones_the_workflow_reads():
    workflow = (ROOT / ".github" / "workflows" / "seguridad.yml").read_text(encoding="utf-8")

    # The report, the SARIF and the threshold of the images, and the threshold of the licenses.
    assert workflow.count("--ignorefile .trivyignore.yaml") == 4
    assert workflow.count("--config .trivy-licencias.yaml") == 1


@pytest.mark.parametrize("kind, exception", EXCEPTIONS, ids=[f"{kind}:{each.get('id')}" for kind, each in EXCEPTIONS])
def test_every_exception_says_why_and_until_when(kind, exception):
    if kind == "vulnerabilities":
        assert str(exception.get("id", "")).startswith(("CVE-", "GHSA-")), exception
    else:
        # A license is excepted for the packages it names, never for every package that has it.
        assert exception.get("paths"), exception
    assert len(str(exception.get("statement", "")).strip()) >= 20, exception
    until = exception.get("expired_at")
    assert isinstance(until, date), exception
    assert until <= date.today() + AT_MOST, exception


def test_copyleft_that_reaches_the_code_is_forbidden_and_lgpl_and_mpl_are_not():
    """Decision of Jeremi of 2026-11-05: GPL, AGPL and SSPL break the build; LGPL and MPL-2.0 pass."""
    forbidden = set(POLICY["forbidden"])

    assert {"GPL-2.0-only", "GPL-2.0-or-later", "GPL-3.0-only", "GPL-3.0-or-later", "AGPL-3.0-only",
            "AGPL-3.0-or-later", "SSPL-1.0"} <= forbidden
    assert not [name for name in forbidden if name.startswith(("LGPL", "MPL"))]
    assert not set(POLICY.get("permissive", [])) & forbidden
