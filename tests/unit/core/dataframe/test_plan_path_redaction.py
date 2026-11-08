from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from benchbox.core.dataframe.profiling import QueryPlan, capture_query_plan, redact_plan_local_paths
from benchbox.core.query_plans.parsers.spark import SparkQueryPlanParser
from benchbox.core.results.anonymization import find_public_path_leaks
from benchbox.core.results.exporter import ResultExporter
from benchbox.core.results.query_plan_models import LogicalOperatorType
from benchbox.core.results.schema import build_plans_payload
from benchbox.platforms.dataframe.benchmark_mixin import BenchmarkExecutionMixin
from tests.fixtures.result_dict_fixtures import make_benchmark_results

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class _FakeLazyFrame:
    def __init__(self, scan_paths: list[Path]):
        self._paths = scan_paths

    def explain(self, optimized: bool = True) -> str:
        scans = "\n".join(f"  Parquet SCAN [{path}]\n  PROJECT 2/16 COLUMNS" for path in self._paths)
        header = 'SELECT [col("revenue").sum()]' if optimized else "FROM"
        return f"{header}\n{scans}"


def _scan_paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    data_dir = tmp_path / "datagen" / "tpch_sf001"
    return data_dir, data_dir / "lineitem.1.parquet", Path.home() / "benchmark_data" / "orders.parquet"


def _forbidden(tmp_path: Path) -> list[str]:
    return [str(Path.home()), str(tmp_path)]


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for child in value.values() for text in _strings(child)]
    if isinstance(value, (list, tuple)):
        return [text for child in value for text in _strings(child)]
    return []


def _captured(tmp_path: Path) -> tuple[QueryPlan, QueryPlan]:
    data_dir, under_data, under_home = _scan_paths(tmp_path)
    frame = _FakeLazyFrame([under_data, under_home])
    captured = capture_query_plan(frame, "polars-df")
    assert captured is not None
    plan = redact_plan_local_paths(captured, (data_dir,))
    raw = QueryPlan(
        platform="polars",
        plan_type="optimized",
        plan_text=frame.explain(optimized=True),
        plan_data={"optimized_plan": frame.explain(optimized=True), "logical_plan": frame.explain(optimized=False)},
    )
    return plan, raw


def test_captured_polars_plan_text_contains_no_local_paths(tmp_path):
    plan, _ = _captured(tmp_path)

    leaked = [
        text for text in _strings([plan.plan_text, plan.plan_data]) if any(p in text for p in _forbidden(tmp_path))
    ]

    assert leaked == []
    assert find_public_path_leaks([plan.plan_text, plan.plan_data]) == []


def test_redaction_preserves_plan_structure(tmp_path):
    plan, raw = _captured(tmp_path)
    data_dir, under_data, under_home = _scan_paths(tmp_path)

    expected = raw.plan_text.replace(str(under_data), f"<redacted>{os.sep}{under_data.name}").replace(
        str(under_home), "<redacted>"
    )

    assert plan.plan_text == expected
    assert plan.plan_data is not None
    assert list(plan.plan_data) == ["optimized_plan", "logical_plan"]
    assert plan.plan_data["optimized_plan"] == plan.plan_text


def test_redaction_leaves_path_free_plans_unchanged():
    plan = QueryPlan(platform="polars", plan_type="optimized", plan_text='SELECT [col("a")]\n  FILTER a < 1')

    assert redact_plan_local_paths(plan) == plan


def test_redaction_covers_roots_outside_the_detector(tmp_path):
    root = tmp_path / "elsewhere"
    plan = QueryPlan(platform="polars", plan_type="optimized", plan_text=f"Parquet SCAN [{root}/a.parquet]")

    assert str(root) in plan.plan_text
    assert str(root) not in redact_plan_local_paths(plan, [root]).plan_text


class _Structurer(BenchmarkExecutionMixin):
    platform_name = "datafusion-df"


def test_structured_dataframe_plan_fingerprint_is_independent_of_local_paths(tmp_path):
    template = (
        "Projection: lineitem.l_orderkey\n  TableScan: lineitem\n"
        "ParquetExec: file_groups={{1 group: [[{path}]]}}, projection=[l_orderkey]"
    )
    structurer = _Structurer.__new__(_Structurer)
    fingerprints = []
    for root in (tmp_path / "machine_a", Path.home() / "machine_b"):
        raw = QueryPlan("datafusion", "logical", template.format(path=root / "lineitem.parquet"))
        dag = structurer._structure_dataframe_plan(redact_plan_local_paths(raw, [root]), "Q1")
        fingerprints.append((dag.plan_fingerprint, dag.raw_explain_output))

    assert fingerprints[0] == fingerprints[1]
    assert not any(str(Path.home()) in (raw or "") or str(tmp_path) in (raw or "") for _, raw in fingerprints)


@pytest.mark.parametrize("anonymize", [False, True])
def test_exported_plans_companion_has_no_local_paths(tmp_path, anonymize):
    out_dir = tmp_path / "results"
    data_dir = tmp_path / "datagen" / "tpch_sf001"
    plan, _ = _captured(tmp_path)
    rows = [
        {
            "query_id": "Q1",
            "status": "SUCCESS",
            "stream_id": 0,
            "iteration": 1,
            "run_type": "measurement",
            "test_type": "power",
            "query_plan": plan,
        }
    ]
    result = make_benchmark_results(
        benchmark_id="tpch",
        benchmark_name="tpch",
        platform="polars-df",
        execution_id="polars_paths",
        timestamp=datetime(2026, 1, 1),
        duration_seconds=1.0,
        total_queries=1,
        successful_queries=1,
        query_plans_captured=1,
        query_results=rows,
        platform_info={"working_dir": str(data_dir)},
    )

    exported = ResultExporter(output_dir=out_dir, anonymize=anonymize).export_result(result, formats=["json"])
    plans_path = out_dir / f"{exported['json'].stem}.plans.json"
    text = plans_path.read_text(encoding="utf-8")
    payload = json.loads(text)

    for forbidden in _forbidden(tmp_path):
        assert forbidden not in text
    assert find_public_path_leaks(payload) == []
    entry = payload["queries"]["Q1"]
    assert entry["plan"]["plan_text"] == plan.plan_text
    assert "fingerprint" not in entry
    assert build_plans_payload(result)["queries"]["Q1"]["plan"]["plan_text"] == plan.plan_text


def test_loaded_data_directories_are_redaction_roots_outside_the_detector(tmp_path):
    data_dir = tmp_path / "benchbox-data" / "tpch_sf001"
    files = {"lineitem": [data_dir / "v8" / "lineitem.1.parquet"], "orders": data_dir / "v8" / "orders.parquet"}
    roots = BenchmarkExecutionMixin._collect_plan_local_roots(files, data_dir)
    plan = QueryPlan("polars", "optimized", f"Parquet SCAN [{files['lineitem'][0]}]\nParquet SCAN [{files['orders']}]")

    redacted = redact_plan_local_paths(plan, roots)

    assert str(tmp_path) not in redacted.plan_text
    assert redacted.plan_text == (
        f"Parquet SCAN [<redacted>{os.sep}lineitem.1.parquet]\nParquet SCAN [<redacted>{os.sep}orders.parquet]"
    )


def test_object_store_paths_without_a_leading_slash_are_redacted(tmp_path):
    data_dir = tmp_path / "datagen"
    relative_home = str(Path.home()).lstrip("/")
    relative_data = str(data_dir / "orders.parquet").lstrip("/")
    plan = QueryPlan(
        "lakesail",
        "extended",
        f"DataSourceExec: file_groups={{1 group: [[{relative_home}/data/lineitem.parquet]]}}, projection=[a]\n"
        f"DataSourceExec: file_groups={{1 group: [[{relative_data}]]}}",
    )

    redacted = redact_plan_local_paths(plan, [data_dir])

    assert relative_home not in redacted.plan_text
    assert relative_data not in redacted.plan_text
    assert "[[<redacted>]]" in redacted.plan_text
    assert f"[[<redacted>{os.sep}orders.parquet]]" in redacted.plan_text
    assert "projection=[a]" in redacted.plan_text


_FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "query_plans"


def _scan_tables(dag) -> list[str | None]:
    names: list[str | None] = []
    stack = [dag.logical_root]
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        if node.operator_type == LogicalOperatorType.SCAN:
            names.append(node.table_name)
    return sorted(names, key=str)


@pytest.mark.parametrize(
    ("fixture", "root"),
    [
        ("lakesail_captured_join.txt", "/tmp/pv/sail"),
        ("lakesail_flat_captured_join.txt", "/tmp/pv/sailflat/tpch_sf1"),
    ],
)
def test_data_root_redaction_keeps_spark_family_tables_and_fingerprint(fixture, root):
    text = (_FIXTURES / fixture).read_text(encoding="utf-8")
    parser = SparkQueryPlanParser()
    original = parser.parse_explain_output("Q1", text)

    redacted_plan = redact_plan_local_paths(QueryPlan("lakesail", "extended", text), [root])
    redacted = parser.parse_explain_output("Q1", redacted_plan.plan_text)

    assert root.lstrip("/") not in redacted_plan.plan_text
    assert _scan_tables(redacted) == _scan_tables(original) == ["customers", "orders"]
    assert redacted.plan_fingerprint == original.plan_fingerprint


def test_data_root_redaction_keeps_spark_jvm_file_scan_names():
    scan = (
        "FileScan parquet [l_orderkey#1L] Batched: true, DataFilters: [], Format: Parquet, "
        "Location: InMemoryFileIndex(1 paths)[file:/srv/d/tpch_sf1/lineitem.parquet], PartitionFilters: []"
    )
    text = f"== Physical Plan ==\n*(1) {scan}\n"
    parser = SparkQueryPlanParser()
    original = parser.parse_explain_output("Q1", text)

    redacted_plan = redact_plan_local_paths(QueryPlan("pyspark", "extended", text), ["/srv/d/tpch_sf1"])
    redacted = parser.parse_explain_output("Q1", redacted_plan.plan_text)

    assert "/srv/d" not in redacted_plan.plan_text
    assert "[file:<redacted>/lineitem.parquet]" in redacted_plan.plan_text
    assert _scan_tables(redacted) == _scan_tables(original)
    assert redacted.plan_fingerprint == original.plan_fingerprint


def test_paths_outside_known_roots_are_redacted_whole():
    plan = QueryPlan("pyspark", "extended", "Location: [file:/home/other/x/lineitem.parquet]")

    redacted = redact_plan_local_paths(plan, ["/srv/d"])

    assert redacted.plan_text == "Location: [file:<redacted>]"


def test_roots_that_are_ancestors_of_home_do_not_leave_user_names():
    plan = QueryPlan("polars", "optimized", f"Parquet SCAN [{Path.home()}/x/a.parquet]")

    redacted = redact_plan_local_paths(plan, [Path.home().parent, Path.home()])

    assert redacted.plan_text == "Parquet SCAN [<redacted>]"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "file_groups={1 group: [[/home/other/a.parquet]], projection=[a]",
            "file_groups={1 group: [[<redacted>]], projection=[a]",
        ),
        (
            "file_groups={1 group: [[/home/other/a.parquet]]}, projection=[a]",
            "file_groups={1 group: [[<redacted>]]}, projection=[a]",
        ),
        ("x={/home/other/a.parquet}", "x={<redacted>}"),
    ],
)
def test_structural_brackets_survive_detector_redaction(text, expected):
    plan = QueryPlan("datafusion", "logical", text)

    assert redact_plan_local_paths(plan).plan_text == expected


@pytest.mark.parametrize("single_segment_root", ["/root", "/tmp", "/data"])
def test_single_segment_roots_do_not_redact_ordinary_words(single_segment_root):
    plan = QueryPlan("polars", "optimized", 'FILTER col("tag") == "root"\n  Filter: tag = root, tmp, data')

    redacted = redact_plan_local_paths(plan, [single_segment_root])

    assert redacted.plan_text == plan.plan_text
