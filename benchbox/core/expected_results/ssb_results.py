from __future__ import annotations

import math

SSB_EXPECTED_ROW_COUNTS: dict[float, dict[str, int]] = {
    0.01: {
        "1.1": 1,
        "1.2": 1,
        "1.3": 1,
        "2.1": 86,
        "2.2": 19,
        "2.3": 2,
        "3.1": 120,
        "3.2": 0,
        "3.3": 0,
        "3.4": 0,
        "4.1": 35,
        "4.2": 18,
        "4.3": 8,
    },
    0.1: {
        "1.1": 1,
        "1.2": 1,
        "1.3": 1,
        "2.1": 280,
        "2.2": 56,
        "2.3": 7,
        "3.1": 150,
        "3.2": 398,
        "3.3": 20,
        "3.4": 2,
        "4.1": 35,
        "4.2": 100,
        "4.3": 242,
    },
    1.0: {
        "1.1": 1,
        "1.2": 1,
        "1.3": 1,
        "2.1": 280,
        "2.2": 56,
        "2.3": 7,
        "3.1": 150,
        "3.2": 600,
        "3.3": 24,
        "3.4": 2,
        "4.1": 35,
        "4.2": 100,
        "4.3": 1854,
    },
    10.0: {
        "1.1": 1,
        "1.2": 1,
        "1.3": 1,
        "2.1": 280,
        "2.2": 56,
        "2.3": 7,
        "3.1": 150,
        "3.2": 600,
        "3.3": 24,
        "3.4": 4,
        "4.1": 35,
        "4.2": 100,
        "4.3": 3992,
    },
}


def ssb_expected_row_counts(scale_factor: float) -> dict[str, int] | None:
    for recorded_scale, counts in SSB_EXPECTED_ROW_COUNTS.items():
        if math.isclose(recorded_scale, scale_factor, rel_tol=1e-9):
            return counts
    return None


def normalize_ssb_query_id(query_id: object) -> str:
    return str(query_id).strip().upper().removeprefix("Q")
