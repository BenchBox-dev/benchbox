from enum import Enum


class TpchComplianceClass(str, Enum):
    OFFICIAL = "official"
    UNOFFICIAL_NONSTANDARD = "unofficial_nonstandard"
    UNOFFICIAL_SUBSCALE = "unofficial_subscale"


OFFICIAL_SCALE_POINTS: frozenset[float] = frozenset(
    {1.0, 10.0, 30.0, 100.0, 300.0, 1000.0, 3000.0, 10000.0, 30000.0, 100000.0}
)


def classify_tpch_run(scale_factor: float, *, official: bool = False) -> TpchComplianceClass:
    from benchbox.core.tpc_patterns import classify_official_scale_run

    return classify_official_scale_run(
        scale_factor, official=official, scale_points=OFFICIAL_SCALE_POINTS, compliance_enum=TpchComplianceClass
    )


def validate_tpch_scale(
    scale_factor: float,
    *,
    official: bool = False,
) -> TpchComplianceClass:
    if scale_factor <= 0:
        raise ValueError(f"TPC-H scale factor must be positive, got {scale_factor}")
    if scale_factor > 100000:
        raise ValueError(f"TPC-H scale factor {scale_factor} exceeds maximum (100000)")

    return classify_tpch_run(scale_factor, official=official)
