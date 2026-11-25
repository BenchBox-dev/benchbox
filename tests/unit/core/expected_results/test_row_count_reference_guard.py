from __future__ import annotations

import copy
import json

import pytest

from benchbox.core.expected_results import row_count_references as references
from benchbox.utils import datagen_version
from benchbox.utils.query_generation import current_query_generation_hash

pytestmark = [pytest.mark.unit, pytest.mark.fast]

INTEGRITY_REFERENCES = [
    (benchmark, scale)
    for benchmark in references.INTEGRITY_BENCHMARKS
    for scale in references.reference_scales(benchmark)
]


def _payload(benchmark: str, scale: float) -> dict:
    return json.loads(references.reference_path(benchmark, scale).read_text(encoding="utf-8"))


@pytest.mark.parametrize(("benchmark", "scale"), INTEGRITY_REFERENCES)
def test_every_reference_is_current_with_the_data_stamp_and_the_query_generation(benchmark, scale) -> None:
    reference = references.load_row_count_reference(benchmark, scale)

    assert reference.query_generation_hash == current_query_generation_hash(benchmark)
    assert reference.data_stamp == datagen_version.compute_base_constants_hash(benchmark)


@pytest.mark.parametrize(("benchmark", "scale"), INTEGRITY_REFERENCES)
def test_every_integrity_section_lists_the_required_draws_engines_and_stamp(benchmark, scale) -> None:
    reference = references.load_row_count_reference(benchmark, scale)
    sample = reference.integrity.sample
    minimum = references.MIN_INTEGRITY_DRAWS if scale <= 1.0 else references.MIN_INTEGRITY_DRAWS_ABOVE_SF1

    assert reference.integrity.draws >= minimum
    assert set(sample["engines"]) == {"duckdb", "datafusion"}
    assert sample["agreement"]["result"] == "identical"
    assert sample["agreement"]["draws"] >= references.MIN_AGREEMENT_DRAWS
    assert {group["group"] for group in sample["groups"]} == references.INTEGRITY_GROUPS
    assert {group["path"] for group in sample["groups"]} >= (
        {"per_template", "streams"} if benchmark == "tpcds" else {"qgen"}
    )
    assert sample["data_stamp"] == reference.provenance["generator"]["data_stamp"]
    assert sample["query_generation"] == reference.provenance["generator"]["query_generation"]
    assert sample["structural_check"]["violations"] == 0


@pytest.mark.parametrize("scale", references.reference_scales("tpcds"))
def test_the_tpcds_exact_streams_never_share_a_generation_key(scale) -> None:
    reference = references.load_row_count_reference("tpcds", scale)
    keys = [(entry.seed, entry.streams_index) for entry in reference.streams.values()]

    assert len(keys) == len(set(keys))
    assert {1001, 1002, 1003, 1004} <= {seed for seed, index in keys if index is None}
    assert {(42, index) for index in range(9)} <= set(keys)


def test_the_stamp_guard_fails_when_the_generator_stamp_changes_without_regenerated_references(monkeypatch) -> None:
    monkeypatch.setattr(datagen_version, "DATA_GENERATION_VERSION", datagen_version.DATA_GENERATION_VERSION + 1)

    with pytest.raises(ValueError, match="data stamp"):
        references.load_row_count_reference("tpcds", 1.0)


def test_the_identity_guard_fails_when_a_template_override_changes_without_regenerated_references(monkeypatch) -> None:
    from benchbox.core.tpcds import c_tools

    monkeypatch.setattr(c_tools, "SUBSCALE_TEMPLATE_OVERRIDES", {})

    with pytest.raises(ValueError, match="query generation"):
        references.load_row_count_reference("tpcds", 1.0)


def test_the_runtime_path_reads_a_stale_reference_instead_of_refusing_it(monkeypatch) -> None:
    from benchbox.core.tpcds import c_tools

    monkeypatch.setattr(c_tools, "SUBSCALE_TEMPLATE_OVERRIDES", {})
    reference = references.load_row_count_reference("tpcds", 1.0, require_current=False)

    assert reference.inapplicable_reason(None, current_query_generation_hash("tpcds")).startswith(
        "reference data differs"
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.pop("integrity"),
        lambda p: p["integrity"]["sample"].update(draws=511),
        lambda p: p["integrity"]["sample"]["groups"].pop(),
        lambda p: p["integrity"]["sample"].pop("agreement"),
        lambda p: p["integrity"]["sample"]["agreement"].update(draws=63),
        lambda p: p["integrity"]["sample"]["agreement"].update(result="disagree"),
        lambda p: p["integrity"]["sample"].update(data_checksum="0" * 64),
        lambda p: p["integrity"]["sample"].update(data_stamp={}),
        lambda p: p["integrity"]["sample"].update(query_generation={}),
        lambda p: p["integrity"]["sample"]["engines"].pop("datafusion"),
        lambda p: p["integrity"]["never_empty"].append("not-a-query"),
        lambda p: p["integrity"]["invariant_counts"].update({"not-a-query": 1}),
        lambda p: p["provenance"]["generator"].pop("query_generation"),
        lambda p: p["provenance"].pop("generator"),
    ],
)
def test_a_reference_without_the_required_integrity_provenance_is_refused(mutate) -> None:
    payload = copy.deepcopy(_payload("tpcds", 1.0))
    mutate(payload)

    with pytest.raises((ValueError, KeyError)):
        references.validate_payload(payload, "tpcds_row_counts_sf1.json")
