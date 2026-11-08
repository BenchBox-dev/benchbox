from __future__ import annotations

import importlib
import json
from pathlib import Path

import duckdb
import pytest
import yaml

from _project.scripts.explorer_pipeline.pipeline import ExplorerPipeline
from tests.unit.scripts.explorer_pipeline.conftest import MINIMAL_BUNDLE

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REGISTRY_PATH = Path(__file__).resolve().parents[4] / "_project/planning/visible_metrics.yaml"


@pytest.fixture(scope="module")
def registry() -> dict:
    with REGISTRY_PATH.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.fixture(scope="module")
def db_columns(tmp_path_factory: pytest.TempPathFactory) -> dict[str, set[str]]:
    base = tmp_path_factory.mktemp("registry_db")
    data_dir = base / "data"
    bundles_dir = data_dir / "bundles"
    bundles_dir.mkdir(parents=True)
    (bundles_dir / "bundle.json").write_text(json.dumps(MINIMAL_BUNDLE), encoding="utf-8")

    output_dir = base / "out"
    ExplorerPipeline().run(data_dir, output_dir, bundle_url_prefix="/results/data/bundles")

    db_path = output_dir / "results.duckdb"
    cols: dict[str, set[str]] = {}
    with duckdb.connect(str(db_path), read_only=True) as con:
        names = [r[0] for r in con.execute("PRAGMA show_tables").fetchall()]
        for name in names:
            cols[name] = {r[0] for r in con.execute(f"DESCRIBE {name}").fetchall()}
    return cols


class TestRegistryStructure:
    def test_registry_loads(self, registry: dict) -> None:
        assert registry["schema_version"] == 1
        assert isinstance(registry["metrics"], list)
        assert len(registry["metrics"]) >= 30

    def test_every_metric_has_required_fields(self, registry: dict) -> None:
        for metric in registry["metrics"]:
            assert "table" in metric
            assert "column" in metric
            assert metric.get("kind") in {"raw_copy", "derived"}
            if metric["kind"] == "raw_copy":
                assert metric.get("source_field"), f"{metric['table']}.{metric['column']} missing source_field"
            else:
                ref = metric.get("canonical_ref", "")
                assert ref, f"{metric['table']}.{metric['column']} missing canonical_ref"
                assert metric.get("tolerance") is not None, f"{metric['table']}.{metric['column']} missing tolerance"


class TestRegistryMatchesSchema:
    def test_every_metric_target_column_exists(self, registry: dict, db_columns: dict[str, set[str]]) -> None:
        missing: list[str] = []
        for metric in registry["metrics"]:
            table = metric["table"]
            column = metric["column"]
            if table not in db_columns:
                missing.append(f"{table}.{column}: table/view absent from DuckDB")
                continue
            if column not in db_columns[table]:
                missing.append(f"{table}.{column}: column absent from DuckDB")
        assert not missing, "registry columns missing from DB:\n  " + "\n  ".join(missing)


class TestDerivedCanonicalRefsImport:
    def _unique_refs(self, registry: dict) -> list[str]:
        refs: set[str] = set()
        for metric in registry["metrics"]:
            if metric["kind"] != "derived":
                continue
            raw = metric["canonical_ref"].split("(", 1)[0].strip()
            refs.add(raw)
        return sorted(refs)

    def test_all_derived_refs_importable(self, registry: dict) -> None:
        failures: list[str] = []
        for ref in self._unique_refs(registry):
            parts = ref.split(".")
            module = None
            attrs: list[str] = []
            for i in range(len(parts), 0, -1):
                candidate = ".".join(parts[:i])
                try:
                    module = importlib.import_module(candidate)
                    attrs = parts[i:]
                    break
                except ImportError:
                    continue
            if module is None:
                failures.append(f"{ref}: no importable module prefix")
                continue
            target: object = module
            for part in attrs:
                if not hasattr(target, part):
                    failures.append(f"{ref}: attribute {part!r} not found")
                    break
                target = getattr(target, part)
        assert not failures, "unresolved canonical_refs:\n  " + "\n  ".join(failures)


class TestRegistryCoverage:
    REQUIRED_TABLES = {
        "results",
        "query_display_timings",
        "query_executions",
        "benchmark_matrix_cells",
        "benchmark_rankings",
        "cohort_metadata",
        "meta_leaderboard",
        "short_ids",
    }

    def test_registry_covers_every_metric_bearing_table(self, registry: dict) -> None:
        by_table = {metric["table"] for metric in registry["metrics"]}
        missing = self.REQUIRED_TABLES - by_table
        assert not missing, f"registry missing required tables: {sorted(missing)}"

    def test_every_registry_table_exists_in_schema(self, registry: dict, db_columns: dict[str, set[str]]) -> None:
        referenced = {m["table"] for m in registry["metrics"]}
        unknown = [t for t in referenced if t not in db_columns]
        assert not unknown, f"registry references tables absent from DB: {sorted(unknown)}"


class TestHasPlansContract:
    def test_has_plans_source_field_requires_captured_plan(self, registry: dict) -> None:
        entry = next(m for m in registry["metrics"] if m["table"] == "results" and m["column"] == "has_plans")
        source_field = entry["source_field"]
        assert "sidecar exists" not in source_field
        assert "captured" in source_field
        assert "errors-only" in source_field
