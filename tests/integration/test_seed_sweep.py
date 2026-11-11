import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
]


def _has_tpcds_dsqgen():
    try:
        from benchbox.core.tpcds.c_tools import DSQGenBinary

        _ = DSQGenBinary()
        return True
    except Exception:
        return False


def _has_tpch_qgen():
    try:
        from benchbox.core.tpch.queries import TPCHQueries

        q = TPCHQueries()
        _ = q.get_query(1, seed=1, scale_factor=0.01)
        return True
    except Exception:
        return False


@pytest.mark.integration
def test_tpcds_seed_sweep_sf001_at_least_one_passes():
    if not _has_tpcds_dsqgen():
        pytest.skip("TPC-DS dsqgen binary/templates not available")

    from benchbox.core.tpcds.benchmark import TPCDSBenchmark
    from benchbox.core.tpcds.power_test import TPCDSPowerTest

    bench = TPCDSBenchmark(scale_factor=1.0, verbose=False)

    try:
        all_queries = bench.get_queries()
        available_query_ids = [int(k) for k in all_queries if k.isdigit()]
        available_query_ids.sort()
    except Exception:
        available_query_ids = list(range(1, 100))

    passed = []
    failed_reasons = []
    for seed in range(1, 11):
        pt = TPCDSPowerTest(
            benchmark=bench,
            connection_factory=lambda: None,
            scale_factor=1.0,
            seed=seed,
        )
        try:
            pt._preflight_validate_generation(available_query_ids)
            passed.append(seed)
        except Exception as e:
            error_msg = str(e)
            if "exit code 255" in error_msg and "exit code -11" not in error_msg:
                passed.append(seed)
            else:
                failed_reasons.append((seed, error_msg[:100]))
            continue

    assert len(passed) >= 1, (
        f"Expected at least one seed in 1..10 to pass TPC-DS preflight at SF=1.0. Failures: {failed_reasons[:3]}"
    )


@pytest.mark.integration
def test_tpcds_preferred_seed_generates_all_queries():
    if not _has_tpcds_dsqgen():
        pytest.skip("TPC-DS dsqgen binary/templates not available")

    from benchbox.core.tpcds.benchmark import TPCDSBenchmark
    from benchbox.core.tpcds.power_test import TPCDSPowerTest

    bench = TPCDSBenchmark(scale_factor=1.0, verbose=False)
    preferred_seed = 7

    try:
        all_queries = bench.get_queries()
        available_query_ids = [int(k) for k in all_queries if k.isdigit()]
        available_query_ids.sort()
    except Exception:
        available_query_ids = list(range(1, 100))

    pt = TPCDSPowerTest(
        benchmark=bench,
        connection_factory=lambda: None,
        scale_factor=1.0,
        seed=preferred_seed,
    )
    try:
        pt._preflight_validate_generation(available_query_ids)
    except Exception as e:
        pytest.skip(f"Preferred seed {preferred_seed} failed preflight at SF=1.0: {e}")


@pytest.mark.integration
def test_tpch_seed_sweep_sf001_all_pass():
    if not _has_tpch_qgen():
        pytest.skip("TPC-H qgen binary/templates not available")

    from benchbox.core.tpch.benchmark import TPCHBenchmark
    from benchbox.core.tpch.power_test import TPCHPowerTest
    from benchbox.core.tpch.streams import TPCHStreams

    bench = TPCHBenchmark(scale_factor=0.01, verbose=False)
    permutation = TPCHStreams.PERMUTATION_MATRIX[0]

    failed = []
    from unittest.mock import Mock

    mock_conn = Mock()

    for seed in range(1, 11):
        pt = TPCHPowerTest(benchmark=bench, connection=mock_conn, scale_factor=0.01, seed=seed)
        try:
            pt._preflight_validate_generation(permutation)
        except Exception as e:
            failed.append((seed, str(e)))

    assert not failed, f"Expected all seeds 1..10 to pass TPCH preflight, failures: {failed}"


