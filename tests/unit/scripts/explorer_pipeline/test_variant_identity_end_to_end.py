from __future__ import annotations

import importlib.util
import json
import shutil
from datetime import datetime
from pathlib import Path
from types import ModuleType

import duckdb
import pytest

from _project.scripts.explorer_pipeline.pipeline import ExplorerPipeline
from benchbox.core.results.builder import RunConfigInput
from benchbox.core.results.exporter import ResultExporter
from benchbox.core.results.loader import reconstruct_benchmark_results

pytestmark = [pytest.mark.unit, pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parents[4]
CORPUS = REPO_ROOT / "results-data" / "bundles"
VALIDATOR = REPO_ROOT / "results-data" / "validate_corpus.py"
POLARS_LEGACY = "tpch_sf1_polars_df_20260825_184959_8ef4d6e2.json"
DUCKDB_LEGACY = "tpch_sf1_duckdb_sql_20260826_162117_67e59fb6.json"


def _load_validator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("validate_corpus", VALIDATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _export_rerun(template: str, out: Path, run_id: str, second: int, engine: str | None = None) -> dict:
    result = reconstruct_benchmark_results(json.loads((CORPUS / template).read_text(encoding="utf-8")))
    if engine is not None:
        from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

        adapter = PolarsDataFrameAdapter(execution_engine=engine, working_dir=out.parent / f"work-{run_id}")
        recorded = {key: value for key, value in (result.platform_info or {}).items() if key != "variant"}
        result.platform_info = {**recorded, **adapter.get_platform_info()}
        result.execution_metadata = {
            **(result.execution_metadata or {}),
            "run_config": RunConfigInput(execution_engine=engine).to_dict(),
        }
    result.execution_id = run_id
    result.timestamp = datetime(2026, 10, 13, 9, 0, second)
    exported = ResultExporter(output_dir=out, anonymize=True).export_result(result)["json"]
    return json.loads(Path(exported).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def mixed_corpus(tmp_path_factory: pytest.TempPathFactory) -> Path:
    data_dir = tmp_path_factory.mktemp("mixed")
    bundles = data_dir / "bundles"
    bundles.mkdir()
    for path in CORPUS.glob("tpch_sf1_*"):
        shutil.copy2(path, bundles / path.name)
    default = _export_rerun(POLARS_LEGACY, bundles, "rerun-default", 1, engine="default")
    streaming = _export_rerun(POLARS_LEGACY, bundles, "rerun-streaming", 2, engine="streaming")
    rerun_duckdb = _export_rerun(DUCKDB_LEGACY, bundles, "rerun-duckdb", 3)
    assert [default["version"], streaming["version"], rerun_duckdb["version"]] == ["2.3"] * 3
    assert default["platform"]["variant"] == "polars-df"
    assert streaming["platform"]["variant"] == "polars-df+streaming"
    assert rerun_duckdb["platform"]["variant"] == "duckdb"
    return data_dir


def test_pipeline_joins_default_reruns_to_legacy_rows(mixed_corpus: Path, tmp_path: Path) -> None:
    ExplorerPipeline().run(data_dir=mixed_corpus, output_dir=tmp_path / "out")
    con = duckdb.connect(str(tmp_path / "out" / "results.duckdb"), read_only=True)
    rows = con.execute(
        "SELECT platform, variant_id, variant_label, count(*) FROM results "
        "WHERE platform IN ('Polars', 'DuckDB') AND scale_factor = 1 GROUP BY ALL ORDER BY ALL"
    ).fetchall()
    variants = {row[0] for row in con.execute("SELECT variant_id FROM meta_leaderboard").fetchall()}
    con.close()

    assert rows == [
        ("DuckDB", "duckdb", "DuckDB", 5),
        ("Polars", "polars", "Polars", 3),
        ("Polars", "polars+streaming", "Polars (streaming)", 1),
    ]
    assert {"polars", "polars+streaming", "duckdb"} <= variants
    assert not {"polars-df", "polars-df+streaming"} & variants


def test_depth_gate_counts_reruns_once_and_streaming_separately(mixed_corpus: Path) -> None:
    validator = _load_validator()
    cohorts = validator.cohort_platforms(validator.discover_bundles(mixed_corpus / "bundles"))
    legacy = validator.cohort_platforms(validator.discover_bundles(CORPUS))

    assert cohorts[("tpch", "1.0")] == legacy[("tpch", "1.0")] | {"polars", "polars+streaming"}
    assert {"duckdb", "polars"} <= cohorts[("tpch", "1.0")]
    assert not {"polars-df", "polars-df+streaming"} & cohorts[("tpch", "1.0")]


def _container_copy(source: str, bundles: Path, *, attested: bool = True, conflicting: bool = False) -> Path:
    payload = json.loads((CORPUS / source).read_text(encoding="utf-8"))
    payload["environment"] = {
        "os": "Linux 6.18.15",
        "arch": "aarch64",
        "cpu_count": 4,
        "memory_gb": 9.8,
        "client_host": {"os": "Linux 6.18.15", "arch": "aarch64", "cpu_count": 4, "memory_gb": 9.8},
    }
    if conflicting:
        payload["environment"]["execution_class"] = "native"
        payload["environment"]["execution_class_source"] = "observed"
    path = bundles / "container-copy.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    if attested:
        path.with_name("container-copy.manifest.json").write_text(
            json.dumps(
                {
                    "execution_class": "container",
                    "container_host_os": "macOS",
                    "execution_class_evidence": "Linux guest on Apple silicon",
                }
            ),
            encoding="utf-8",
        )
    return path


def test_container_variant_reaches_every_pipeline_table_with_its_own_label(tmp_path: Path) -> None:
    bundles = tmp_path / "data" / "bundles"
    bundles.mkdir(parents=True)
    for path in CORPUS.glob("tpch_sf1_*"):
        shutil.copy2(path, bundles / path.name)
    _container_copy(DUCKDB_LEGACY, bundles)

    ExplorerPipeline().run(data_dir=tmp_path / "data", output_dir=tmp_path / "out")
    con = duckdb.connect(str(tmp_path / "out" / "results.duckdb"), read_only=True)
    label = "DuckDB (Linux container on macOS)"
    tables = {
        table: con.execute(f"SELECT count(*) FROM {table} WHERE variant_id = 'duckdb^container'").fetchone()[0]
        for table in ("results", "platform_index_rows", "benchmark_rankings")
    }
    labels = {
        row[0]
        for row in con.execute("SELECT variant_label FROM results WHERE variant_id = 'duckdb^container'").fetchall()
    }
    plain = {row[0] for row in con.execute("SELECT variant_label FROM results WHERE variant_id = 'duckdb'").fetchall()}
    environment = con.execute(
        "SELECT e.os, e.cpu_count FROM results r JOIN result_environment e USING (result_id) "
        "WHERE r.variant_id = 'duckdb^container'"
    ).fetchall()
    con.close()

    assert all(count > 0 for count in tables.values()), tables
    assert labels == {label}
    assert plain == {"DuckDB"}
    assert environment == [("Linux 6.18.15", 4)]


def test_a_conflicting_execution_class_fails_the_pipeline_instead_of_skipping_the_bundle(tmp_path: Path) -> None:
    from benchbox.core.results.execution_variant import ExecutionClassError

    bundles = tmp_path / "data" / "bundles"
    bundles.mkdir(parents=True)
    shutil.copy2(CORPUS / DUCKDB_LEGACY, bundles / DUCKDB_LEGACY)
    _container_copy(DUCKDB_LEGACY, bundles, conflicting=True)

    with pytest.raises(ExecutionClassError, match="attests"):
        ExplorerPipeline().run(data_dir=tmp_path / "data", output_dir=tmp_path / "out")
