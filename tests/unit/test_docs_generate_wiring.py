from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parents[2]

GENERATOR_MARKERS = (
    "scripts/generate_landing_quickstarts.py",
    "scripts/doc_contracts.py --check",
    "scripts/doc_reference_inventory.py --check",
)


@pytest.mark.skipif(shutil.which("make") is None, reason="make not installed")
def test_docs_generate_runs_every_generator() -> None:
    plan = subprocess.run(["make", "-n", "docs-generate"], cwd=REPO_ROOT, capture_output=True, text=True, check=True)
    for marker in GENERATOR_MARKERS:
        assert marker in plan.stdout
