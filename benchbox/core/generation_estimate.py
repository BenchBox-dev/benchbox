from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any

from benchbox.utils.printing import emit
from benchbox.utils.run_deadline import current_run_deadline

ESTIMATE_TEXT = (
    "Rough estimate: generation will take about {estimate} "
    "(measured on {host_class} at scale {scales}{extrapolated_note})."
)
LIMIT_WARNING_TEXT = (
    "The run limit of {limit} seconds is shorter than the estimated generation time of about {estimate}."
)
EXTRAPOLATED_NOTE = "; extrapolated for scale {scale}, above the largest measured scale {largest}"

DATA_GENERATION = "data_generation"
QUERY_GENERATION = "query_generation"
SECONDS_KEYS = {DATA_GENERATION: "wall_seconds", QUERY_GENERATION: "total_seconds"}


@dataclass(frozen=True)
class GenerationEstimate:
    seconds: float
    host_class: str
    measured_scales: tuple[float, ...]
    extrapolated: bool
    scale: float

    def duration_text(self) -> str:
        return format_estimate_duration(self.seconds)

    def message(self) -> str:
        note = (
            EXTRAPOLATED_NOTE.format(scale=f"{self.scale:g}", largest=f"{self.measured_scales[-1]:g}")
            if self.extrapolated
            else ""
        )
        return ESTIMATE_TEXT.format(
            estimate=self.duration_text(),
            host_class=self.host_class,
            scales=", ".join(f"{scale:g}" for scale in self.measured_scales),
            extrapolated_note=note,
        )


@lru_cache(maxsize=1)
def _measurements() -> dict[str, Any]:
    with resources.files(__package__).joinpath("generation_estimate.json").open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("generation_estimate.json must contain a mapping")
    return payload


def format_estimate_duration(seconds: float) -> str:
    if seconds < 60:
        whole = max(1, round(seconds))
        return f"{whole} second{'' if whole == 1 else 's'}"
    if seconds < 3600:
        whole = round(seconds / 60)
        return f"{whole} minute{'' if whole == 1 else 's'}"
    return f"{seconds / 3600:.1f} hours"


def estimate_generation(kind: str, benchmark: str, scale: float) -> GenerationEstimate | None:
    payload = _measurements()
    points = payload.get(kind, {}).get(str(benchmark).lower())
    if not points:
        return None
    key = SECONDS_KEYS[kind]
    table = sorted((float(point["scale"]), float(point[key])) for point in points)
    scale = float(scale)
    largest_scale, largest_seconds = table[-1]
    if scale <= table[0][0]:
        seconds = table[0][1]
    elif scale >= largest_scale:
        seconds = largest_seconds * scale / largest_scale
    else:
        seconds = table[-1][1]
        for (low_scale, low_seconds), (high_scale, high_seconds) in zip(table, table[1:]):
            if low_scale <= scale <= high_scale:
                fraction = (scale - low_scale) / (high_scale - low_scale)
                seconds = low_seconds + fraction * (high_seconds - low_seconds)
                break
    return GenerationEstimate(
        seconds=seconds,
        host_class=f"{payload['host_class']} ({payload['host']})",
        measured_scales=tuple(scale_value for scale_value, _ in table),
        extrapolated=scale > largest_scale,
        scale=scale,
    )


def generation_estimate_messages(benchmark: str, scale: float) -> list[str]:
    estimates = (estimate_generation(kind, benchmark, scale) for kind in (DATA_GENERATION, QUERY_GENERATION))
    return [estimate.message() for estimate in estimates if estimate is not None]


def announce_generation_estimate(kind: str, benchmark: str, scale: float) -> GenerationEstimate | None:
    estimate = estimate_generation(kind, benchmark, scale)
    if estimate is None:
        return None
    emit(estimate.message())
    deadline = current_run_deadline()
    if deadline is not None and deadline.timeout_seconds < estimate.seconds:
        emit(LIMIT_WARNING_TEXT.format(limit=deadline.timeout_seconds, estimate=estimate.duration_text()))
