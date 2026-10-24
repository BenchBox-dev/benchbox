#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import logging
import os
import re
import sys
import tempfile
import tomllib
import warnings
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCHEMA_VERSION = 1
SCHEMA_PATH = ROOT / "quality/doc-contracts.schema.json"
DEFAULT_OUTPUT = Path("doc-contracts.json")
HELP_CATALOG = ROOT / "benchbox/data/cli_help_catalog.yaml"
API_SYMBOLS = ROOT / "_project/design/api-public-symbols.json"
SF1_BASELINE = ROOT / "tests/unit/generators/sf1_size_baseline.json"
RUN_SURFACE = ROOT / "quality/mcp-run-surface.json"
OWNERSHIP = ROOT / "quality/docs-ownership.json"
PUBLISH_ALLOWLIST = ROOT / "docs/publish-allowlist.txt"
PUBLISH_EXCLUSIONS = ROOT / "docs/publish-exclusions.txt"
LINKCHECK_IGNORE = ROOT / "docs/linkcheck_ignore.txt"
PUBLISH_LIST_ROOTS = ("development", "operations")
SF1_DOCUMENTED_WITHOUT_GENERATION = ("ai_primitives", "metadata_primitives")
SUPPORT_STATUSES = ("stable", "beta", "experimental", "repo_only", "deprecated", "document_only")


def _load_script(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(f"_doc_contracts_{name}", ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, list | tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, set | frozenset):
        return sorted(_json_value(item) for item in value)
    return str(value)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_list(path: Path) -> list[str]:
    if not path.is_file():
        return []
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def _package() -> dict[str, Any]:
    with open(ROOT / "pyproject.toml", "rb") as handle:
        project = tomllib.load(handle)["project"]
    import benchbox

    names = re.compile(r"[<>=!\[; ~]")
    update_version = _load_script("update_version")
    release = update_version.DOC_RELEASE_PATTERN
    return {
        "name": project["name"],
        "version": project["version"],
        "development_status_classifiers": sorted(
            c for c in project.get("classifiers", []) if "Development Status" in c
        ),
        "core_dependencies": sorted({names.split(spec, maxsplit=1)[0].strip() for spec in project["dependencies"]}),
        "optional_extras": sorted(project.get("optional-dependencies", {})),
        "console_scripts": sorted(project.get("scripts", {})),
        "experimental_exported": "experimental" in benchbox.__all__,
        "release_marker": {
            "pattern": release.pattern,
            "flags": int(release.flags),
            "version_group": "version",
        },
    }


def _benchmarks() -> dict[str, Any]:
    import benchbox
    from benchbox.base import BENCHMARK_API_SURFACE, RUN_WITH_PLATFORM_API_SURFACE
    from benchbox.core.base_benchmark import BENCHMARK_API_DECISION, BENCHMARK_API_SURFACE as CORE_API_SURFACE
    from benchbox.core.benchmark_loader import BENCHMARK_LOADER_API_SURFACE
    from benchbox.core.benchmark_registry import (
        BENCHMARK_CLASS_NAMES,
        BENCHMARK_DATA_SOURCE_PROBE_IDS,
        BENCHMARK_METADATA,
        get_benchmark_registry_summary,
        get_benchmark_support_status,
        list_benchmark_ids,
        list_loader_benchmark_ids,
        list_public_benchmark_ids,
    )
    from benchbox.core.results.benchmark_specs import BENCHMARK_SPECS

    public_ids = set(list_public_benchmark_ids())
    exported = set(BENCHMARK_CLASS_NAMES.values()) & set(benchbox.__all__)
    lazy = set(benchbox._BENCHMARK_REGISTRY) & exported
    return {
        "ids": sorted(list_benchmark_ids()),
        "public_ids": sorted(public_ids),
        "loader_ids": sorted(list_loader_benchmark_ids()),
        "core_only_ids": sorted(
            benchmark_id
            for benchmark_id, class_name in BENCHMARK_CLASS_NAMES.items()
            if class_name not in benchbox.__all__
        ),
        "spec_ids": sorted(set(BENCHMARK_SPECS) & public_ids),
        "support_status": {
            benchmark_id: get_benchmark_support_status(benchmark_id) for benchmark_id in sorted(BENCHMARK_METADATA)
        },
        "data_source": {
            benchmark_id: BENCHMARK_METADATA[benchmark_id].get("data_source")
            for benchmark_id in sorted(BENCHMARK_METADATA)
        },
        "num_queries": {
            benchmark_id: int(BENCHMARK_METADATA[benchmark_id]["num_queries"])
            for benchmark_id in sorted(BENCHMARK_METADATA)
        },
        "supports_dataframe": {
            benchmark_id: bool(BENCHMARK_METADATA[benchmark_id].get("supports_dataframe", False))
            for benchmark_id in sorted(BENCHMARK_METADATA)
        },
        "data_source_probe_ids": list(BENCHMARK_DATA_SOURCE_PROBE_IDS),
        "registry_summary": _json_value(get_benchmark_registry_summary()),
        "facades": {
            "top_level": len(exported),
            "lazy": len(lazy),
            "eager": len(exported - lazy),
        },
        "api_surfaces": {
            "public_base": BENCHMARK_API_SURFACE,
            "run_with_platform": RUN_WITH_PLATFORM_API_SURFACE,
            "core_base": CORE_API_SURFACE,
            "core_base_decision": BENCHMARK_API_DECISION,
            "loader": BENCHMARK_LOADER_API_SURFACE,
        },
    }


def _platforms() -> dict[str, Any]:
    from benchbox.core.platform_registry import PlatformRegistry

    metadata = PlatformRegistry.get_all_platform_metadata()
    entries: dict[str, Any] = {}
    for key in sorted(metadata):
        capabilities = metadata[key].get("capabilities", {})
        entries[key] = {
            "display_name": metadata[key].get("display_name", key),
            "category": metadata[key].get("category", "unknown"),
            "support_status": metadata[key]["support_status"],
            "supports_sql": bool(capabilities.get("supports_sql", False)),
            "supports_dataframe": bool(capabilities.get("supports_dataframe", False)),
            "requires_cloud_storage": PlatformRegistry.requires_cloud_storage(key),
        }
    snapshot: dict[str, dict[str, int]] = {}
    for status in SUPPORT_STATUSES:
        members = PlatformRegistry.get_platforms_by_support_status(status)
        snapshot[status] = {
            "entries": len(members),
            "sql": sum(1 for name in members if entries[name]["supports_sql"]),
            "dataframe": sum(1 for name in members if entries[name]["supports_dataframe"]),
        }
    return {
        "count_summary": _json_value(PlatformRegistry.get_platform_count_summary()),
        "support_status_values": list(SUPPORT_STATUSES),
        "support_status_snapshot": snapshot,
        "entries": entries,
    }


def _platform_manifest() -> dict[str, Any]:
    from benchbox.core.platform_manifest import PLATFORM_MANIFEST, get_all_platform_aliases

    entries = []
    for entry in sorted(PLATFORM_MANIFEST, key=lambda item: item.key):
        capabilities = dict(entry.metadata_dict()["capabilities"])
        entries.append(
            {
                "key": entry.key,
                "support_status": entry.support_status,
                "supports_sql": bool(capabilities.get("supports_sql")),
                "supports_dataframe": bool(capabilities.get("supports_dataframe")),
                "aliases": [
                    {"name": alias.name, "scopes": sorted(alias.scopes), "implied_mode": alias.implied_mode}
                    for alias in sorted(entry.aliases, key=lambda item: item.name)
                ],
                "adapter": None
                if entry.adapter is None
                else {
                    "registration_order": entry.adapter.registration_order,
                    "module": entry.adapter.module,
                    "class_name": entry.adapter.class_name,
                },
            }
        )
    return {"entries": entries, "aliases": _json_value(get_all_platform_aliases())}


def _execution() -> dict[str, Any]:
    from benchbox.core.compute_resource import COMPUTE_RESOURCE_OPTION, COMPUTE_SIZE_OPTION, get_compute_declaration
    from benchbox.core.deployment import DEPLOYMENT_ALIAS_KEYS
    from benchbox.core.execution_engine import (
        DEFAULT_EXECUTION_ENGINE,
        DEPRECATED_OPTION_ALIASES,
        EXECUTION_ENGINE_CLASSES,
        RESERVED_OPTION_KEYS,
        supported_execution_engines,
    )
    from benchbox.core.platform_manifest import PLATFORM_MANIFEST

    engines: dict[str, Any] = {}
    compute: dict[str, Any] = {}
    for entry in sorted(PLATFORM_MANIFEST, key=lambda item: item.key):
        declared = entry.capabilities.get("execution_engines", {})
        if declared:
            engines[entry.key] = {
                "declared": {name: {"class": engine["class"]} for name, engine in sorted(declared.items())},
                "selectable": sorted(supported_execution_engines(entry.key)),
            }
        declaration = get_compute_declaration(entry.key)
        if declaration is not None:
            compute[entry.key] = {
                "resource_kind": declaration.resource_kind,
                "resource_aliases": sorted(declaration.resource_aliases),
                "size_aliases": sorted(declaration.size_aliases),
                "size_choices": sorted(str(choice) for choice in declaration.size_choices),
            }
    return {
        "default_engine": DEFAULT_EXECUTION_ENGINE,
        "engine_classes": list(EXECUTION_ENGINE_CLASSES),
        "reserved_option_keys": sorted(RESERVED_OPTION_KEYS),
        "deprecated_option_aliases": [
            {"platform": platform, "key": key, "target": alias.target, "removed_in": alias.removed_in}
            for (platform, key), alias in sorted(DEPRECATED_OPTION_ALIASES.items())
        ],
        "deployment_alias_keys": {platform: sorted(keys) for platform, keys in sorted(DEPLOYMENT_ALIAS_KEYS.items())},
        "engines": engines,
        "compute_resource_option": COMPUTE_RESOURCE_OPTION,
        "compute_size_option": COMPUTE_SIZE_OPTION,
        "compute": compute,
    }


def _value_kind(spec: Any) -> str:
    from benchbox.core.hooks.platform_hooks import PlatformOptionError, parse_bool

    if spec.parser is parse_bool:
        return "boolean"
    name = getattr(spec.parser, "__name__", "")
    if name == "_identity":
        return "string"
    if name == "_parse_port":
        return "port"

    def accepts(raw: str) -> bool:
        try:
            spec.parser(raw)
        except (PlatformOptionError, ValueError, TypeError):
            return False
        return True

    if accepts("1") and accepts("1.5"):
        return "number"
    if accepts("1"):
        return "integer"
    return "custom"


def _platform_options() -> dict[str, Any]:
    from benchbox.core.gateway import register_gateway_connection_specs
    from benchbox.core.hooks.platform_hooks import PlatformHookRegistry

    importlib.import_module("benchbox.platforms")
    importlib.import_module("benchbox.cli.app")
    register_gateway_connection_specs()
    platforms: dict[str, Any] = {}
    for platform in sorted(PlatformHookRegistry._option_specs):
        specs = PlatformHookRegistry.list_option_specs(platform)
        platforms[platform] = {
            name: {
                "value_kind": _value_kind(spec),
                "choices": None if not spec.choices else sorted(str(choice) for choice in spec.choices),
                "default": _json_value(spec.default),
                "aliases": sorted(spec.aliases),
            }
            for name, spec in sorted(specs.items())
        }
    return {"platforms": platforms, "alias_index": _json_value(PlatformHookRegistry._alias_index)}


def _tuning() -> dict[str, Any]:
    from benchbox.core.tuning.capability_registry import (
        PLATFORM_ALIASES,
        PLATFORM_TUNING_CAPABILITIES,
        SESSION_CONFIG_CAPABILITIES,
        is_not_rendered_at_execution,
    )

    matrix = _load_script("generate_tuning_support_matrix")
    platforms: dict[str, Any] = {}
    for platform in sorted(PLATFORM_TUNING_CAPABILITIES.keys() | SESSION_CONFIG_CAPABILITIES.keys()):
        capabilities = PLATFORM_TUNING_CAPABILITIES.get(platform, {})
        applied = sorted(t.value for t, c in capabilities.items() if not is_not_rendered_at_execution(c))
        introspector = matrix._adapter_has_introspector(platform)
        platforms[platform] = {
            "applied_types": applied,
            "not_applied_types": sorted(t.value for t, c in capabilities.items() if is_not_rendered_at_execution(c)),
            "session_config": platform in SESSION_CONFIG_CAPABILITIES,
            "has_catalog_introspector": bool(introspector),
            "can_reach_applied_verified": bool(introspector and applied),
        }
    return {"aliases": _json_value(PLATFORM_ALIASES), "platforms": platforms}


def _command_options(command: Any) -> list[dict[str, Any]]:
    import click

    options = []
    for param in command.params:
        if not isinstance(param, click.Option):
            continue
        options.append(
            {
                "names": sorted([*param.opts, *param.secondary_opts]),
                "is_flag": bool(param.is_flag),
                "multiple": bool(param.multiple),
                "required": bool(param.required),
                "hidden": bool(param.hidden),
                "choices": sorted(str(c) for c in param.type.choices) if isinstance(param.type, click.Choice) else None,
            }
        )
    return sorted(options, key=lambda item: item["names"])


def _resolve_command(command: Any) -> Any:
    loader = getattr(command, "_load", None)
    return loader() if callable(loader) else command


def _walk_commands(command: Any, path: tuple[str, ...], ctx: Any, found: list[dict[str, Any]]) -> None:
    import click

    hidden = bool(getattr(command, "hidden", False))
    command = _resolve_command(command)
    found.append(
        {
            "path": " ".join(path),
            "hidden": hidden,
            "is_group": isinstance(command, click.Group),
            "options": _command_options(command),
        }
    )
    if isinstance(command, click.Group):
        for name in sorted(command.list_commands(ctx)):
            child = command.get_command(ctx, name)
            if child is not None:
                _walk_commands(child, (*path, name), click.Context(child, parent=ctx, info_name=name), found)


def _cli() -> dict[str, Any]:
    import click

    from benchbox.cli.main import cli

    commands: list[dict[str, Any]] = []
    _walk_commands(cli, ("benchbox",), click.Context(cli, info_name="benchbox"), commands)
    catalog = yaml.safe_load(HELP_CATALOG.read_text(encoding="utf-8"))
    return {
        "commands": sorted(commands, key=lambda item: item["path"]),
        "help_catalog": _json_value(catalog),
    }


@contextmanager
def _isolated_mcp_state() -> Iterator[dict[str, str]]:
    from benchbox.utils.printing import is_quiet, set_quiet

    loggers = [logging.getLogger("benchbox"), logging.getLogger("mcp.server.transport_security")]
    saved = [
        (logger, logger.level, list(logger.handlers), list(logger.filters), logger.propagate, logger.disabled)
        for logger in loggers
    ]
    quiet = is_quiet()
    try:
        yield {key: value for key, value in os.environ.items() if not key.startswith("OTEL_EXPORTER_OTLP_")}
    finally:
        for logger, level, handlers, filters, propagate, disabled in saved:
            logger.setLevel(level)
            logger.handlers[:] = handlers
            logger.filters[:] = filters
            logger.propagate = propagate
            logger.disabled = disabled
        set_quiet(quiet)


def _mcp() -> dict[str, Any]:
    import anyio

    from benchbox.mcp import create_server
    from benchbox.mcp.security import RemoteSecurityRuntime, token_sha256

    def schema_type(prop: dict[str, Any]) -> str:
        if "type" in prop:
            return str(prop["type"])
        if "anyOf" in prop:
            return " or ".join(option["type"] for option in prop["anyOf"])
        return "unknown"

    def tools(server: Any) -> dict[str, Any]:
        async def collect() -> list[Any]:
            return await server.list_tools()

        listed: dict[str, Any] = {}
        for tool in anyio.run(collect):
            schema = tool.input_schema
            required = set(schema.get("required", []))
            listed[tool.name] = {
                "params": {
                    name: {
                        "type": schema_type(prop),
                        "required": name in required,
                        "default": None if name in required else _json_value(prop.get("default")),
                    }
                    for name, prop in schema["properties"].items()
                }
            }
        return dict(sorted(listed.items()))

    with _isolated_mcp_state() as env, tempfile.TemporaryDirectory(prefix="doc-contracts-") as scratch:
        local = tools(create_server(log_level="ERROR", env=env))
        base = Path(scratch)
        policy = {
            "issuer_url": "https://auth.example.test",
            "resource_server_url": "https://mcp.example.test/mcp",
            "allowed_hosts": ["127.0.0.1"],
            "allowed_origins": ["https://client.example.test"],
            "workspace_root": str(base / "workspaces"),
            "state_db": str(base / "security.sqlite3"),
            "tokens": [
                {
                    "token_sha256": token_sha256("doc-contracts"),
                    "client_id": "doc-contracts",
                    "subject": "doc-contracts",
                    "scopes": ["benchbox:read", "benchbox:execute"],
                }
            ],
            "allowed_platforms": ["duckdb"],
            "allowed_benchmarks": ["tpch"],
            "max_scale_factor": 1.0,
        }
        config = base / "security.json"
        config.write_text(json.dumps(policy), encoding="utf-8")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            runtime = RemoteSecurityRuntime.from_file(config)
            remote = tools(create_server(log_level="ERROR", env=env, remote_security=runtime))
    return {
        "tools": local,
        "remote_only_tools": sorted(set(remote) - set(local)),
        "run_surface": _read_json(RUN_SURFACE),
    }


def _public_symbols() -> dict[str, Any]:
    inventory = _read_json(API_SYMBOLS)
    return {
        "dropped": sorted(inventory["dropped"]),
        "symbols": sorted(
            (
                {
                    "symbol": item["symbol"],
                    "name": item.get("name"),
                    "aliases": sorted(item.get("aliases", [])),
                    "kind": item.get("kind"),
                }
                for item in inventory["symbols"]
            ),
            key=lambda item: item["symbol"],
        ),
    }


def _sf1() -> dict[str, Any]:
    baseline = _read_json(SF1_BASELINE)
    measure = _load_script("measure_sf1_sizes")
    return {
        "measured_at": baseline["measured_at"],
        "scale_factor": baseline["scale_factor"],
        "compression": baseline["compression"],
        "tolerance": baseline["tolerance"],
        "sizes": dict(sorted(baseline["sizes"].items())),
        "measurable_benchmark_ids": sorted(measure.all_benchmark_ids()),
        "documented_without_generation": list(SF1_DOCUMENTED_WITHOUT_GENERATION),
    }


def _write_primitives() -> dict[str, Any]:
    from benchbox.core.write_primitives.catalog.loader import load_write_primitives_catalog

    catalog = load_write_primitives_catalog()
    bounds: dict[str, dict[str, Any]] = {}
    for op_id, op in sorted(catalog.operations.items()):
        ranged = {
            query.id: {"min": query.expected_value_min, "max": query.expected_value_max}
            for query in op.validation_queries
            if query.expected_value_min is not None
        }
        if ranged:
            bounds[op_id] = dict(sorted(ranged.items()))
    return {"validation_bounds": bounds}


def _publication() -> dict[str, Any]:
    docs = ROOT / "docs"
    gated = sorted(
        page.relative_to(docs).as_posix()
        for root in PUBLISH_LIST_ROOTS
        for suffix in ("*.md", "*.rst")
        for page in (docs / root).rglob(suffix)
    )
    exclusions = _read_list(PUBLISH_EXCLUSIONS)
    return {
        "gated_roots": list(PUBLISH_LIST_ROOTS),
        "allowlist": sorted(_read_list(PUBLISH_ALLOWLIST)),
        "exclusions": sorted(exclusions),
        "excluded_roots": sorted(entry.rstrip("/") for entry in exclusions if entry.endswith("/")),
        "gated_pages": gated,
        "linkcheck_ignore_patterns": _read_list(LINKCHECK_IGNORE),
    }


def _ownership() -> dict[str, Any]:
    manifest = _read_json(OWNERSHIP)
    counts: dict[str, int] = {}
    for owner in manifest["files"].values():
        counts[owner] = counts.get(owner, 0) + 1
    return {
        "counts": dict(sorted(counts.items())),
        "site_owned": sorted(path for path, owner in manifest["files"].items() if owner == "site-owned"),
        "core_generated": sorted(record["path"] for record in manifest["generated"]),
        "transitions": _json_value(manifest["transitions"]),
    }


def build_contracts() -> dict[str, Any]:
    return _json_value(
        {
            "schema_version": SCHEMA_VERSION,
            "package": _package(),
            "result_schema_version": _result_schema_version(),
            "benchmarks": _benchmarks(),
            "platforms": _platforms(),
            "platform_manifest": _platform_manifest(),
            "execution": _execution(),
            "platform_options": _platform_options(),
            "tuning": _tuning(),
            "cli": _cli(),
            "mcp": _mcp(),
            "public_symbols": _public_symbols(),
            "sf1_calibrated_sizes": _sf1(),
            "write_primitives": _write_primitives(),
            "publication": _publication(),
            "ownership": _ownership(),
        }
    )


def _result_schema_version() -> str:
    from benchbox.core.results.schema import SCHEMA_VERSION as RESULT_SCHEMA_VERSION

    return str(RESULT_SCHEMA_VERSION)


def render_contracts(contracts: dict[str, Any] | None = None) -> str:
    return json.dumps(contracts if contracts is not None else build_contracts(), indent=2, sort_keys=True) + "\n"


def validate_contracts(contracts: dict[str, Any]) -> list[str]:
    import jsonschema

    validator = jsonschema.Draft202012Validator(_read_json(SCHEMA_PATH))
    return sorted(
        f"{'/'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}"
        for error in validator.iter_errors(contracts)
    )


def write_contracts(path: Path) -> Path:
    text = render_contracts()
    errors = validate_contracts(json.loads(text))
    if errors:
        raise ValueError("doc-contracts.json violates its schema: " + "; ".join(errors[:5]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export the facts that site-owned documentation is validated against.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check", action="store_true", help="Build twice, validate the schema and require identical output."
    )
    args = parser.parse_args(argv)
    if args.check:
        first = render_contracts()
        second = render_contracts()
        errors = validate_contracts(json.loads(first))
        if errors:
            print("\n".join(errors), file=sys.stderr)
            return 1
        if first != second:
            print("doc-contracts.json is not deterministic across two builds", file=sys.stderr)
            return 1
        print(f"doc-contracts ok: {len(first)} bytes, deterministic, schema-valid")
        return 0
    print(write_contracts(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
