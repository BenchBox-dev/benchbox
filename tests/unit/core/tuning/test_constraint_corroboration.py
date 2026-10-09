from __future__ import annotations

import pytest

from benchbox.core.tuning.applied_ledger import PHASE_DDL, AppliedStatement, AppliedTuningLedger
from benchbox.core.tuning.introspection import (
    ABSENT,
    CONSTRAINT_FOREIGN_KEY,
    CONSTRAINT_PRIMARY_KEY,
    CONSTRAINT_UNIQUE,
    CORROBORATED,
    KIND_CONSTRAINT,
    KIND_SORT_KEY,
    MISMATCH,
    UNVERIFIABLE,
    IntrospectedObject,
    IntrospectedState,
    _classify,
    _match_object,
    corroborate,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ALL_TYPES = frozenset({CONSTRAINT_PRIMARY_KEY, CONSTRAINT_UNIQUE, CONSTRAINT_FOREIGN_KEY})

ORDERS_DDL = """CREATE TABLE orders (
    o_orderkey INTEGER NOT NULL,
    o_custkey INTEGER NOT NULL,
    o_comment VARCHAR(79) NOT NULL,
    PRIMARY KEY (o_orderkey),
    UNIQUE (o_custkey, o_comment),
    FOREIGN KEY (o_custkey) REFERENCES customer(c_custkey)
);"""


def _fact(constraint_type, columns, table="orders", referenced_table=None, referenced_columns=()):
    return IntrospectedObject(
        kind=KIND_CONSTRAINT,
        table=table,
        columns=tuple(columns),
        constraint_type=constraint_type,
        referenced_table=referenced_table,
        referenced_columns=tuple(referenced_columns),
    )


def _orders_facts(**overrides):
    facts = {
        CONSTRAINT_PRIMARY_KEY: _fact(CONSTRAINT_PRIMARY_KEY, ["o_orderkey"]),
        CONSTRAINT_UNIQUE: _fact(CONSTRAINT_UNIQUE, ["o_custkey", "o_comment"]),
        CONSTRAINT_FOREIGN_KEY: _fact(CONSTRAINT_FOREIGN_KEY, ["o_custkey"], "orders", "customer", ["c_custkey"]),
    }
    facts.update(overrides)
    return [fact for fact in facts.values() if fact is not None]


def _stmt(ddl):
    return AppliedStatement(statement=ddl, phase=PHASE_DDL)


def _state(objects, constraint_types=ALL_TYPES, platform="duckdb"):
    return IntrospectedState(platform=platform, objects=list(objects), constraint_types=constraint_types)


def _receipt(ddl, state):
    ledger = AppliedTuningLedger()
    ledger.record(ddl, PHASE_DDL)
    return corroborate(ledger, state)


def _verdicts(receipt):
    return {entry.constraint_type: entry.verdict for entry in receipt.entries if entry.kind == KIND_CONSTRAINT}


class TestClassification:
    def test_table_level_constraints_become_typed_intents(self):
        klass, intents = _classify(_stmt(ORDERS_DDL))
        assert klass == "verifiable"
        assert [(i.kind, i.constraint_type, i.table, i.columns) for i in intents] == [
            (KIND_CONSTRAINT, CONSTRAINT_PRIMARY_KEY, "orders", ("o_orderkey",)),
            (KIND_CONSTRAINT, CONSTRAINT_UNIQUE, "orders", ("o_custkey", "o_comment")),
            (KIND_CONSTRAINT, CONSTRAINT_FOREIGN_KEY, "orders", ("o_custkey",)),
        ]
        assert (intents[2].referenced_table, intents[2].referenced_columns) == ("customer", ("c_custkey",))
        assert intents[0].referenced_table is None and intents[0].referenced_columns == ()

    def test_column_level_constraints_become_typed_intents(self):
        ddl = (
            "CREATE TABLE part (p_partkey INTEGER PRIMARY KEY, p_name VARCHAR UNIQUE NOT NULL, "
            "p_suppkey INTEGER REFERENCES supplier (s_suppkey), p_price DECIMAL(15,2) NOT NULL)"
        )
        _klass, intents = _classify(_stmt(ddl))
        assert [(i.constraint_type, i.columns, i.referenced_table, i.referenced_columns) for i in intents] == [
            (CONSTRAINT_PRIMARY_KEY, ("p_partkey",), None, ()),
            (CONSTRAINT_UNIQUE, ("p_name",), None, ()),
            (CONSTRAINT_FOREIGN_KEY, ("p_suppkey",), "supplier", ("s_suppkey",)),
        ]

    def test_quoted_identifiers_keep_their_names(self):
        ddl = 'CREATE TABLE t ("Key A" INTEGER, "b" INTEGER, PRIMARY KEY ("Key A", "b"))'
        _klass, intents = _classify(_stmt(ddl))
        assert [i.columns for i in intents] == [("key a", "b")]

    @pytest.mark.parametrize(
        "ddl",
        [
            'CREATE TABLE "Region" ("r_key" INT NOT NULL, PRIMARY KEY ("r_key"))',
            'CREATE TABLE IF NOT EXISTS "Region" ("r_key" INT NOT NULL, PRIMARY KEY ("r_key"))',
        ],
    )
    def test_quoted_table_name_binds_constraints_to_that_table(self, ddl):
        klass, intents = _classify(_stmt(ddl))
        assert klass == "verifiable"
        assert [(i.table, i.columns) for i in intents] == [("region", ("r_key",))]

    def test_quoted_table_name_does_not_widen_layout_clauses(self):
        ddl = 'CREATE TABLE "t" ("a" Int64, PRIMARY KEY ("a")) ENGINE = MergeTree() ORDER BY ("a")'
        assert _classify(_stmt(ddl)) == (UNVERIFIABLE, [])

    def test_trailing_semicolon_alone_is_not_a_second_statement(self):
        klass, intents = _classify(_stmt("CREATE TABLE t (a INTEGER PRIMARY KEY);  \n"))
        assert klass == "verifiable"
        assert [i.columns for i in intents] == [("a",)]

    def test_quoted_table_name_with_a_space_cannot_corroborate(self):
        ddl = 'CREATE TABLE "my t" (a INTEGER, PRIMARY KEY (a))'
        receipt = _receipt(ddl, _state([_fact(CONSTRAINT_PRIMARY_KEY, ["a"], table="my t")]))
        assert receipt.corroborated is False

    def test_quoted_column_with_inline_key_uses_the_quoted_name(self):
        ddl = 'CREATE TABLE t ("Unique" INTEGER PRIMARY KEY)'
        _klass, intents = _classify(_stmt(ddl))
        assert [(i.constraint_type, i.columns) for i in intents] == [(CONSTRAINT_PRIMARY_KEY, ("unique",))]

    def test_keywords_in_literals_and_comments_create_no_intent(self):
        ddl = (
            "CREATE TABLE t (a INTEGER DEFAULT 'PRIMARY KEY (b)', -- UNIQUE (a)\n"
            "b VARCHAR /* FOREIGN KEY (b) REFERENCES x(y) */, PRIMARY KEY (a))"
        )
        _klass, intents = _classify(_stmt(ddl))
        assert [(i.constraint_type, i.columns) for i in intents] == [(CONSTRAINT_PRIMARY_KEY, ("a",))]

    def test_engine_primary_key_outside_the_column_list_is_not_a_constraint(self):
        ddl = "CREATE TABLE t (a Int64, b Int64) ENGINE = MergeTree() ORDER BY (a, b) PRIMARY KEY (a)"
        _klass, intents = _classify(_stmt(ddl))
        assert [(i.kind, i.columns) for i in intents] == [(KIND_SORT_KEY, ("a", "b"))]

    def test_create_table_with_only_not_null_columns_has_no_rule(self):
        klass, intents = _classify(_stmt("CREATE TABLE t (a INTEGER NOT NULL)"))
        assert (klass, intents) == (UNVERIFIABLE, [])

    @pytest.mark.parametrize(
        "ddl",
        [
            "CREATE TABLE t (a INTEGER, CHECK (a > 0), PRIMARY KEY (a))",
            "CREATE TABLE t (a INTEGER CHECK (a > 0) PRIMARY KEY)",
            "CREATE TABLE t (a INTEGER, CONSTRAINT pk_t PRIMARY KEY (a))",
            "CREATE TABLE t (a INTEGER, FOREIGN KEY (a) REFERENCES u(b) ON DELETE CASCADE)",
            "CREATE TABLE t (a INTEGER REFERENCES u(b) ON UPDATE RESTRICT)",
            "CREATE TABLE t (a INTEGER REFERENCES u)",
            "CREATE TABLE t (a INTEGER, FOREIGN KEY (a) REFERENCES u)",
            "CREATE TABLE t (a INTEGER, PRIMARY KEY ())",
            "CREATE TABLE t (a INTEGER, PRIMARY KEY a)",
            "CREATE TABLE t (a INTEGER, UNIQUE (a) NULLS NOT DISTINCT)",
            "CREATE TABLE t (a INTEGER, PRIMARY KEY (a)",
            "CREATE TABLE t (a INTEGER PRIMARY KEY); CREATE TABLE u (b INTEGER CHECK (b > 0));",
            "CREATE TABLE t (a INTEGER PRIMARY KEY);\nALTER TABLE t ADD COLUMN c INTEGER",
        ],
        ids=[
            "check",
            "inline-check",
            "named-constraint",
            "on-delete",
            "inline-on-update",
            "inline-references-without-columns",
            "references-without-columns",
            "empty-key",
            "unparenthesized-key",
            "trailing-clause",
            "unbalanced-body",
            "second-statement",
            "trailing-alter",
        ],
    )
    def test_constraint_text_that_is_not_fully_parsed_blocks_the_statement(self, ddl):
        assert _classify(_stmt(ddl)) == (UNVERIFIABLE, [])

    def test_unparsed_constraint_also_blocks_a_parsed_sort_key_in_the_same_statement(self):
        ddl = "CREATE TABLE t (a Int64, CHECK (a > 0)) ENGINE = MergeTree() ORDER BY (a)"
        assert _classify(_stmt(ddl)) == (UNVERIFIABLE, [])


class TestPositiveMatches:
    @pytest.mark.parametrize("constraint_type", [CONSTRAINT_PRIMARY_KEY, CONSTRAINT_UNIQUE, CONSTRAINT_FOREIGN_KEY])
    def test_each_constraint_kind_corroborates(self, constraint_type):
        receipt = _receipt(ORDERS_DDL, _state(_orders_facts()))
        assert _verdicts(receipt)[constraint_type] == CORROBORATED

    def test_all_constraints_corroborated_verifies_the_statement(self):
        receipt = _receipt(ORDERS_DDL, _state(_orders_facts()))
        assert receipt.corroborated is True
        assert receipt.summary == {"corroborated": 3, "gate_relevant_total": 3}

    def test_identifier_case_and_quotes_are_normalized(self):
        ddl = 'CREATE TABLE Orders (A INTEGER, B INTEGER, FOREIGN KEY ("A", B) REFERENCES "Customer"(X, "Y"))'
        fact = _fact(CONSTRAINT_FOREIGN_KEY, ["a", "b"], "ORDERS", "customer", ["x", "y"])
        assert _verdicts(_receipt(ddl, _state([fact]))) == {CONSTRAINT_FOREIGN_KEY: CORROBORATED}


class TestNegativeMatches:
    def test_primary_key_on_different_columns_is_mismatch(self):
        state = _state(_orders_facts(**{CONSTRAINT_PRIMARY_KEY: _fact(CONSTRAINT_PRIMARY_KEY, ["o_custkey"])}))
        receipt = _receipt(ORDERS_DDL, state)
        assert _verdicts(receipt)[CONSTRAINT_PRIMARY_KEY] == MISMATCH
        assert receipt.corroborated is False
        entry = next(e for e in receipt.entries if e.constraint_type == CONSTRAINT_PRIMARY_KEY)
        assert entry.diff == "expected ['o_orderkey'] != observed ['o_custkey']"

    def test_foreign_key_with_same_child_columns_but_different_referenced_table_is_mismatch(self):
        wrong = _fact(CONSTRAINT_FOREIGN_KEY, ["o_custkey"], "orders", "supplier", ["c_custkey"])
        receipt = _receipt(ORDERS_DDL, _state(_orders_facts(**{CONSTRAINT_FOREIGN_KEY: wrong})))
        assert _verdicts(receipt)[CONSTRAINT_FOREIGN_KEY] == MISMATCH
        assert receipt.corroborated is False
        entry = next(e for e in receipt.entries if e.constraint_type == CONSTRAINT_FOREIGN_KEY)
        assert "references customer['c_custkey']" in entry.diff
        assert "references supplier['c_custkey']" in entry.diff

    def test_foreign_key_with_different_referenced_columns_is_mismatch(self):
        wrong = _fact(CONSTRAINT_FOREIGN_KEY, ["o_custkey"], "orders", "customer", ["c_nationkey"])
        receipt = _receipt(ORDERS_DDL, _state(_orders_facts(**{CONSTRAINT_FOREIGN_KEY: wrong})))
        assert _verdicts(receipt)[CONSTRAINT_FOREIGN_KEY] == MISMATCH

    def test_foreign_key_with_different_child_columns_is_mismatch(self):
        wrong = _fact(CONSTRAINT_FOREIGN_KEY, ["o_orderkey"], "orders", "customer", ["c_custkey"])
        receipt = _receipt(ORDERS_DDL, _state(_orders_facts(**{CONSTRAINT_FOREIGN_KEY: wrong})))
        assert _verdicts(receipt)[CONSTRAINT_FOREIGN_KEY] == MISMATCH

    def test_key_column_order_is_significant(self):
        reordered = _fact(CONSTRAINT_UNIQUE, ["o_comment", "o_custkey"])
        receipt = _receipt(ORDERS_DDL, _state(_orders_facts(**{CONSTRAINT_UNIQUE: reordered})))
        assert _verdicts(receipt)[CONSTRAINT_UNIQUE] == MISMATCH

    def test_unique_fact_does_not_stand_in_for_a_primary_key(self):
        unique_instead = _fact(CONSTRAINT_UNIQUE, ["o_orderkey"])
        state = _state(_orders_facts(**{CONSTRAINT_PRIMARY_KEY: None}) + [unique_instead])
        receipt = _receipt(ORDERS_DDL, state)
        assert _verdicts(receipt)[CONSTRAINT_PRIMARY_KEY] == ABSENT

    def test_fact_on_another_table_does_not_corroborate(self):
        elsewhere = _fact(CONSTRAINT_PRIMARY_KEY, ["o_orderkey"], table="orders_archive")
        state = _state(_orders_facts(**{CONSTRAINT_PRIMARY_KEY: None}) + [elsewhere])
        receipt = _receipt(ORDERS_DDL, state)
        entry = next(e for e in receipt.entries if e.constraint_type == CONSTRAINT_PRIMARY_KEY)
        assert (entry.verdict, entry.reason) == (ABSENT, "no PRIMARY KEY constraint found in catalog")

    def test_index_fact_does_not_stand_in_for_a_constraint(self):
        index = IntrospectedObject(kind="index", table="orders", columns=("o_orderkey",))
        receipt = _receipt("CREATE TABLE orders (o_orderkey INTEGER, PRIMARY KEY (o_orderkey))", _state([index]))
        assert _verdicts(receipt) == {CONSTRAINT_PRIMARY_KEY: ABSENT}


class TestUnverifiable:
    @pytest.mark.parametrize(
        ("referenced_table", "referenced_columns"),
        [(None, ["c_custkey"]), ("customer", []), (None, [])],
    )
    def test_foreign_key_fact_without_referenced_fields_is_unverifiable(self, referenced_table, referenced_columns):
        bare = _fact(CONSTRAINT_FOREIGN_KEY, ["o_custkey"], "orders", referenced_table, referenced_columns)
        receipt = _receipt(ORDERS_DDL, _state(_orders_facts(**{CONSTRAINT_FOREIGN_KEY: bare})))
        entry = next(e for e in receipt.entries if e.constraint_type == CONSTRAINT_FOREIGN_KEY)
        assert entry.verdict == UNVERIFIABLE
        assert entry.reason == "catalog foreign key fact has no referenced table or columns"
        assert receipt.corroborated is False

    def test_complete_fact_still_wins_over_an_incomplete_duplicate(self):
        bare = _fact(CONSTRAINT_FOREIGN_KEY, ["o_custkey"], "orders")
        receipt = _receipt(ORDERS_DDL, _state([bare, *_orders_facts()]))
        assert _verdicts(receipt)[CONSTRAINT_FOREIGN_KEY] == CORROBORATED

    @pytest.mark.parametrize("platform", ["snowflake", "clickhouse"])
    def test_platform_without_a_constraint_source_stays_unverifiable(self, platform):
        state = _state(_orders_facts(), constraint_types=frozenset(), platform=platform)
        receipt = _receipt(ORDERS_DDL, state)
        assert set(_verdicts(receipt).values()) == {UNVERIFIABLE}
        assert receipt.corroborated is False
        assert receipt.entries[0].reason == f"{platform} introspection reads no PRIMARY KEY constraints"

    def test_constraint_type_the_introspector_does_not_read_stays_unverifiable(self):
        state = _state(_orders_facts(), constraint_types=frozenset({CONSTRAINT_PRIMARY_KEY, CONSTRAINT_UNIQUE}))
        receipt = _receipt(ORDERS_DDL, state)
        assert _verdicts(receipt) == {
            CONSTRAINT_PRIMARY_KEY: CORROBORATED,
            CONSTRAINT_UNIQUE: CORROBORATED,
            CONSTRAINT_FOREIGN_KEY: UNVERIFIABLE,
        }
        assert receipt.corroborated is False

    def test_constraint_next_to_a_corroborated_sort_key_still_blocks_without_a_source(self):
        ddl = "CREATE TABLE t (a Int64, PRIMARY KEY (a)) ENGINE = MergeTree() ORDER BY (a)"
        sort_key = IntrospectedObject(kind=KIND_SORT_KEY, table="t", columns=("a",))
        receipt = _receipt(ddl, IntrospectedState(platform="clickhouse", objects=[sort_key]))
        assert [(e.kind, e.verdict) for e in receipt.entries] == [
            (KIND_SORT_KEY, CORROBORATED),
            (KIND_CONSTRAINT, UNVERIFIABLE),
        ]
        assert receipt.corroborated is False

    def test_degraded_state_marks_constraints_unverifiable(self):
        state = IntrospectedState(platform="duckdb", error="catalog read failed", constraint_types=ALL_TYPES)
        receipt = _receipt(ORDERS_DDL, state)
        assert set(_verdicts(receipt).values()) == {UNVERIFIABLE}
        assert {e.reason for e in receipt.entries} == {"introspection degraded"}

    def test_match_object_applies_the_constraint_rules(self):
        _klass, intents = _classify(_stmt(ORDERS_DDL))
        fk = intents[2]
        wrong = _fact(CONSTRAINT_FOREIGN_KEY, ["o_custkey"], "orders", "supplier", ["c_custkey"])
        assert _match_object(fk, _state([wrong]))[0] == MISMATCH
        assert _match_object(fk, _state([wrong], constraint_types=frozenset()))[0] == UNVERIFIABLE


class TestPayload:
    def test_entries_and_observed_facts_carry_the_constraint_type(self):
        payload = _receipt(ORDERS_DDL, _state(_orders_facts())).to_payload()
        assert [entry["constraint_type"] for entry in payload["entries"]] == [
            CONSTRAINT_PRIMARY_KEY,
            CONSTRAINT_UNIQUE,
            CONSTRAINT_FOREIGN_KEY,
        ]
        assert {item["constraint_type"] for item in payload["observed"]} == ALL_TYPES

    def test_non_constraint_entries_have_no_constraint_type_key(self):
        ddl = "CREATE TABLE t (a Int64) ENGINE = MergeTree() ORDER BY (a)"
        sort_key = IntrospectedObject(kind=KIND_SORT_KEY, table="t", columns=("a",))
        payload = _receipt(ddl, IntrospectedState(platform="clickhouse", objects=[sort_key])).to_payload()
        assert "constraint_type" not in payload["entries"][0]
        assert "constraint_type" not in payload["observed"][0]
