from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from _project.scripts.explorer_pipeline.transformer import BundleTransformer
from tests.unit.scripts.explorer_pipeline.conftest import MINIMAL_BUNDLE

pytestmark = [pytest.mark.unit, pytest.mark.fast]

DAMAGED_IDS = [
    "ualify_row_number",
    "ualify_dense_rank",
    "ualify_percentile",
    "ualify_ntile",
    "ualify_cume_dist",
    "ualify_lag_lead",
]


def _write_bundle(tmp_path: Path, benchmark_id: str, query_ids: list[str]) -> Path:
    data = copy.deepcopy(MINIMAL_BUNDLE)
    data["benchmark"]["id"] = benchmark_id
    data["queries"] = [
        {"id": query_id, "ms": 100.0 + index, "status": "SUCCESS", "run_type": "measurement", "iter": 1, "stream": 0}
        for index, query_id in enumerate(query_ids)
    ]
    bundle = tmp_path / f"{benchmark_id}.json"
    bundle.write_text(json.dumps(data), encoding="utf-8")
    return bundle


class TestDamagedReadPrimitiveQueryIds:
    def test_damaged_qualify_ids_are_repaired_in_read_model_rows(self, tmp_path: Path) -> None:
        bundle = _write_bundle(tmp_path, "read_primitives", ["ualify_cume_dist", "ualify_row_number"])

        transformer = BundleTransformer()
        rid = transformer.result_id_from_bundle(bundle)
        detail = transformer.to_detail_result(bundle, rid)

        assert [row.query_id for row in detail.queries] == ["qualify_cume_dist", "qualify_row_number"]

    @pytest.mark.parametrize("query_id", DAMAGED_IDS)
    def test_every_damaged_read_primitive_id_maps_to_its_q_prefixed_form(self, tmp_path: Path, query_id: str) -> None:
        bundle = _write_bundle(tmp_path, "read_primitives", [query_id])

        transformer = BundleTransformer()
        rid = transformer.result_id_from_bundle(bundle)
        detail = transformer.to_detail_result(bundle, rid)

        assert detail.queries[0].query_id == "q" + query_id

    def test_lowercase_q_read_primitive_ids_are_left_unchanged(self, tmp_path: Path) -> None:
        bundle = _write_bundle(tmp_path, "read_primitives", ["qualify_cume_dist"])

        transformer = BundleTransformer()
        rid = transformer.result_id_from_bundle(bundle)
        detail = transformer.to_detail_result(bundle, rid)

        assert detail.queries[0].query_id == "qualify_cume_dist"

    def test_repair_is_limited_to_read_primitives(self, tmp_path: Path) -> None:
        bundle = _write_bundle(tmp_path, "tpch", ["ualify_cume_dist"])

        transformer = BundleTransformer()
        rid = transformer.result_id_from_bundle(bundle)
        detail = transformer.to_detail_result(bundle, rid)

        assert detail.queries[0].query_id == "ualify_cume_dist"


ALL_REPAIRS = (
    "ualify_cume_dist→qualify_cume_dist; "
    "ualify_dense_rank→qualify_dense_rank; "
    "ualify_lag_lead→qualify_lag_lead; "
    "ualify_ntile→qualify_ntile; "
    "ualify_percentile→qualify_percentile; "
    "ualify_row_number→qualify_row_number"
)


class TestQueryIdRepairsAreRecorded:
    def test_damaged_bundle_records_every_mapping_on_entry_and_detail(self, tmp_path: Path) -> None:
        bundle = _write_bundle(tmp_path, "read_primitives", [*DAMAGED_IDS, "ualify_ntile", "scan_full"])

        transformer = BundleTransformer()
        rid = transformer.result_id_from_bundle(bundle)
        entry = transformer.to_manifest_entry(bundle, result_id=rid)
        detail = transformer.to_detail_result(bundle, rid)

        assert entry.query_id_repairs == ALL_REPAIRS
        assert detail.query_id_repairs == ALL_REPAIRS

    def test_partially_damaged_bundle_records_only_the_ids_it_repaired(self, tmp_path: Path) -> None:
        bundle = _write_bundle(tmp_path, "read_primitives", ["ualify_ntile", "qualify_cume_dist"])

        transformer = BundleTransformer()
        rid = transformer.result_id_from_bundle(bundle)

        assert transformer.to_detail_result(bundle, rid).query_id_repairs == "ualify_ntile→qualify_ntile"

    @pytest.mark.parametrize(
        ("benchmark_id", "query_ids"),
        [
            ("read_primitives", ["qualify_cume_dist", "scan_full"]),
            ("tpch", ["ualify_cume_dist"]),
        ],
    )
    def test_undamaged_bundle_records_no_repairs(self, tmp_path: Path, benchmark_id: str, query_ids: list[str]) -> None:
        bundle = _write_bundle(tmp_path, benchmark_id, query_ids)

        transformer = BundleTransformer()
        rid = transformer.result_id_from_bundle(bundle)

        assert transformer.to_manifest_entry(bundle, result_id=rid).query_id_repairs is None
        assert transformer.to_detail_result(bundle, rid).query_id_repairs is None
