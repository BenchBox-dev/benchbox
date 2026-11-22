from __future__ import annotations

import tempfile
from functools import cache

from benchbox.core.clickbench.benchmark import ClickBenchBenchmark
from benchbox.core.clickbench.queries import ClickBenchQueryManager
from tests.unit.sql_compat.golden_corpus import Case, frozen_legacy

AREA = "area_clickbench"

CLICKBENCH_TARGETS = ("snowflake",)

CLICKBENCH_STRINGS = {
    "regex_nested_group": "SELECT REGEXP_REPLACE(Referer, '^https?://(?:www\\.)?([^/]+)/.*$', '\\\\1') AS k FROM hits",
    "regex_two_noncapturing": "SELECT REGEXP_REPLACE(a, '(?:x)(?:y)(z)', '\\\\1') FROM hits",
    "regex_capturing_only": "SELECT REGEXP_REPLACE(a, '(x)(y)', '\\\\2') FROM hits",
    "regex_no_groups": "SELECT REGEXP_REPLACE(a, 'abc', 'def') FROM hits",
    "regex_replace_plain_one": "SELECT REGEXP_REPLACE(a, '(?:www\\.)?([^/]+)', '\\\\1') FROM hits",
    "regex_extract": "SELECT REGEXP_EXTRACT(a, '(?:x)(y)', 1) FROM hits",
}


@cache
def clickbench_benchmark() -> ClickBenchBenchmark:
    return ClickBenchBenchmark(output_dir=tempfile.mkdtemp(prefix="golden_clickbench_"))


def clickbench_inputs() -> dict[str, str]:
    queries = ClickBenchQueryManager().get_all_queries()
    inputs = {f"query-{query_id}": sql for query_id, sql in queries.items()}
    inputs.update({f"string-{name}": sql for name, sql in CLICKBENCH_STRINGS.items()})
    return inputs


def cases() -> list[Case]:
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
