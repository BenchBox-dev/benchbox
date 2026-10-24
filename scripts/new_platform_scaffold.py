#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

from benchbox.core.platform_manifest import (
    PLATFORM_MANIFEST,
    _parse_manifest_entry,
    _validate_manifest_set,
)

SQL_BASE_IMPORT = "from benchbox.platforms.base import PlatformAdapter"
SQL_BASE_CLASS = "PlatformAdapter"
DATAFRAME_BASE_IMPORT = "from benchbox.platforms.dataframe.expression_family import ExpressionFamilyAdapter"
DATAFRAME_BASE_CLASS = "ExpressionFamilyAdapter"
RECEIPT_IMPORT = "from benchbox.core.execution_engine import ExecutionEngineReceipt, platform_default_receipt"
MANIFEST_FILE = "manifest-entry.json"

FILE_PLAN_SQL = (
    "{module_file}",
    "benchbox/core/platform_manifest.py (canonical entry)",
    "pyproject.toml (optional dependency extra)",
    "SQL compatibility rule or explicit exemption",
    "tests/unit/platforms/test_<key>.py",
    "docs/platforms/<key>.md",
)

FILE_PLAN_DATAFRAME = (
    "{module_file}",
    "benchbox/core/platform_manifest.py (canonical entry with the <key>-df alias)",
    "benchbox/platforms/__init__.py (_DATAFRAME_PLATFORM_ROWS and lazy import entry for {class_name})",
    "benchbox/platforms/dataframe/platform_checker.py (DATAFRAME_PLATFORMS entry)",
    "pyproject.toml (optional dependency extra)",
    "tests/unit/platforms/test_<key>_df.py",
    "docs/platforms/<key>-df.md",
)


def class_name(*, key: str, mode: str) -> str:
    base = "".join(part.capitalize() for part in key.replace("-", "_").split("_"))
    return f"{base}DataFrameAdapter" if mode == "dataframe" else f"{base}Adapter"


def module_path(*, key: str, mode: str) -> str:
    module_key = key.replace("-", "_")
    if mode == "dataframe":
        return f"benchbox.platforms.dataframe.{module_key}_df"
    return f"benchbox.platforms.{module_key}"


def module_file(*, key: str, mode: str) -> str:
    return module_path(key=key, mode=mode).replace(".", "/") + ".py"


def _next_registration_order() -> int:
    return sum(1 for entry in PLATFORM_MANIFEST if entry.adapter is not None)


def manifest_snippet(*, key: str, display_name: str, mode: str) -> dict:
    snippet: dict = {"key": key}
    if mode == "dataframe":
        snippet["aliases"] = [{"name": f"{key}-df", "scopes": ["cli"], "implied_mode": "dataframe"}]
    else:
        snippet["adapter"] = {
            "module": module_path(key=key, mode=mode),
            "class_name": class_name(key=key, mode=mode),
            "registration_order": _next_registration_order(),
            "stream_connection_capability": "unsupported",
        }
    snippet.update(
        {
            "display_name": display_name,
            "description": f"{display_name} benchmark adapter",
            "category": "dataframe" if mode == "dataframe" else "analytical",
            "libraries": [{"name": "<driver-package>", "required": True}],
            "requirements": ["<driver-package>"],
            "installation_command": "uv add <driver-package>",
            "adoption": "emerging",
            "supports": ["dataframe"] if mode == "dataframe" else ["olap"],
            "driver_package": None if mode == "dataframe" else "<driver-package>",
            "support_status": "experimental",
            "capabilities": {
                "supports_sql": mode == "sql",
                "supports_dataframe": mode == "dataframe",
                "default_mode": mode,
                "execution_engines": {},
            },
        }
    )
    if mode == "sql":
        snippet["capabilities"]["sql_dialect"] = key.replace("-", "_")
    return snippet


def adapter_stub(*, key: str, display_name: str, mode: str) -> str:
    base_import, base_class = (
        (DATAFRAME_BASE_IMPORT, DATAFRAME_BASE_CLASS) if mode == "dataframe" else (SQL_BASE_IMPORT, SQL_BASE_CLASS)
    )
    lines = [
        "from __future__ import annotations",
        "",
        RECEIPT_IMPORT,
        base_import,
        "",
        "",
        f"class {class_name(key=key, mode=mode)}({base_class}):",
        f'    platform_name = "{display_name}"',
        "",
        '    driver_package = "<driver-package>"',
        '    installation_command = "uv add <driver-package>"',
        "",
        "    def resolve_execution_engine(self, requested: str) -> ExecutionEngineReceipt:",
        f'        return platform_default_receipt("{key}", requested)',
        "",
    ]
    return "\n".join(lines)


def file_plan(*, key: str, mode: str) -> tuple[str, ...]:
    plan = FILE_PLAN_SQL if mode == "sql" else FILE_PLAN_DATAFRAME
    values = {"module_file": module_file(key=key, mode=mode), "class_name": class_name(key=key, mode=mode)}
    return tuple(entry.format(**values).replace("<key>", key) for entry in plan)


def manifest_json(snippet: dict) -> str:
    return json.dumps(snippet, indent=2) + "\n"


def render(*, key: str, display_name: str, mode: str) -> str:
    sections = [
        f"platform: {key} ({mode})",
        "",
        "manifest entry (add to _PLATFORM_MANIFEST_JSON in benchbox/core/platform_manifest.py):",
        manifest_json(manifest_snippet(key=key, display_name=display_name, mode=mode)).rstrip(),
        "",
        f"adapter stub ({module_file(key=key, mode=mode)}):",
        adapter_stub(key=key, display_name=display_name, mode=mode).rstrip(),
        "",
        "file plan:",
        *[f"- {entry}" for entry in file_plan(key=key, mode=mode)],
    ]
    return "\n".join(sections) + "\n"


def validate_snippet(snippet: dict) -> list[str]:
    try:
        entry = _parse_manifest_entry(copy.deepcopy(snippet))
        _validate_manifest_set([*PLATFORM_MANIFEST, entry])
    except (ValueError, KeyError, TypeError) as exc:
        return [str(exc)]
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print the new-platform file plan with manifest and adapter stubs.")
    parser.add_argument("--name", required=True, help="Platform registry key, for example myengine.")
    parser.add_argument("--display-name", default="", help="Human-readable platform name.")
    parser.add_argument("--kind", choices=("sql", "dataframe"), default="sql")
    parser.add_argument("--output-dir", default="", help=f"Write {MANIFEST_FILE} and the adapter stub here.")
    args = parser.parse_args(argv)
    display_name = args.display_name or args.name.replace("_", " ").replace("-", " ").title()
    snippet = manifest_snippet(key=args.name, display_name=display_name, mode=args.kind)
    problems = validate_snippet(snippet)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    if args.output_dir:
        out = Path(args.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / MANIFEST_FILE).write_text(manifest_json(snippet), encoding="utf-8")
        stub_name = Path(module_file(key=args.name, mode=args.kind)).name
        (out / stub_name).write_text(adapter_stub(key=args.name, display_name=display_name, mode=args.kind))
    sys.stdout.write(render(key=args.name, display_name=display_name, mode=args.kind))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
