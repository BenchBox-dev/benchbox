from __future__ import annotations

from pathlib import Path

import yaml


def _load_rows_per_scale_factor() -> dict[str, int]:
    with (Path(__file__).with_name("generator_specs.yaml")).open(encoding="utf-8") as handle:
        return {name: int(rows) for name, rows in yaml.safe_load(handle)["rows_per_scale_factor"].items()}


ROWS_PER_SCALE_FACTOR = _load_rows_per_scale_factor()


def scaled_rows(rows_per_scale_factor: float, scale_factor: float) -> int:
    return max(1, int(rows_per_scale_factor * scale_factor))


__all__ = ["ROWS_PER_SCALE_FACTOR", "scaled_rows"]
