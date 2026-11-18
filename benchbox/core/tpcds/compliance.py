from enum import Enum


class TpcdsComplianceClass(str, Enum):
    OFFICIAL = "official"
    UNOFFICIAL_NONSTANDARD = "unofficial_nonstandard"
    UNOFFICIAL_SUBSCALE = "unofficial_subscale"


OFFICIAL_SCALE_POINTS: frozenset[float] = frozenset(
    {1.0, 10.0, 100.0, 300.0, 1000.0, 3000.0, 10000.0, 30000.0, 100000.0}
)

TPCDS_MIN_SUBSCALE: float = 0.01
TPCDS_MAX_SCALE: float = 3000.0


def classify_tpcds_run(scale_factor: float, *, official: bool = False) -> TpcdsComplianceClass:
    from benchbox.core.tpc_patterns import classify_official_scale_run

    return classify_official_scale_run(
        scale_factor, official=official, scale_points=OFFICIAL_SCALE_POINTS, compliance_enum=TpcdsComplianceClass
    )


def validate_tpcds_scale(
    scale_factor: float,
    *,
    official: bool = False,
) -> TpcdsComplianceClass:
    if scale_factor <= 0:
        raise ValueError(f"TPC-DS scale factor must be positive, got {scale_factor}")
    if scale_factor > TPCDS_MAX_SCALE:
        raise ValueError(
            f"TPC-DS scale factor {scale_factor:g} exceeds maximum ({TPCDS_MAX_SCALE:g}). The bundled dsqgen "
            "cannot generate query 9 above this scale: it builds a key permutation of rowcount(store_sales)/5 "
            "entries, which took over 45 s and 3.6 GiB at scale factor 10000 and over 14 GiB at 100000."
        )

    compliance = classify_tpcds_run(scale_factor, official=official)

    if compliance is TpcdsComplianceClass.UNOFFICIAL_SUBSCALE and scale_factor < TPCDS_MIN_SUBSCALE:
        raise ValueError(
            f"TPC-DS scale_factor {scale_factor} is below the minimum subscale "
            f"({TPCDS_MIN_SUBSCALE}). The bundled dsdgen does not finish below this "
            "scale (it loops in catalog_sales date scaling), so generation is refused "
            "before dsdgen is started."
        )

    return compliance
