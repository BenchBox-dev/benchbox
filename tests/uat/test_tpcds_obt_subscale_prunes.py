import pytest

from tests.uat import compatibility, matrix

pytestmark = pytest.mark.medium

_PRUNED_PLATFORMS = ("questdb", "postgresql", "timescaledb", "pg-mooncake")


def _rule(platform, scale):
    benchmarks = matrix.load_benchmarks()
    return compatibility.compatibility_rule_for(
        platform,
        "tpcds_obt",
        benchmarks["tpcds_obt"],
        include_release_gate_runtime_envelopes=True,
        scale=scale,
    )


@pytest.mark.parametrize("platform", _PRUNED_PLATFORMS)
def test_tpcds_obt_sf001_is_pruned_with_run_evidence(platform):
    rule = _rule(platform, 0.01)

    assert rule is not None
    assert rule.rule_id == f"uat.compat.{platform}.tpcds_obt.sf0.01.release_gate_runtime_envelope"
    assert rule.status == "blocked"
    assert "43117035812" in rule.evidence


@pytest.mark.parametrize("platform", _PRUNED_PLATFORMS)
def test_tpcds_obt_prunes_do_not_cover_other_scales(platform):
    assert _rule(platform, 0.1) is None


def test_prune_reasons_name_the_concrete_causes():
    assert "17 of 93 queries" in _rule("questdb", 0.01).reason
    assert "timed out at 1200s" in _rule("postgresql", 0.01).reason
    assert "timed out at 1200s" in _rule("timescaledb", 0.01).reason
    assert "mooncake_scan" in _rule("pg-mooncake", 0.01).reason


@pytest.mark.parametrize("platform", ["singlestore", "doris", "starrocks", "duckdb"])
def test_other_platforms_keep_tpcds_obt_sf001(platform):
    assert _rule(platform, 0.01) is None
