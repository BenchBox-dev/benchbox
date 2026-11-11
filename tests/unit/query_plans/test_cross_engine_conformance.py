from __future__ import annotations

from pathlib import Path

import pytest

from benchbox.core.query_plans.comparison import (
    comparable_subset_signature,
    structural_backbone_counts,
    structural_backbones_match,
)
from benchbox.core.query_plans.parsers.registry import get_parser_for_platform
from benchbox.core.results.query_plan_models import (
    LogicalOperator,
    LogicalOperatorType,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "query_plans"


def _load(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def _op(op_type: LogicalOperatorType, children=None) -> LogicalOperator:
    return LogicalOperator(
        operator_type=op_type,
        operator_id=f"{op_type.value.lower()}_{id(children)}",
        children=children or [],
    )


_CORPUS: dict[str, dict] = {
    "tpch_join_aggregate": {
        "invariant": {
            LogicalOperatorType.SCAN.value: 2,
            LogicalOperatorType.JOIN.value: 1,
            LogicalOperatorType.AGGREGATE.value: 1,
        },
        "fixtures": {
            "clickhouse": "clickhouse_explain_plan_sample.txt",
            "trino": "trino_explain_sample.json",
            "presto": "presto_explain_sample.json",
            "spark": "spark_explain_sample.txt",
            "snowflake": "snowflake_explain_sample.json",
            "databend": "databend_explain_sample.txt",
            "doris": "doris_shape_plan_sample.txt",
            "singlestore": "singlestore_explain_sample.txt",
            "firebolt": "firebolt_explain_sample.txt",
            "duckdb": "motherduck_duckdb_explain_sample.json",
        },
    },
}


def _corpus_cases():
    cases = []
    for query_id, spec in _CORPUS.items():
        for engine, fixture in spec["fixtures"].items():
            cases.append(pytest.param(query_id, engine, fixture, id=f"{query_id}-{engine}"))
    return cases


class TestComparableSubsetProjection:
    def test_drops_wrapper_nodes(self):
        tree = _op(
            LogicalOperatorType.PROJECT,
            [
                _op(
                    LogicalOperatorType.FILTER,
                    [
                        _op(
                            LogicalOperatorType.OTHER,
                            [_op(LogicalOperatorType.SCAN)],
                        )
                    ],
                )
            ],
        )
        assert structural_backbone_counts(tree) == {LogicalOperatorType.SCAN.value: 1}

    def test_collapses_consecutive_same_type(self):
        tree = _op(
            LogicalOperatorType.AGGREGATE,
            [_op(LogicalOperatorType.AGGREGATE, [_op(LogicalOperatorType.SCAN)])],
        )
        assert structural_backbone_counts(tree) == {
            LogicalOperatorType.AGGREGATE.value: 1,
            LogicalOperatorType.SCAN.value: 1,
        }

    def test_collapse_looks_through_wrappers(self):
        tree = _op(
            LogicalOperatorType.AGGREGATE,
            [
                _op(
                    LogicalOperatorType.OTHER,
                    [_op(LogicalOperatorType.AGGREGATE, [_op(LogicalOperatorType.SCAN)])],
                )
            ],
        )
        assert structural_backbone_counts(tree)[LogicalOperatorType.AGGREGATE.value] == 1

    @pytest.mark.parametrize(
        "boundary",
        [
            LogicalOperatorType.FILTER,
            LogicalOperatorType.PROJECT,
            LogicalOperatorType.SUBQUERY,
            LogicalOperatorType.CTE,
        ],
    )
    def test_semantic_boundary_resets_collapse(self, boundary):
        tree = _op(
            LogicalOperatorType.AGGREGATE,
            [_op(boundary, [_op(LogicalOperatorType.AGGREGATE, [_op(LogicalOperatorType.SCAN)])])],
        )
        assert structural_backbone_counts(tree)[LogicalOperatorType.AGGREGATE.value] == 2

    def test_stacked_joins_are_not_collapsed(self):
        tree = _op(
            LogicalOperatorType.JOIN,
            [
                _op(
                    LogicalOperatorType.JOIN,
                    [_op(LogicalOperatorType.SCAN), _op(LogicalOperatorType.SCAN)],
                ),
                _op(LogicalOperatorType.SCAN),
            ],
        )
        counts = structural_backbone_counts(tree)
        assert counts[LogicalOperatorType.JOIN.value] == 2
        assert counts[LogicalOperatorType.SCAN.value] == 3

    def test_stacked_scans_are_not_collapsed(self):
        tree = _op(LogicalOperatorType.SCAN, [_op(LogicalOperatorType.SCAN)])
        assert structural_backbone_counts(tree)[LogicalOperatorType.SCAN.value] == 2

    def test_unrelated_aggregates_separated_by_join_both_count(self):
        tree = _op(
            LogicalOperatorType.AGGREGATE,
            [
                _op(
                    LogicalOperatorType.JOIN,
                    [
                        _op(LogicalOperatorType.AGGREGATE, [_op(LogicalOperatorType.SCAN)]),
                        _op(LogicalOperatorType.SCAN),
                    ],
                )
            ],
        )
        assert structural_backbone_counts(tree)[LogicalOperatorType.AGGREGATE.value] == 2

    def test_empty_only_is_rejected(self):
        tree = _op(LogicalOperatorType.SCAN)
        with pytest.raises(ValueError):
            structural_backbone_counts(tree, only=set())

    def test_sibling_scans_counted_separately(self):
        tree = _op(
            LogicalOperatorType.JOIN,
            [_op(LogicalOperatorType.SCAN), _op(LogicalOperatorType.SCAN)],
        )
        counts = structural_backbone_counts(tree)
        assert counts[LogicalOperatorType.SCAN.value] == 2
        assert counts[LogicalOperatorType.JOIN.value] == 1

    def test_only_restriction_reports_missing_as_zero(self):
        tree = _op(LogicalOperatorType.SCAN)
        counts = structural_backbone_counts(tree, only={LogicalOperatorType.SCAN.value, LogicalOperatorType.JOIN.value})
        assert counts == {LogicalOperatorType.SCAN.value: 1, LogicalOperatorType.JOIN.value: 0}

    def test_signature_is_deterministic_and_sorted(self):
        tree = _op(
            LogicalOperatorType.JOIN,
            [_op(LogicalOperatorType.SCAN), _op(LogicalOperatorType.SCAN)],
        )
        assert comparable_subset_signature(tree) == "Join:1|Scan:2"

    def test_backbones_match_ignores_wrappers(self):
        bare = _op(
            LogicalOperatorType.JOIN,
            [_op(LogicalOperatorType.SCAN), _op(LogicalOperatorType.SCAN)],
        )
        wrapped = _op(
            LogicalOperatorType.PROJECT,
            [
                _op(
                    LogicalOperatorType.JOIN,
                    [
                        _op(LogicalOperatorType.OTHER, [_op(LogicalOperatorType.SCAN)]),
                        _op(LogicalOperatorType.SCAN),
                    ],
                )
            ],
        )
        assert structural_backbones_match(bare, wrapped) is True


class TestCrossEngineConformanceCorpus:
    @pytest.mark.parametrize("query_id, engine, fixture", _corpus_cases())
    def test_engine_meets_backbone_invariant(self, query_id, engine, fixture):
        invariant = _CORPUS[query_id]["invariant"]
        dag = get_parser_for_platform(engine).parse_explain_output(query_id, _load(fixture))
        assert dag is not None, f"{engine} fixture {fixture} failed to parse"

        counts = structural_backbone_counts(dag.logical_root, only=set(invariant))
        assert counts == invariant, (
            f"{engine} backbone {counts} does not meet the {query_id} invariant {invariant}. "
            "A parser's operator harmonization changed; justify it or fix the mapping."
        )

    @pytest.mark.parametrize("query_id", sorted(_CORPUS))
    def test_operator_shape_signature_is_identical_across_engines(self, query_id):
        invariant = _CORPUS[query_id]["invariant"]
        signatures = {}
        for engine, fixture in _CORPUS[query_id]["fixtures"].items():
            dag = get_parser_for_platform(engine).parse_explain_output(query_id, _load(fixture))
            signatures[engine] = comparable_subset_signature(dag.logical_root, only=set(invariant))

        distinct = set(signatures.values())
        assert len(distinct) == 1, (
            f"Operator-shape signatures diverged across engines for {query_id}: {signatures}. "
            "The wrapper-stripped operator-shape backbone must be identical across engines for the same query."
        )

    @pytest.mark.parametrize("query_id", sorted(_CORPUS))
    def test_raw_fingerprints_remain_per_engine_distinct(self, query_id):
        fingerprints = {}
        for engine, fixture in _CORPUS[query_id]["fixtures"].items():
            dag = get_parser_for_platform(engine).parse_explain_output(query_id, _load(fixture))
            fingerprints[engine] = dag.plan_fingerprint

        assert len(set(fingerprints.values())) == len(fingerprints), (
            "Raw per-engine fingerprints unexpectedly collided across engines; "
            "the raw fingerprint is documented as NOT cross-engine comparable."
        )


