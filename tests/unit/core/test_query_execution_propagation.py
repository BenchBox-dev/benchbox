from __future__ import annotations

import pytest

from benchbox.core.plan_capture_phase import propagate_query_execution_metadata

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_propagate_query_execution_metadata_copies_plan_and_resource_usage():
    source = {
        "_plan_capture_key": "k1",
        "query_plan": "plan_obj",
        "plan_fingerprint": "fp1",
        "plan_fingerprint_normalized": "nfp1",
        "plan_capture_time_ms": 12.5,
        "resource_usage": {"bytes_billed": 1000, "bytes_processed": 500},
    }
    target = {}
    propagate_query_execution_metadata(source, target)
    assert target["_plan_capture_key"] == "k1"
    assert target["query_plan"] == "plan_obj"
    assert target["plan_fingerprint"] == "fp1"
    assert target["plan_fingerprint_normalized"] == "nfp1"
    assert target["plan_capture_time_ms"] == 12.5
    assert target["resource_usage"] == {"bytes_billed": 1000, "bytes_processed": 500}


def test_propagate_query_execution_metadata_ignores_missing_and_none():
    source = {"_plan_capture_key": None, "other": 123}
    target = {"existing": "val"}
    propagate_query_execution_metadata(source, target)
    assert target == {"existing": "val"}


