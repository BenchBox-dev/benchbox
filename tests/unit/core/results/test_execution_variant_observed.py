from __future__ import annotations

import pytest

from benchbox.core.execution_engine import ExecutionEngineReceipt
from benchbox.core.results.anonymization import AnonymizationManager
from benchbox.core.results.execution_variant import read_execution_engine
from benchbox.core.results.loader import reconstruct_benchmark_results
from benchbox.core.results.schema import SchemaV2Validator, build_result_payload
from tests.fixtures.result_dict_fixtures import make_benchmark_results

pytestmark = [pytest.mark.unit, pytest.mark.fast]

RECEIPTS = {
    "mixed": {"observed": "mixed", "observed_source": "explain"},
    "not_captured": {"observed_reason": "the library reported no engine for 2 of 5 queries"},
    "observed": {"observed": "streaming", "observed_source": "runtime_state"},
    "default": {},
}


@pytest.mark.parametrize("name", sorted(RECEIPTS))
def test_observed_engine_round_trips_through_the_result_schema(name):
    receipt = ExecutionEngineReceipt(
        requested="default", applied="auto", applied_class="adaptive", resolution="version_default", **RECEIPTS[name]
    )
    result = make_benchmark_results(
        platform="duckdb", platform_info={"execution_mode": "dataframe", "execution_engine": receipt}
    )
    payload = build_result_payload(result)
    SchemaV2Validator().validate(payload)
    block = payload["platform"]["execution_engine"]

    assert block["observed"] == receipt.observed
    assert block["observed_source"] == receipt.observed_source
    assert block.get("observed_reason") == receipt.observed_reason
    if name == "not_captured":
        assert block["observed_reason"]
    assert read_execution_engine(payload) == block
    anonymized = AnonymizationManager().anonymize_result_payload(payload)
    assert anonymized["platform"]["execution_engine"] == block
    assert reconstruct_benchmark_results(payload).execution_engine == block


def test_default_receipt_of_an_adapter_with_no_observation_carries_no_reason():
    receipt = ExecutionEngineReceipt(
        requested="default", applied=None, applied_class=None, resolution="platform_default"
    )
    payload = build_result_payload(
        make_benchmark_results(platform="duckdb", platform_info={"execution_mode": "sql", "execution_engine": receipt})
    )
    assert "observed_reason" not in payload["platform"]["execution_engine"]
