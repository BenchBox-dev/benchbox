#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CHECKOUT_ROOT = Path(__file__).resolve().parents[1]
if str(CHECKOUT_ROOT) not in sys.path:
    sys.path.insert(0, str(CHECKOUT_ROOT))

from benchbox.core import benchmark_registry

PROJECTION_PATH = CHECKOUT_ROOT / "benchbox" / "core" / "results" / "standard_scales.json"
PROJECTION_SCHEMA_VERSION = 1
REGENERATE_COMMAND = "uv run -- python scripts/generate_standard_scales.py --write"
STALE_MESSAGE = (
    "benchbox/core/results/standard_scales.json is out of date with the benchmark registry. "
    f"Regenerate it in this pull request: {REGENERATE_COMMAND}"
)

CLI_DESCRIPTION = (
    "Write or check the standard-scale projection that registry-less validators read, "
    "so it cannot drift from the benchmark registry."
)


def compute_projection() -> dict[str, object]:
    return {
        "schema_version": PROJECTION_SCHEMA_VERSION,
        "generated_by": REGENERATE_COMMAND,
        "match_tolerance": benchmark_registry._SCALE_MATCH_TOLERANCE,
        "benchmarks": {
            benchmark_id: list(benchmark_registry.get_scale_factor_rule(benchmark_id).standard)
            for benchmark_id in sorted(benchmark_registry.list_benchmark_ids())
        },
    }


def render_projection() -> str:
    return json.dumps(compute_projection(), indent=2, sort_keys=True) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Fail if the committed projection differs.")
    mode.add_argument("--write", action="store_true", help="Write the regenerated projection to disk.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    rendered = render_projection()

    if args.write:
        PROJECTION_PATH.write_text(rendered, encoding="utf-8")
        print(f"Wrote {PROJECTION_PATH.relative_to(CHECKOUT_ROOT)}")
        return 0

    if not PROJECTION_PATH.is_file() or PROJECTION_PATH.read_text(encoding="utf-8") != rendered:
        print(f"FAIL: {STALE_MESSAGE}", file=sys.stderr)
        return 1
    print("OK: standard_scales.json matches the benchmark registry")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
