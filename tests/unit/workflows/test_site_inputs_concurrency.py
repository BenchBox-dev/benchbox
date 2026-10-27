from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

WORKFLOW = Path(__file__).resolve().parents[3] / ".github/workflows/site-inputs.yml"


def _concurrency() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["concurrency"]


def test_site_inputs_shares_one_group_across_trunk_completions() -> None:
    assert _concurrency()["group"] == "site-inputs"


def test_site_inputs_queues_every_run_so_an_older_commit_never_displaces_a_newer_one() -> None:
    concurrency = _concurrency()
    assert concurrency["queue"] == "max"
    assert concurrency["cancel-in-progress"] is False
