import pytest

from tests.uat import compatibility, matrix

pytestmark = pytest.mark.medium

_PRUNED_CELLS = {
    ("tpcdi", 0.01): "square root of a negative number",
    ("tpcdi", 0.1): "square root of a negative number",
    ("tpcdi", 1.0): "unable to allocate working memory",
    ("read_primitives", 0.01): "to_date(text, text)",
    ("read_primitives", 0.1): "IS JSON is not implemented",
    ("write_primitives", 0.01): "current transaction is aborted",
    ("write_primitives", 0.1): "current transaction is aborted",
    ("write_primitives", 1.0): "current transaction is aborted",
    ("transaction_primitives", 0.01): "current transaction is aborted",
    ("transaction_primitives", 0.1): "current transaction is aborted",
    ("transaction_primitives", 1.0): "current transaction is aborted",
    ("flightdata", 1.0): "exit 124",
    ("vector_search", 0.01): "CREATE EXTENSION not implemented yet",
    ("vector_search", 0.1): "CREATE EXTENSION not implemented yet",
    ("vector_search", 1.0): "CREATE EXTENSION not implemented yet",
}

_TRINO_PRESTO_PRUNED_CELLS = {
    ("trino", "tpcds_obt"): "Column 'd_year' cannot be resolved",
    ("trino", "tpcdi"): "boolean = integer",
    ("trino", "clickbench"): "Column 'm' cannot be resolved",
    ("trino", "amplab"): "date <= varchar(10)",
    ("trino", "read_primitives"): "approx_most_frequent",
    ("trino", "coffeeshop"): "Column 'day_part' cannot be resolved",
    ("trino", "tsbs_devops"): "timestamp(3) <= varchar(19)",
    ("trino", "nyctaxi"): "timestamp(3) <= varchar(10)",
    ("trino", "flightdata"): "date <= varchar(10)",
    ("trino", "vector_search"): "array_cosine_similarity",
    ("presto", "tpcds_obt"): "'channel'",
    ("presto", "tpcdi"): "boolean, integer",
    ("presto", "clickbench"): "date, varchar(10)",
    ("presto", "amplab"): "date, varchar(10)",
    ("presto", "coffeeshop"): "dim_locations",
    ("presto", "tsbs_devops"): "'hostname'",
    ("presto", "nyctaxi"): "timestamp, varchar(10)",
    ("presto", "flightdata"): "date, varchar(10)",
    ("presto", "datavault"): "MEMORY_LIMIT_EXCEEDED",
    ("presto", "vector_search"): "array(real)",
}

_TPCDI_SF1_PRUNED_PLATFORMS = ("spark", "datafusion")


def _rule(platform, bench_name, scale):
    benchmarks = matrix.load_benchmarks()
    return compatibility.compatibility_rule_for(
        platform,
        bench_name,
        benchmarks[bench_name],
        include_release_gate_runtime_envelopes=True,
        scale=scale,
    )


@pytest.mark.parametrize(("bench_name", "scale"), sorted(_PRUNED_CELLS))
def test_cedardb_failing_cells_are_pruned_with_run_evidence(bench_name, scale):
    rule = _rule("cedardb", bench_name, scale)

    assert rule is not None
    assert rule.rule_id == f"uat.compat.cedardb.{bench_name}.sf{scale:g}.release_gate_runtime_envelope"
    assert rule.status == "blocked"
    assert "43154678095" in rule.evidence
    assert "43154678095" in rule.reason
    assert _PRUNED_CELLS[(bench_name, scale)] in rule.reason


@pytest.mark.parametrize(
    ("bench_name", "scale"),
    [("read_primitives", 1.0), ("flightdata", 0.01), ("flightdata", 0.1), ("tpch", 0.01), ("datavault", 1.0)],
)
def test_cedardb_passing_cells_stay_in_the_gate(bench_name, scale):
    assert _rule("cedardb", bench_name, scale) is None


@pytest.mark.parametrize("bench_name", sorted({bench_name for bench_name, _ in _PRUNED_CELLS}))
def test_other_platforms_keep_the_benchmark(bench_name):
    assert _rule("duckdb", bench_name, 0.01) is None


def test_stage2_enumeration_keeps_only_the_passing_cedardb_cells():
    from pathlib import Path

    from tests.uat import config
    from tests.uat.phases.enumerate import enumerate_cells_with_pruning

    stage = config.load_config(Path(__file__).parent / "configs" / "release-gate-02-docker-nonoltp.yaml")
    cells = {
        (cell.benchmark, cell.scale) for cell in enumerate_cells_with_pruning(stage).cells if cell.platform == "cedardb"
    }

    assert len(cells) == 34
    assert cells.isdisjoint(_PRUNED_CELLS)


@pytest.mark.parametrize(("platform", "bench_name"), sorted(_TRINO_PRESTO_PRUNED_CELLS))
def test_trino_and_presto_failing_sf001_cells_are_pruned_with_run_evidence(platform, bench_name):
    rule = _rule(platform, bench_name, 0.01)

    assert rule is not None
    assert rule.rule_id == f"uat.compat.{platform}.{bench_name}.sf0.01.release_gate_runtime_envelope"
    assert "43154678095" in rule.evidence
    assert "43154678095" in rule.reason
    assert _TRINO_PRESTO_PRUNED_CELLS[(platform, bench_name)] in rule.reason


@pytest.mark.parametrize("platform", ["trino", "presto"])
def test_trino_and_presto_keep_tpcds_and_other_scales(platform):
    assert _rule(platform, "tpch", 0.01) is None
    assert _rule(platform, "tpcdi", 0.1) is None


@pytest.mark.parametrize("platform", _TPCDI_SF1_PRUNED_PLATFORMS)
def test_tpcdi_sf1_is_pruned_after_the_fact_table_growth(platform):
    rule = _rule(platform, "tpcdi", 1.0)

    assert rule is not None
    assert rule.rule_id == f"uat.compat.{platform}.tpcdi.sf1.release_gate_runtime_envelope"
    assert "275k to 6.77M rows" in rule.reason
    assert "timed out at 1200s" in rule.reason
    assert "43154678095" in rule.evidence
    assert _rule(platform, "tpcdi", 0.1) is None


def test_tpcdi_sf1_stays_in_the_gate_on_clickhouse_local():
    assert _rule("clickhouse-local", "tpcdi", 1.0) is None


@pytest.mark.parametrize(("platform", "detail"), [("trino", "date + integer"), ("presto", "QUERY_HAS_TOO_MANY_STAGES")])
def test_tpcds_sf001_is_pruned_on_trino_and_presto(platform, detail):
    rule = _rule(platform, "tpcds", 0.01)

    assert rule is not None
    assert rule.rule_id == f"uat.compat.{platform}.tpcds.sf0.01.release_gate_runtime_envelope"
    assert "43229090202" in rule.evidence
    assert "43229090202" in rule.reason
    assert detail in rule.reason
    assert _rule(platform, "tpcds", 0.1) is None
