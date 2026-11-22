from __future__ import annotations

import math
from typing import Any

DIGEN_MIN_SCALE = 3
DIGEN_DEFAULT_SCALE = 5
DIGEN_STANDARD_SCALES = (3, 5, 10, 100)

SCALE_REFUSAL_MESSAGE = "TPC-DI data generation needs a whole-number scale factor of 3 or more. Got: {sf}."


class TPCDIScaleError(ValueError):
    pass


def validate_digen_scale(scale_factor: Any) -> int:
    refusal = TPCDIScaleError(SCALE_REFUSAL_MESSAGE.format(sf=scale_factor))
    if isinstance(scale_factor, bool):
        raise refusal
    try:
        value = float(scale_factor)
    except (TypeError, ValueError):
        raise refusal from None
    if not math.isfinite(value) or value != int(value) or value < DIGEN_MIN_SCALE:
        raise refusal
    return int(value)
