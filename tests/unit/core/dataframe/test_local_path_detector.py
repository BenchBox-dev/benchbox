from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest

from benchbox.core.dataframe.benchmark_suite import BenchmarkConfig, DataFrameBenchmarkSuite
from benchbox.core.dataframe.profiling import QueryPlan, redact_plan_local_paths
from benchbox.core.query_plans.parsers.spark import SparkQueryPlanParser
from benchbox.core.results.anonymization import (
    PRIVATE_LOCAL_PATH_RE,
    find_public_path_leaks,
    local_path_detector,
)
from benchbox.core.results.query_plan_models import LogicalOperatorType

pytestmark = [pytest.mark.unit, pytest.mark.fast]

_FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "query_plans"


@pytest.mark.parametrize(
    "text",
    [
        "/Users/alice/data/lineitem.parquet",
        "/home/alice/data/lineitem.parquet",
        "/root/data/lineitem.parquet",
        "/tmp/bench/lineitem.parquet",
        "/var/folders/ab/cd/T/x.parquet",
        "/private/var/folders/ab/cd/T/x.parquet",
        "/private/tmp/bench/x.parquet",
        "/mnt/disk1/tpch/lineitem.parquet",
        "/opt/bench/lineitem.parquet",
        "/Volumes/ssd/lineitem.parquet",
        "~/bench/lineitem.parquet",
        "C:\\Users\\alice\\lineitem.parquet",
        "D:\\data\\lineitem.parquet",
        "z:\\scratch\\x",
        "E:/bench/lineitem.parquet",
        "\\\\fileserver\\share\\tpch\\lineitem.parquet",
        "//fileserver/share/tpch/lineitem.parquet",
        "Parquet SCAN [/tmp/bench/lineitem.parquet]",
        "Location: InMemoryFileIndex(1 paths)[file:/mnt/x/lineitem.parquet]",
        "Location: file:///opt/bench/x",
        "scan D:\\\\data\\\\orders",
    ],
)
def test_detector_flags_local_roots_drives_and_unc(text):
    assert PRIVATE_LOCAL_PATH_RE.search(text)
    assert find_public_path_leaks({"plan": text}) == ["plan"]
    assert local_path_detector().contains(text)


@pytest.mark.parametrize(
    "text",
    [
        "<redacted>",
        "<redacted>/lineitem.parquet",
        "file:<redacted>",
        "/data",
        "/data/",
        "/output",
        "s3://bucket/tpch/lineitem.parquet",
        "https://example.com/tmp/opt/home",
        "gs://bucket/mnt/x",
        "abfss://c@acct.dfs.core.windows.net/opt/x",
        "tpch/sf1/lineitem.parquet",
        "lineitem.1.parquet",
        "catalog.schema.lineitem",
        "main.tpch_sf1.orders",
        'FILTER col("tag") == "tmp", opt, mnt, home',
        "Parquet SCAN [<redacted>/lineitem.parquet]",
        "FileScan parquet [l_orderkey#1L] Location: InMemoryFileIndex(1 paths)[file:<redacted>/lineitem.parquet]",
        "a:b c:d x=1:2",
        "ratio 3:4 http://host:80/x",
    ],
)
def test_detector_does_not_flag_placeholders_urls_or_relative_tails(text):
    assert not PRIVATE_LOCAL_PATH_RE.search(text)
    assert find_public_path_leaks({"plan": text}) == []
    assert not local_path_detector(redact_home=False).contains(text)


def test_neutral_placeholders_are_allowlisted_even_when_configured_as_roots():
    detector = local_path_detector(["/data", "/output"], redact_home=False)

    assert not detector.contains("/data")
    assert not detector.contains("scan /output/")
    assert detector.redact_text("/data/tpch") == "/data/tpch"


def test_configured_roots_are_flagged_and_redacted_with_tail_kept():
    detector = local_path_detector(["/srv/runs/tpch_sf1"], redact_home=False)
    text = "Parquet SCAN [/srv/runs/tpch_sf1/lineitem.parquet]"

    assert detector.contains(text)
    assert find_public_path_leaks(text, roots=["/srv/runs/tpch_sf1"]) == ["<root>"]
    assert find_public_path_leaks(text) == []
    assert detector.redact_text(text) == "Parquet SCAN [<redacted>/lineitem.parquet]"
    assert not detector.contains(detector.redact_text(text))


@pytest.mark.parametrize(
    ("root", "text", "expected"),
    [
        ("D:\\bench\\tpch_sf1", "SCAN D:\\bench\\tpch_sf1\\lineitem.parquet", "SCAN <redacted>\\lineitem.parquet"),
        ("E:/bench/tpch_sf1", "SCAN E:/bench/tpch_sf1/lineitem.parquet", "SCAN <redacted>/lineitem.parquet"),
    ],
)
def test_configured_windows_roots_are_redacted_with_tail_kept(root, text, expected):
    detector = local_path_detector([root], redact_home=False)

    assert detector.contains(text)
    assert detector.redact_text(text) == expected


def test_redaction_of_tmp_and_drive_paths_without_configured_roots():
    plan = QueryPlan(
        "polars",
        "optimized",
        "Parquet SCAN [/tmp/bench/lineitem.parquet]\nParquet SCAN [D:\\data\\orders.parquet]\nSCAN catalog.s.t",
    )

    redacted = redact_plan_local_paths(plan)

    assert redacted.plan_text == "Parquet SCAN [<redacted>]\nParquet SCAN [<redacted>]\nSCAN catalog.s.t"
    assert find_public_path_leaks(redacted.plan_text) == []


def test_nested_raw_plan_strings_are_redacted_without_mutating_input():
    plan_data = {
        "raw": ["scan /mnt/x/a.parquet", ("D:\\bench\\b.parquet", {"inner": "file:/opt/y/c"})],
        "keep": 3,
        "tail": "relative/table/tail",
    }
    original = copy.deepcopy(plan_data)
    plan = QueryPlan("polars", "optimized", "t", plan_data=plan_data, optimization_hints=["hint /tmp/z/x"])

    redacted = redact_plan_local_paths(plan)

    assert plan_data == original
    assert find_public_path_leaks([redacted.plan_data, redacted.optimization_hints]) == []
    assert redacted.plan_data["raw"][1][1]["inner"] == "file:<redacted>"
    assert isinstance(redacted.plan_data["raw"][1], tuple)
    assert redacted.plan_data["keep"] == 3
    assert redacted.plan_data["tail"] == "relative/table/tail"
    assert set(redacted.plan_data) == set(original)


@pytest.mark.parametrize(
    ("fixture", "root", "platform"),
    [
        ("lakesail_captured_join.txt", "/tmp/pv/sail", "lakesail-df"),
        ("lakesail_flat_captured_join.txt", "/tmp/pv/sailflat/tpch_sf1", "lakesail-df"),
    ],
)
def test_comparison_suite_passes_data_dir_so_scan_tables_survive(fixture, root, platform, monkeypatch):
    import benchbox.core.dataframe.benchmark_suite as suite_module

    text = (_FIXTURES / fixture).read_text(encoding="utf-8")
    parser = SparkQueryPlanParser()
    original = parser.parse_explain_output("Q1", text)

    monkeypatch.setattr(
        suite_module, "capture_query_plan", lambda frame, platform_name: QueryPlan("pyspark", "extended", text)
    )

    class _Query:
        def execute(self, context, family):
            return object()

        def get_impl_for_family(self, family):
            return lambda ctx: [1]

    class _Registry:
        def get(self, query_id):
            return _Query()

    suite = DataFrameBenchmarkSuite(
        config=BenchmarkConfig(
            scale_factor=0.01, warmup_iterations=0, benchmark_iterations=1, track_memory=False, capture_plans=True
        )
    )
    suite._query_registry = _Registry()
    monkeypatch.setattr(suite_module, "plan_capture_support", lambda name: suite_module.PlanCaptureSupport.DAG)

    result = suite._benchmark_query("Q1", object(), "expression", platform, plan_roots=(Path(root),))

    assert result.query_plan is not None
    assert root.lstrip("/") not in result.query_plan
    redacted = parser.parse_explain_output("Q1", result.query_plan)
    assert _scan_tables(redacted) == _scan_tables(original) == ["customers", "orders"]
    assert redacted.plan_fingerprint == original.plan_fingerprint


def _scan_tables(dag):
    names = []
    stack = [dag.logical_root]
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        if node.operator_type == LogicalOperatorType.SCAN:
            names.append(node.table_name)
    return sorted(names, key=str)


_LEGACY_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"(?:~|/Users|/home|/root|/private/var|/var/folders|/var/run|/Volumes)/[^\s'\",;)]*"
    r"|[A-Za-z]:\\Users\\[^\s'\",;)]*"
    r")",
    flags=re.IGNORECASE,
)


@pytest.mark.parametrize(
    "text",
    [
        "Parquet SCAN [/tmp/bench/lineitem.parquet]",
        "/mnt/disk1/tpch/lineitem.parquet",
        "/opt/bench/lineitem.parquet",
        "scan D:\\bench\\data\\orders.parquet",
        "//fileserver/share/tpch/lineitem.parquet",
    ],
)
def test_legacy_detector_missed_these_paths_and_the_shared_detector_flags_them(text):
    assert _LEGACY_PATTERN.search(text) is None
    assert find_public_path_leaks({"plan": text}) == ["plan"]


def test_relative_configured_root_is_resolved_and_keeps_the_table_tail(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    detector = local_path_detector(["benchmark_runs/tpch/sf1/data"], redact_home=False)
    absolute = (tmp_path / "benchmark_runs/tpch/sf1/data").resolve()

    assert detector.redact_text(f"Scan [{absolute}/lineitem.parquet]") == "Scan [<redacted>/lineitem.parquet]"
    assert detector.contains(f"Scan [{absolute}/lineitem.parquet]")


def test_backslash_rooted_configured_root_matches_forward_slash_plan_text():
    detector = local_path_detector(["\\srv\\data\\tpch"], redact_home=False)

    assert detector.redact_text("Scan [/srv/data/tpch/lineitem.parquet]") == "Scan [<redacted>/lineitem.parquet]"


def test_custom_home_is_a_leak_scan_root(monkeypatch):
    monkeypatch.setenv("HOME", "/srv/synthetic-home-xyz")
    monkeypatch.setenv("USERPROFILE", "/srv/synthetic-home-xyz")

    assert find_public_path_leaks({"a": "open /srv/synthetic-home-xyz/data/x.parquet"}) == ["a"]
    assert find_public_path_leaks({"a": "open /srv/synthetic-home-xyz"}) == ["a"]


@pytest.mark.parametrize("home", ["/", "", "/data", "/output", "/data/"])
def test_neutral_or_placeholder_home_is_not_a_leak_root(monkeypatch, home):
    monkeypatch.setenv("HOME", home)
    monkeypatch.setenv("USERPROFILE", home)

    assert find_public_path_leaks({"a": "scan /srv/other/x.parquet", "b": "/data/x", "c": "/output/y"}) == []


def test_home_that_prefixes_another_name_is_not_a_false_hit(monkeypatch):
    monkeypatch.setenv("HOME", "/srv/synthetic-home")
    monkeypatch.setenv("USERPROFILE", "/srv/synthetic-home")

    assert find_public_path_leaks({"a": "/srv/synthetic-home-two/x", "b": "/srv/synthetic-homes"}) == []


