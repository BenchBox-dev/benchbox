#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from benchbox.core.execution_engine import EXECUTION_ENGINE_CLASSES

SQL_BASE_IMPORT = "from benchbox.platforms.base import PlatformAdapter"
DATAFRAME_BASE_IMPORT = "from benchbox.platforms.base import PlatformAdapter"

FILE_PLAN_SQL = (
    "benchbox/platforms/<key>_platform.py",
    "benchbox/core/platform_manifest.py (canonical entry)",
    "pyproject.toml (optional dependency extra)",
    "SQL compatibility rule or explicit exemption",
    "tests/unit/platforms/test_<key>_platform.py",
    "docs/platforms/<key>.md",
)

FILE_PLAN_DATAFRAME = (
    "benchbox/platforms/dataframe/<key>_df.py",
    "benchbox/core/platform_manifest.py (canonical entry)",
    "pyproject.toml (optional dependency extra)",
    "tests/unit/platforms/test_<key>_df.py",
    "docs/platforms/<key>-df.md",
)


def manifest_snippet(*, key: str, display_name: str, mode: str) -> dict:
    return {
        "key": key,
        "display_name": display_name,
        "support_status": "experimental",
        "adapter": {"module": f"benchbox.platforms.{key}_platform"},
        "capabilities": {
            "supports_sql": mode == "sql",
            "supports_dataframe": mode == "dataframe",
            "execution_engines": {},
        },
    }


def adapter_stub(*, key: str, display_name: str, mode: str) -> str:
    base_import = SQL_BASE_IMPORT if mode == "sql" else DATAFRAME_BASE_IMPORT
    class_name = "".join(part.capitalize() for part in key.split("_")) + "Adapter"
    lines = [
        "from __future__ import annotations",
        "",
        base_import,
        "",
        "",
        f"class {class_name}(PlatformAdapter):",
        f'    platform_name = "{key}"',
        f'    display_name = "{display_name}"',
        "",
        '    driver_package = "<driver-package>"',
        '    installation_command = "uv add <driver-package>"',
        "",
    ]
    return "\n".join(lines)


def file_plan(*, mode: str) -> tuple[str, ...]:
    return FILE_PLAN_SQL if mode == "sql" else FILE_PLAN_DATAFRAME


def render(*, key: str, display_name: str, mode: str) -> str:
    snippet = yaml.safe_dump(manifest_snippet(key=key, display_name=display_name, mode=mode), sort_keys=False)
    sections = [
        f"platform: {key} ({mode})",
        "",
        "manifest entry:",
        snippet.rstrip(),
        "",
        "adapter stub:",
        adapter_stub(key=key, display_name=display_name, mode=mode).rstrip(),
        "",
        "file plan:",
        *[f"- {entry.replace('<key>', key)}" for entry in file_plan(mode=mode)],
    ]
    return "\n".join(sections) + "\n"


def validate_snippet(snippet: dict) -> list[str]:
    problems: list[str] = []
    capabilities = snippet.get("capabilities", {})
    engines = capabilities.get("execution_engines", {})
    for name, engine in engines.items():
        if engine.get("class") not in EXECUTION_ENGINE_CLASSES:
            problems.append(f"unknown engine class for {name!r}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print the new-platform file plan with manifest and adapter stubs.")
    parser.add_argument("--name", required=True, help="Platform registry key, for example myengine.")
    parser.add_argument("--display-name", default="", help="Human-readable platform name.")
    parser.add_argument("--kind", choices=("sql", "dataframe"), default="sql")
    parser.add_argument("--output-dir", default="", help="Write manifest.yaml and adapter stub files here.")
    args = parser.parse_args(argv)
    display_name = args.display_name or args.name.replace("_", " ").title()
    snippet = manifest_snippet(key=args.name, display_name=display_name, mode=args.kind)
    problems = validate_snippet(snippet)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    if args.output_dir:
        out = Path(args.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "manifest.yaml").write_text(yaml.safe_dump(snippet, sort_keys=False))
        (out / f"{args.name}_platform.py").write_text(
            adapter_stub(key=args.name, display_name=display_name, mode=args.kind)
        )
    sys.stdout.write(render(key=args.name, display_name=display_name, mode=args.kind))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
