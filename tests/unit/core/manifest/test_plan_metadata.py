from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from benchbox.core.manifest import (
    ManifestV2,
    PlanMetadata,
    create_plan_metadata_from_results,
    load_manifest,
    merge_plan_metadata,
    update_plan_versions,
    validate_plan_metadata,
    write_manifest,
)
from benchbox.core.results.query_plan_models import (
    LogicalOperator,
    LogicalOperatorType,
    QueryPlanDAG,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _mock_query_result(query_id: str, execution_time_ms: float = 100.0, query_plan: QueryPlanDAG | None = None) -> dict:
    return {"query_id": query_id, "execution_time_ms": execution_time_ms, "query_plan": query_plan}


@dataclass
class MockBenchmarkResults:
    run_id: str = "test_run"
    platform: str = "duckdb"
    platform_version: str = "0.9.0"
    query_results: list[dict] = field(default_factory=list)


def _create_plan_with_fingerprint(query_id: str, fingerprint: str) -> QueryPlanDAG:
    root = LogicalOperator(
        operator_id="1",
        operator_type=LogicalOperatorType.SCAN,
        table_name="test",
        children=[],
    )
    return QueryPlanDAG(
        query_id=query_id,
        platform="test",
        logical_root=root,
        plan_fingerprint=fingerprint,
        raw_explain_output="test",
    )


class TestPlanMetadataDataclass:
    def test_default_values(self) -> None:

        metadata = PlanMetadata()

        assert metadata.plan_fingerprints == {}
        assert metadata.plan_versions == {}
        assert metadata.plan_capture_timestamp == {}
        assert metadata.platform is None
        assert metadata.platform_version is None

    def test_to_dict_empty(self) -> None:

        metadata = PlanMetadata()
        result = metadata.to_dict()
        assert result == {}

    def test_to_dict_with_values(self) -> None:

        metadata = PlanMetadata(
            plan_fingerprints={"q1": "a" * 64, "q2": "b" * 64},
            plan_versions={"q1": 1, "q2": 2},
            plan_capture_timestamp={"q1": "2024-01-01T00:00:00Z"},
            platform="duckdb",
            platform_version="0.9.0",
        )

        result = metadata.to_dict()

        assert result["plan_fingerprints"] == {"q1": "a" * 64, "q2": "b" * 64}
        assert result["plan_versions"] == {"q1": 1, "q2": 2}
        assert result["plan_capture_timestamp"] == {"q1": "2024-01-01T00:00:00Z"}
        assert result["platform"] == "duckdb"
        assert result["platform_version"] == "0.9.0"

    def test_from_dict(self) -> None:

        data = {
            "plan_fingerprints": {"q1": "a" * 64},
            "plan_versions": {"q1": 1},
            "plan_capture_timestamp": {"q1": "2024-01-01T00:00:00Z"},
            "platform": "duckdb",
            "platform_version": "0.9.0",
        }

        metadata = PlanMetadata.from_dict(data)

        assert metadata.plan_fingerprints == {"q1": "a" * 64}
        assert metadata.plan_versions == {"q1": 1}
        assert metadata.platform == "duckdb"

    def test_from_dict_empty(self) -> None:

        metadata = PlanMetadata.from_dict({})

        assert metadata.plan_fingerprints == {}
        assert metadata.plan_versions == {}


class TestManifestV2PlanMetadata:
    def test_manifest_with_plan_metadata(self) -> None:
        metadata = PlanMetadata(
            plan_fingerprints={"q1": "a" * 64},
            plan_versions={"q1": 1},
            platform="duckdb",
        )

        manifest = ManifestV2(
            benchmark="tpch",
            scale_factor=1.0,
            plan_metadata=metadata,
        )

        assert manifest.plan_metadata is not None
        assert manifest.plan_metadata.plan_fingerprints == {"q1": "a" * 64}

    def test_manifest_without_plan_metadata(self) -> None:
        manifest = ManifestV2(
            benchmark="tpch",
            scale_factor=1.0,
        )

        assert manifest.plan_metadata is None

    def test_write_and_load_manifest_with_plan_metadata(self) -> None:

        metadata = PlanMetadata(
            plan_fingerprints={"q1": "a" * 64, "q2": "b" * 64},
            plan_versions={"q1": 1, "q2": 2},
            plan_capture_timestamp={"q1": "2024-01-01T00:00:00Z", "q2": "2024-01-02T00:00:00Z"},
            platform="duckdb",
            platform_version="0.9.0",
        )

        manifest = ManifestV2(
            benchmark="tpch",
            scale_factor=1.0,
            plan_metadata=metadata,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "manifest.json"
            write_manifest(manifest, path)

            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            assert "plan_metadata" in data
            assert data["plan_metadata"]["plan_fingerprints"]["q1"] == "a" * 64
            assert data["plan_metadata"]["platform"] == "duckdb"

            loaded = load_manifest(path)

            assert isinstance(loaded, ManifestV2)
            assert loaded.plan_metadata is not None
            assert loaded.plan_metadata.plan_fingerprints == {"q1": "a" * 64, "q2": "b" * 64}
            assert loaded.plan_metadata.plan_versions == {"q1": 1, "q2": 2}
            assert loaded.plan_metadata.platform == "duckdb"

    def test_load_manifest_without_plan_metadata(self) -> None:

        manifest_data = {
            "version": 2,
            "benchmark": "tpch",
            "scale_factor": 1.0,
            "tables": {},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "manifest.json"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(manifest_data, f)

            loaded = load_manifest(path)

            assert isinstance(loaded, ManifestV2)
            assert loaded.plan_metadata is None


class TestCreatePlanMetadataFromResults:
    def test_creates_metadata_from_results(self) -> None:

        plan1 = _create_plan_with_fingerprint("q1", "a" * 64)
        plan2 = _create_plan_with_fingerprint("q2", "b" * 64)

        results = MockBenchmarkResults(
            platform="duckdb",
            platform_version="0.9.0",
            query_results=[
                _mock_query_result("q1", 100.0, plan1),
                _mock_query_result("q2", 200.0, plan2),
            ],
        )

        metadata = create_plan_metadata_from_results(results)

        assert metadata.platform == "duckdb"
        assert metadata.platform_version == "0.9.0"
        assert metadata.plan_fingerprints == {"q1": "a" * 64, "q2": "b" * 64}
        assert metadata.plan_versions == {"q1": 1, "q2": 1}
        assert "q1" in metadata.plan_capture_timestamp
        assert "q2" in metadata.plan_capture_timestamp
        assert metadata.plan_fingerprint_versions == {
            "q1": plan1.fingerprint_version,
            "q2": plan2.fingerprint_version,
        }

    def test_skips_executions_without_plans(self) -> None:

        plan1 = _create_plan_with_fingerprint("q1", "a" * 64)

        results = MockBenchmarkResults(
            query_results=[
                _mock_query_result("q1", 100.0, plan1),
                _mock_query_result("q2", 200.0, None),
            ],
        )

        metadata = create_plan_metadata_from_results(results)

        assert "q1" in metadata.plan_fingerprints
        assert "q2" not in metadata.plan_fingerprints

    def test_handles_empty_results(self) -> None:

        results = MockBenchmarkResults(query_results=[])

        metadata = create_plan_metadata_from_results(results)

        assert metadata.plan_fingerprints == {}
        assert metadata.plan_versions == {}


class TestUpdatePlanVersions:
    def test_first_run_sets_version_one(self) -> None:
        current = PlanMetadata(
            plan_fingerprints={"q1": "a" * 64, "q2": "b" * 64},
            plan_versions={},
        )

        update_plan_versions(None, current)

        assert current.plan_versions == {"q1": 1, "q2": 1}

    def test_unchanged_plans_keep_version(self) -> None:

        prev = PlanMetadata(
            plan_fingerprints={"q1": "a" * 64},
            plan_versions={"q1": 3},
        )
        current = PlanMetadata(
            plan_fingerprints={"q1": "a" * 64},
            plan_versions={},
        )

        update_plan_versions(prev, current)

        assert current.plan_versions["q1"] == 3

    def test_changed_plans_increment_version(self) -> None:

        prev = PlanMetadata(
            plan_fingerprints={"q1": "a" * 64},
            plan_versions={"q1": 3},
        )
        current = PlanMetadata(
            plan_fingerprints={"q1": "b" * 64},
            plan_versions={},
        )

        update_plan_versions(prev, current)

        assert current.plan_versions["q1"] == 4

    def test_new_queries_start_at_version_one(self) -> None:
        prev = PlanMetadata(
            plan_fingerprints={"q1": "a" * 64},
            plan_versions={"q1": 3},
        )
        current = PlanMetadata(
            plan_fingerprints={"q1": "a" * 64, "q2": "b" * 64},
            plan_versions={},
        )

        update_plan_versions(prev, current)

        assert current.plan_versions["q1"] == 3
        assert current.plan_versions["q2"] == 1

    def test_new_query_across_fingerprint_version_boundary_starts_at_one(self) -> None:
        prev = PlanMetadata(
            plan_fingerprints={"q1": "a" * 64},
            plan_versions={"q1": 3},
            plan_fingerprint_versions={"q1": 1},
        )
        current = PlanMetadata(
            plan_fingerprints={"q1": "a" * 64, "q2": "b" * 64},
            plan_versions={},
            plan_fingerprint_versions={"q1": 1, "q2": 2},
        )

        update_plan_versions(prev, current)

        assert current.plan_versions["q1"] == 3
        assert current.plan_versions["q2"] == 1
        assert not validate_plan_metadata(current)

    def test_fingerprint_version_boundary_does_not_bump_version(self) -> None:
        prev = PlanMetadata(
            plan_fingerprints={"q1": "a" * 64},
            plan_versions={"q1": 3},
            plan_fingerprint_versions={"q1": 1},
        )
        current = PlanMetadata(
            plan_fingerprints={"q1": "b" * 64},
            plan_versions={},
            plan_fingerprint_versions={"q1": 2},
        )

        update_plan_versions(prev, current)

        assert current.plan_versions["q1"] == 3

    def test_same_fingerprint_version_still_increments_on_real_change(self) -> None:
        prev = PlanMetadata(
            plan_fingerprints={"q1": "a" * 64},
            plan_versions={"q1": 3},
            plan_fingerprint_versions={"q1": 2},
        )
        current = PlanMetadata(
            plan_fingerprints={"q1": "b" * 64},
            plan_versions={},
            plan_fingerprint_versions={"q1": 2},
        )

        update_plan_versions(prev, current)

        assert current.plan_versions["q1"] == 4


class TestValidatePlanMetadata:
    def test_valid_metadata(self) -> None:

        metadata = PlanMetadata(
            plan_fingerprints={"q1": "a" * 64, "q2": "b" * 64},
            plan_versions={"q1": 1, "q2": 2},
        )

        errors = validate_plan_metadata(metadata)

        assert errors == []

    def test_invalid_fingerprint_length(self) -> None:

        metadata = PlanMetadata(
            plan_fingerprints={"q1": "toolshort"},
            plan_versions={"q1": 1},
        )

        errors = validate_plan_metadata(metadata)

        assert len(errors) == 1
        assert "Invalid fingerprint" in errors[0]

    def test_invalid_fingerprint_characters(self) -> None:

        metadata = PlanMetadata(
            plan_fingerprints={"q1": "g" * 64},
            plan_versions={"q1": 1},
        )

        errors = validate_plan_metadata(metadata)

        assert len(errors) == 1
        assert "Invalid fingerprint" in errors[0]

    def test_invalid_version(self) -> None:

        metadata = PlanMetadata(
            plan_fingerprints={"q1": "a" * 64},
            plan_versions={"q1": 0},
        )

        errors = validate_plan_metadata(metadata)

        assert len(errors) == 1
        assert "Invalid version" in errors[0]

    def test_mismatched_keys(self) -> None:

        metadata = PlanMetadata(
            plan_fingerprints={"q1": "a" * 64, "q2": "b" * 64},
            plan_versions={"q1": 1},
        )

        errors = validate_plan_metadata(metadata)

        assert len(errors) == 1
        assert "Missing versions" in errors[0]


class TestMergePlanMetadata:
    def test_merge_basic(self) -> None:

        base = PlanMetadata(
            plan_fingerprints={"q1": "a" * 64},
            plan_versions={"q1": 1},
            platform="duckdb",
        )
        overlay = PlanMetadata(
            plan_fingerprints={"q2": "b" * 64},
            plan_versions={"q2": 1},
        )

        merged = merge_plan_metadata(base, overlay)

        assert merged.plan_fingerprints == {"q1": "a" * 64, "q2": "b" * 64}
        assert merged.plan_versions == {"q1": 1, "q2": 1}
        assert merged.platform == "duckdb"

    def test_overlay_takes_precedence(self) -> None:

        base = PlanMetadata(
            plan_fingerprints={"q1": "a" * 64},
            plan_versions={"q1": 1},
            platform="duckdb",
        )
        overlay = PlanMetadata(
            plan_fingerprints={"q1": "b" * 64},
            plan_versions={"q1": 2},
            platform="datafusion",
        )

        merged = merge_plan_metadata(base, overlay)

        assert merged.plan_fingerprints["q1"] == "b" * 64
        assert merged.plan_versions["q1"] == 2
        assert merged.platform == "datafusion"


