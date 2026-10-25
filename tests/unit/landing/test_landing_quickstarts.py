from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "generate_landing_quickstarts.py"
CATALOG_PATH = REPO_ROOT / "landing" / "prompts" / "catalog.yaml"
FORBIDDEN_JSON = REPO_ROOT / "landing" / "prompts" / "recipes.json"
KNOWN_PAID_PLATFORM_COST_CLASSES = {
    "athena": "paid_credits",
    "athena-spark": "paid_compute",
    "bigquery": "paid_credits",
    "clickhouse-cloud": "paid_compute",
    "databend": "paid_compute",
    "databricks": "paid_credits",
    "databricks-df": "paid_credits",
    "dataproc": "paid_compute",
    "dataproc-serverless": "paid_compute",
    "emr-serverless": "paid_compute",
    "fabric-lakehouse": "paid_compute",
    "fabric-spark": "paid_compute",
    "fabric_dw": "paid_compute",
    "firebolt": "paid_compute",
    "glue": "paid_compute",
    "motherduck": "paid_credits",
    "pg-duckdb": "paid_credits",
    "quanton": "paid_compute",
    "redshift": "paid_compute",
    "singlestore": "paid_compute",
    "snowflake": "paid_credits",
    "snowpark-connect": "paid_credits",
    "starburst": "paid_compute",
    "synapse": "paid_compute",
    "synapse-spark": "paid_compute",
}


def _load_generator():
    spec = importlib.util.spec_from_file_location("generate_landing_quickstarts", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("generate_landing_quickstarts", module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gen():
    return _load_generator()


@pytest.fixture(scope="module")
def catalog():
    with CATALOG_PATH.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.fixture(scope="module")
def browser_catalog(gen, catalog):
    return gen.build_prompt_catalog(catalog)


def _filter_platform_ids(catalog_payload, interface, deployment):
    return {
        platform["id"]
        for platform in catalog_payload["platforms"]
        if interface in platform["interfaces"] and deployment in platform["deployments"]
    }


pytestmark = [pytest.mark.fast, pytest.mark.unit]


def test_committed_catalog_validates(gen, catalog):
    assert gen.validate(catalog) == []


def test_platform_inclusion_list_is_rejected(gen, catalog):
    bad = copy.deepcopy(catalog)
    bad["platforms"] = [{"id": "duckdb", "deployments": ["local"], "interfaces": ["sql"]}]
    errors = gen.validate(bad)
    assert any("platforms[] is no longer supported" in e for e in errors)


def test_platform_overrides_apply_without_controlling_inclusion(gen, catalog):
    overridden = copy.deepcopy(catalog)
    overridden["platform_overrides"] = {
        "duckdb": {
            "label": "DuckDB Local Test Label",
            "safety_terms": {
                "dependency": "Custom dependency check.",
                "dry_run": "Custom dry-run check.",
                "no_secrets": "Custom secret handling.",
            },
        }
    }

    browser_catalog = gen.build_prompt_catalog(overridden)
    duckdb = next(platform for platform in browser_catalog["platforms"] if platform["id"] == "duckdb")

    assert "platform_overrides" not in browser_catalog
    assert {p["id"] for p in browser_catalog["platforms"]} == gen._supported_registry_platform_ids()
    assert duckdb["label"] == "DuckDB Local Test Label"
    assert duckdb["safety_terms"]["no_secrets"] == "Custom secret handling."


def test_unknown_platform_override_is_rejected(gen, catalog):
    bad = copy.deepcopy(catalog)
    bad["platform_overrides"] = {"definitely-not-a-platform": {"label": "Nope"}}
    errors = gen.validate(bad)
    assert any("platform_overrides['definitely-not-a-platform']" in e for e in errors)


def test_unknown_platform_override_key_is_rejected(gen, catalog):
    bad = copy.deepcopy(catalog)
    bad["platform_overrides"] = {"duckdb": {"interfaces": ["sql", "dataframe"]}}
    errors = gen.validate(bad)
    assert any("unsupported keys ['interfaces']" in e for e in errors)


def test_platform_override_cannot_control_filter_membership(gen, catalog):
    for key in ("deployments", "credential_deployments"):
        bad = copy.deepcopy(catalog)
        bad["platform_overrides"] = {"polars": {key: ["managed"]}}

        errors = gen.validate(bad)

        assert any(f"unsupported keys ['{key}']" in e for e in errors)


def test_malformed_platform_override_values_are_rejected(gen, catalog):
    bad = copy.deepcopy(catalog)
    bad["platform_overrides"] = {
        "duckdb": {
            "label": [],
            "dependency_check_platform": [],
            "safety_terms": {"dependency": [], "extra": "not allowed"},
        }
    }

    errors = gen.validate(bad)

    assert any(".label: must be a non-empty string" in e for e in errors)
    assert any(".dependency_check_platform: must be a non-empty string" in e for e in errors)
    assert any("safety_terms['dependency']: must be a non-empty string" in e for e in errors)
    assert any("safety_terms['extra']: must be one of" in e for e in errors)


def test_cost_class_override_constrained_to_enum(gen, catalog):
    good = copy.deepcopy(catalog)
    good["platform_overrides"] = {"motherduck": {"cost_class": "free"}}
    assert gen.validate(good) == []
    motherduck = next(
        platform for platform in gen.build_prompt_catalog(good)["platforms"] if platform["id"] == "motherduck"
    )
    assert motherduck["cost_class"] == "free"

    bad = copy.deepcopy(catalog)
    bad["platform_overrides"] = {"motherduck": {"cost_class": "paid_magic"}}
    errors = gen.validate(bad)
    assert any("cost_class: must be one of" in e for e in errors)


def test_platform_option_hints_validate_shape_and_flag(gen, catalog):
    bad = copy.deepcopy(catalog)
    bad["platform_overrides"] = {
        "duckdb": {
            "platform_option_hints": [
                "--port 9030",
                "",
                42,
            ]
        }
    }

    errors = gen.validate(bad)

    assert any("platform_option_hints[0]: must contain --platform-option" in e for e in errors)
    assert any("platform_option_hints[1]: must be a non-empty string" in e for e in errors)
    assert any("platform_option_hints[2]: must be a non-empty string" in e for e in errors)


def test_platform_option_hints_apply_only_to_promptable_platforms(gen, catalog):
    bad = copy.deepcopy(catalog)
    bad["platform_overrides"] = {
        "definitely-not-a-platform": {"platform_option_hints": ["--platform-option port=9030"]}
    }

    errors = gen.validate(bad)

    assert any("unknown or non-promptable platform id" in e for e in errors)


def test_bundled_dsdgen_metadata_validates_only_for_tpcds(gen, catalog):
    bad = copy.deepcopy(catalog)
    bad["benchmarks"] = copy.deepcopy(catalog["benchmarks"])
    bad["benchmarks"][0]["requires_bundled_dsdgen_below"] = "1.0"
    bad["benchmarks"][1]["requires_bundled_dsdgen_below"] = "0.2"

    errors = gen.validate(bad)

    assert any("benchmarks['tpch'].requires_bundled_dsdgen_below: only tpcds" in e for e in errors)
    assert any("benchmarks['tpcds'].requires_bundled_dsdgen_below: must match" in e for e in errors)


def test_bundled_dsdgen_metadata_requires_string_float(gen, catalog):
    bad = copy.deepcopy(catalog)
    bad["benchmarks"] = copy.deepcopy(catalog["benchmarks"])
    bad["benchmarks"][1]["requires_bundled_dsdgen_below"] = "not-a-float"

    errors = gen.validate(bad)

    assert any("requires_bundled_dsdgen_below: must be parseable as a float" in e for e in errors)

    bad = copy.deepcopy(catalog)
    bad["benchmarks"] = copy.deepcopy(catalog["benchmarks"])
    bad["benchmarks"][1]["requires_bundled_dsdgen_below"] = 1.0
    errors = gen.validate(bad)
    assert any("requires_bundled_dsdgen_below: must be a string present in scales[]" in e for e in errors)


def test_unknown_benchmark_id_is_rejected(gen, catalog):
    bad = copy.deepcopy(catalog)
    bad["benchmarks"].append({"id": "definitely-not-a-benchmark"})
    errors = gen.validate(bad)
    assert any("definitely-not-a-benchmark" in e for e in errors)


def test_invalid_deployment_mode_is_rejected(gen, catalog):
    bad = copy.deepcopy(catalog)
    bad["deployments"].append({"id": "server", "label": "Server"})
    errors = gen.validate(bad)
    assert any("'server'" in e for e in errors)


def test_unknown_mcp_tool_is_rejected(gen, catalog):
    bad = copy.deepcopy(catalog)
    bad["mcp"]["run_tool"] = "tool_that_does_not_exist"
    errors = gen.validate(bad)
    assert any("tool_that_does_not_exist" in e for e in errors)

    for key in ("analysis_tool", "system_profile_tool", "plan_tool"):
        bad = copy.deepcopy(catalog)
        bad["mcp"][key] = "tool_that_does_not_exist"
        errors = gen.validate(bad)
        assert any(f"mcp.{key}" in e and "tool_that_does_not_exist" in e for e in errors)


def test_unknown_mcp_prompt_is_rejected(gen, catalog):
    bad = copy.deepcopy(catalog)
    bad["mcp"]["prompts"]["benchmark_run"] = "prompt_that_does_not_exist"
    errors = gen.validate(bad)
    assert any("prompt_that_does_not_exist" in e for e in errors)


def test_mcp_prompt_catalog_matches_registered_prompt_surface(catalog):
    from benchbox.mcp import create_server
    from tests.unit.mcp.public_api import list_prompt_names

    configured = catalog["mcp"]["prompts"]
    assert set(configured) == {"analyze_results", "benchmark_run", "compare_platforms"}
    assert set(configured.values()) <= list_prompt_names(create_server())


def test_credential_platforms_declare_safety_terms(browser_catalog):
    for platform in browser_catalog["platforms"]:
        if platform.get("credential_deployments"):
            safety = platform.get("safety_terms") or {}
            assert {"dependency", "dry_run", "no_secrets"} <= set(safety), platform["id"]


def test_compare_cli_template_uses_two_platform_flags(gen, catalog):
    compare = catalog["templates"]["cli"]["compare"]
    templates = catalog["templates"]["cli"]

    assert compare.count("--platform ") >= 2, compare
    assert templates["test_one"] == (
        "uv run benchbox run --platform {platform} --benchmark {benchmark} --scale {scale} --non-interactive"
    )
    assert templates["compare"] == (
        "uv run benchbox compare --platform {platform_a} --platform {platform_b} "
        "--benchmark {benchmark} --scale {scale} --non-interactive"
    )
    assert templates["dependency_check"] == "uv run benchbox check-deps --platform {platform}"
    assert templates["dry_run"] == (
        "uv run benchbox run --dry-run {dry_run_dir} --platform {platform} --benchmark {benchmark} --scale {scale}"
    )

    bad = copy.deepcopy(catalog)
    bad["templates"]["cli"]["compare"] = "uv run benchbox compare -p {platform_a} -b {benchmark}"
    bad["templates"]["cli"]["test_one"] = "uv run benchbox run -p {platform} -b {benchmark} -s {scale}"
    bad["templates"]["cli"]["dependency_check"] = "uv run benchbox check-dependencies {platform}"
    bad["templates"]["cli"]["dry_run"] = "uv run benchbox run -p {platform} -b {benchmark} -s {scale} --dry-run"
    errors = gen.validate(bad)
    assert any("two --platform flags" in e for e in errors)
    assert any("test_one must use long run flags" in e for e in errors)
    assert any("dependency_check must call check-deps --platform" in e for e in errors)
    assert any("dry_run must use long run flags" in e for e in errors)
    assert any("dry_run must include an explicit dry-run output dir" in e for e in errors)


def test_test_one_template_uses_non_interactive(gen, catalog):
    templates = catalog["templates"]["cli"]
    assert " --non-interactive" in templates["test_one"]

    bad = copy.deepcopy(catalog)
    bad["templates"]["cli"]["test_one"] = bad["templates"]["cli"]["test_one"].replace(" --non-interactive", "")
    errors = gen.validate(bad)
    assert any("test_one must include --non-interactive" in e for e in errors)


def test_compare_template_uses_non_interactive(gen, catalog):
    templates = catalog["templates"]["cli"]
    assert " --non-interactive" in templates["compare"]

    bad = copy.deepcopy(catalog)
    bad["templates"]["cli"]["compare"] = bad["templates"]["cli"]["compare"].replace(" --non-interactive", "")
    errors = gen.validate(bad)
    assert any("compare must include --non-interactive" in e for e in errors)


def test_results_paths_template_present(gen, catalog):
    templates = catalog["templates"]["cli"]
    assert "benchbox results --paths" in templates["results_paths"]

    bad = copy.deepcopy(catalog)
    bad["templates"]["cli"]["results_paths"] = "uv run benchbox results list"
    errors = gen.validate(bad)
    assert any("results_paths must call benchbox results --paths" in e for e in errors)


def test_show_cli_template_present(gen, catalog):
    templates = catalog["templates"]["cli"]
    assert "benchbox results show-cli" in templates["show_cli"]

    bad = copy.deepcopy(catalog)
    bad["templates"]["cli"]["show_cli"] = "uv run benchbox report <result-json>"
    errors = gen.validate(bad)
    assert any("show_cli must call benchbox results show-cli" in e for e in errors)


def test_provenance_snapshot_present_in_templates(catalog):
    templates = catalog["templates"]["cli"]
    assert "provenance_snapshot" in templates
    assert "<bundle-dir>/_provenance" in templates["provenance_snapshot"]


def test_provenance_snapshot_includes_version_and_profile_commands(gen, catalog):
    templates = catalog["templates"]["cli"]
    assert "benchbox --version-json" in templates["provenance_snapshot"]
    assert "benchbox profile" in templates["provenance_snapshot"]

    bad = copy.deepcopy(catalog)
    bad["templates"]["cli"]["provenance_snapshot"] = "mkdir -p <bundle-dir>/_provenance"
    errors = gen.validate(bad)
    assert any("provenance_snapshot must call benchbox --version-json" in e for e in errors)
    assert any("provenance_snapshot must call benchbox profile" in e for e in errors)


def test_capture_plans_footnote_present(gen, catalog):
    templates = catalog["templates"]["cli"]
    assert "--capture-plans" in templates["capture_plans_footer"]
    assert "before `2>&1 | tee" in templates["capture_plans_footer"]
    assert "benchbox show-plan" in templates["capture_plans_footer"]
    assert "--run" in templates["capture_plans_footer"]
    assert "--query-id" in templates["capture_plans_footer"]

    bad = copy.deepcopy(catalog)
    bad["templates"]["cli"]["capture_plans_footer"] = "Capture plans separately."
    errors = gen.validate(bad)
    assert any("capture_plans_footer must mention --capture-plans" in e for e in errors)
    assert any("capture_plans_footer must say --capture-plans goes before the tee pipeline" in e for e in errors)
    assert any("capture_plans_footer must mention benchbox show-plan" in e for e in errors)
    assert any("capture_plans_footer must use show-plan --run and --query-id" in e for e in errors)

    bad = copy.deepcopy(catalog)
    bad["templates"]["cli"]["capture_plans_footer"] = (
        "To capture EXPLAIN plans, add `--capture-plans` to the live command and inspect with "
        "`benchbox show-plan --run <result-json> --query-id Q1`."
    )
    errors = gen.validate(bad)
    assert errors == ["templates.cli.capture_plans_footer must say --capture-plans goes before the tee pipeline"]


def test_runtime_hints_well_formed(gen, catalog):
    hints = catalog["runtime_hints"]
    assert set(hints) == {"log_dir", "log_slug_template", "long_run_threshold_scale"}
    assert isinstance(hints["log_dir"], str)
    assert isinstance(hints["log_slug_template"], str)
    assert isinstance(hints["long_run_threshold_scale"], str)
    assert hints["long_run_threshold_scale"] in catalog["scales"]

    bad = copy.deepcopy(catalog)
    bad["runtime_hints"]["long_run_threshold_scale"] = 0.1
    errors = gen.validate(bad)
    assert any("long_run_threshold_scale must be a string present in scales[]" in e for e in errors)

    bad = copy.deepcopy(catalog)
    bad["runtime_hints"]["log_slug_template"] = 123
    errors = gen.validate(bad)
    assert any("runtime_hints.log_slug_template must be a non-empty string" in e for e in errors)

    bad = copy.deepcopy(catalog)
    bad["runtime_hints"]["log_slug_template"] = ""
    errors = gen.validate(bad)
    assert any("runtime_hints.log_slug_template must be a non-empty string" in e for e in errors)

    bad = copy.deepcopy(catalog)
    bad["runtime_hints"] = []
    errors = gen.validate(bad)
    assert any("runtime_hints must be a mapping" in e for e in errors)


def test_runtime_hints_log_dir_under_tmp(gen, catalog):
    assert catalog["runtime_hints"]["log_dir"].startswith("/tmp")

    bad = copy.deepcopy(catalog)
    bad["runtime_hints"]["log_dir"] = "/var/log"
    errors = gen.validate(bad)
    assert any("runtime_hints.log_dir must start with '/tmp'" in e for e in errors)


def test_defaults_resolve_to_known_ids(browser_catalog):
    platform_ids = {p["id"] for p in browser_catalog["platforms"]}
    benchmark_ids = {b["id"] for b in browser_catalog["benchmarks"]}
    goal_ids = {g["id"] for g in browser_catalog["goals"]}
    surface_ids = {s["id"] for s in browser_catalog["surfaces"]}
    interface_ids = {i["id"] for i in browser_catalog["interfaces"]}
    deployment_ids = {d["id"] for d in browser_catalog["deployments"]}
    platform = next(p for p in browser_catalog["platforms"] if p["id"] == browser_catalog["defaults"]["platform"])
    benchmark = next(b for b in browser_catalog["benchmarks"] if b["id"] == browser_catalog["defaults"]["benchmark"])
    catalog = browser_catalog
    assert catalog["defaults"]["goal"] in goal_ids
    assert catalog["defaults"]["surface"] in surface_ids
    assert catalog["defaults"]["interface"] in interface_ids
    assert catalog["defaults"]["platform"] in platform_ids
    assert catalog["defaults"]["benchmark"] in benchmark_ids
    assert catalog["defaults"]["deployment"] in deployment_ids
    assert catalog["defaults"]["scale"] in catalog["scales"]
    assert catalog["defaults"]["interface"] in platform["interfaces"]
    assert catalog["defaults"]["deployment"] in platform["deployments"]
    assert catalog["defaults"]["interface"] in benchmark["interfaces"]
    assert all(isinstance(scale, str) for scale in catalog["scales"])
    assert "0.01" in catalog["scales"]
    assert "10.0" in catalog["scales"]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("goal", "definitely-not-a-goal", "defaults.goal"),
        ("surface", "definitely-not-a-surface", "defaults.surface"),
        ("interface", "definitely-not-an-interface", "defaults.interface"),
        ("scale", "999.0", "defaults.scale"),
    ],
)
def test_invalid_default_selector_values_are_rejected(gen, catalog, field, value, message):
    bad = copy.deepcopy(catalog)
    bad["defaults"][field] = value

    errors = gen.validate(bad)

    assert any(message in e for e in errors)


def test_default_selection_must_match_platform_and_benchmark_interfaces(gen, catalog):
    bad = copy.deepcopy(catalog)
    bad["defaults"]["interface"] = "dataframe"
    bad["defaults"]["platform"] = "duckdb"
    bad["defaults"]["benchmark"] = "clickbench"

    errors = gen.validate(bad)

    assert any("defaults.platform='duckdb'" in e for e in errors)
    assert any("defaults.benchmark='clickbench'" in e for e in errors)


def test_default_selection_must_match_platform_deployment(gen, catalog):
    bad = copy.deepcopy(catalog)
    bad["defaults"]["deployment"] = "managed"
    bad["defaults"]["platform"] = "duckdb"

    errors = gen.validate(bad)

    assert any("defaults.deployment='managed'" in e and "defaults.platform='duckdb'" in e for e in errors)


def test_agent_catalog_is_removed(catalog):
    assert "agents" not in catalog
    assert "agent" not in catalog["defaults"]


def test_agent_catalog_is_rejected(gen, catalog):
    bad = copy.deepcopy(catalog)
    bad["agents"] = [{"id": "codex", "label": "Codex"}]
    errors = gen.validate(bad)
    assert any("agents[] is no longer supported" in e for e in errors)


def test_agent_default_is_rejected(gen, catalog):
    bad = copy.deepcopy(catalog)
    bad["defaults"]["agent"] = "codex"
    errors = gen.validate(bad)
    assert any("defaults.agent is no longer supported" in e for e in errors)


def test_no_recipes_json_committed():

    assert not FORBIDDEN_JSON.exists(), "landing/prompts/recipes.json is forbidden for MVP — see the decision record"


def test_generated_platforms_cover_registry_ids(gen, browser_catalog):
    generated_ids = {p["id"] for p in browser_catalog["platforms"]}
    expected_ids = gen._supported_registry_platform_ids()
    assert generated_ids == expected_ids


def test_generated_platforms_have_complete_filter_metadata(gen, browser_catalog):
    for platform in browser_catalog["platforms"]:
        assert platform["label"], platform["id"]
        assert platform["cost_class"] in gen.VALID_COST_CLASSES
        assert platform["interfaces"], platform["id"]
        assert platform["deployments"], platform["id"]
        assert set(platform["interfaces"]) <= {"sql", "dataframe"}
        assert set(platform["deployments"]) <= gen.VALID_DEPLOYMENT_MODES
        assert "remote" not in platform["deployments"]


def test_filter_pools_match_registry_capabilities(gen, browser_catalog):
    metadata_by_id = gen.PlatformRegistry.get_all_platform_metadata()
    for interface in ("sql", "dataframe"):
        for deployment in gen.VALID_DEPLOYMENT_MODES:
            expected = set()
            for platform_id, metadata in metadata_by_id.items():
                caps = gen.PlatformRegistry.get_platform_capabilities(platform_id)
                if not caps:
                    continue
                supports_interface = caps.supports_sql if interface == "sql" else caps.supports_dataframe
                if not supports_interface:
                    continue
                deployments, _credential_deployments = gen._derive_deployments(platform_id, metadata)
                if deployment in deployments:
                    expected.add(platform_id)

            assert _filter_platform_ids(browser_catalog, interface, deployment) == expected


def test_known_paid_platforms_carry_cost_class(browser_catalog):
    platform_by_id = {platform["id"]: platform for platform in browser_catalog["platforms"]}
    for platform_id, expected_cost_class in KNOWN_PAID_PLATFORM_COST_CLASSES.items():
        assert platform_by_id[platform_id]["cost_class"] == expected_cost_class

    assert platform_by_id["duckdb"]["cost_class"] == "free"
    assert platform_by_id["datafusion"]["cost_class"] == "free"
    assert platform_by_id["postgresql"]["cost_class"] == "free"
    assert platform_by_id["sqlite"]["cost_class"] == "free"


def test_non_ui_registry_deployment_modes_are_normalized(gen, browser_catalog):
    assert gen._deployment_from_registry_mode("remote", requires_network=True) == "self-hosted"
    assert gen._deployment_from_registry_mode("remote", requires_network=False) == "local"

    velox = next(platform for platform in browser_catalog["platforms"] if platform["id"] == "velox")
    assert "remote" not in velox["deployments"]
    assert "self-hosted" in velox["deployments"]


def test_generated_platforms_include_install_commands(browser_catalog):
    assert all(platform.get("install_command") for platform in browser_catalog["platforms"])
    duckdb = next(platform for platform in browser_catalog["platforms"] if platform["id"] == "duckdb")
    databricks = next(platform for platform in browser_catalog["platforms"] if platform["id"] == "databricks")
    fabric_dw = next(platform for platform in browser_catalog["platforms"] if platform["id"] == "fabric_dw")

    assert "dependency_check_command" not in duckdb
    assert databricks["dependency_check_platform"] == "databricks"
    assert databricks["dependency_check_command"] == "uv run benchbox check-deps --platform databricks"
    assert fabric_dw["dependency_check_platform"] == "fabric"
    assert fabric_dw["dependency_check_command"] == "uv run benchbox check-deps --platform fabric"


def test_duckdb_is_sql_only_in_prompt_catalog(browser_catalog):
    duckdb = next(platform for platform in browser_catalog["platforms"] if platform["id"] == "duckdb")
    assert duckdb["interfaces"] == ["sql"]


def test_self_hosted_platforms_show_credential_safety(browser_catalog):
    postgresql = next(platform for platform in browser_catalog["platforms"] if platform["id"] == "postgresql")
    assert "self-hosted" in postgresql["deployments"]
    assert "self-hosted" in postgresql["credential_deployments"]
    assert "Do NOT paste credentials in chat" in postgresql["safety_terms"]["no_secrets"]


def test_non_credentialed_remote_modes_do_not_show_credential_safety(browser_catalog):
    velox = next(platform for platform in browser_catalog["platforms"] if platform["id"] == "velox")
    assert velox["deployments"] == ["local", "self-hosted"]
    assert "credential_deployments" not in velox
    assert "safety_terms" not in velox


def test_spark_keeps_local_prompt_deployment_when_registry_modes_are_missing(gen, browser_catalog):
    caps = gen.PlatformRegistry.get_platform_capabilities("spark")
    assert caps is not None
    assert caps.deployment_modes == {}

    spark = next(platform for platform in browser_catalog["platforms"] if platform["id"] == "spark")
    assert spark["deployments"] == ["local", "self-hosted"]
    assert spark["credential_deployments"] == ["self-hosted"]
