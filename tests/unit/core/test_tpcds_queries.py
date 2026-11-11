import pytest

from benchbox.core.tpcds.c_tools import TPCDSError
from benchbox.core.tpcds.queries import TPCDSQueryManager

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class FakeDSQGen:
    def __init__(self):
        self.calls = []

    def generate(self, qid, *, seed=None, scale_factor=1.0, stream_id=None, dialect="ansi"):
        self.calls.append(("generate", qid, seed, scale_factor, stream_id, dialect))

        if qid == 1:
            return "-- SQL 1"
        if qid == 2:
            return "-- SQL 2"
        if qid == 3:
            raise TPCDSError("missing query")
        if qid == 4:
            raise ValueError("bad id")
        return f"-- SQL {qid}"

    def get_query_variations(self, qid):
        self.calls.append(("variations", qid))
        return [str(qid), f"{qid}a", f"{qid}b"]

    def validate_query_id(self, qid):
        self.calls.append(("validate", qid))
        try:
            qi = int(qid)
        except Exception:
            return False
        return 1 <= qi <= 99

    def generate_with_parameters(self, qid, params, *, scale_factor=1.0, dialect="ansi"):
        self.calls.append(("generate_with_params", qid, params, scale_factor, dialect))
        return f"-- SQL {qid} with {sorted(params.keys())}"


def make_manager_with_fake():
    mgr = TPCDSQueryManager()
    mgr.dsqgen = FakeDSQGen()
    return mgr


def test_get_query_validates_inputs_and_calls_generate():
    mgr = make_manager_with_fake()

    with pytest.raises(TypeError):
        mgr.get_query("1")
    with pytest.raises(ValueError):
        mgr.get_query(0)
    with pytest.raises(ValueError):
        mgr.get_query(100)
    with pytest.raises(TypeError):
        mgr.get_query(1, scale_factor="x")
    with pytest.raises(ValueError):
        mgr.get_query(1, scale_factor=0)
    with pytest.raises(TypeError):
        mgr.get_query(1, seed=1.5)
    with pytest.raises(TypeError):
        mgr.get_query(1, stream_id="x")

    sql = mgr.get_query(1, seed=42, scale_factor=10.0, stream_id=2, dialect="ansi")
    assert "SQL 1" in sql

    assert ("generate", 1, 42, 10.0, 2, "ansi") in mgr.dsqgen.calls


def test_get_query_forwards_variant_to_dsqgen():
    mgr = make_manager_with_fake()

    sql = mgr.get_query(14, seed=1, scale_factor=2.0, stream_id=3, dialect="ansi", variant="a")
    assert "SQL 14a" in sql
    assert ("generate", "14a", 1, 2.0, 3, "ansi") in mgr.dsqgen.calls

    with pytest.raises(ValueError):
        mgr.get_query(14, variant="z")
    with pytest.raises(TypeError):
        mgr.get_query(14, variant=1)


def test_get_all_queries_skips_failures_and_collects():
    mgr = make_manager_with_fake()

    res = mgr.get_all_queries(scale_factor=1.0)
    assert 1 in res and 2 in res
    assert 3 not in res and 4 not in res


def test_get_all_queries_raises_when_all_zero_queries_generated():
    mgr = TPCDSQueryManager()
    mgr.dsqgen = FakeDSQGen()

    def always_fail(qid, **kwargs):
        raise TPCDSError("dsqgen binary not found")

    mgr.dsqgen.generate = always_fail
    with pytest.raises(RuntimeError, match="dsqgen failed for all"):
        mgr.get_all_queries(scale_factor=1.0)


def test_get_all_queries_logs_warnings_for_individual_failures(caplog):
    import logging

    mgr = make_manager_with_fake()

    with caplog.at_level(logging.WARNING, logger="benchbox.core.tpcds.queries"):
        result = mgr.get_all_queries(scale_factor=1.0)

    assert any("dsqgen failed for query" in r.message for r in caplog.records)

    assert 1 in result and 2 in result


def test_variations_validate_and_params_delegate():
    mgr = make_manager_with_fake()
    vars_ = mgr.get_query_variations(14)
    assert vars_ == ["14", "14a", "14b"]
    assert mgr.validate_query_id("15") is True
    assert mgr.validate_query_id("not") is False

    sql = mgr.generate_with_parameters(10, {"X": 1, "Y": 2}, scale_factor=1.0, dialect="ansi")
    assert "SQL 10 with ['X', 'Y']" in sql
    assert any(c[0] == "generate_with_params" for c in mgr.dsqgen.calls)


