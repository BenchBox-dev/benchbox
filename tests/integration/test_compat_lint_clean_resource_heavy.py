from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.resource_heavy,
]

_BENCHBOX_ROOT = Path(__file__).parent.parent.parent / "benchbox"


def test_current_run_benchmark_gate_shape_is_detected():
    from benchbox.sql_compat.inventory import _validate_mandatory_sites, is_benchmark_gate_site, scan

    entries = scan(_BENCHBOX_ROOT)
    gate_sites = [entry for entry in entries if is_benchmark_gate_site(entry)]

    assert gate_sites
    assert not [err for err in _validate_mandatory_sites(entries) if "benchmark_gate" in err]


