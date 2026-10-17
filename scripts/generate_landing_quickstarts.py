# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License.

from __future__ import annotations

import argparse
import copy
import sys
from functools import cache
from pathlib import Path
from typing import Any

import yaml

from benchbox.core.benchmark_registry import BENCHMARK_METADATA
from benchbox.core.platform_registry import PlatformRegistry
from benchbox.utils.dependencies import list_available_dependency_groups

CLI_DESCRIPTION = "Validate landing/prompts/catalog.yaml, the source of the site's prompt catalog."

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE = REPO_ROOT / "landing" / "prompts" / "catalog.yaml"
FORBIDDEN_JSON = REPO_ROOT / "landing" / "prompts" / "recipes.json"

VALID_DEPLOYMENT_MODES = frozenset({"local", "self-hosted", "managed"})
VALID_COST_CLASSES = frozenset({"free", "paid_credits", "paid_compute"})
MANAGED_SAFETY_KEYS = frozenset({"dependency", "dry_run", "no_secrets"})
PLATFORM_OVERRIDE_KEYS = frozenset(
    {
        "cost_class",
        "dependency_check_command",
        "dependency_check_platform",
        "install_command",
        "label",
        "platform_option_hints",
        "safety_terms",
    }
)
STRING_PLATFORM_OVERRIDE_KEYS = frozenset(
    {"cost_class", "dependency_check_command", "dependency_check_platform", "install_command", "label"}
)
DEPLOYMENT_ORDER = {"local": 0, "self-hosted": 1, "managed": 2}
LOCAL_CATEGORIES = frozenset({"analytical", "dataframe", "embedded"})
CLOUD_HINTS = frozenset({"aws", "azure", "cloud", "gcs", "multi_cloud", "onelake", "s3", "saas", "serverless"})
SELF_HOSTED_CATEGORIES = frozenset({"distributed", "relational", "timeseries"})
LOCAL_EXECUTION_HINTS = frozenset({"local", "embedded", "in-process", "in_memory", "file_based"})
DEPENDENCY_CHECK_ALIASES = {
    "fabric-lakehouse": "fabric",
    "fabric_dw": "fabric",
}


def load_catalog(path: Path = SOURCE) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise SystemExit(f"catalog.yaml must be a mapping, got {type(data).__name__}")
    return data


def _known_mcp_names() -> tuple[frozenset[str], frozenset[str]]:
    mcp_dir = REPO_ROOT / "benchbox" / "mcp"
    tools: set[str] = set()
    prompts: set[str] = set()
    for py in mcp_dir.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        in_tools = "@mcp.tool" in text
        in_prompts = "@mcp.prompt" in text
        if not (in_tools or in_prompts):
            continue
        lines = text.splitlines()
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("@mcp.tool"):
                for follow in lines[i + 1 : i + 6]:
                    fs = follow.strip()
                    if fs.startswith("def "):
                        name = fs[4:].split("(", 1)[0]
                        tools.add(name)
                        break
            elif stripped.startswith("@mcp.prompt"):
                for follow in lines[i + 1 : i + 6]:
                    fs = follow.strip()
                    if fs.startswith("def "):
                        name = fs[4:].split("(", 1)[0]
                        prompts.add(name)
                        break
    return frozenset(tools), frozenset(prompts)


def _ordered(values: set[str]) -> list[str]:
    return sorted(values, key=lambda value: (DEPLOYMENT_ORDER[value], value))


def _is_cloud_like(metadata: dict[str, Any]) -> bool:
    supports = set(metadata.get("supports") or [])
    return metadata.get("category") == "cloud" or bool(supports & CLOUD_HINTS)


def _has_local_execution_hint(metadata: dict[str, Any]) -> bool:
    supports = {str(value).lower() for value in metadata.get("supports") or []}
    if supports & LOCAL_EXECUTION_HINTS:
        return True
    text = " ".join(str(metadata.get(key) or "").lower() for key in ("description", "notes"))
    return any(hint.replace("_", " ") in text for hint in LOCAL_EXECUTION_HINTS)


def _infer_default_deployments(metadata: dict[str, Any]) -> tuple[set[str], set[str]]:
    category = metadata.get("category")
    supports = set(metadata.get("supports") or [])

    if _is_cloud_like(metadata):
        return {"managed"}, {"managed"}
    if category in SELF_HOSTED_CATEGORIES or "self-hosted" in supports:
        if _has_local_execution_hint(metadata):
            return {"local", "self-hosted"}, {"self-hosted"}
        return {"self-hosted"}, {"self-hosted"}
    if category in LOCAL_CATEGORIES:
        return {"local"}, set()
    return {"local"}, set()


def _deployment_from_registry_mode(mode: str, requires_network: bool) -> str:
    if mode in VALID_DEPLOYMENT_MODES:
        return mode
    if mode == "remote":
        return "self-hosted" if requires_network else "local"
    raise ValueError(f"unsupported registry deployment mode {mode!r}")


def _derive_deployments(platform_id: str, metadata: dict[str, Any]) -> tuple[list[str], list[str]]:
    caps = PlatformRegistry.get_platform_capabilities(platform_id)
    if caps is None:
        return [], []
    if not caps.deployment_modes:
        deployments, credential_deployments = _infer_default_deployments(metadata)
        return _ordered(deployments), _ordered(credential_deployments)

    deployments: set[str] = set()
    credential_deployments: set[str] = set()
    for dep in caps.deployment_modes.values():
        deployment = _deployment_from_registry_mode(dep.mode, dep.requires_network)
        deployments.add(deployment)
        if dep.requires_credentials:
            credential_deployments.add(deployment)
    return _ordered(deployments), _ordered(credential_deployments)


def _derive_interfaces(platform_id: str) -> list[str]:
    caps = PlatformRegistry.get_platform_capabilities(platform_id)
    if caps is None:
        return []
    interfaces: list[str] = []
    if caps.supports_sql:
        interfaces.append("sql")
    if caps.supports_dataframe:
        interfaces.append("dataframe")
    return interfaces


def _platform_label(metadata: dict[str, Any], platform_id: str) -> str:
    return str(metadata.get("display_name") or platform_id)


def _platform_safety_terms(platform_id: str, credential_deployments: list[str]) -> dict[str, str] | None:
    if not credential_deployments:
        return None
    return {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. "
        "Do NOT paste credentials in chat.",
    }


def _dependency_check_platform(platform_id: str) -> str | None:
    candidate = DEPENDENCY_CHECK_ALIASES.get(platform_id, platform_id)
    if candidate in _dependency_groups():
        return candidate
    return None


@cache
def _dependency_groups() -> dict[str, Any]:
    return list_available_dependency_groups()


def _apply_platform_override(entry: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    updated = copy.deepcopy(entry)
    for key in ("label", "install_command", "dependency_check_platform", "dependency_check_command", "cost_class"):
        if isinstance(override.get(key), str):
            updated[key] = override[key]
    if isinstance(override.get("safety_terms"), dict):
        updated["safety_terms"] = dict(override["safety_terms"])
    if isinstance(override.get("platform_option_hints"), list):
        updated["platform_option_hints"] = list(override["platform_option_hints"])
    if isinstance(override.get("dependency_check_platform"), str) and "dependency_check_command" not in override:
        platform = updated["dependency_check_platform"]
        updated["dependency_check_command"] = f"uv run benchbox check-deps --platform {platform}"
    return updated


def _platform_overrides(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    overrides = catalog.get("platform_overrides") or {}
    if not isinstance(overrides, dict):
        return {}
    return {str(pid): override for pid, override in overrides.items() if isinstance(override, dict)}


def _derive_platform_entries(catalog: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    metadata_by_id = PlatformRegistry.get_all_platform_metadata()
    overrides = _platform_overrides(catalog or {})
    for platform_id, metadata in sorted(metadata_by_id.items()):
        caps = PlatformRegistry.get_platform_capabilities(platform_id)
        if caps is None:
            continue
        interfaces = _derive_interfaces(platform_id)
        if not interfaces:
            continue
        deployments, credential_deployments = _derive_deployments(platform_id, metadata)
        if not deployments:
            continue
        entry: dict[str, Any] = {
            "id": platform_id,
            "cost_class": caps.cost_class,
            "install_command": metadata.get("installation_command") or f"uv add benchbox --extra {platform_id}",
            "label": _platform_label(metadata, platform_id),
            "deployments": deployments,
            "interfaces": interfaces,
        }
        dependency_check_platform = _dependency_check_platform(platform_id)
        if dependency_check_platform:
            entry["dependency_check_platform"] = dependency_check_platform
            entry["dependency_check_command"] = f"uv run benchbox check-deps --platform {dependency_check_platform}"
        if credential_deployments:
            entry["credential_deployments"] = credential_deployments
            safety_terms = _platform_safety_terms(platform_id, credential_deployments)
            if safety_terms:
                entry["safety_terms"] = safety_terms
        if platform_id in overrides:
            entry = _apply_platform_override(entry, overrides[platform_id])
        entries.append(entry)
    return entries


def build_prompt_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    expanded = copy.deepcopy(catalog)
    expanded.pop("platform_overrides", None)
    expanded["platforms"] = _derive_platform_entries(catalog)
    return expanded


def _validate_one_platform(entry: dict[str, Any], known_platforms: set[str]) -> list[str]:
    errors: list[str] = []
    pid = entry.get("id")
    if pid not in known_platforms:
        errors.append(f"platforms[{pid!r}]: unknown platform id (not in PlatformRegistry)")
    if not entry.get("label"):
        errors.append(f"platforms[{pid!r}]: non-empty label is required")
    if entry.get("cost_class") not in VALID_COST_CLASSES:
        errors.append(
            f"platforms[{pid!r}].cost_class={entry.get('cost_class')!r}: must be one of {sorted(VALID_COST_CLASSES)}"
        )
    if not entry.get("deployments"):
        errors.append(f"platforms[{pid!r}]: at least one deployment is required")
    for d in entry.get("deployments") or []:
        if d not in VALID_DEPLOYMENT_MODES:
            errors.append(f"platforms[{pid!r}].deployments={d!r}: must be one of {sorted(VALID_DEPLOYMENT_MODES)}")
    if "managed" in (entry.get("deployments") or []):
        safety = entry.get("safety_terms") or {}
        missing = MANAGED_SAFETY_KEYS - set(safety.keys())
        if missing:
            errors.append(
                f"platforms[{pid!r}]: managed deployment requires safety_terms "
                f"covering {sorted(MANAGED_SAFETY_KEYS)}; missing {sorted(missing)}"
            )
    for d in entry.get("credential_deployments") or []:
        if d not in entry.get("deployments", []):
            errors.append(f"platforms[{pid!r}].credential_deployments={d!r}: must be in deployments[]")
    ifaces = entry.get("interfaces") or []
    if not ifaces:
        errors.append(f"platforms[{pid!r}]: at least one interface is required")
    for iface in ifaces:
        if iface not in {"sql", "dataframe"}:
            errors.append(f"platforms[{pid!r}].interfaces={iface!r}: must be 'sql' or 'dataframe'")
    return errors


def _supported_registry_platform_ids() -> set[str]:
    supported: set[str] = set()
    for platform_id in PlatformRegistry.get_all_platform_metadata():
        caps = PlatformRegistry.get_platform_capabilities(platform_id)
        if caps and (caps.supports_sql or caps.supports_dataframe):
            supported.add(platform_id)
    return supported


def _validate_platform_option_hints(platform_id: str, hints: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(hints, list):
        return [f"platform_overrides[{platform_id!r}].platform_option_hints: must be a list"]
    for idx, hint in enumerate(hints):
        if not isinstance(hint, str) or not hint:
            errors.append(
                f"platform_overrides[{platform_id!r}].platform_option_hints[{idx}]: must be a non-empty string"
            )
        elif "--platform-option" not in hint:
            errors.append(
                f"platform_overrides[{platform_id!r}].platform_option_hints[{idx}]: must contain --platform-option"
            )
    return errors


def _validate_platform_overrides(catalog: dict[str, Any], known_platforms: set[str]) -> list[str]:
    errors: list[str] = []
    overrides = catalog.get("platform_overrides")
    if overrides is None:
        return errors
    if not isinstance(overrides, dict):
        return ["platform_overrides must be a mapping of PlatformRegistry id to override metadata"]
    for platform_id, override in overrides.items():
        if platform_id not in known_platforms:
            errors.append(f"platform_overrides[{platform_id!r}]: unknown or non-promptable platform id")
            continue
        if not isinstance(override, dict):
            errors.append(f"platform_overrides[{platform_id!r}]: override must be a mapping")
            continue
        unknown = set(override) - PLATFORM_OVERRIDE_KEYS
        if unknown:
            errors.append(
                f"platform_overrides[{platform_id!r}]: unsupported keys {sorted(unknown)}; "
                f"allowed keys are {sorted(PLATFORM_OVERRIDE_KEYS)}"
            )
        for key in sorted(STRING_PLATFORM_OVERRIDE_KEYS & set(override)):
            if not isinstance(override[key], str) or not override[key]:
                errors.append(f"platform_overrides[{platform_id!r}].{key}: must be a non-empty string")
        if "cost_class" in override and override["cost_class"] not in VALID_COST_CLASSES:
            errors.append(
                f"platform_overrides[{platform_id!r}].cost_class: must be one of {sorted(VALID_COST_CLASSES)}"
            )
        if "safety_terms" in override:
            safety = override["safety_terms"]
            if not isinstance(safety, dict):
                errors.append(f"platform_overrides[{platform_id!r}].safety_terms: must be a mapping")
            else:
                for safety_key, value in safety.items():
                    if safety_key not in MANAGED_SAFETY_KEYS:
                        errors.append(
                            f"platform_overrides[{platform_id!r}].safety_terms[{safety_key!r}]: "
                            f"must be one of {sorted(MANAGED_SAFETY_KEYS)}"
                        )
                    if not isinstance(value, str) or not value:
                        errors.append(
                            f"platform_overrides[{platform_id!r}].safety_terms[{safety_key!r}]: "
                            "must be a non-empty string"
                        )
        if "platform_option_hints" in override:
            errors.extend(_validate_platform_option_hints(platform_id, override["platform_option_hints"]))
    return errors


def _catalog_entry_ids(catalog: dict[str, Any], key: str) -> set[str]:
    return {entry["id"] for entry in catalog.get(key) or [] if isinstance(entry, dict) and "id" in entry}


def _validate_defaults(
    catalog: dict[str, Any],
    platforms: list[dict[str, Any]],
    benchmarks: list[dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    defaults = catalog.get("defaults") or {}
    platform_by_id = {p["id"]: p for p in platforms if isinstance(p, dict) and "id" in p}
    benchmark_by_id = {b["id"]: b for b in benchmarks if isinstance(b, dict) and "id" in b}

    goal_ids = _catalog_entry_ids(catalog, "goals")
    surface_ids = _catalog_entry_ids(catalog, "surfaces")
    interface_ids = _catalog_entry_ids(catalog, "interfaces")
    deployment_ids = _catalog_entry_ids(catalog, "deployments")
    scale_ids = set(catalog.get("scales") or [])

    if defaults.get("goal") not in goal_ids:
        errors.append(f"defaults.goal={defaults.get('goal')!r}: not in goals[]")
    if defaults.get("surface") not in surface_ids:
        errors.append(f"defaults.surface={defaults.get('surface')!r}: not in surfaces[]")
    if defaults.get("interface") not in interface_ids:
        errors.append(f"defaults.interface={defaults.get('interface')!r}: not in interfaces[]")
    if defaults.get("deployment") not in deployment_ids:
        errors.append(f"defaults.deployment={defaults.get('deployment')!r}: not in deployments[]")
    if defaults.get("scale") not in scale_ids:
        errors.append(f"defaults.scale={defaults.get('scale')!r}: not in scales[]")

    default_platform = platform_by_id.get(defaults.get("platform"))
    default_benchmark = benchmark_by_id.get(defaults.get("benchmark"))
    default_interface = defaults.get("interface")
    default_deployment = defaults.get("deployment")

    if default_platform is None:
        errors.append(f"defaults.platform={defaults.get('platform')!r}: not in platforms[]")
    else:
        if default_interface not in (default_platform.get("interfaces") or []):
            errors.append(
                f"defaults.interface={default_interface!r}: not supported by "
                f"defaults.platform={defaults.get('platform')!r}"
            )
        if default_deployment not in (default_platform.get("deployments") or []):
            errors.append(
                f"defaults.deployment={default_deployment!r}: not supported by "
                f"defaults.platform={defaults.get('platform')!r}"
            )

    if default_benchmark is None:
        errors.append(f"defaults.benchmark={defaults.get('benchmark')!r}: not in benchmarks[]")
    elif default_interface not in (default_benchmark.get("interfaces") or []):
        errors.append(
            f"defaults.interface={default_interface!r}: not supported by "
            f"defaults.benchmark={defaults.get('benchmark')!r}"
        )

    if defaults.get("deployment") not in VALID_DEPLOYMENT_MODES:
        errors.append(
            f"defaults.deployment={defaults.get('deployment')!r}: must be in {sorted(VALID_DEPLOYMENT_MODES)}"
        )
    return errors


def _validate_mcp(catalog: dict[str, Any], known_tools: frozenset[str], known_prompts: frozenset[str]) -> list[str]:
    errors: list[str] = []
    mcp = catalog.get("mcp") or {}
    for key in ("run_tool", "list_tool", "analysis_tool", "system_profile_tool", "plan_tool"):
        if mcp.get(key) and mcp[key] not in known_tools:
            errors.append(f"mcp.{key}={mcp[key]!r}: not a registered MCP tool")
    for key, name in (mcp.get("prompts") or {}).items():
        if name not in known_prompts:
            errors.append(f"mcp.prompts.{key}={name!r}: not a registered MCP prompt")
    return errors


def _validate_benchmarks(catalog: dict[str, Any], known_benchmarks: set[str]) -> list[str]:
    errors: list[str] = []
    scales = catalog.get("scales") or []
    scale_numbers = set()
    for scale in scales:
        try:
            scale_numbers.add(float(scale))
        except (TypeError, ValueError):
            pass

    for entry in catalog.get("benchmarks") or []:
        bid = entry.get("id")
        if bid not in known_benchmarks:
            errors.append(f"benchmarks[{bid!r}]: unknown benchmark id (not in BENCHMARK_METADATA)")
        if "requires_bundled_dsdgen_below" not in entry:
            continue

        threshold = entry["requires_bundled_dsdgen_below"]
        if bid != "tpcds":
            errors.append(f"benchmarks[{bid!r}].requires_bundled_dsdgen_below: only tpcds may declare bundled dsdgen")
        if not isinstance(threshold, str):
            errors.append(f"benchmarks[{bid!r}].requires_bundled_dsdgen_below: must be a string present in scales[]")
            continue
        try:
            threshold_number = float(threshold)
        except ValueError:
            errors.append(f"benchmarks[{bid!r}].requires_bundled_dsdgen_below: must be parseable as a float")
            continue
        if threshold_number not in scale_numbers:
            errors.append(f"benchmarks[{bid!r}].requires_bundled_dsdgen_below: must match a numeric value in scales[]")
    return errors


def _validate_agent_removed(catalog: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if "agents" in catalog:
        errors.append("agents[] is no longer supported; /prompts/ uses agent-agnostic copy")
    if "agent" in (catalog.get("defaults") or {}):
        errors.append("defaults.agent is no longer supported; /prompts/ uses agent-agnostic copy")
    return errors


def _validate_platforms_are_derived(catalog: dict[str, Any]) -> list[str]:
    if "platforms" not in catalog:
        return []
    return ["platforms[] is no longer supported in catalog.yaml; prompt platforms are derived from PlatformRegistry"]


def _validate_provenance_templates(provenance_tpl: str, capture_plans_footer: str) -> list[str]:
    errors: list[str] = []
    if "benchbox --version-json" not in provenance_tpl:
        errors.append("templates.cli.provenance_snapshot must call benchbox --version-json")
    if "benchbox profile" not in provenance_tpl:
        errors.append("templates.cli.provenance_snapshot must call benchbox profile")
    if "--capture-plans" not in capture_plans_footer:
        errors.append("templates.cli.capture_plans_footer must mention --capture-plans")
    if "before `2>&1 | tee" not in capture_plans_footer:
        errors.append("templates.cli.capture_plans_footer must say --capture-plans goes before the tee pipeline")
    if "benchbox show-plan" not in capture_plans_footer:
        errors.append("templates.cli.capture_plans_footer must mention benchbox show-plan")
    if "--run" not in capture_plans_footer or "--query-id" not in capture_plans_footer:
        errors.append("templates.cli.capture_plans_footer must use show-plan --run and --query-id")
    return errors


def _validate_cli_templates_and_runtime_hints(catalog: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    cli_templates = (catalog.get("templates") or {}).get("cli") or {}
    compare_tpl = cli_templates.get("compare") or ""
    if compare_tpl.count("--platform ") < 2:
        errors.append(f"templates.cli.compare must include two --platform flags (got: {compare_tpl!r})")

    test_one_tpl = cli_templates.get("test_one") or ""
    dry_run_tpl = cli_templates.get("dry_run") or ""
    dependency_tpl = cli_templates.get("dependency_check") or ""
    results_paths_tpl = cli_templates.get("results_paths") or ""
    show_cli_tpl = cli_templates.get("show_cli") or ""
    provenance_tpl = cli_templates.get("provenance_snapshot") or ""
    capture_plans_footer = cli_templates.get("capture_plans_footer") or ""
    runtime_hints_raw = catalog.get("runtime_hints")
    if runtime_hints_raw is None:
        runtime_hints_raw = {}
    runtime_hints = runtime_hints_raw if isinstance(runtime_hints_raw, dict) else {}
    if " -p " in test_one_tpl or " -b " in test_one_tpl or " -s " in test_one_tpl:
        errors.append(f"templates.cli.test_one must use long run flags (got: {test_one_tpl!r})")
    if " --non-interactive" not in test_one_tpl:
        errors.append(f"templates.cli.test_one must include --non-interactive (got: {test_one_tpl!r})")
    if " --non-interactive" not in compare_tpl:
        errors.append(f"templates.cli.compare must include --non-interactive (got: {compare_tpl!r})")
    if " -p " in dry_run_tpl or " -b " in dry_run_tpl or " -s " in dry_run_tpl:
        errors.append(f"templates.cli.dry_run must use long run flags (got: {dry_run_tpl!r})")
    if "--dry-run {dry_run_dir}" not in dry_run_tpl:
        errors.append(f"templates.cli.dry_run must include an explicit dry-run output dir (got: {dry_run_tpl!r})")
    if dependency_tpl != "uv run benchbox check-deps --platform {platform}":
        errors.append(f"templates.cli.dependency_check must call check-deps --platform (got: {dependency_tpl!r})")
    if "benchbox results --paths" not in results_paths_tpl:
        errors.append(f"templates.cli.results_paths must call benchbox results --paths (got: {results_paths_tpl!r})")
    if "benchbox results show-cli" not in show_cli_tpl:
        errors.append(f"templates.cli.show_cli must call benchbox results show-cli (got: {show_cli_tpl!r})")
    errors.extend(_validate_provenance_templates(provenance_tpl, capture_plans_footer))

    if not isinstance(runtime_hints_raw, dict):
        errors.append("runtime_hints must be a mapping")
    threshold_scale = runtime_hints.get("long_run_threshold_scale")
    if not isinstance(threshold_scale, str) or threshold_scale not in set(catalog.get("scales") or []):
        errors.append("runtime_hints.long_run_threshold_scale must be a string present in scales[]")
    log_dir = runtime_hints.get("log_dir")
    if not isinstance(log_dir, str) or not log_dir.startswith("/tmp"):
        errors.append("runtime_hints.log_dir must start with '/tmp'")
    log_slug_template = runtime_hints.get("log_slug_template")
    if not isinstance(log_slug_template, str) or not log_slug_template:
        errors.append("runtime_hints.log_slug_template must be a non-empty string")
    return errors


def validate(catalog: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    errors.extend(_validate_platforms_are_derived(catalog))
    known_platforms = _supported_registry_platform_ids()
    known_benchmarks = set(BENCHMARK_METADATA.keys())
    known_tools, known_prompts = _known_mcp_names()
    errors.extend(_validate_platform_overrides(catalog, known_platforms))
    browser_catalog = build_prompt_catalog(catalog)

    platforms = browser_catalog.get("platforms") or []
    for entry in platforms:
        errors.extend(_validate_one_platform(entry, known_platforms))

    benchmarks = catalog.get("benchmarks") or []
    errors.extend(_validate_benchmarks(catalog, known_benchmarks))

    for d in catalog.get("deployments") or []:
        did = d.get("id") if isinstance(d, dict) else None
        if did not in VALID_DEPLOYMENT_MODES:
            errors.append(f"deployments[{did!r}]: must be in {sorted(VALID_DEPLOYMENT_MODES)}")

    errors.extend(_validate_defaults(catalog, platforms, benchmarks))
    errors.extend(_validate_agent_removed(catalog))
    errors.extend(_validate_mcp(catalog, known_tools, known_prompts))
    errors.extend(_validate_cli_templates_and_runtime_hints(catalog))

    if FORBIDDEN_JSON.exists():
        errors.append(f"forbidden file present: {FORBIDDEN_JSON.relative_to(REPO_ROOT)}")

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument(
        "--source",
        type=Path,
        default=SOURCE,
        help="Path to catalog.yaml (default: landing/prompts/catalog.yaml).",
    )
    args = parser.parse_args(argv)

    errors = validate(load_catalog(args.source))
    for error in errors:
        print(f"validation error: {error}", file=sys.stderr)
    if errors:
        return 2
    print("catalog: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
