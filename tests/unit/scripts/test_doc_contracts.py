from __future__ import annotations

import copy
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("mcp", reason="MCP SDK not installed. Install with: uv add benchbox --extra mcp")

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).resolve().parents[3]

if not (ROOT / "_project/design/api-public-symbols.json").exists():
    pytest.skip("the documentation contract tooling needs the development tree (release tree)", allow_module_level=True)


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


doc_contracts = _load("doc_contracts")
inventory = _load("doc_reference_inventory")
ci_units = _load("ci_units")


@pytest.fixture(scope="module")
def contracts() -> dict[str, Any]:
    return doc_contracts.build_contracts()


def test_contracts_validate_against_the_checked_in_schema(contracts: dict[str, Any]) -> None:
    assert doc_contracts.validate_contracts(contracts) == []


def test_two_builds_are_byte_identical(contracts: dict[str, Any]) -> None:
    assert doc_contracts.render_contracts(contracts) == doc_contracts.render_contracts()


def test_write_contracts_round_trips_to_the_rendered_text(tmp_path: Path) -> None:
    written = doc_contracts.write_contracts(tmp_path / "nested" / "doc-contracts.json")
    assert written.read_text(encoding="utf-8") == doc_contracts.render_contracts()


def test_schema_rejects_a_missing_section(contracts: dict[str, Any]) -> None:
    broken = copy.deepcopy(contracts)
    del broken["mcp"]
    assert any("'mcp' is a required property" in error for error in doc_contracts.validate_contracts(broken))


def test_schema_rejects_a_wrong_type_and_an_unknown_field(contracts: dict[str, Any]) -> None:
    broken = copy.deepcopy(contracts)
    broken["platforms"]["count_summary"]["total"] = "many"
    broken["package"]["unreviewed"] = True
    errors = doc_contracts.validate_contracts(broken)
    assert any("platforms/count_summary/total" in error for error in errors)
    assert any("unreviewed" in error for error in errors)


def test_check_flag_passes_for_the_live_tree(capsys: pytest.CaptureFixture[str]) -> None:
    assert doc_contracts.main(["--check"]) == 0
    assert "deterministic, schema-valid" in capsys.readouterr().out


def test_registry_facts_equal_the_live_registries(contracts: dict[str, Any]) -> None:
    from benchbox.core.benchmark_registry import list_loader_benchmark_ids, list_public_benchmark_ids
    from benchbox.core.platform_registry import PlatformRegistry
    from benchbox.core.results.schema import SCHEMA_VERSION

    assert contracts["benchmarks"]["public_ids"] == sorted(list_public_benchmark_ids())
    assert contracts["benchmarks"]["loader_ids"] == sorted(list_loader_benchmark_ids())
    assert contracts["platforms"]["count_summary"] == PlatformRegistry.get_platform_count_summary()
    assert contracts["result_schema_version"] == str(SCHEMA_VERSION)
    assert len(contracts["platforms"]["entries"]) == contracts["platforms"]["count_summary"]["total"]


def test_support_status_snapshot_sums_to_the_registry_counts(contracts: dict[str, Any]) -> None:
    snapshot = contracts["platforms"]["support_status_snapshot"]
    summary = contracts["platforms"]["count_summary"]
    assert {status: row["entries"] for status, row in snapshot.items()} == summary["support_status"]
    assert sum(row["entries"] for row in snapshot.values()) == summary["total"]


def test_benchmark_facts_are_internally_consistent(contracts: dict[str, Any]) -> None:
    benchmarks = contracts["benchmarks"]
    assert set(benchmarks["support_status"]) == set(benchmarks["ids"])
    assert set(benchmarks["num_queries"]) == set(benchmarks["ids"])
    assert set(benchmarks["public_ids"]) | set(benchmarks["core_only_ids"]) >= set(benchmarks["public_ids"])
    assert benchmarks["facades"]["top_level"] == benchmarks["facades"]["lazy"] + benchmarks["facades"]["eager"]
    assert benchmarks["registry_summary"]["public"] == len(benchmarks["public_ids"])


def test_platform_option_kinds_capture_the_hook_registry(contracts: dict[str, Any]) -> None:
    databend = contracts["platform_options"]["platforms"]["databend"]
    assert databend["disable_result_cache"]["value_kind"] == "boolean"
    assert databend["compute_resource"]["aliases"] == ["warehouse"]
    kinds = {
        spec["value_kind"]
        for options in contracts["platform_options"]["platforms"].values()
        for spec in options.values()
    }
    assert {"string", "boolean", "integer", "port"} <= kinds


def test_execution_vocabulary_facts(contracts: dict[str, Any]) -> None:
    execution = contracts["execution"]
    assert execution["default_engine"] == "default"
    assert execution["default_engine"] not in execution["engine_classes"]
    assert execution["engines"]["databricks"]["selectable"] == sorted(execution["engines"]["databricks"]["selectable"])
    assert execution["compute"]["databricks"]["resource_aliases"] == ["warehouse_id"]
    assert {"engine", "mode", "backend", "runtime", "executor", "warehouse"} == set(execution["reserved_option_keys"])


def test_cli_commands_expose_the_documented_surface(contracts: dict[str, Any]) -> None:
    by_path = {command["path"]: command for command in contracts["cli"]["commands"]}
    assert {"benchbox", "benchbox run", "benchbox setup", "benchbox results", "benchbox check-deps"} <= set(by_path)
    run_options = {name for option in by_path["benchbox run"]["options"] for name in option["names"]}
    assert {"--platform", "--benchmark", "--platform-option", "--dry-run"} <= run_options
    assert by_path["benchbox"]["is_group"] is True


def test_mcp_tools_cover_the_run_surface_data(contracts: dict[str, Any]) -> None:
    mcp = contracts["mcp"]
    surface = mcp["run_surface"]
    assert set(surface["tool_cli_counterparts"]) == set(mcp["tools"])
    assert set(mcp["tools"]["run_benchmark"]["params"]) == set(surface["run_parameter_cli_options"])
    assert set(surface["omission_tiers"].values()) <= set(surface["ratified_tiers"])
    assert set(surface["omitted_cli_families"].values()) <= set(surface["ratified_tiers"])
    assert set(surface["security_scoped_required"]) <= {
        option for option, tier in surface["omission_tiers"].items() if tier == "security-scoped"
    }
    assert "start_benchmark" in mcp["remote_only_tools"]
    assert not set(mcp["remote_only_tools"]) & set(mcp["tools"])


def test_collecting_mcp_facts_leaves_logging_quiet_mode_and_tracing_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    import logging

    from opentelemetry import trace

    from benchbox.utils.printing import is_quiet, set_quiet

    benchbox_logger = logging.getLogger("benchbox")
    transport_logger = logging.getLogger("mcp.server.transport_security")
    benchbox_logger.setLevel(logging.WARNING)
    benchbox_logger.handlers[:] = []
    monkeypatch.setattr(transport_logger, "filters", [])
    set_quiet(False)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9")

    def state() -> tuple[Any, ...]:
        return (
            benchbox_logger.level,
            list(benchbox_logger.handlers),
            benchbox_logger.propagate,
            benchbox_logger.disabled,
            list(transport_logger.filters),
            is_quiet(),
            trace.get_tracer_provider(),
        )

    before = state()
    doc_contracts._mcp()
    assert state() == before


def test_public_symbols_match_the_inventory_file(contracts: dict[str, Any]) -> None:
    source = json.loads((ROOT / "_project/design/api-public-symbols.json").read_text(encoding="utf-8"))
    assert [item["symbol"] for item in contracts["public_symbols"]["symbols"]] == sorted(
        item["symbol"] for item in source["symbols"]
    )


def test_sf1_facts_cover_the_baseline(contracts: dict[str, Any]) -> None:
    sf1 = contracts["sf1_calibrated_sizes"]
    assert set(sf1["sizes"]) <= set(sf1["measurable_benchmark_ids"])
    assert sf1["tolerance"] > 0


def test_write_primitive_bounds_are_ordered(contracts: dict[str, Any]) -> None:
    bounds = contracts["write_primitives"]["validation_bounds"]
    assert bounds
    for validations in bounds.values():
        for bound in validations.values():
            assert bound["max"] is None or bound["min"] <= bound["max"]


def test_tuning_verifiable_platforms_require_an_introspector_and_applied_types(contracts: dict[str, Any]) -> None:
    platforms = contracts["tuning"]["platforms"]
    verifiable = {name for name, row in platforms.items() if row["can_reach_applied_verified"]}
    assert verifiable
    for name in verifiable:
        assert platforms[name]["has_catalog_introspector"] and platforms[name]["applied_types"]


def test_platform_manifest_entries_match_the_registry_keys(contracts: dict[str, Any]) -> None:
    keys = [entry["key"] for entry in contracts["platform_manifest"]["entries"]]
    assert keys == sorted(keys)
    assert set(contracts["platforms"]["entries"]) <= set(keys)


def test_release_marker_pattern_matches_the_version_substitution_format(contracts: dict[str, Any]) -> None:
    marker = contracts["package"]["release_marker"]
    pattern = re.compile(marker["pattern"], marker["flags"])
    match = pattern.search("Current release: `v1.2.3`.")
    assert match is not None and match.group(marker["version_group"]) == "1.2.3"


def test_publication_facts_agree_with_the_publish_lists(contracts: dict[str, Any]) -> None:
    publication = contracts["publication"]
    assert set(publication["allowlist"]) <= set(publication["gated_pages"])
    assert {"agent", "internal"} <= set(publication["excluded_roots"])
    assert all(publication["linkcheck_ignore_patterns"])
    for pattern in publication["linkcheck_ignore_patterns"]:
        re.compile(pattern)


def test_ownership_facts_match_the_manifest(contracts: dict[str, Any]) -> None:
    manifest = json.loads((ROOT / "quality/docs-ownership.json").read_text(encoding="utf-8"))
    ownership = contracts["ownership"]
    assert ownership["counts"]["site-owned"] == len(ownership["site_owned"])
    assert sum(ownership["counts"].values()) == len(manifest["files"])
    assert set(ownership["transitions"]) == set(manifest["transitions"])


def test_every_lock_test_row_resolves_and_every_reference_is_dispositioned(contracts: dict[str, Any]) -> None:
    errors = inventory.check_lock_tests(contracts, json.loads(inventory.INVENTORY_PATH.read_text(encoding="utf-8")))
    assert errors == []


def test_reference_rule_contract_fields_exist(contracts: dict[str, Any]) -> None:
    rules = inventory.read_json(inventory.RULES_PATH)["rules"]
    missing = [
        (rule["id"], pointer)
        for rule in rules
        for pointer in rule.get("contract_fields", [])
        if not inventory.pointer_exists(contracts, pointer)
    ]
    assert missing == []


def test_run_surface_data_file_matches_the_retained_test_constants() -> None:
    data = json.loads((ROOT / "quality/mcp-run-surface.json").read_text(encoding="utf-8"))
    assert data["schema"] == 1
    assert set(data["omission_tiers"].values()) <= set(data["ratified_tiers"])


def _exporter_inputs(contracts: dict[str, Any]) -> set[str]:
    data_files = {
        doc_contracts.HELP_CATALOG,
        doc_contracts.API_SYMBOLS,
        doc_contracts.SF1_BASELINE,
        doc_contracts.RUN_SURFACE,
        doc_contracts.OWNERSHIP,
        doc_contracts.PUBLISH_ALLOWLIST,
        doc_contracts.PUBLISH_EXCLUSIONS,
        doc_contracts.LINKCHECK_IGNORE,
        doc_contracts.SCHEMA_PATH,
        ROOT / "pyproject.toml",
        ROOT / "scripts/doc_contracts.py",
        ROOT / "scripts/update_version.py",
        ROOT / "scripts/measure_sf1_sizes.py",
        ROOT / "scripts/generate_tuning_support_matrix.py",
    }
    modules = {
        Path(module.__file__).resolve()
        for module in list(sys.modules.values())
        if getattr(module, "__file__", None) and str(module.__file__).startswith(str(ROOT / "benchbox"))
    }
    assert contracts
    return {path.relative_to(ROOT).as_posix() for path in data_files | modules}


def test_every_file_the_exporter_reads_triggers_a_ci_check(contracts: dict[str, Any]) -> None:
    rules = ci_units.load_unit_rules(ROOT / ".github/ci-units.yml")
    uncovered = []
    for path in sorted(_exporter_inputs(contracts)):
        decision = ci_units.classify_units([path], rules)
        if not (decision["code_tests_needed"] or decision["units"]["docs"]):
            uncovered.append(path)
    assert uncovered == []


@pytest.mark.parametrize(
    "path",
    [
        "scripts/doc_contracts.py",
        "scripts/doc_reference_inventory.py",
        "quality/doc-contracts.schema.json",
        "quality/doc-lock-tests.json",
        "quality/docs-reference-inventory.json",
        "quality/docs-reference-rules.json",
        "quality/mcp-run-surface.json",
        "_project/design/api-public-symbols.json",
        "benchbox/core/platform_manifest.py",
        "benchbox/core/tuning/capability_registry.py",
        "benchbox/core/hooks/platform_hooks.py",
        "benchbox/mcp/server.py",
        "docs/publish-allowlist.txt",
    ],
)
def test_exporter_inputs_run_the_docs_unit(path: str) -> None:
    rules = ci_units.load_unit_rules(ROOT / ".github/ci-units.yml")
    assert ci_units.classify_units([path], rules)["units"]["docs"] is True


def test_the_contract_guard_runs_in_the_lint_job_and_the_local_mirror() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "run: make doc-contracts-guard" in workflow
    assert "$(MAKE) doc-contracts-guard" in makefile
