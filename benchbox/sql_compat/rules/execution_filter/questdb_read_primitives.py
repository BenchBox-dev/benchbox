from __future__ import annotations

from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import Phase
from benchbox.sql_compat.decision import CompatibilityDecision, FailureMode, SkipQueryPayload, SupportLevel
from benchbox.sql_compat.registry import REGISTRY

_NO_FULLTEXT = (
    "QuestDB 10.0.1 has no full-text search: `SELECT p_partkey FROM part WHERE MATCH(p_name) AGAINST ('STEEL')` "
    "fails with 'unexpected token [AGAINST]'."
)
_NO_STRUCT = (
    "QuestDB 10.0.1 has no STRUCT type: `SELECT STRUCT(c_name, c_phone) FROM customer` fails with "
    "'unknown function name: STRUCT(VARCHAR,VARCHAR)'."
)
_NO_MAP = (
    "QuestDB 10.0.1 has no MAP type: `SELECT map_from_entries(array_agg(1)) FROM long_sequence(2)` fails with "
    "'unknown function name: map_from_entries(DOUBLE[])'."
)

QUESTDB_READ_PRIMITIVES_SKIPS: dict[str, str] = {
    "approx_top_k_lineitem": (
        "QuestDB 10.0.1 has no approximate top-k aggregate: `SELECT approx_top_k(l_shipmode, 5) FROM lineitem` "
        "fails with 'unknown function name: approx_top_k(SYMBOL,INT)'."
    ),
    "json_aggregates": (
        "QuestDB 10.0.1 has no JSON aggregate functions: `SELECT json_arrayagg(p_name) FROM part` fails with "
        "'unknown function name: json_arrayagg(VARCHAR)'."
    ),
    "fulltext_simple_search": _NO_FULLTEXT,
    "fulltext_boolean_search": _NO_FULLTEXT,
    "fulltext_phrase_search": _NO_FULLTEXT,
    "array_distinct": (
        "QuestDB 10.0.1 arrays hold only DOUBLE elements, so the query's VARCHAR array cannot be built: "
        "`SELECT array_agg(l_shipmode) FROM lineitem` fails with 'there is no matching function `array_agg` "
        "with the argument types: (SYMBOL)', and there is no `array_distinct` "
        "('unknown function name: array_distinct(DOUBLE[])')."
    ),
    "struct_construction": _NO_STRUCT,
    "struct_access": _NO_STRUCT,
    "array_of_struct": _NO_STRUCT,
    "map_construction": _NO_MAP,
    "map_access": _NO_MAP,
    "map_keys_values": _NO_MAP,
    "list_filter": (
        "QuestDB 10.0.1 has no lambda expressions: `SELECT filter(array_agg(x), y -> y > 1) FROM long_sequence(3)` "
        "fails with 'too few arguments for 'filter' [found=1,expected=2]'."
    ),
}

for _query_id, _reason in QUESTDB_READ_PRIMITIVES_SKIPS.items():
    REGISTRY.register(
        CompatibilityDecision(
            rule_id=f"execution_filter.questdb.read_primitives.{_query_id}",
            action=CompatAction.SKIP_QUERY,
            support_level=SupportLevel.SKIPPED_QUERY,
            failure_mode=FailureMode.UNSUPPORTED_FEATURE,
            payload=SkipQueryPayload(reason=_reason, query_id=_query_id),
            reason=_reason,
        ),
        Phase.EXECUTION_FILTER,
        "questdb",
        benchmark="read_primitives",
        query_id=_query_id,
    )
