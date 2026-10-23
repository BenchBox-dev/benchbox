from __future__ import annotations

import re
import tempfile
from functools import cache

from benchbox.core.clickbench.benchmark import ClickBenchBenchmark
from benchbox.core.clickbench.queries import ClickBenchQueryManager
from benchbox.core.tpcdi.benchmark import BOOLEAN_FLAG_STYLES, TPCDI_FLAG_COLUMNS, TPCDIBenchmark
from benchbox.utils.dialect_utils import apply_rewrite
from tests.unit.sql_compat.golden_corpus import Case, frozen_legacy

AREA = "area_tpcdi_clickbench"

IDIOM_RE = re.compile(r"JULIANDAY|DATE\s*\(\s*['\"]now|\b(?:IsCurrent|TT_IS_SELL|HolidayFlag)\b\s*=\s*[01]\b", re.I)

ADAPTER_DIALECTS = ("bigquery", "databricks")
TPCDI_TARGETS = (
    "duckdb",
    "postgres",
    "datafusion",
    "snowflake",
    "clickhouse",
    "starrocks",
    "doris",
    "bigquery",
    "databricks",
)

TARGET_DIALECT_STRINGS = {"bq_flag_quoted": "SELECT * FROM `DIMCUSTOMER` WHERE `ISCURRENT` = 1"}

TPCDI_STRINGS = {
    "bq_flag_one": "SELECT * FROM DimCustomer WHERE IsCurrent = 1 AND BatchID = 1",
    "dbx_flag_zero": "SELECT CASE WHEN TT_IS_SELL = 0 THEN 1 ELSE 0 END FROM TradeType",
    "holiday_flag": "SELECT * FROM DimDate WHERE HolidayFlag = 1 OR HolidayFlag = 0",
    "julianday_now_diff": "SELECT JULIANDAY(DATE('now')) - JULIANDAY(MIN(d.DateValue))",
    "relative_90": "SELECT * FROM t WHERE d.DateValue >= DATE('now', '-90 days')",
    "relative_1_day": "SELECT * FROM t WHERE d.DateValue >= DATE('now', '-1 day')",
    "relative_double_quotes": 'SELECT * FROM t WHERE d.DateValue >= DATE("now", "-30 days")',
    "date_now_only": "SELECT COUNT(*) as future_birth_dates FROM DimCustomer WHERE DOB > DATE('now')",
    "lone_julianday": "SELECT JULIANDAY(d.DateValue) FROM DimDate d",
    "julianday_max_min": "SELECT JULIANDAY(MAX(d.DateValue)) - JULIANDAY(MIN(d.DateValue)) FROM DimDate d",
    "julianday_greatest": (
        "SELECT COUNT(t.TradeID) / GREATEST(JULIANDAY(MAX(d.DateValue)) - JULIANDAY(MIN(d.DateValue)), 1) FROM t"
    ),
    "julianday_abs": "SELECT ABS(JULIANDAY(t1_date.DateValue) - JULIANDAY(t2_date.DateValue)) <= 30 FROM a",
    "julianday_case": "SELECT AVG(CASE WHEN s.d IS NOT NULL THEN JULIANDAY(s.d) - JULIANDAY(b.d) END) FROM x",
    "julianday_nested_func": "SELECT JULIANDAY(COALESCE(a.x, b.x)) - JULIANDAY(DATE(c.y)) FROM t",
    "mixed_flags_julianday": "SELECT * FROM t WHERE IsCurrent = 1 AND TT_IS_SELL = 0 AND JULIANDAY(a) - JULIANDAY(b) > 3",
    "no_idiom": "SELECT a, b FROM t WHERE c = 1",
}


@cache
def tpcdi_benchmark() -> TPCDIBenchmark:
    return TPCDIBenchmark(scale_factor=0.01, output_dir=tempfile.mkdtemp(prefix="golden_tpcdi_"))


@cache
def clickbench_benchmark() -> ClickBenchBenchmark:
    return ClickBenchBenchmark(output_dir=tempfile.mkdtemp(prefix="golden_clickbench_"))


def tpcdi_queries() -> dict[str, str]:
    manager = tpcdi_benchmark().query_manager
    queries = {query_id: manager.get_query(query_id, None, dialect=None) for query_id in manager._all_queries}
    return {query_id: sql for query_id, sql in queries.items() if IDIOM_RE.search(sql)}


def fork_tpcdi(query: str, dialect: str) -> str:
    return tpcdi_benchmark().translate_query_text(query, dialect)


def tpcdi_cases() -> list[Case]:
    cases = []
    inputs = {f"query-{query_id}": sql for query_id, sql in tpcdi_queries().items()}
    inputs.update({f"string-{name}": sql for name, sql in TPCDI_STRINGS.items()})
    for name, sql in inputs.items():
        for dialect in TPCDI_TARGETS:
            cases.append(
                Case(
                    f"tpcdi:{dialect}:{name}",
                    frozen_legacy(AREA, f"tpcdi:{dialect}:{name}"),
                    lambda sql=sql, dialect=dialect: fork_tpcdi(sql, dialect),
                )
            )
    for name, sql in TARGET_DIALECT_STRINGS.items():
        for dialect in ADAPTER_DIALECTS:
            cases.append(
                Case(
                    f"tpcdi-adapter-raw:{dialect}:{name}",
                    frozen_legacy(AREA, f"tpcdi-adapter-raw:{dialect}:{name}"),
                    lambda sql=sql, dialect=dialect: apply_rewrite(
                        "flag_comparisons_to_boolean",
                        sql,
                        dialect,
                        columns=TPCDI_FLAG_COLUMNS,
                        use_is=BOOLEAN_FLAG_STYLES[dialect] == "is",
                    ),
                )
            )
    return cases


CLICKBENCH_TARGETS = ("snowflake",)

CLICKBENCH_STRINGS = {
    "regex_nested_group": "SELECT REGEXP_REPLACE(Referer, '^https?://(?:www\\.)?([^/]+)/.*$', '\\\\1') AS k FROM hits",
    "regex_two_noncapturing": "SELECT REGEXP_REPLACE(a, '(?:x)(?:y)(z)', '\\\\1') FROM hits",
    "regex_capturing_only": "SELECT REGEXP_REPLACE(a, '(x)(y)', '\\\\2') FROM hits",
    "regex_no_groups": "SELECT REGEXP_REPLACE(a, 'abc', 'def') FROM hits",
    "regex_replace_plain_one": "SELECT REGEXP_REPLACE(a, '(?:www\\.)?([^/]+)', '\\\\1') FROM hits",
    "regex_extract": "SELECT REGEXP_EXTRACT(a, '(?:x)(y)', 1) FROM hits",
}


def clickbench_inputs() -> dict[str, str]:
    queries = ClickBenchQueryManager().get_all_queries()
    inputs = {f"query-{query_id}": sql for query_id, sql in queries.items()}
    inputs.update({f"string-{name}": sql for name, sql in CLICKBENCH_STRINGS.items()})
    return inputs


def clickbench_cases() -> list[Case]:
    cases = []
    for name, sql in clickbench_inputs().items():
        for dialect in CLICKBENCH_TARGETS:
            cases.append(
                Case(
                    f"clickbench:{dialect}:{name}",
                    frozen_legacy(AREA, f"clickbench:{dialect}:{name}"),
                    lambda sql=sql, dialect=dialect: clickbench_benchmark().translate_query_text(sql, dialect),
                )
            )
    return cases


def cases() -> list[Case]:
    return tpcdi_cases() + clickbench_cases()
