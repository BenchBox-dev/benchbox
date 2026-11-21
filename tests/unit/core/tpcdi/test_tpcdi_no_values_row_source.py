from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

PACKAGE_ROOT = Path(__file__).parents[4] / "benchbox"
VALUES_ROW_SOURCE = re.compile(r"FROM\s*\(?\s*VALUES\s*\(", re.IGNORECASE)


def test_no_source_in_benchbox_uses_a_values_row_source():
    offenders = [
        str(path.relative_to(PACKAGE_ROOT))
        for path in PACKAGE_ROOT.rglob("*.py")
        if "_vendor" not in path.parts and VALUES_ROW_SOURCE.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
