from __future__ import annotations

from enum import Enum


class TpcdsComplianceClass(str, Enum):
    OFFICIAL = "official"
    UNOFFICIAL_NONSTANDARD = "unofficial_nonstandard"
    UNOFFICIAL_SUBSCALE = "unofficial_subscale"


TPCDS_MIN_SUBSCALE: float = 0.01

TPCDS_BELOW_FLOOR_REASON: str = (
    f"The bundled dsdgen did not finish at SF 0.001, so scale factors below {TPCDS_MIN_SUBSCALE} "
    "are refused before dsdgen is started."
)

OFFICIAL_SCALE_POINTS: frozenset[float] = frozenset(
    {1.0, 10.0, 100.0, 300.0, 1000.0, 3000.0, 10000.0, 30000.0, 100000.0}
)


def classify_tpcds_run(scale_factor: float, *, official: bool = False) -> TpcdsComplianceClass | None:
    from benchbox.core.tpc_patterns import classify_official_scale_run

    return classify_official_scale_run(
        scale_factor, benchmark_id="tpcds", official=official, compliance_enum=TpcdsComplianceClass
    )


def validate_tpcds_scale(
    scale_factor: float,
    *,
    official: bool = False,
) -> TpcdsComplianceClass | None:
    if scale_factor <= 0:
        raise ValueError(f"TPC-DS scale factor must be positive, got {scale_factor}")
    if scale_factor > 100000:
        raise ValueError(f"TPC-DS scale factor {scale_factor} exceeds maximum (100000)")

    compliance = classify_tpcds_run(scale_factor, official=official)

    if compliance is TpcdsComplianceClass.UNOFFICIAL_SUBSCALE and scale_factor < TPCDS_MIN_SUBSCALE:
        raise ValueError(
            f"TPC-DS scale_factor {scale_factor} is below the minimum subscale "
            f"({TPCDS_MIN_SUBSCALE}). {TPCDS_BELOW_FLOOR_REASON}"
        )

    return compliance
