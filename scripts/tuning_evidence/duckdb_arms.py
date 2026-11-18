from __future__ import annotations

import argparse
import copy
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "benchbox" / "core" / "tuning" / "templates" / "duckdb"
BENCHMARKS = ("tpch", "ssb", "tpcds")
VARIANTS = ("constraints_only", "sort_only")


def load_template(benchmark: str, template_dir: Path = TEMPLATE_DIR) -> dict[str, Any]:
    return yaml.safe_load((template_dir / f"{benchmark}_tuned.yaml").read_text(encoding="utf-8"))


def derive(template: dict[str, Any], variant: str) -> dict[str, Any]:
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant!r}; known: {', '.join(VARIANTS)}")
    arm = copy.deepcopy(template)
    if variant == "constraints_only":
        arm["table_tunings"] = {}
    else:
        arm["primary_keys"]["enabled"] = False
        arm["foreign_keys"]["enabled"] = False
    arm.setdefault("_metadata", {})["configuration_type"] = variant
    return arm


def write_arms(benchmark: str, out_dir: Path, template_dir: Path = TEMPLATE_DIR) -> dict[str, Path]:
    template = load_template(benchmark, template_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for variant in VARIANTS:
        path = out_dir / f"{benchmark}_{variant}.yaml"
        path.write_text(yaml.safe_dump(derive(template, variant), sort_keys=False), encoding="utf-8")
        paths[variant] = path
    return paths


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.tuning_evidence.duckdb_arms")
    parser.add_argument("--benchmark", required=True, choices=BENCHMARKS)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    for variant, path in write_arms(args.benchmark, args.out).items():
        print(f"{variant}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
