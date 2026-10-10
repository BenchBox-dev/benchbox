from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from benchbox.cli.config import ConfigManager
from benchbox.core.tuning import template_evidence
from benchbox.core.tuning.template_evidence import (
    evidence_state,
    evidence_state_for_run,
    evidence_warning,
    is_tuned_template_ref,
    load_registry,
    normalize_benchmark,
    template_cell,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REPO_ROOT = Path(__file__).resolve().parents[4]
PACKAGED_ROOT = REPO_ROOT / "benchbox" / "core" / "tuning" / "templates"
EXAMPLES_ROOT = REPO_ROOT / "examples" / "tunings"

REQUIRED_ENTRY_KEYS = ("state", "scale_factor", "memory", "engine_version", "measured_on", "evidence")
KNOWN_STATES = ("unmeasured", "measured")


def _shipped_tuned_templates() -> list[tuple[str, str, Path]]:
    found: list[tuple[str, str, Path]] = []
    for root in (PACKAGED_ROOT, EXAMPLES_ROOT):
        for path in sorted(root.glob("*/*_tuned.yaml")):
            stem = path.stem
            if stem.endswith("_liquid_tuned"):
                benchmark = stem[: -len("_liquid_tuned")]
            else:
                benchmark = stem[: -len("_tuned")]
            found.append((path.parent.name.lower(), benchmark.lower(), path))
    return found


def test_registry_covers_every_shipped_tuned_template():
    registry = load_registry(refresh=True)
    missing = [
        f"{platform}/{benchmark} ({path})"
        for platform, benchmark, path in _shipped_tuned_templates()
        if not isinstance(registry.get(platform), dict) or not isinstance(registry[platform].get(benchmark), dict)
    ]
    assert _shipped_tuned_templates(), "no shipped tuned templates found"
    assert missing == []


def test_registry_has_no_entry_without_a_shipped_template():
    shipped = {(platform, benchmark) for platform, benchmark, _ in _shipped_tuned_templates()}
    registry = load_registry(refresh=True)
    stale = [
        f"{platform}/{benchmark}"
        for platform, entries in registry.items()
        if isinstance(entries, dict)
        for benchmark in entries
        if (platform, benchmark) not in shipped
    ]
    assert stale == []


def test_registry_entries_carry_the_full_evidence_shape():
    registry = load_registry(refresh=True)
    assert registry, "registry is empty"
    for platform, entries in registry.items():
        assert isinstance(entries, dict)
        for benchmark, entry in entries.items():
            assert isinstance(entry, dict), f"{platform}/{benchmark}"
            assert tuple(entry.keys()) == REQUIRED_ENTRY_KEYS, f"{platform}/{benchmark}"
            assert entry["state"] in KNOWN_STATES, f"{platform}/{benchmark}"


def test_all_registry_states_start_unmeasured():
    registry = load_registry(refresh=True)
    measured = [
        f"{platform}/{benchmark}"
        for platform, entries in registry.items()
        if isinstance(entries, dict)
        for benchmark, entry in entries.items()
        if isinstance(entry, dict) and entry.get("state") != "unmeasured"
    ]
    assert measured == []


def test_evidence_state_resolves_known_cells():
    assert evidence_state("duckdb", "tpch") == "unmeasured"
    assert evidence_state("DuckDB", "TPCH") == "unmeasured"
    assert evidence_state("databricks", "tpcds") == "unmeasured"
    assert evidence_state("snowflake", "tpch") == "unmeasured"
    assert evidence_state("clickhouse", "ssb") == "unmeasured"


def test_evidence_state_normalizes_platform_aliases():
    assert evidence_state("clickhouse-local", "tpch") == "unmeasured"
    assert evidence_state("clickhouse-server", "tpch") == "unmeasured"
    assert evidence_state("chdb", "tpch") == "unmeasured"
    assert evidence_state("spark", "tpch") == "unmeasured"


def test_evidence_state_returns_none_off_registry():
    assert evidence_state("duckdb", "nosuchbench") is None
    assert evidence_state("nosuchplatform", "tpch") is None
    assert evidence_state(None, "tpch") is None
    assert evidence_state("duckdb", None) is None
    assert evidence_state("", "") is None


def test_is_tuned_template_ref():
    assert is_tuned_template_ref("examples/tunings/duckdb/tpch_tuned.yaml")
    assert is_tuned_template_ref("examples/tunings/databricks/tpch_liquid_tuned.yaml")
    assert is_tuned_template_ref("tpch_tuned.yaml:abcdef1234567890")
    assert not is_tuned_template_ref("examples/tunings/duckdb/tpch_notuning.yaml")
    assert not is_tuned_template_ref("my_custom.yaml")
    assert not is_tuned_template_ref(None)
    assert not is_tuned_template_ref("")


def test_evidence_warning_exact_text_for_unmeasured_run():
    message = evidence_warning(
        "duckdb",
        "tpch",
        source_file="examples/tunings/duckdb/tpch_tuned.yaml",
        tunings_applied={"table_tunings": {}},
    )
    assert message == (
        "Tuned template duckdb/tpch has no measured benefit: the result shows "
        "the template was applied, not that it is faster than notuning."
    )


def test_evidence_warning_absent_without_tuned_template():
    assert (
        evidence_warning(
            "duckdb", "tpch", source_file="examples/tunings/duckdb/tpch_notuning.yaml", tunings_applied={"a": 1}
        )
        is None
    )
    assert (
        evidence_warning(
            "duckdb",
            "tpch",
            source_file="examples/tunings/duckdb/tpch_tuned.yaml",
            tunings_applied=None,
        )
        is None
    )
    assert (
        evidence_warning("duckdb", "tpch", source_file="examples/tunings/duckdb/tpch_tuned.yaml", tunings_applied={})
        is None
    )
    assert evidence_warning("nosuchplatform", "tpch", source_file="tpch_tuned.yaml", tunings_applied={"a": 1}) is None


def test_evidence_warning_absent_for_measured_template(monkeypatch):
    monkeypatch.setattr(template_evidence, "_REGISTRY", {"duckdb": {"tpch": {"state": "measured"}}})
    assert (
        evidence_warning(
            "duckdb",
            "tpch",
            source_file="examples/tunings/duckdb/tpch_tuned.yaml",
            tunings_applied={"table_tunings": {}},
        )
        is None
    )


def test_shipped_templates_hash_without_evidence_metadata():
    shipped = _shipped_tuned_templates()
    assert shipped
    for platform, _, path in shipped:
        config = ConfigManager().load_unified_tuning_config(path, platform=platform)
        digest = config.get_configuration_hash()
        assert len(digest) == 64
        assert digest == config.get_configuration_hash()
        assert "template_evidence" not in config.to_dict()


def test_registry_file_matches_expected_template_count():
    raw = yaml.safe_load(
        (REPO_ROOT / "benchbox" / "core" / "tuning" / "profiles" / "template_evidence.yaml").read_text()
    )
    count = sum(len(entries) for entries in raw.values() if isinstance(entries, dict))
    assert count == len({(p, b) for p, b, _ in _shipped_tuned_templates()})


def test_normalize_benchmark_handles_display_names():
    assert normalize_benchmark("tpch") == "tpch"
    assert normalize_benchmark("TPC-H") == "tpch"
    assert normalize_benchmark("TPC-DS") == "tpcds"
    assert normalize_benchmark("star_schema") == "ssb"
    assert normalize_benchmark(None) is None
    assert normalize_benchmark("") is None


def test_evidence_state_resolves_display_benchmark_names():
    assert evidence_state("duckdb", "TPC-H") == "unmeasured"
    assert evidence_state("DuckDB", "TPC-H") == "unmeasured"
    assert evidence_state("databricks", "TPC-DS") == "unmeasured"


def test_template_cell_reads_cell_from_template_path():
    assert template_cell("examples/tunings/duckdb/tpch_tuned.yaml", "DuckDB", "TPC-H") == ("duckdb", "tpch")
    assert template_cell("examples/tunings/databricks/tpch_liquid_tuned.yaml", "spark", "TPC-H") == (
        "databricks",
        "tpch",
    )


def test_template_cell_falls_back_to_run_identity_for_bare_refs():
    assert template_cell("tpch_tuned.yaml:abcdef1234567890", "DuckDB", "TPC-H") == ("duckdb", "tpch")
    assert template_cell("tpch_tuned.yaml:abcdef1234567890", None, None) is None
    assert template_cell("my_custom.yaml", "duckdb", "tpch") is None


def test_evidence_state_for_run_matches_console_warning_inputs():
    assert (
        evidence_state_for_run(
            "examples/tunings/duckdb/tpch_tuned.yaml",
            "DuckDB",
            "TPC-H",
            tunings_applied={"table_tunings": {}},
        )
        == "unmeasured"
    )
    assert (
        evidence_state_for_run(
            "examples/tunings/duckdb/tpch_tuned.yaml",
            "DuckDB",
            "TPC-H",
            tunings_applied=None,
        )
        is None
    )
