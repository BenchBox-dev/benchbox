from __future__ import annotations

import json
import sys
from pathlib import Path

import generate_corpus_inventory as script
import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _minimal_bundle(*, benchmark_id: str = "tpch", scale_factor: float = 0.01, platform: str = "DuckDB") -> dict:
    return {
        "version": "2.1",
        "run": {
            "id": f"{benchmark_id}-{platform.lower()}",
            "timestamp": "2026-04-12T12:00:00+00:00",
            "total_duration_ms": 1200,
        },
        "benchmark": {
            "id": benchmark_id,
            "name": benchmark_id.upper(),
            "scale_factor": scale_factor,
            "test_type": "power",
        },
        "platform": {
            "name": platform,
            "version": "1.0.0",
        },
        "summary": {
            "queries": {"total": 2, "passed": 2, "failed": 0},
        },
        "queries": [
            {"id": "Q1", "ms": 100, "status": "pass"},
            {"id": "Q2", "ms": 200, "status": "pass"},
        ],
    }


def _write_bundle(
    path: Path,
    *,
    benchmark_id: str = "tpch",
    scale_factor: float = 0.01,
    platform: str = "DuckDB",
    provenance: dict | None = None,
) -> None:
    bundle = _minimal_bundle(benchmark_id=benchmark_id, scale_factor=scale_factor, platform=platform)
    if provenance is not None:
        bundle["provenance"] = provenance
    path.write_text(json.dumps(bundle), encoding="utf-8")


class TestGenerateInventory:
    def test_empty_dir_has_consistent_shape(self, tmp_path: Path) -> None:
        inventory = script.generate_inventory(tmp_path)

        assert inventory["bundles"] == []
        assert inventory["cohorts"] == {}
        assert inventory["summary"] == {
            "total_bundles": 0,
            "by_benchmark": {},
            "by_platform": {},
            "by_trust_label": {},
            "by_funding": {},
            "by_phase": {},
        }

    def test_sidecar_sets_community_trust_label(self, tmp_path: Path) -> None:
        bundle_dir = tmp_path / "tpch" / "duckdb" / "sf0.01"
        bundle_dir.mkdir(parents=True)
        _write_bundle(bundle_dir / "result.json")
        (bundle_dir / "submission-manifest.json").write_text("{}", encoding="utf-8")

        inventory = script.generate_inventory(tmp_path)

        assert inventory["bundles"][0]["trust_label"] == "community-submission"

    def test_per_bundle_sidecar_sets_community_trust_label(self, tmp_path: Path) -> None:
        bundle_dir = tmp_path / "tpch" / "duckdb" / "sf0.01"
        bundle_dir.mkdir(parents=True)
        _write_bundle(bundle_dir / "result.json")
        (bundle_dir / "result.manifest.json").write_text("{}", encoding="utf-8")

        inventory = script.generate_inventory(tmp_path)

        assert inventory["bundles"][0]["trust_label"] == "community-submission"

    @pytest.mark.parametrize(
        ("result_source", "expected"),
        [
            ("internal", "maintainer-run"),
            ("community", "community-submission"),
            ("vendor", "vendor-supplied"),
        ],
    )
    def test_recorded_result_source_decides_the_trust_label(
        self, tmp_path: Path, result_source: str, expected: str
    ) -> None:
        bundle_dir = tmp_path / "tpch" / "duckdb" / "sf0.01"
        bundle_dir.mkdir(parents=True)
        _write_bundle(bundle_dir / "result.json")
        (bundle_dir / "result.manifest.json").write_text(json.dumps({"result_source": result_source}), encoding="utf-8")

        inventory = script.generate_inventory(tmp_path)

        assert inventory["bundles"][0]["trust_label"] == expected

    def test_unrecognized_result_source_fails_safe_to_community(self, tmp_path: Path) -> None:
        bundle_dir = tmp_path / "tpch" / "duckdb" / "sf0.01"
        bundle_dir.mkdir(parents=True)
        _write_bundle(bundle_dir / "result.json")
        (bundle_dir / "result.manifest.json").write_text(json.dumps({"result_source": "who-knows"}), encoding="utf-8")

        inventory = script.generate_inventory(tmp_path)

        assert inventory["bundles"][0]["trust_label"] == "community-submission"

    def test_unreadable_sidecar_fails_safe_to_community(self, tmp_path: Path) -> None:
        bundle_dir = tmp_path / "tpch" / "duckdb" / "sf0.01"
        bundle_dir.mkdir(parents=True)
        _write_bundle(bundle_dir / "result.json")
        (bundle_dir / "result.manifest.json").write_text("{not json", encoding="utf-8")

        inventory = script.generate_inventory(tmp_path)

        assert inventory["bundles"][0]["trust_label"] == "community-submission"

    def test_agrees_with_the_explorer_pipeline_derivation(self, tmp_path: Path) -> None:
        import importlib

        pipeline_module = importlib.import_module("_project.scripts.explorer_pipeline.pipeline")

        bundles_dir = tmp_path
        cases = [
            ("none", None),
            ("empty", {}),
            ("internal", {"result_source": "internal"}),
            ("community", {"result_source": "community"}),
            ("vendor_src", {"result_source": "vendor"}),
            ("unknown", {"result_source": "who-knows"}),
        ]
        for name, manifest in cases:
            bundle_dir = bundles_dir / name
            bundle_dir.mkdir(parents=True)
            bundle_path = bundle_dir / "result.json"
            _write_bundle(bundle_path)
            if manifest is not None:
                (bundle_dir / "result.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            from_inventory = script._bundle_trust_label(bundle_path, bundles_dir)
            from_pipeline = pipeline_module._manifest_trust_label(bundle_path, "maintainer-run")
            assert from_inventory == from_pipeline, f"derivations disagree for {name}"

    def test_top_level_vendor_subtree_sets_vendor_label(self, tmp_path: Path) -> None:
        bundle_dir = tmp_path / "vendor"
        bundle_dir.mkdir(parents=True)
        _write_bundle(bundle_dir / "result.json")

        inventory = script.generate_inventory(tmp_path)

        assert inventory["bundles"][0]["trust_label"] == "vendor-supplied"

    def test_nested_vendor_dir_does_not_grant_vendor_label(self, tmp_path: Path) -> None:
        bundle_dir = tmp_path / "community" / "vendor"
        bundle_dir.mkdir(parents=True)
        _write_bundle(bundle_dir / "result.json")

        inventory = script.generate_inventory(tmp_path)

        assert inventory["bundles"][0]["trust_label"] == "maintainer-run"

    def test_funding_from_bundle_provenance(self, tmp_path: Path) -> None:
        _write_bundle(tmp_path / "result.json", provenance={"funding": "free-trial"})

        inventory = script.generate_inventory(tmp_path)

        assert inventory["bundles"][0]["funding"] == "free-trial"

    def test_funding_defaults_to_unspecified(self, tmp_path: Path) -> None:
        _write_bundle(tmp_path / "result.json")

        inventory = script.generate_inventory(tmp_path)

        assert inventory["bundles"][0]["funding"] == "unspecified"
        assert "by_funding" in inventory["summary"]

    def test_submit_manifest_funding_override_wins_over_bundle_provenance(self, tmp_path: Path) -> None:
        _write_bundle(tmp_path / "result.json", provenance={"funding": "employer"})
        (tmp_path / "result.manifest.json").write_text(json.dumps({"funding": "personal"}), encoding="utf-8")

        inventory = script.generate_inventory(tmp_path)

        assert inventory["bundles"][0]["funding"] == "personal"

    def test_excludes_companions_and_sorts_deterministically(self, tmp_path: Path) -> None:
        bundle_dir = tmp_path / "bundles"
        bundle_dir.mkdir()
        _write_bundle(bundle_dir / "b.json", benchmark_id="tpch", platform="DuckDB")
        _write_bundle(bundle_dir / "a.json", benchmark_id="tpch", platform="DataFusion")
        (bundle_dir / "a.plans.json").write_text("{}", encoding="utf-8")
        (bundle_dir / "a.applied.json").write_text("{}", encoding="utf-8")
        (bundle_dir / "a.manifest.json").write_text("{}", encoding="utf-8")
        (bundle_dir / "submission-manifest.json").write_text("{}", encoding="utf-8")

        inventory = script.generate_inventory(bundle_dir)

        assert [entry["file"] for entry in inventory["bundles"]] == ["a.json", "b.json"]
        assert inventory["summary"]["total_bundles"] == 2

    def test_discovery_ignores_json_named_directories_and_mixed_case_companions(self, tmp_path: Path) -> None:
        (tmp_path / "directory.json").mkdir()
        _write_bundle(tmp_path / "result.JSON")
        (tmp_path / "result.APPLIED.JSON").write_text("{}", encoding="utf-8")

        inventory = script.generate_inventory(tmp_path)

        assert [entry["file"] for entry in inventory["bundles"]] == ["result.JSON"]

    def test_cohort_summary_groups_platforms(self, tmp_path: Path) -> None:
        _write_bundle(tmp_path / "duckdb.json", benchmark_id="tpch", scale_factor=0.1, platform="DuckDB")
        _write_bundle(tmp_path / "datafusion.json", benchmark_id="tpch", scale_factor=0.1, platform="DataFusion")
        _write_bundle(tmp_path / "polars.json", benchmark_id="tpch", scale_factor=0.1, platform="Polars")

        inventory = script.generate_inventory(tmp_path)

        assert inventory["cohorts"] == {
            "tpch@sf0.1": ["DataFusion v1.0.0", "DuckDB v1.0.0", "Polars v1.0.0"],
        }

    def test_duckdb_cohort_uses_resolved_package_version(self, tmp_path: Path) -> None:
        bundle = _minimal_bundle(benchmark_id="tpch", scale_factor=10.0)
        bundle["platform"].update({"version": "2.0.0-alpha38615", "client_version": "1.6.0.dev365"})
        bundle["execution"] = {
            "driver_version_actual": "2.0.0-alpha38615",
            "driver_version_resolved": "1.6.0.dev365",
        }
        (tmp_path / "duckdb-dev.json").write_text(json.dumps(bundle), encoding="utf-8")

        inventory = script.generate_inventory(tmp_path)

        assert inventory["bundles"][0]["platform_version"] == "1.6.0.dev365"
        assert inventory["cohorts"] == {"tpch@sf10.0": ["DuckDB v1.6.0.dev365"]}

    def test_internal_benchmark_bundles_are_hidden(self, tmp_path: Path) -> None:
        _write_bundle(tmp_path / "joinorder.json", benchmark_id="joinorder", scale_factor=1.0, platform="DuckDB")
        _write_bundle(
            tmp_path / "joinorder_synthetic.json",
            benchmark_id="joinorder_synthetic",
            scale_factor=1.0,
            platform="DuckDB",
        )

        inventory = script.generate_inventory(tmp_path)

        assert [entry["benchmark"] for entry in inventory["bundles"]] == ["joinorder"]
        assert inventory["summary"]["by_benchmark"] == {"joinorder": 1}

    def test_extract_metadata_raises_clear_error_for_malformed_json(self, tmp_path: Path) -> None:
        bad_bundle = tmp_path / "broken.json"
        bad_bundle.write_text("{", encoding="utf-8")

        with pytest.raises(ValueError, match="Malformed bundle JSON"):
            script.extract_metadata(bad_bundle, tmp_path)


class TestMain:
    def test_check_mode_succeeds_when_inventory_matches(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        bundles_dir = tmp_path / "bundles"
        bundles_dir.mkdir()
        _write_bundle(bundles_dir / "result.json")
        inventory_path = tmp_path / "corpus-inventory.json"

        monkeypatch.setattr(script, "BUNDLES_DIR", bundles_dir)
        monkeypatch.setattr(script, "INVENTORY_PATH", inventory_path)

        assert script.main(["--write"]) == 0
        assert script.main(["--check"]) == 0

    def test_check_mode_detects_drift(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        bundles_dir = tmp_path / "bundles"
        bundles_dir.mkdir()
        _write_bundle(bundles_dir / "result.json")
        inventory_path = tmp_path / "corpus-inventory.json"
        inventory_path.write_text(json.dumps({"bundles": []}), encoding="utf-8")

        monkeypatch.setattr(script, "BUNDLES_DIR", bundles_dir)
        monkeypatch.setattr(script, "INVENTORY_PATH", inventory_path)

        assert script.main(["--check"]) == 1
        assert "out of date" in capsys.readouterr().err

    def test_unknown_phase_merges_with_power_cohort(self, tmp_path: Path) -> None:
        power = _minimal_bundle(benchmark_id="tpch", scale_factor=1.0, platform="DuckDB")
        power["benchmark"]["test_type"] = "power"
        (tmp_path / "power.json").write_text(json.dumps(power), encoding="utf-8")
        unknown = _minimal_bundle(benchmark_id="tpch", scale_factor=1.0, platform="DataFusion")
        (tmp_path / "unknown.json").write_text(json.dumps(unknown), encoding="utf-8")

        inventory = script.generate_inventory(tmp_path)

        assert inventory["cohorts"] == {"tpch@sf1.0": ["DataFusion v1.0.0", "DuckDB v1.0.0"]}

    def test_throughput_runs_do_not_pollute_power_cohorts(self, tmp_path: Path) -> None:
        power = _minimal_bundle(benchmark_id="tpch", scale_factor=1.0, platform="DuckDB")
        power["benchmark"]["test_type"] = "power"
        (tmp_path / "power.json").write_text(json.dumps(power), encoding="utf-8")
        tp = _minimal_bundle(benchmark_id="tpch", scale_factor=1.0, platform="DataFusion")
        tp["benchmark"]["test_type"] = "throughput"
        (tmp_path / "throughput.json").write_text(json.dumps(tp), encoding="utf-8")

        inventory = script.generate_inventory(tmp_path)

        assert inventory["cohorts"]["tpch@sf1.0"] == ["DuckDB v1.0.0"]
        assert inventory["cohorts"]["tpch@sf1.0#throughput"] == ["DataFusion v1.0.0"]

    def test_phase_is_recorded_per_bundle_and_summarised(self, tmp_path: Path) -> None:
        power = _minimal_bundle(benchmark_id="tpch", scale_factor=1.0, platform="DuckDB")
        (tmp_path / "power.json").write_text(json.dumps(power), encoding="utf-8")
        standard = _minimal_bundle(benchmark_id="tpch", scale_factor=1.0, platform="Spark")
        standard["benchmark"]["test_type"] = "Standard"
        (tmp_path / "standard.json").write_text(json.dumps(standard), encoding="utf-8")
        throughput = _minimal_bundle(benchmark_id="tpch", scale_factor=1.0, platform="Doris")
        throughput["benchmark"]["test_type"] = "throughput"
        throughput["phases"] = {"throughput_test": {"status": "COMPLETED", "stream_results": [{}, {}, {}]}}
        (tmp_path / "throughput.json").write_text(json.dumps(throughput), encoding="utf-8")
        inferred = _minimal_bundle(benchmark_id="tpch", scale_factor=1.0, platform="Polars")
        del inferred["benchmark"]["test_type"]
        inferred["phases"] = {"power_test": {"status": "NOT_RUN"}, "throughput_test": {"status": "COMPLETED"}}
        (tmp_path / "inferred.json").write_text(json.dumps(inferred), encoding="utf-8")
        undeclared = _minimal_bundle(benchmark_id="tpch", scale_factor=1.0, platform="SQLite")
        undeclared["benchmark"]["test_type"] = None
        (tmp_path / "undeclared.json").write_text(json.dumps(undeclared), encoding="utf-8")

        inventory = script.generate_inventory(tmp_path)

        phases = {entry["file"]: entry["phase"] for entry in inventory["bundles"]}
        assert phases == {
            "power.json": "power",
            "standard.json": "power",
            "throughput.json": "throughput",
            "inferred.json": "throughput",
            "undeclared.json": "unknown",
        }
        assert inventory["summary"]["by_phase"] == {"power": 2, "throughput": 2, "unknown": 1}
        assert inventory["cohorts"]["tpch@sf1.0#throughput#3streams"] == ["Doris v1.0.0"]


class TestMirrorFallback:
    def test_non_public_fallback_matches_registry(self) -> None:
        from benchbox.core.benchmark_registry import get_benchmark_surface, list_benchmark_ids

        registry_non_public = {bid for bid in list_benchmark_ids() if get_benchmark_surface(bid) != "public"}

        assert set(script.NON_PUBLIC_BENCHMARKS) == registry_non_public

    def test_internal_benchmark_bundles_stay_hidden_without_the_registry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(sys.modules, "benchbox.core.benchmark_registry", None)
        _write_bundle(tmp_path / "joinorder.json", benchmark_id="joinorder", scale_factor=1.0, platform="DuckDB")
        _write_bundle(
            tmp_path / "joinorder_synthetic.json",
            benchmark_id="joinorder_synthetic",
            scale_factor=1.0,
            platform="DuckDB",
        )

        inventory = script.generate_inventory(tmp_path)

        assert [entry["benchmark"] for entry in inventory["bundles"]] == ["joinorder"]
