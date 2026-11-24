import pytest

from tests.uat import compatibility, matrix

pytestmark = pytest.mark.medium


def _rule(benchmark, scale):
    benchmarks = matrix.load_benchmarks()
    return compatibility.compatibility_rule_for(
        "doris",
        benchmark,
        benchmarks[benchmark],
        include_release_gate_runtime_envelopes=True,
        scale=scale,
    )


def test_doris_joinorder_is_pruned_with_the_memory_evidence():
    rule = _rule("joinorder", 1.0)

    assert rule is not None
    assert rule.rule_id == "uat.compat.doris.joinorder.release_gate_runtime_envelope"
    assert rule.status == "blocked"
    assert "MEM_LIMIT_EXCEEDED" in rule.reason
    assert "query 17d" in rule.reason
    assert "45155067627" in rule.evidence
    assert "45389336355" in rule.evidence


def test_doris_joinorder_stays_enabled_outside_the_release_gate():
    benchmarks = matrix.load_benchmarks()

    assert (
        compatibility.compatibility_rule_for(
            "doris", "joinorder", benchmarks["joinorder"], include_release_gate_runtime_envelopes=False, scale=1.0
        )
        is None
    )


@pytest.mark.parametrize("benchmark_id", ["tpch", "tpcds", "ssb", "clickbench", "amplab", "tpchavoc"])
def test_other_doris_cells_stay_in_the_gate(benchmark_id):
    assert _rule(benchmark_id, 0.01) is None
