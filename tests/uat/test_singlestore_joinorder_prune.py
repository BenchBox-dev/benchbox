import pytest

from tests.uat import compatibility, matrix

pytestmark = pytest.mark.medium


def _rule(benchmark, scale=None):
    benchmarks = matrix.load_benchmarks()
    return compatibility.compatibility_rule_for(
        "singlestore",
        benchmark,
        benchmarks[benchmark],
        include_release_gate_runtime_envelopes=True,
        scale=scale,
    )


def test_singlestore_joinorder_is_pruned_with_the_oom_evidence():
    rule = _rule("joinorder", 1.0)

    assert rule is not None
    assert rule.rule_id == "uat.compat.singlestore.joinorder.release_gate_runtime_envelope"
    assert rule.status == "blocked"
    assert "timed out at 1200s" in rule.reason
    assert "OOM-kills memsqld" in rule.reason
    assert "query 7c" in rule.reason
    assert "no smaller scale exists" in rule.reason
    assert "oom-kill" in rule.evidence


@pytest.mark.parametrize(
    "benchmark_id",
    [
        "tpcds",
        "tpcdi",
        "clickbench",
        "read_primitives",
        "write_primitives",
        "tpchavoc",
        "nyctaxi",
        "vector_search",
        "metadata_primitives",
        "tpcds_obt",
    ],
)
def test_other_singlestore_cells_stay_in_the_gate(benchmark_id):
    assert _rule(benchmark_id, 0.01) is None
    assert _rule(benchmark_id, 1.0) is None


