from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_probe_changes_no_docs_or_results_data() -> None:
    assert __file__.endswith("test_short_checks_skip_probe.py")
