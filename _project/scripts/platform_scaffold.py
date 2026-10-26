from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from typing import Literal

PlatformKind = Literal["sql", "dataframe"]


CLI_DESCRIPTION = (
    "Print a file plan and checklist for adding a BenchBox platform.\n"
    "\n"
    "This helper is intentionally non-mutating. It measures extension cost up front\n"
    "without creating another framework around platform authoring.\n"
)


@dataclass(frozen=True)
class ScaffoldPlan:
    name: str
    kind: PlatformKind
    files: tuple[str, ...]
    checklist: tuple[str, ...]

    @property
    def file_count(self) -> int:
        return len(self.files)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": self.kind,
            "file_count": self.file_count,
            "files": list(self.files),
            "checklist": list(self.checklist),
        }


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    if not slug:
        raise ValueError("platform name must contain at least one alphanumeric character")
    return slug


def build_plan(name: str, kind: PlatformKind) -> ScaffoldPlan:
    slug = _slugify(name)
    cli_name = slug.replace("_", "-")

    common_files = (
        "benchbox/core/platform_manifest.py",
        "pyproject.toml",
    )

    if kind == "sql":
        files = (
            f"benchbox/platforms/{slug}.py",
            *common_files,
            f"tests/unit/platforms/test_{slug}_adapter.py",
        )
        checklist = (
            "Add adapter with lazy/guarded SDK imports.",
            "Add one manifest entry with canonical key, scoped aliases, adapter coordinates/order, metadata, and capabilities.",
            "Assign the next contiguous adapter registration_order; never reorder existing adapters.",
            "Declare exactly one support_status distinct from dependency availability in that manifest entry.",
            "Add or reuse an optional dependency extra.",
            "Add SQL compatibility rules or explicit DDL/query exemptions.",
            "Add focused unit tests and define smoke/UAT scope.",
            "Run make platform-manifest and make platform-manifest-check.",
        )
    else:
        files = (
            f"benchbox/platforms/dataframe/{slug}_df.py",
            "benchbox/platforms/dataframe/__init__.py",
            "benchbox/platforms/adapter_factory.py",
            *common_files,
            f"tests/unit/platforms/dataframe/test_{slug}_df.py",
        )
        checklist = (
            "Choose expression, pandas, Spark, or new DataFrame family.",
            "Add native DataFrame adapter and factory routing.",
            "Add one manifest entry; make -df aliases CLI-scoped with implied_mode=dataframe.",
            "Add adapter class, availability constant, and nonempty install guidance to the DataFrame lazy/factory surfaces.",
            "Set supports_sql=False unless dual-mode is real.",
            "Declare exactly one support_status distinct from dependency availability in that manifest entry.",
            "Add native query coverage or benchmark-gate unsupported workloads.",
            "Validate parity against DuckDB SQL at SF=0.01 where applicable.",
            "Run make platform-manifest and make platform-manifest-check.",
        )

    return ScaffoldPlan(name=cli_name, kind=kind, files=files, checklist=checklist)


def _format_markdown(plan: ScaffoldPlan) -> str:
    lines = [
        f"# Platform Scaffold: {plan.name}",
        "",
        f"Kind: `{plan.kind}`",
        f"Estimated durable files touched: **{plan.file_count}**",
        "",
        "## Files",
        "",
    ]
    lines.extend(f"- `{path}`" for path in plan.files)
    lines.extend(["", "## Checklist", ""])
    lines.extend(f"- [ ] {item}" for item in plan.checklist)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--name", required=True, help="Platform CLI/display slug, for example newdatabase")
    parser.add_argument("--kind", choices=("sql", "dataframe"), required=True, help="Primary platform kind")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown", help="Output format")
    args = parser.parse_args()

    plan = build_plan(args.name, args.kind)
    if args.format == "json":
        print(json.dumps(plan.to_dict(), indent=2, sort_keys=True))
    else:
        print(_format_markdown(plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
