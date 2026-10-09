from __future__ import annotations

from unittest.mock import Mock

import pytest

from benchbox.core.tuning.applied_ledger import (
    APPLIED_UNVERIFIED,
    FAILED,
    NOOP,
    PHASE_DDL,
    PHASE_POST_LOAD,
    PHASE_SESSION,
    SATISFIED_BY_PREEXISTING_STATE,
    STATEMENT_FAILED,
    AppliedTuningLedger,
)
from benchbox.core.tuning.capability_registry import get_capability
from benchbox.core.tuning.interface import (
    PlatformOptimizationConfiguration,
    TableTuning,
    TuningColumn,
    TuningType,
    UnifiedTuningConfiguration,
)
from benchbox.core.tuning.introspection import (
    CORROBORATED,
    KIND_INDEX,
    KIND_SORT_KEY,
    IntrospectedObject,
    IntrospectedState,
    corroborate,
)
from benchbox.core.tuning.reconciliation import (
    DDL_REALIZED_REASON,
    NOT_RENDERED_REASON,
    RequestedIntent,
    declared_constraint_types,
    reconcile_requested_intents,
    requested_intents,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

DUCKDB_SORT_INDEX = 'CREATE INDEX IF NOT EXISTS idx_lineitem_sort ON "lineitem" ("l_orderkey", "l_linenumber")'
CLICKHOUSE_DDL = (
    "CREATE TABLE lineitem (l_orderkey Int32, l_linenumber Int32, l_shipdate Date) "
    "ENGINE = MergeTree() PARTITION BY (toYYYYMM(l_shipdate)) ORDER BY (l_orderkey, l_linenumber)"
)


def _columns(*names: str) -> list[TuningColumn]:
    return [TuningColumn(name=name, type="INTEGER", order=order) for order, name in enumerate(names, start=1)]


def _config(*, constraints: bool = False, **slots: list[TuningColumn]) -> UnifiedTuningConfiguration:
    config = UnifiedTuningConfiguration()
    if not constraints:
        config.disable_all_constraints()
    if slots:
        config.table_tunings["LINEITEM"] = TableTuning(table_name="LINEITEM", **slots)
    return config


def _sorted_config() -> UnifiedTuningConfiguration:
    return _config(sorting=_columns("L_ORDERKEY", "L_LINENUMBER"))


def _reconcile(config: UnifiedTuningConfiguration, ledger: AppliedTuningLedger, platform: str, **kwargs) -> None:
    reconcile_requested_intents(config, ledger, platform, **kwargs)


class TestRequestedIntents:
    def test_table_layout_constraint_toggles_and_platform_optimizations_are_intents(self):
        config = _config(
            constraints=True,
            partitioning=_columns("L_SHIPDATE"),
            sorting=[
                TuningColumn(name="L_LINENUMBER", type="INTEGER", order=2),
                TuningColumn(name="L_ORDERKEY", type="INTEGER", order=1),
            ],
        )
        config.check_constraints.enabled = False
        config.platform_optimizations = PlatformOptimizationConfiguration(bloom_filters_enabled=True)

        intents = requested_intents(config)

        assert intents == [
            RequestedIntent(TuningType.PARTITIONING, "LINEITEM", ("L_SHIPDATE",)),
            RequestedIntent(TuningType.SORTING, "LINEITEM", ("L_ORDERKEY", "L_LINENUMBER")),
            RequestedIntent(TuningType.PRIMARY_KEYS),
            RequestedIntent(TuningType.FOREIGN_KEYS),
            RequestedIntent(TuningType.UNIQUE_CONSTRAINTS),
            RequestedIntent(TuningType.BLOOM_FILTERS),
        ]
        assert intents[1].label == "sorting:LINEITEM (L_ORDERKEY, L_LINENUMBER)"
        assert intents[2].label == "primary_keys"

    def test_disabled_constraints_and_empty_slots_request_nothing(self):
        assert requested_intents(_config()) == []


class TestEvidence:
    def test_matching_executed_statement_is_done_without_an_entry(self):
        ledger = AppliedTuningLedger()
        ledger.record(DUCKDB_SORT_INDEX, PHASE_DDL)

        _reconcile(_sorted_config(), ledger, "duckdb")

        assert ledger.dropped == []
        assert ledger.satisfied == []

    def test_post_load_statement_is_evidence(self):
        ledger = AppliedTuningLedger()
        ledger.record(DUCKDB_SORT_INDEX, PHASE_POST_LOAD, mechanism="sort_index", table="lineitem")

        _reconcile(_sorted_config(), ledger, "duckdb")

        assert ledger.dropped == []

    def test_statement_on_other_columns_is_not_evidence(self):
        ledger = AppliedTuningLedger()
        ledger.record('CREATE INDEX idx_lineitem_sort ON "lineitem" ("l_partkey")', PHASE_DDL)

        _reconcile(_sorted_config(), ledger, "duckdb")

        [dropped] = ledger.dropped
        assert dropped.intent == "sorting:LINEITEM (L_ORDERKEY, L_LINENUMBER)"
        assert dropped.reason == NOT_RENDERED_REASON

    def test_statement_on_other_table_is_not_evidence(self):
        ledger = AppliedTuningLedger()
        ledger.record('CREATE INDEX idx_orders_sort ON "orders" ("l_orderkey", "l_linenumber")', PHASE_DDL)

        _reconcile(_sorted_config(), ledger, "duckdb")

        assert len(ledger.dropped) == 1

    def test_session_statement_is_never_evidence(self):
        ledger = AppliedTuningLedger()
        ledger.record("SET preserve_insertion_order = false", PHASE_SESSION)
        ledger.record(DUCKDB_SORT_INDEX, PHASE_SESSION)

        _reconcile(_sorted_config(), ledger, "duckdb")

        assert len(ledger.dropped) == 1

    def test_clickhouse_fallback_order_by_does_not_realize_a_sort(self):
        ledger = AppliedTuningLedger()
        ledger.record("CREATE TABLE lineitem (l_orderkey Int32) ENGINE = MergeTree() ORDER BY tuple()", PHASE_DDL)

        _reconcile(_sorted_config(), ledger, "clickhouse-local")

        assert ledger.satisfied == []
        [dropped] = ledger.dropped
        assert dropped.reason == NOT_RENDERED_REASON

    def test_column_named_only_inside_a_string_literal_is_not_evidence(self):
        ledger = AppliedTuningLedger()
        ledger.record(
            "CREATE TABLE lineitem (l_orderkey Int32) ENGINE = MergeTree() ORDER BY tuple() "
            "COMMENT 'l_orderkey l_linenumber'",
            PHASE_DDL,
        )

        _reconcile(_sorted_config(), ledger, "clickhouse-local")

        assert ledger.satisfied == []
        assert len(ledger.dropped) == 1

    def test_columns_in_another_order_are_not_evidence(self):
        ledger = AppliedTuningLedger()
        ledger.record(
            "CREATE TABLE lineitem (l_orderkey Int32, l_linenumber Int32) ENGINE = MergeTree() "
            "ORDER BY (l_linenumber, l_orderkey)",
            PHASE_DDL,
        )

        _reconcile(_sorted_config(), ledger, "clickhouse-local")

        assert ledger.satisfied == []
        assert len(ledger.dropped) == 1

    def test_index_predicate_columns_are_not_index_columns(self):
        ledger = AppliedTuningLedger()
        ledger.record('CREATE INDEX idx_lineitem_sort ON "lineitem" ("l_orderkey") WHERE l_linenumber > 0', PHASE_DDL)

        _reconcile(_sorted_config(), ledger, "duckdb")

        assert len(ledger.dropped) == 1

    def test_folded_clustering_columns_ahead_of_the_sort_still_realize_it(self):
        ledger = AppliedTuningLedger()
        ledger.record(
            "CREATE TABLE lineitem (l_shipdate Date, l_orderkey Int32, l_linenumber Int32) ENGINE = MergeTree() "
            "ORDER BY (l_shipdate, l_orderkey, l_linenumber)",
            PHASE_DDL,
        )

        _reconcile(_sorted_config(), ledger, "clickhouse-local")

        assert ledger.dropped == []
        assert len(ledger.satisfied) == 1

    def test_backtick_quoted_starrocks_clauses_are_evidence(self):
        ledger = AppliedTuningLedger()
        ledger.record("DISTRIBUTED BY HASH(`l_orderkey`) BUCKETS 8", PHASE_DDL, table="lineitem")
        ledger.record("PRIMARY KEY (l_orderkey, l_linenumber)", PHASE_DDL, table="lineitem")
        config = _config(distribution=_columns("L_ORDERKEY"))
        config.primary_keys.enabled = True

        _reconcile(config, ledger, "starrocks")

        assert ledger.dropped == []
        assert [(item.intent, item.satisfied_by) for item in ledger.satisfied] == [
            ("distribution:LINEITEM (L_ORDERKEY)", 0)
        ]

    def test_property_style_columns_inside_literals_are_evidence(self):
        ledger = AppliedTuningLedger()
        ledger.record(
            "CREATE TABLE lineitem (l_orderkey BIGINT, l_shipdate DATE) WITH (partitioned_by = ARRAY['l_shipdate'])",
            PHASE_DDL,
        )

        _reconcile(_config(partitioning=_columns("L_SHIPDATE")), ledger, "hive")

        assert ledger.dropped == []

    def test_schema_qualified_and_quoted_table_names_match(self):
        ledger = AppliedTuningLedger()
        ledger.record('ALTER TABLE "BENCH"."LINEITEM" CLUSTER BY (L_SHIPDATE)', PHASE_DDL)

        _reconcile(_config(clustering=_columns("L_SHIPDATE")), ledger, "snowflake")

        assert ledger.dropped == []

    def test_executed_sorted_ingestion_table_is_interim_sort_evidence(self):
        ledger = AppliedTuningLedger()

        _reconcile(_sorted_config(), ledger, "redshift", sorted_tables=["lineitem"])

        assert ledger.dropped == []

    def test_executed_sorted_ingestion_never_stands_in_for_other_types(self):
        ledger = AppliedTuningLedger()

        _reconcile(_config(distribution=_columns("L_ORDERKEY")), ledger, "redshift", sorted_tables=["lineitem"])

        assert len(ledger.dropped) == 1


class TestDropReasons:
    @pytest.mark.parametrize(
        ("platform", "slot", "tuning_type"),
        [
            ("bigquery", "partitioning", TuningType.PARTITIONING),
            ("bigquery", "clustering", TuningType.CLUSTERING),
            ("postgresql", "partitioning", TuningType.PARTITIONING),
            ("redshift", "distribution", TuningType.DISTRIBUTION),
            ("trino", "sorting", TuningType.SORTING),
            ("duckdb", "partitioning", TuningType.PARTITIONING),
        ],
    )
    def test_registry_none_or_preview_only_drops_with_registry_notes(self, platform, slot, tuning_type):
        ledger = AppliedTuningLedger()

        _reconcile(_config(**{slot: _columns("L_SHIPDATE")}), ledger, platform)

        [dropped] = ledger.dropped
        capability = get_capability(platform, tuning_type)
        assert capability is not None and capability.rendered_via == "none"
        assert dropped.reason == capability.notes

    @pytest.mark.parametrize("platform", ["datafusion", "emr-serverless", "cedardb", "influxdb"])
    def test_platform_without_registry_entry_drops_as_not_rendered(self, platform):
        ledger = AppliedTuningLedger()

        _reconcile(_config(sorting=_columns("L_ORDERKEY")), ledger, platform)

        [dropped] = ledger.dropped
        assert dropped.reason == NOT_RENDERED_REASON

    def test_registry_rendered_type_without_evidence_drops_as_not_rendered(self):
        ledger = AppliedTuningLedger()

        _reconcile(_sorted_config(), ledger, "duckdb")

        [dropped] = ledger.dropped
        assert dropped.reason == NOT_RENDERED_REASON


class TestDDLRealizedIntents:
    def test_ddl_realized_intents_are_satisfied_by_the_executed_statement(self):
        ledger = AppliedTuningLedger()
        ledger.record("SET max_threads = 4", PHASE_SESSION)
        ledger.record(CLICKHOUSE_DDL, PHASE_DDL)

        _reconcile(
            _config(partitioning=_columns("L_SHIPDATE"), sorting=_columns("L_ORDERKEY", "L_LINENUMBER")),
            ledger,
            "chdb",
        )

        assert ledger.dropped == []
        assert [(item.intent, item.satisfied_by, item.reason) for item in ledger.satisfied] == [
            ("partitioning:LINEITEM (L_SHIPDATE)", 1, DDL_REALIZED_REASON),
            ("sorting:LINEITEM (L_ORDERKEY, L_LINENUMBER)", 1, DDL_REALIZED_REASON),
        ]

    def test_existing_sorted_ingestion_satisfied_entry_is_not_duplicated(self):
        ledger = AppliedTuningLedger()
        ledger.record(CLICKHOUSE_DDL, PHASE_DDL)
        ledger.record_satisfied("sorted_ingestion lineitem ORDER BY l_orderkey, l_linenumber", 0, "sort realized")

        _reconcile(_sorted_config(), ledger, "clickhouse-local")

        assert [item.intent for item in ledger.satisfied] == [
            "sorted_ingestion lineitem ORDER BY l_orderkey, l_linenumber"
        ]

    def test_post_load_evidence_is_done_not_satisfied(self):
        ledger = AppliedTuningLedger()
        ledger.record("ALTER TABLE LINEITEM CLUSTER BY (L_SHIPDATE)", PHASE_POST_LOAD)

        _reconcile(_config(clustering=_columns("L_SHIPDATE")), ledger, "snowflake")

        assert ledger.dropped == []
        assert ledger.satisfied == []

    def test_inline_constraints_are_satisfied_by_the_create_table(self):
        ledger = AppliedTuningLedger()
        ledger.record("CREATE TABLE nation (n_nationkey INTEGER PRIMARY KEY, n_name VARCHAR)", PHASE_DDL)
        config = UnifiedTuningConfiguration()
        config.disable_all_constraints()
        config.primary_keys.enabled = True

        _reconcile(config, ledger, "duckdb")

        assert [(item.intent, item.satisfied_by) for item in ledger.satisfied] == [("primary_keys", 0)]
        assert ledger.dropped == []

    def test_satisfied_entries_never_change_the_corroboration_outcome(self):
        state = IntrospectedState(
            platform="clickhouse",
            objects=[IntrospectedObject(kind=KIND_SORT_KEY, table="lineitem", columns=("l_orderkey",))],
        )
        ledger = AppliedTuningLedger()
        ledger.record(CLICKHOUSE_DDL, PHASE_DDL)
        before = corroborate(ledger, state)

        _reconcile(_config(partitioning=_columns("L_SHIPDATE")), ledger, "clickhouse-local")
        after = corroborate(ledger, state)

        assert ledger.satisfied and ledger.dropped == []
        assert [entry.verdict for entry in after.entries] == [entry.verdict for entry in before.entries]
        assert after.corroborated is before.corroborated is False


class TestExistingOutcomes:
    def test_adapter_drop_for_the_same_intent_is_not_duplicated(self):
        ledger = AppliedTuningLedger()
        ledger.record_dropped("partitioning:LINEITEM", "DuckDB applies partitioning at data-loading time, not via DDL")

        _reconcile(_config(partitioning=_columns("L_SHIPDATE")), ledger, "duckdb")

        assert [item.intent for item in ledger.dropped] == ["partitioning:LINEITEM"]

    def test_sorted_ingestion_drop_accounts_for_the_sort(self):
        ledger = AppliedTuningLedger()
        ledger.record_dropped("sorted_ingestion LINEITEM ORDER BY L_ORDERKEY", "sorted_ingestion_mode=off")

        _reconcile(_sorted_config(), ledger, "snowflake")

        assert len(ledger.dropped) == 1

    def test_failed_attempt_accounts_for_the_intent(self):
        ledger = AppliedTuningLedger()
        ledger.record(DUCKDB_SORT_INDEX, PHASE_DDL, status=STATEMENT_FAILED, error="boom")

        _reconcile(_sorted_config(), ledger, "duckdb")

        assert ledger.dropped == []

    def test_preexisting_snowflake_clustering_accounts_for_folded_partitioning(self):
        ledger = AppliedTuningLedger()
        ledger.record_satisfied(
            "ALTER TABLE LINEITEM CLUSTER BY (L_SHIPDATE)",
            SATISFIED_BY_PREEXISTING_STATE,
            "already present in Snowflake catalog; ALTER skipped",
        )

        _reconcile(_config(partitioning=_columns("L_SHIPDATE")), ledger, "snowflake")

        assert ledger.dropped == []
        assert len(ledger.satisfied) == 1

    def test_skipped_platform_optimization_accounts_for_the_flag(self):
        ledger = AppliedTuningLedger()
        ledger.record_dropped("platform_optimization:bloom_filters_enabled", "not mapped")
        config = _config()
        config.platform_optimizations = PlatformOptimizationConfiguration(bloom_filters_enabled=True)

        _reconcile(config, ledger, "snowpark-connect")

        assert len(ledger.dropped) == 1


class TestNeverMoreVerified:
    def test_reconciliation_only_appends_outcomes_and_keeps_statements_and_hash(self):
        ledger = AppliedTuningLedger()
        ledger.record(DUCKDB_SORT_INDEX, PHASE_DDL)
        ledger.record("CREATE TABLE t (a INT PRIMARY KEY)", PHASE_DDL, status=STATEMENT_FAILED, error="x")
        ledger.record_dropped("distribution:LINEITEM", "single node")
        statements = [statement.to_dict() for statement in ledger.statements]
        ledger_hash = ledger.applied_ledger_hash()
        dropped = list(ledger.dropped)
        config = _config(
            constraints=True, sorting=_columns("L_ORDERKEY", "L_LINENUMBER"), clustering=_columns("L_SHIPDATE")
        )

        _reconcile(config, ledger, "duckdb")

        assert [statement.to_dict() for statement in ledger.statements] == statements
        assert ledger.applied_ledger_hash() == ledger_hash
        assert ledger.dropped[: len(dropped)] == dropped
        assert len(ledger.dropped) > len(dropped)

    def test_a_reconciliation_drop_blocks_an_otherwise_corroborated_ledger(self):
        state = IntrospectedState(
            platform="duckdb",
            objects=[
                IntrospectedObject(
                    kind=KIND_INDEX, table="lineitem", columns=("l_orderkey", "l_linenumber"), name="idx_lineitem_sort"
                )
            ],
        )
        ledger = AppliedTuningLedger()
        ledger.record(DUCKDB_SORT_INDEX, PHASE_DDL)
        assert corroborate(ledger, state).corroborated is True
        config = _sorted_config()
        config.table_tunings["ORDERS"] = TableTuning(table_name="ORDERS", sorting=_columns("O_ORDERDATE"))

        _reconcile(config, ledger, "duckdb")

        receipt = corroborate(ledger, state)
        assert {entry.verdict for entry in receipt.entries} == {CORROBORATED}
        assert receipt.corroborated is False
        assert receipt.dropped == [{"intent": "sorting:ORDERS (O_ORDERDATE)", "reason": NOT_RENDERED_REASON}]


class TestStatusVocabulary:
    def test_drops_only_is_noop(self):
        ledger = AppliedTuningLedger()

        _reconcile(_sorted_config(), ledger, "datafusion")

        assert ledger.dropped
        assert ledger.overall_status(tuning_enabled=True, has_config=True) == NOOP

    def test_executed_statements_with_drops_are_applied_unverified(self):
        ledger = AppliedTuningLedger()
        ledger.record("ANALYZE lineitem", PHASE_POST_LOAD)

        _reconcile(_config(distribution=_columns("L_ORDERKEY")), ledger, "redshift")

        assert ledger.dropped
        assert ledger.overall_status(tuning_enabled=True, has_config=True) == APPLIED_UNVERIFIED

    def test_drops_never_produce_failed(self):
        ledger = AppliedTuningLedger()
        ledger.record("SET x = 1", PHASE_SESSION)

        _reconcile(_config(constraints=True, sorting=_columns("L_ORDERKEY")), ledger, "polars")

        assert len(ledger.dropped) == 5
        assert ledger.overall_status(tuning_enabled=True, has_config=True) == APPLIED_UNVERIFIED

    def test_failed_physical_statements_still_fail_with_drops(self):
        ledger = AppliedTuningLedger()
        ledger.record(DUCKDB_SORT_INDEX, PHASE_DDL, status=STATEMENT_FAILED, error="boom")

        _reconcile(_config(constraints=True, sorting=_columns("L_ORDERKEY", "L_LINENUMBER")), ledger, "duckdb")

        assert ledger.dropped
        assert ledger.overall_status(tuning_enabled=True, has_config=True) == FAILED


def test_mechanism_footprints_name_registry_mechanisms():
    from benchbox.core.tuning.capability_registry import PLATFORM_TUNING_CAPABILITIES
    from benchbox.core.tuning.reconciliation import _MECHANISM_FOOTPRINTS

    mechanisms = {
        capability.mechanism_id for entries in PLATFORM_TUNING_CAPABILITIES.values() for capability in entries.values()
    }
    assert set(_MECHANISM_FOOTPRINTS) <= mechanisms


class TestDeclaredConstraints:
    def test_probe_reads_the_constraint_types_a_benchmark_declares(self, tmp_path):
        from benchbox.core.ssb.benchmark import SSBBenchmark
        from benchbox.core.tpch.benchmark import TPCHBenchmark

        tpch = declared_constraint_types(TPCHBenchmark(scale_factor=0.01, output_dir=tmp_path))
        ssb = declared_constraint_types(SSBBenchmark(scale_factor=0.01, output_dir=tmp_path))

        assert tpch == {TuningType.PRIMARY_KEYS, TuningType.FOREIGN_KEYS}
        assert ssb == {TuningType.PRIMARY_KEYS}

    @pytest.mark.parametrize(
        "candidate",
        [None, object(), Mock(), Mock(get_create_tables_sql=Mock(side_effect=TypeError("no tuning_config")))],
    )
    def test_probe_is_unknown_when_the_benchmark_cannot_say(self, candidate):
        assert declared_constraint_types(candidate) is None

    def test_unique_and_check_toggles_without_declared_constraints_give_no_drop(self):
        ledger = AppliedTuningLedger()
        ledger.record("CREATE TABLE nation (n_nationkey INTEGER PRIMARY KEY)", PHASE_DDL)
        ledger.record("CREATE TABLE region (r_regionkey INTEGER REFERENCES nation (n_nationkey))", PHASE_DDL)
        config = UnifiedTuningConfiguration()

        _reconcile(
            config,
            ledger,
            "duckdb",
            declared_constraints=frozenset({TuningType.PRIMARY_KEYS, TuningType.FOREIGN_KEYS}),
        )

        assert ledger.dropped == []
        assert [item.intent for item in ledger.satisfied] == ["primary_keys", "foreign_keys"]

    @pytest.mark.parametrize("platform", ["duckdb", "datafusion", "postgresql"])
    def test_declared_keys_that_were_not_rendered_still_drop(self, platform):
        ledger = AppliedTuningLedger()

        _reconcile(
            UnifiedTuningConfiguration(),
            ledger,
            platform,
            declared_constraints=frozenset({TuningType.PRIMARY_KEYS, TuningType.FOREIGN_KEYS}),
        )

        assert {item.intent: item.reason for item in ledger.dropped} == {
            "primary_keys": NOT_RENDERED_REASON,
            "foreign_keys": NOT_RENDERED_REASON,
        }

    def test_unknown_declarations_reconcile_every_enabled_toggle(self):
        ledger = AppliedTuningLedger()

        _reconcile(UnifiedTuningConfiguration(), ledger, "datafusion", declared_constraints=None)

        assert [item.intent for item in ledger.dropped] == [
            "primary_keys",
            "foreign_keys",
            "unique_constraints",
            "check_constraints",
        ]
