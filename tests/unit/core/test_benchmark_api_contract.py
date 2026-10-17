from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import benchbox
from benchbox.base import BENCHMARK_API_SURFACE, RUN_WITH_PLATFORM_API_SURFACE, BaseBenchmark as PublicBaseBenchmark
from benchbox.core.base_benchmark import (
    BENCHMARK_API_DECISION as CORE_BASE_API_DECISION,
    BENCHMARK_API_SURFACE as CORE_BASE_API_SURFACE,
    BaseBenchmark as CoreBaseBenchmark,
)
from benchbox.core.benchmark_loader import BENCHMARK_LOADER_API_SURFACE, get_benchmark_class as get_core_benchmark_class
from benchbox.core.benchmark_registry import (
    BENCHMARK_CLASS_NAMES,
    BENCHMARK_DATA_SOURCE_PROBE_IDS,
    BENCHMARK_METADATA,
    BENCHMARK_SUPPORT_STATUS_VALUES,
    CORE_BENCHMARK_CLASS_NAMES,
    get_benchmark_default_scale,
    get_benchmark_id_for_class_name,
    get_benchmark_registry_summary,
    get_benchmark_support_status,
    get_benchmarks_by_support_status,
    get_public_benchmark_class,
    list_loader_benchmark_ids,
    list_public_benchmark_ids,
)
from benchbox.core.simple_benchmark_mixin import SimpleBenchmarkMixin

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PUBLIC_CONTRACTS_DOC = PROJECT_ROOT / "docs/reference/public-contracts.md"
SUPPORT_STATUS_CRITERIA_DOC = PROJECT_ROOT / "docs/benchmarks/support-status.md"
_CRITERIA_MATRIX_ROW = re.compile(
    r"^\|\s*`(?P<bid>[a-z0-9_]+)`\s*\|\s*`(?P<status>[a-z_]+)`\s*\|\s*(?P<rationale>.*?)\s*\|"
)
_CRITERIA_MATRIX_QUERY_COUNT = re.compile(r"\b(?P<count>\d+)-(?:query|variant)\b")
CORE_ONLY_BENCHMARK_IDS = {"ai_primitives", "joinorder_synthetic"}
BENCHMARK_API_COUNT_MARKER = (
    "Benchmark API snapshot: **23** registry entries; **23** loader-resolved core families; "
    "**22** public discovery entries; **21** top-level Python benchmark facades; "
    "**15** lazy facades; **6** eager facades; **2** core-only benchmark IDs. "
    "Benchmark support status: **6** stable, **11** beta, **5** experimental, **1** repo-only, "
    "**0** deprecated, **0** document-only."
)
BENCHMARK_SUPPORT_STATUS_COUNTS = {
    "stable": 6,
    "beta": 11,
    "experimental": 5,
    "repo_only": 1,
    "deprecated": 0,
    "document_only": 0,
}
_REGISTRY_CLAIM_NUMBER = re.compile(r"\d+")


def _registry_claim_fragment_pattern(fragment: str) -> re.Pattern[str]:
    pattern_parts: list[str] = []
    offset = 0
    for match in _REGISTRY_CLAIM_NUMBER.finditer(fragment):
        pattern_parts.append(re.escape(fragment[offset : match.start()]))
        pattern_parts.append(rf"\b{re.escape(match.group())}\b")
        offset = match.end()
    pattern_parts.append(re.escape(fragment[offset:]))
    return re.compile("".join(pattern_parts))


def _contains_registry_derived_claim(text: str, fragment: str) -> bool:
    return _registry_claim_fragment_pattern(fragment).search(text) is not None


def _collect_criteria_matrix_rows(
    doc_text: str, registry_status: dict[str, str]
) -> tuple[dict[str, dict[str, str]], list[str], list[str]]:
    matrix_rows: dict[str, dict[str, str]] = {}
    duplicates: list[str] = []
    non_registry_rows: list[str] = []
    in_benchmark_matrix = False

    for line in doc_text.splitlines():
        if line == "## Benchmark Status Rationale":
            in_benchmark_matrix = True
            continue
        if in_benchmark_matrix and line.startswith("## "):
            break
        if not in_benchmark_matrix:
            continue

        match = _CRITERIA_MATRIX_ROW.match(line)
        if match is None:
            continue
        benchmark_id = match.group("bid")
        if benchmark_id not in registry_status:
            non_registry_rows.append(benchmark_id)
            continue
        if benchmark_id in matrix_rows:
            duplicates.append(benchmark_id)
        matrix_rows[benchmark_id] = {
            "status": match.group("status"),
            "rationale": match.group("rationale"),
        }

    return matrix_rows, duplicates, non_registry_rows


def test_benchmark_api_surface_markers_match_contract_map() -> None:

    contract_doc = PUBLIC_CONTRACTS_DOC.read_text()

    assert BENCHMARK_API_SURFACE == "beta-public"
    assert RUN_WITH_PLATFORM_API_SURFACE == "beta-public"
    assert PublicBaseBenchmark.api_surface == "beta-public"
    assert PublicBaseBenchmark.run_with_platform_api_surface == "beta-public"
    assert "| `benchbox.base.BaseBenchmark` | `beta-public` |" in contract_doc
    assert "| `BaseBenchmark.run_with_platform` | `beta-public` |" in contract_doc

    assert CORE_BASE_API_SURFACE == "deprecated"
    assert CORE_BASE_API_DECISION == "retained-internal-compatibility-base"
    assert CoreBaseBenchmark.api_surface == "deprecated"
    assert CoreBaseBenchmark.compatibility_marker == "retained-internal-compatibility-base"
    assert "| `benchbox.core.base_benchmark.BaseBenchmark` | `deprecated` |" in contract_doc

    assert BENCHMARK_LOADER_API_SURFACE == "internal"
    assert "| `benchbox.core.benchmark_loader` | `internal` |" in contract_doc


def test_deprecated_core_base_usage_is_pinned_to_current_migration_exceptions() -> None:

    deprecated_consumers = {
        benchmark_id
        for benchmark_id in list_loader_benchmark_ids()
        if issubclass(get_core_benchmark_class(benchmark_id), CoreBaseBenchmark)
    }

    assert deprecated_consumers == set()


def test_benchmark_registry_wrapper_and_loader_counts_match_contract_map() -> None:

    top_level_benchmark_exports = set(BENCHMARK_CLASS_NAMES.values()) & set(benchbox.__all__)
    lazy_benchmark_exports = set(benchbox._BENCHMARK_REGISTRY)
    eager_benchmark_exports = top_level_benchmark_exports - lazy_benchmark_exports
    core_only_benchmark_ids = {
        benchmark_id for benchmark_id, class_name in BENCHMARK_CLASS_NAMES.items() if class_name not in benchbox.__all__
    }

    assert len(BENCHMARK_METADATA) == 23
    assert len(BENCHMARK_CLASS_NAMES) == 23
    assert len(CORE_BENCHMARK_CLASS_NAMES) == 23
    assert len(list_loader_benchmark_ids()) == 23
    assert len(list_public_benchmark_ids()) == 22
    assert len(top_level_benchmark_exports) == 21
    assert len(lazy_benchmark_exports) == 15
    assert len(eager_benchmark_exports) == 6
    assert core_only_benchmark_ids == CORE_ONLY_BENCHMARK_IDS
    assert set(list_loader_benchmark_ids()) == set(BENCHMARK_METADATA)
    assert set(list_public_benchmark_ids()) == set(BENCHMARK_METADATA) - {"joinorder_synthetic"}

    assert BENCHMARK_API_COUNT_MARKER in PUBLIC_CONTRACTS_DOC.read_text()


def test_benchmark_class_reverse_lookup_matches_registry_maps() -> None:

    for benchmark_id, class_name in BENCHMARK_CLASS_NAMES.items():
        assert get_benchmark_id_for_class_name(class_name) == benchmark_id
    for benchmark_id, class_name in CORE_BENCHMARK_CLASS_NAMES.items():
        assert get_benchmark_id_for_class_name(class_name) == benchmark_id
    assert get_benchmark_id_for_class_name("CustomDownstreamBenchmark") is None


def test_simple_benchmark_mixin_rejects_missing_contract_members() -> None:

    with pytest.raises(TypeError, match="missing required contract members"):

        class BadSimple(SimpleBenchmarkMixin):
            pass


def test_simple_benchmark_mixin_rejects_inherited_abstract_contract_members() -> None:

    with pytest.raises(TypeError, match="get_query"):

        class BadSimpleWithAbstractParent(SimpleBenchmarkMixin, PublicBaseBenchmark):
            _benchmark_label = "bad"
            _table_load_order = []

            def execute_query(self) -> None:
                return None

            def get_create_tables_sql(self) -> dict[str, str]:
                return {}

            def _get_table_schema(self) -> dict[str, dict[str, str]]:
                return {}


def test_public_benchmark_class_lookup_name_returns_public_wrapper() -> None:

    assert get_public_benchmark_class("tpch").__name__ == "TPCH"


def test_benchmark_support_status_metadata_matches_contract_map() -> None:

    valid = set(BENCHMARK_SUPPORT_STATUS_VALUES)
    for benchmark_id, meta in BENCHMARK_METADATA.items():
        assert set(meta).intersection({"support_status"}) == {"support_status"}
        assert meta["support_status"] in valid, f"{benchmark_id} has invalid support_status"

    summary = get_benchmark_registry_summary()
    assert summary["support_status"] == BENCHMARK_SUPPORT_STATUS_COUNTS
    assert summary["surface"] == {"internal": 1, "public": 22}
    assert summary["public"] == len(list_public_benchmark_ids())
    assert summary["loader"] == len(list_loader_benchmark_ids())

    assert get_benchmark_support_status("tpch") == "stable"
    assert get_benchmark_support_status("joinorder_synthetic") == "repo_only"
    assert get_benchmark_support_status("missing") is None
    assert get_benchmarks_by_support_status("repo_only") == ["joinorder_synthetic"]
    assert "joinorder_synthetic" not in list_public_benchmark_ids()

    with pytest.raises(ValueError, match="Unknown benchmark support_status"):
        get_benchmarks_by_support_status("unknown")

    contract_doc = PUBLIC_CONTRACTS_DOC.read_text()
    assert "Benchmark support status: **6** stable, **11** beta, **5** experimental" in contract_doc
    assert "support status counts are stable=6, beta=11, experimental=5" in contract_doc


def test_external_dataset_stable_promotion_requires_complete_content_pins(tmp_path: Path) -> None:
    from benchbox.core.flightdata.downloader import PINNED_SOURCE_SHA256, FlightDataDownloader
    from benchbox.core.nyctaxi.downloader import PINNED_SOURCE_SHA256 as NYC_SHA256, NYCTaxiDataDownloader

    required_urls = {
        "flightdata": set(FlightDataDownloader(scale_factor=1, output_dir=tmp_path).source_contract()["urls"]),
        "nyctaxi": set(NYCTaxiDataDownloader(output_dir=tmp_path).source_contract()["urls"]),
    }
    configured = {"flightdata": PINNED_SOURCE_SHA256, "nyctaxi": NYC_SHA256}
    for benchmark_id in required_urls:
        if BENCHMARK_METADATA[benchmark_id]["support_status"] == "stable":
            assert set(configured[benchmark_id]) == required_urls[benchmark_id], (
                f"{benchmark_id} cannot be stable until its default external corpus has complete SHA-256 pins"
            )
            assert all(re.fullmatch(r"[0-9a-f]{64}", digest) for digest in configured[benchmark_id].values())


def test_benchmark_support_status_criteria_matrix_covers_every_benchmark() -> None:

    registry_status = {bid: meta["support_status"] for bid, meta in BENCHMARK_METADATA.items()}
    doc_text = SUPPORT_STATUS_CRITERIA_DOC.read_text()

    matrix_rows, duplicates, non_registry_rows = _collect_criteria_matrix_rows(doc_text, registry_status)

    assert duplicates == [], f"benchmarks with more than one criteria-matrix row: {sorted(duplicates)}"
    assert non_registry_rows == [], f"criteria-matrix rows for non-registry benchmarks: {sorted(non_registry_rows)}"

    missing_rows = sorted(set(registry_status) - set(matrix_rows))
    assert missing_rows == [], f"benchmarks missing a criteria-matrix row: {missing_rows}"

    mismatched = {
        benchmark_id: {"matrix": matrix_rows[benchmark_id]["status"], "registry": registry_status[benchmark_id]}
        for benchmark_id in registry_status
        if matrix_rows[benchmark_id]["status"] != registry_status[benchmark_id]
    }
    assert mismatched == {}, f"criteria-matrix status differs from registry: {mismatched}"

    missing_query_claims: list[str] = []
    query_count_mismatched: dict[str, dict[str, int]] = {}
    dataframe_claim_mismatched: dict[str, str] = {}
    for benchmark_id, meta in BENCHMARK_METADATA.items():
        rationale = matrix_rows[benchmark_id]["rationale"]

        query_count = _CRITERIA_MATRIX_QUERY_COUNT.search(rationale)
        if query_count is None:
            missing_query_claims.append(benchmark_id)
        elif int(query_count.group("count")) != int(meta["num_queries"]):
            query_count_mismatched[benchmark_id] = {
                "matrix": int(query_count.group("count")),
                "registry": int(meta["num_queries"]),
            }

        if meta.get("supports_dataframe", False):
            if "DataFrame-capable" not in rationale or "not DataFrame-capable" in rationale:
                dataframe_claim_mismatched[benchmark_id] = "expected DataFrame-capable"
        elif "not DataFrame-capable" not in rationale:
            dataframe_claim_mismatched[benchmark_id] = "expected not DataFrame-capable"

    assert missing_query_claims == [], (
        f"benchmarks missing parseable matrix query-count evidence: {missing_query_claims}"
    )
    assert query_count_mismatched == {}, (
        f"criteria-matrix query-count evidence differs from registry: {query_count_mismatched}"
    )
    assert dataframe_claim_mismatched == {}, (
        f"criteria-matrix DataFrame evidence differs from registry: {dataframe_claim_mismatched}"
    )


def test_benchmark_support_status_criteria_matrix_rejects_stale_benchmark_rows() -> None:

    doc_text = """
## Acceptance Criteria by Status

| Status | User docs | Result-integrity spec |
|---|---|---|
| `future_status` | `not_a_benchmark` | Outside the benchmark matrix. |

## Benchmark Status Rationale

| Benchmark | Status | Rationale and evidence | Promotion / demotion blockers |
|---|---|---|---|
| `tpch` | `stable` | Canonical 22-query TPC-H; DataFrame-capable. | None. |
| `removed_benchmark` | `beta` | Stale 1-query row; not DataFrame-capable. | Remove. |

## Promotion Candidates and Blockers
"""

    matrix_rows, duplicates, non_registry_rows = _collect_criteria_matrix_rows(doc_text, {"tpch": "stable"})

    assert duplicates == []
    assert matrix_rows == {
        "tpch": {
            "status": "stable",
            "rationale": "Canonical 22-query TPC-H; DataFrame-capable.",
        }
    }
    assert non_registry_rows == ["removed_benchmark"]


def test_registry_derived_claim_matcher_requires_numeric_boundaries() -> None:

    assert _contains_registry_derived_claim(
        "Explore 22 benchmarks (TPC-H, TPC-DS, and more)",
        "22 benchmarks (TPC",
    )
    assert _contains_registry_derived_claim(
        "BenchBox includes **22** public-discovery benchmarks",
        "BenchBox includes **22** public-discovery benchmarks",
    )
    assert not _contains_registry_derived_claim(
        "Explore 122 benchmarks (TPC-H, TPC-DS, and more)",
        "22 benchmarks (TPC",
    )
    assert not _contains_registry_derived_claim(
        "Specs cover 20 of 122 benchmarks",
        "20 of 22 benchmarks",
    )
    assert not _contains_registry_derived_claim(
        "Specs cover 120 of 22 benchmarks",
        "20 of 22 benchmarks",
    )


def test_public_benchmark_count_claims_are_registry_derived() -> None:
    from benchbox.core.results.benchmark_specs import BENCHMARK_SPECS

    public_ids = set(list_public_benchmark_ids())
    public_count = len(public_ids)
    spec_count = len(set(BENCHMARK_SPECS) & public_ids)

    required_claims = {
        "docs/design/architecture.md": [f"currently {public_count} benchmarks"],
        "docs/development/result-integrity-validation.md": [
            f"Specs exist for {spec_count} of {public_count} registered benchmarks"
        ],
        "docs/reference/cli/utilities.md": [f"Specs cover {spec_count} of {public_count} benchmarks"],
        "docs/usage/faq.md": [f"BenchBox includes **{public_count}** public-discovery benchmarks"],
    }

    stale: list[str] = []
    for rel_path, fragments in required_claims.items():
        text = (PROJECT_ROOT / rel_path).read_text(encoding="utf-8")
        stale.extend(
            f"{rel_path}: missing registry-derived claim {fragment!r}"
            for fragment in fragments
            if not _contains_registry_derived_claim(text, fragment)
        )

    assert stale == [], "Stale public benchmark count claims (update to match the registry):\n" + "\n".join(stale)


def test_benchmark_data_source_metadata_matches_runtime_declarations() -> None:

    assert all("data_source" in meta for meta in BENCHMARK_METADATA.values())
    assert BENCHMARK_DATA_SOURCE_PROBE_IDS == (
        "read_primitives",
        "write_primitives",
        "transaction_primitives",
        "ai_primitives",
        "tpcds_obt",
    )
    assert BENCHMARK_METADATA["read_primitives"]["data_source"] == "tpch"
    assert BENCHMARK_METADATA["write_primitives"]["data_source"] == "tpch"
    assert BENCHMARK_METADATA["transaction_primitives"]["data_source"] == "tpch"
    assert BENCHMARK_METADATA["ai_primitives"]["data_source"] == "tpch"
    assert BENCHMARK_METADATA["tpcds_obt"]["data_source"] == "tpcds"
    assert BENCHMARK_METADATA["tpch"]["data_source"] is None

    from benchbox.core.benchmark_loader import get_benchmark_class

    for benchmark_id in BENCHMARK_DATA_SOURCE_PROBE_IDS:
        benchmark_class = get_benchmark_class(benchmark_id)
        benchmark = benchmark_class(scale_factor=get_benchmark_default_scale(benchmark_id))
        assert benchmark.get_data_source_benchmark() == BENCHMARK_METADATA[benchmark_id]["data_source"]


def test_benchmark_estimate_metadata_is_complete() -> None:

    for benchmark_id, meta in BENCHMARK_METADATA.items():
        assert "estimated_time_range" in meta, benchmark_id
        assert "base_memory_gb" in meta, benchmark_id
        low, high = meta["estimated_time_range"]
        assert low >= 0, benchmark_id
        assert high >= low, benchmark_id
        assert meta["base_memory_gb"] > 0, benchmark_id


def test_benchmark_api_import_boundary_excludes_platform_adapter_imports() -> None:

    targets = [
        PROJECT_ROOT / "benchbox/base.py",
        PROJECT_ROOT / "benchbox/core/base_benchmark.py",
        PROJECT_ROOT / "benchbox/core/benchmark_loader.py",
        PROJECT_ROOT / "benchbox/core/__init__.py",
        *sorted((PROJECT_ROOT / "benchbox/core/tuning").rglob("*.py")),
    ]

    violations: list[str] = []
    for path in targets:
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "benchbox.platforms" or alias.name.startswith("benchbox.platforms."):
                        violations.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} imports {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                imports_platform_package = module == "benchbox" and any(
                    alias.name == "platforms" for alias in node.names
                )
                imports_platform_module = module == "benchbox.platforms" or module.startswith("benchbox.platforms.")
                if imports_platform_package or imports_platform_module:
                    imported_names = ", ".join(alias.name for alias in node.names)
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} imports {module}.{imported_names}"
                    )

    assert violations == []
