# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

TUNED = "tuned"

TUNED_FALLBACK = "tuned-fallback"

NOTUNING = "notuning"

AUTO = "auto"

CUSTOM = "custom"

MODES: tuple[str, ...] = (TUNED, TUNED_FALLBACK, NOTUNING, AUTO, CUSTOM)

NOT_RECORDED = "not-recorded"

BASELINE_SOURCE = "baseline"


def is_canonical_mode(value: str | None) -> bool:
    return value in MODES
