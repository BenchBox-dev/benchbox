# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _assert_both_families_match_sql(benchmark, query_id, tables) -> None:

    from types import SimpleNamespace

    import duckdb
    import pandas as pd
    import polars as pl

    from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

    frames = {name: pd.DataFrame(data) for name, data in tables.items()}
    with duckdb.connect() as connection:
        for name, frame in frames.items():
            connection.register(name, frame)
        expected = connection.execute(benchmark.get_query(query_id)).fetchall()
    query = benchmark.get_dataframe_queries().get_or_raise(query_id)
    assert query.pandas_impl is not None
    assert query.expression_impl is not None
    pandas_result = query.pandas_impl(SimpleNamespace(get_table=frames.__getitem__))
    assert list(pandas_result.itertuples(index=False, name=None)) == expected
    ctx = PolarsDataFrameAdapter().create_context()
    for name, frame in frames.items():
        ctx.register_table(name, pl.from_pandas(frame).lazy())
    assert query.expression_impl(ctx).collect().rows() == expected


def test_public_custom_registries_use_instance_sql_and_reload(tmp_path) -> None:
    from benchbox.core.joinorder_synthetic.benchmark import JoinOrderSyntheticBenchmark

    benchmarks = []
    for value in (1, 2):
        directory = tmp_path / f"queries-{value}"
        directory.mkdir()
        (directory / "1a.sql").write_text(
            f"SELECT MIN(t.title) AS title FROM title AS t WHERE t.id = {value};", encoding="utf-8"
        )
        if value == 2:
            (directory / "custom.sql").write_text(
                "SELECT MIN(t.title) AS title FROM title AS t WHERE t.id = 3;", encoding="utf-8"
            )
        benchmarks.append(
            JoinOrderSyntheticBenchmark(queries_dir=str(directory), output_dir=tmp_path / f"data-{value}")
        )
    tables = {"title": {"id": [1, 2, 3], "title": ["First", "Second", "Third"]}}
    first, second = benchmarks
    assert first.get_dataframe_queries().get_query_ids() == ["1a"]
    assert second.get_dataframe_queries().get_query_ids() == ["1a", "custom"]
    for benchmark in benchmarks:
        for query_id in benchmark.get_queries():
            _assert_both_families_match_sql(benchmark, query_id, tables)
    original = first.get_dataframe_queries()
    first.load_queries_from_directory(second.queries_dir)
    assert first.get_dataframe_queries() is not original
    assert first.get_dataframe_queries().get_query_ids() == ["1a", "custom"]
    _assert_both_families_match_sql(first, "1a", tables)
    _assert_both_families_match_sql(first, "custom", tables)
    previous = first.get_dataframe_queries()
    (tmp_path / "queries-2" / "1a.sql").write_text(
        "SELECT MIN(t.title) AS title FROM title AS t WHERE t.id = 3;", encoding="utf-8"
    )
    first.load_queries_from_directory(first.queries_dir)
    assert first.get_dataframe_queries() is not previous
    _assert_both_families_match_sql(first, "1a", tables)


@pytest.mark.parametrize("operator", ["LIKE", "NOT LIKE"])
@pytest.mark.parametrize("text", ["voice", "(voice)"])
def test_public_custom_query_preserves_literal_like_parentheses(tmp_path, operator, text) -> None:
    from benchbox.core.joinorder_synthetic.benchmark import JoinOrderSyntheticBenchmark

    (tmp_path / "literal.sql").write_text(
        f"SELECT MIN(n.name) AS name FROM name AS n WHERE n.name {operator} '%(voice)%';", encoding="utf-8"
    )
    benchmark = JoinOrderSyntheticBenchmark(queries_dir=str(tmp_path), output_dir=tmp_path / "data")
    _assert_both_families_match_sql(benchmark, "literal", {"name": {"id": [1], "name": [text]}})


@pytest.mark.parametrize("query_id", ["13b", "31a"])
def test_large_comma_join_query_category_counts_actual_tables(query_id) -> None:
    from benchbox.core.dataframe.query import QueryCategory
    from benchbox.core.joinorder_synthetic.dataframe_queries import get_dataframe_queries

    assert QueryCategory.MULTI_JOIN in get_dataframe_queries().get_or_raise(query_id).categories


class TestQueryRegistration:
    def test_all_113_queries_registered(self):
        from benchbox.core.joinorder_synthetic.dataframe_queries import get_dataframe_queries

        registry = get_dataframe_queries()
        assert len(registry) == 113

    def test_registry_returns_consistent_object(self):
        from benchbox.core.joinorder_synthetic.dataframe_queries import get_dataframe_queries

        r1 = get_dataframe_queries()
        r2 = get_dataframe_queries()
        assert r1.get_query_ids() == r2.get_query_ids()

    def test_expected_query_ids_present(self):
        from benchbox.core.joinorder_synthetic.dataframe_queries import get_dataframe_queries

        registry = get_dataframe_queries()
        ids = registry.get_query_ids()
        expected = ["1a", "1b", "2a", "3a", "4a", "5a", "6a", "7a", "8a", "9a", "10a", "11a", "12a"]
        for qid in expected:
            assert qid in ids, f"Query {qid!r} not found in registry"

    def test_all_queries_have_both_impls(self):
        from benchbox.core.joinorder_synthetic.dataframe_queries import get_dataframe_queries

        registry = get_dataframe_queries()
        for query in registry.get_all_queries():
            assert query.has_expression_impl(), f"{query.query_id} missing expression_impl"
            assert query.has_pandas_impl(), f"{query.query_id} missing pandas_impl"

    def test_all_queries_have_categories(self):
        from benchbox.core.joinorder_synthetic.dataframe_queries import get_dataframe_queries

        registry = get_dataframe_queries()
        for query in registry.get_all_queries():
            assert query.categories, f"{query.query_id} has no categories"

    def test_benchmark_registry_supports_dataframe(self):
        from benchbox.core.benchmark_registry import BENCHMARK_METADATA

        assert BENCHMARK_METADATA["joinorder_synthetic"]["supports_dataframe"] is True

    def test_benchmark_class_exposes_registry(self):
        from benchbox.core.joinorder_synthetic.benchmark import JoinOrderSyntheticBenchmark

        bm = JoinOrderSyntheticBenchmark(scale_factor=1.0)
        registry = bm.get_dataframe_queries()
        assert len(registry) == 113


class TestPandasImplExecute:
    @pytest.fixture
    def pandas_ctx(self):

        pytest.importorskip("pandas")
        import pandas as pd

        tables = {
            "title": pd.DataFrame(
                {
                    "id": range(1, 11),
                    "title": [f"Movie {i}" for i in range(1, 11)],
                    "kind_id": [1] * 10,
                    "production_year": [2000 + i for i in range(10)],
                }
            ),
            "company_type": pd.DataFrame(
                {
                    "id": [1, 2, 3],
                    "kind": ["production companies", "distributors", "misc"],
                }
            ),
            "company_name": pd.DataFrame(
                {
                    "id": range(1, 6),
                    "name": [f"Company {i}" for i in range(1, 6)],
                    "country_code": ["[de]", "[us]", "[jp]", "[fr]", "[gb]"],
                }
            ),
            "info_type": pd.DataFrame(
                {
                    "id": [1, 2, 3, 4, 5],
                    "info": ["top 250 rank", "rating", "votes", "countries", "genres"],
                }
            ),
            "kind_type": pd.DataFrame(
                {
                    "id": [1, 2, 3],
                    "kind": ["movie", "tv series", "video"],
                }
            ),
            "movie_companies": pd.DataFrame(
                {
                    "id": range(1, 11),
                    "movie_id": range(1, 11),
                    "company_id": [1, 2, 3, 4, 5, 1, 2, 3, 4, 5],
                    "company_type_id": [1] * 10,
                    "note": [
                        "presents",
                        "co-production",
                        "presents (theatrical) (France)",
                        None,
                        "co-production",
                        "presents",
                        None,
                        "presents",
                        "co-production",
                        None,
                    ],
                }
            ),
            "movie_info": pd.DataFrame(
                {
                    "id": range(1, 11),
                    "movie_id": range(1, 11),
                    "info_type_id": [1, 2, 3, 4, 5, 1, 2, 3, 4, 4],
                    "info": [
                        "top 250 rank",
                        "8.5",
                        "1000",
                        "Germany",
                        "Action",
                        "top 250 rank",
                        "8.5",
                        "1000",
                        "Sweden",
                        "Drama",
                    ],
                    "note": [None] * 10,
                }
            ),
            "movie_info_idx": pd.DataFrame(
                {
                    "id": range(1, 11),
                    "movie_id": range(1, 11),
                    "info_type_id": [1, 2, 1, 2, 1, 2, 1, 2, 1, 2],
                    "info": ["top 250 rank", "8.5"] * 5,
                    "note": [None] * 10,
                }
            ),
            "cast_info": pd.DataFrame(
                {
                    "id": range(1, 11),
                    "person_id": range(1, 11),
                    "movie_id": range(1, 11),
                    "role_id": [1, 2, 3, 1, 2, 3, 1, 2, 3, 1],
                    "person_role_id": [1, 2, 3, 4, 5, 1, 2, 3, 4, 5],
                    "note": ["(producer)"] + [None] * 9,
                }
            ),
            "char_name": pd.DataFrame(
                {
                    "id": range(1, 6),
                    "name": [f"Character {i}" for i in range(1, 6)],
                }
            ),
            "name": pd.DataFrame(
                {
                    "id": range(1, 11),
                    "name": [
                        "Robert Downey Jr.",
                        "Person 2",
                        "Yo Actress",
                        "Person 4",
                        "Person 5",
                        "Person 6",
                        "Person 7",
                        "An Actress",
                        "Person 9",
                        "Person 10",
                    ],
                    "gender": ["m", "f", "f", "f", "m", "f", "m", "f", "m", "f"],
                    "name_pcode_cf": ["D163", "P325", "Y200", "P325", "D163", "P325", "D163", "A222", "P325", "D163"],
                }
            ),
            "role_type": pd.DataFrame(
                {
                    "id": [1, 2, 3],
                    "role": ["actor", "actress", "producer"],
                }
            ),
            "keyword": pd.DataFrame(
                {
                    "id": range(1, 6),
                    "keyword": ["sequel", "character-name-in-title", "revenge", "superhero", "alien"],
                    "phonetic_code": [None] * 5,
                }
            ),
            "movie_keyword": pd.DataFrame(
                {
                    "id": range(1, 11),
                    "movie_id": range(1, 11),
                    "keyword_id": [1, 2, 3, 4, 5, 1, 2, 3, 4, 5],
                }
            ),
            "aka_name": pd.DataFrame(
                {
                    "id": range(1, 6),
                    "person_id": range(1, 6),
                    "name": [f"Alias {i}" for i in range(1, 6)],
                }
            ),
            "person_info": pd.DataFrame(
                {
                    "id": range(1, 6),
                    "person_id": range(1, 6),
                    "info_type_id": [1, 2, 3, 4, 5],
                    "info": ["test"] * 5,
                    "note": [None] * 5,
                }
            ),
            "link_type": pd.DataFrame(
                {
                    "id": [1, 2],
                    "link": ["sequel", "prequel"],
                }
            ),
            "movie_link": pd.DataFrame(
                {
                    "id": range(1, 6),
                    "movie_id": range(1, 6),
                    "linked_movie_id": [2, 3, 4, 5, 1],
                    "link_type_id": [1, 2, 1, 2, 1],
                }
            ),
            "aka_title": pd.DataFrame(
                {
                    "id": range(1, 6),
                    "movie_id": range(1, 6),
                    "title": [f"Alt Title {i}" for i in range(1, 6)],
                }
            ),
            "complete_cast": pd.DataFrame(
                {
                    "id": range(1, 6),
                    "movie_id": range(1, 6),
                    "subject_id": [1] * 5,
                    "status_id": [1] * 5,
                }
            ),
            "comp_cast_type": pd.DataFrame(
                {
                    "id": [1, 2],
                    "kind": ["complete cast", "complete crew"],
                }
            ),
        }

        class SimplePandasContext:
            def get_table(self, name: str):
                return tables[name]

            def col(self, name: str):
                raise NotImplementedError("Expression API not available in pandas context")

            def lit(self, value):
                raise NotImplementedError("Expression API not available in pandas context")

        return SimplePandasContext()

    def test_q1a_pandas(self, pandas_ctx):
        from benchbox.core.joinorder_synthetic.dataframe_queries import q1a_pandas_impl

        result = q1a_pandas_impl(pandas_ctx)
        assert result is not None
        assert "production_note" in result

    def test_q1b_pandas(self, pandas_ctx):
        from benchbox.core.joinorder_synthetic.dataframe_queries import q1b_pandas_impl

        result = q1b_pandas_impl(pandas_ctx)
        assert result is not None
        assert "production_note" in result

    def test_q2a_pandas(self, pandas_ctx):
        from benchbox.core.joinorder_synthetic.dataframe_queries import q2a_pandas_impl

        result = q2a_pandas_impl(pandas_ctx)
        assert result is not None
        assert "movie_title" in result

    def test_q3a_pandas(self, pandas_ctx):
        from benchbox.core.joinorder_synthetic.dataframe_queries import q3a_pandas_impl

        result = q3a_pandas_impl(pandas_ctx)
        assert result is not None
        assert "movie_title" in result

    def test_q4a_pandas(self, pandas_ctx):
        from benchbox.core.joinorder_synthetic.dataframe_queries import q4a_pandas_impl

        result = q4a_pandas_impl(pandas_ctx)
        assert result is not None
        assert "movie_title" in result

    def test_q5a_pandas(self, pandas_ctx):
        from benchbox.core.joinorder_synthetic.dataframe_queries import q5a_pandas_impl

        result = q5a_pandas_impl(pandas_ctx)
        assert result is not None
        assert "typical_european_movie" in result

    def test_q6a_pandas(self, pandas_ctx):
        from benchbox.core.joinorder_synthetic.dataframe_queries import q6a_pandas_impl

        result = q6a_pandas_impl(pandas_ctx)
        assert result is not None
        assert "marvel_movie" in result

    @pytest.mark.parametrize("actor_name", ["Robert Downey Jr.", "Downey Robert"])
    def test_q6a_pandas_actor_filter_preserves_sql_order(self, actor_name):
        import duckdb
        import pandas as pd

        from benchbox.core.joinorder_synthetic.dataframe_queries import q6a_pandas_impl
        from benchbox.core.joinorder_synthetic.queries import JoinOrderQueryManager

        class MinimalPandasContext:
            def __init__(self):
                self.tables = {
                    "cast_info": pd.DataFrame({"id": [1], "person_id": [1], "movie_id": [1]}),
                    "keyword": pd.DataFrame({"id": [1], "keyword": ["marvel-cinematic-universe"]}),
                    "movie_keyword": pd.DataFrame({"id": [1], "movie_id": [1], "keyword_id": [1]}),
                    "name": pd.DataFrame({"id": [1], "name": [actor_name]}),
                    "title": pd.DataFrame({"id": [1], "title": ["Iron Man"], "production_year": [2012]}),
                }

            def get_table(self, name):
                return self.tables[name]

            def col(self, name):
                raise NotImplementedError("Expression API not available in pandas context")

            def lit(self, value):
                raise NotImplementedError("Expression API not available in pandas context")

        ctx = MinimalPandasContext()
        with duckdb.connect() as connection:
            for name, frame in ctx.tables.items():
                connection.register(name, frame)
            expected = connection.execute(JoinOrderQueryManager().get_query("6a")).fetchall()
        assert list(q6a_pandas_impl(ctx).itertuples(index=False, name=None)) == expected

    def test_q7a_pandas(self, pandas_ctx):
        from benchbox.core.joinorder_synthetic.dataframe_queries import q7a_pandas_impl

        result = q7a_pandas_impl(pandas_ctx)
        assert result is not None
        assert "biography_movie" in result

    def test_q8a_pandas(self, pandas_ctx):
        from benchbox.core.joinorder_synthetic.dataframe_queries import q8a_pandas_impl

        result = q8a_pandas_impl(pandas_ctx)
        assert result is not None
        assert "japanese_movie_dubbed" in result

    def test_q9a_pandas(self, pandas_ctx):
        from benchbox.core.joinorder_synthetic.dataframe_queries import q9a_pandas_impl

        result = q9a_pandas_impl(pandas_ctx)
        assert result is not None
        assert "movie" in result

    def test_q10a_pandas(self, pandas_ctx):
        from benchbox.core.joinorder_synthetic.dataframe_queries import q10a_pandas_impl

        result = q10a_pandas_impl(pandas_ctx)
        assert result is not None
        assert "russian_movie" in result

    def test_q11a_pandas(self, pandas_ctx):
        from benchbox.core.joinorder_synthetic.dataframe_queries import q11a_pandas_impl

        result = q11a_pandas_impl(pandas_ctx)
        assert result is not None
        assert "non_polish_sequel_movie" in result

    def test_q12a_pandas(self, pandas_ctx):
        from benchbox.core.joinorder_synthetic.dataframe_queries import q12a_pandas_impl

        result = q12a_pandas_impl(pandas_ctx)
        assert result is not None
        assert "drama_horror_movie" in result

    def test_results_are_dataframes(self, pandas_ctx):

        import pandas as pd

        from benchbox.core.joinorder_synthetic.dataframe_queries import (
            q1a_pandas_impl,
            q2a_pandas_impl,
            q3a_pandas_impl,
            q4a_pandas_impl,
            q5a_pandas_impl,
            q6a_pandas_impl,
            q7a_pandas_impl,
            q8a_pandas_impl,
            q9a_pandas_impl,
            q10a_pandas_impl,
            q11a_pandas_impl,
            q12a_pandas_impl,
        )

        all_impls = [
            q1a_pandas_impl,
            q2a_pandas_impl,
            q3a_pandas_impl,
            q4a_pandas_impl,
            q5a_pandas_impl,
            q6a_pandas_impl,
            q7a_pandas_impl,
            q8a_pandas_impl,
            q9a_pandas_impl,
            q10a_pandas_impl,
            q11a_pandas_impl,
            q12a_pandas_impl,
        ]
        for fn in all_impls:
            result = fn(pandas_ctx)
            assert isinstance(result, pd.DataFrame), f"{fn.__name__} should return pd.DataFrame"

    def test_q1a_executes_via_pandas_adapter(self, pandas_ctx):
        from benchbox.core.joinorder_synthetic.dataframe_queries import get_dataframe_queries
        from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter

        query = get_dataframe_queries().get_or_raise("1a")
        result = PandasDataFrameAdapter().execute_query(pandas_ctx, query)

        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 1


