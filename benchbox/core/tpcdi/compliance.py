from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

DIGEN_MIN_SCALE = 3
DIGEN_DEFAULT_SCALE = 5
DIGEN_STANDARD_SCALES = (3, 5, 10, 100)
DIGEN_MAX_SCALE = 2147483647

SCALE_REFUSAL_MESSAGE = "TPC-DI data generation needs a whole-number scale factor of 3 or more. Got: {sf}."


class TPCDIScaleError(ValueError):
    pass


def validate_digen_scale(scale_factor: Any) -> int:
    refusal = TPCDIScaleError(SCALE_REFUSAL_MESSAGE.format(sf=scale_factor))
    if isinstance(scale_factor, bool):
        raise refusal
    try:
        value = Decimal(scale_factor.strip() if isinstance(scale_factor, str) else scale_factor)
    except (TypeError, ValueError, InvalidOperation):
        raise refusal from None
    if not value.is_finite() or value != value.to_integral_value():
        raise refusal
    if value < DIGEN_MIN_SCALE or value > DIGEN_MAX_SCALE:
        raise refusal
    return int(value)
