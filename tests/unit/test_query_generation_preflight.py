from unittest.mock import Mock

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


def _mk_tpcds_power(raise_on_ids=None):
    from benchbox.core.tpcds.benchmark import TPCDSBenchmark
    from benchbox.core.tpcds.power_test import TPCDSPowerTest

    bench = TPCDSBenchmark(scale_factor=1.0, verbose=False)
    raise_on_ids = set(raise_on_ids or [])

    def _get_query(query_id, **kwargs):
        if query_id in raise_on_ids:
            raise RuntimeError(f"Template substitution error for q{query_id}")
        return f"SELECT {query_id}"

    bench.get_query = _get_query
    power = TPCDSPowerTest(benchmark=bench, connection_factory=lambda: Mock(), scale_factor=0.01)
    return power


def _mk_tpch_power(raise_on_ids=None):
    from benchbox.core.tpch.benchmark import TPCHBenchmark
    from benchbox.core.tpch.power_test import TPCHPowerTest

    bench = TPCHBenchmark(scale_factor=0.01, verbose=False)
    raise_on_ids = set(raise_on_ids or [])

    def _get_query(query_id, **kwargs):
        if query_id in raise_on_ids:
            raise RuntimeError(f"QGen error for q{query_id}")
        return f"SELECT {query_id}"

    bench.get_query = _get_query
    power = TPCHPowerTest(benchmark=bench, connection=Mock(), scale_factor=0.01)
    return power


def test_tpcds_power_preflight_detects_generation_failures():
    power = _mk_tpcds_power(raise_on_ids={3, 17})
    with pytest.raises(RuntimeError) as ei:
        power.run()
    assert "preflight failed" in str(ei.value).lower()


def test_tpch_power_preflight_detects_generation_failures():
    power = _mk_tpch_power(raise_on_ids={5})
    with pytest.raises(RuntimeError) as ei:
        power.run()
    assert "preflight failed" in str(ei.value).lower()


def _mk_tpcds_throughput(num_streams=2):
    from benchbox.core.tpcds.benchmark import TPCDSBenchmark
    from benchbox.core.tpcds.throughput_test import TPCDSThroughputTest

    bench = TPCDSBenchmark(scale_factor=1.0, verbose=False)
    return TPCDSThroughputTest(
        benchmark=bench,
        connection_factory=lambda: Mock(),
        scale_factor=0.01,
        num_streams=num_streams,
    )


def _fake_dsqgen_streams(num_streams, query_ids=(3, 7, 17)):
    from benchbox.core.tpcds.streams import StreamQuery

    return {
        stream_id: [
            StreamQuery(stream_id=stream_id, position=position, query_id=qid, sql=f"SELECT {qid}")
            for position, qid in enumerate(query_ids)
        ]
        for stream_id in range(num_streams)
    }


def test_tpcds_throughput_preflight_detects_dsqgen_batch_failure(monkeypatch):
    from benchbox.core.tpcds import streams

    def _boom(**kwargs):
        raise streams.DSQGenStreamsError("dsqgen exited 1")

    monkeypatch.setattr(streams, "generate_dsqgen_streams", _boom)

    with pytest.raises(RuntimeError) as ei:
        _mk_tpcds_throughput().run()
    assert "dsqgen -streams generation failed" in str(ei.value).lower()


def test_tpcds_throughput_preflight_detects_generation_failures(monkeypatch):
    from benchbox.core.tpcds import streams

    monkeypatch.setattr(streams, "generate_dsqgen_streams", lambda **kw: _fake_dsqgen_streams(kw["num_streams"]))

    thr = _mk_tpcds_throughput()

    def _translate(sql, source, target):
        if sql == "SELECT 7":
            raise RuntimeError("Template substitution error for q7")
        return sql

    thr.benchmark.translate_query_text = _translate

    with pytest.raises(RuntimeError) as ei:
        thr.run()
    assert "preflight failed" in str(ei.value).lower()
    assert "q7" in str(ei.value)


