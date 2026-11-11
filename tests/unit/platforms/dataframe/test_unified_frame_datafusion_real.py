from __future__ import annotations

import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

try:
    import datafusion
    import pyarrow as pa

    from benchbox.platforms.dataframe.unified_frame import UnifiedExpr, UnifiedLazyFrame

    HAS_DATAFUSION = True
except ImportError:
    HAS_DATAFUSION = False
    datafusion = None
    pa = None
    UnifiedExpr = None
    UnifiedLazyFrame = None


@pytest.fixture()
def datafusion_frame():
    ctx = datafusion.SessionContext()
    table = pa.table(
        {
            "name": ["Alice", "Bob"],
            "text": ["Alice Smith", "Bob Jones"],
            "arr": [[3, 1, 3], [2, 4]],
            "d": [datetime.date(2024, 1, 15), datetime.date(2024, 1, 16)],
            "dt": [datetime.datetime(2024, 1, 15, 10, 30), datetime.datetime(2024, 1, 16, 11, 45)],
            "a": [1, 2],
            "b": [3, 4],
        }
    )
    ctx.register_record_batches("t", [table.to_batches()])
    frame = UnifiedLazyFrame(ctx.sql("SELECT * FROM t"), adapter=SimpleNamespace(platform_name="DataFusion"))
    return ctx, frame


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_string_operations(datafusion_frame):
    _, frame = datafusion_frame

    result = frame.with_columns(
        UnifiedExpr(datafusion.col("name")).str.starts_with("A").alias("starts"),
        UnifiedExpr(datafusion.col("name")).str.ends_with("e").alias("ends"),
        UnifiedExpr(datafusion.col("text")).str.contains("Smith|Jones").alias("contains"),
        UnifiedExpr(datafusion.col("name")).str.slice(1, 3).alias("slice"),
        UnifiedExpr(datafusion.col("name")).str.to_uppercase().alias("upper"),
        UnifiedExpr(datafusion.col("name")).str.to_lowercase().alias("lower"),
        UnifiedExpr(datafusion.col("name")).str.len_chars().alias("name_len"),
        UnifiedExpr(datafusion.col("text")).str.split(" ").list.get(1).alias("last_name"),
    ).collect()

    assert result.to_pydict()["starts"] == [True, False]
    assert result.to_pydict()["ends"] == [True, False]
    assert result.to_pydict()["contains"] == [True, True]
    assert result.to_pydict()["slice"] == ["lic", "ob"]
    assert result.to_pydict()["upper"] == ["ALICE", "BOB"]
    assert result.to_pydict()["lower"] == ["alice", "bob"]
    assert result.to_pydict()["name_len"] == [5, 3]
    assert result.to_pydict()["last_name"] == ["Smith", "Jones"]


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_split_returns_full_list_expression(datafusion_frame):
    from benchbox.platforms.dataframe.unified_frame import UnifiedListExpr

    _, frame = datafusion_frame

    split_expr = UnifiedExpr(datafusion.col("text")).str.split(" ")
    assert isinstance(split_expr, UnifiedListExpr)

    result = frame.with_columns(
        split_expr.list.get(0).alias("first_name"),
        UnifiedExpr(datafusion.col("text")).str.split(" ").list.len().alias("word_count"),
        UnifiedExpr(datafusion.col("text")).str.split(" ").list.contains("Smith").alias("has_smith"),
        UnifiedExpr(datafusion.col("text")).str.split(" ").alias("words"),
    ).collect()

    result_dict = result.to_pydict()
    assert result_dict["first_name"] == ["Alice", "Bob"]
    assert result_dict["word_count"] == [2, 2]
    assert result_dict["has_smith"] == [True, False]
    assert result_dict["words"] == [["Alice", "Smith"], ["Bob", "Jones"]]


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_split_edge_cases_match_list_semantics(datafusion_frame):
    _, frame = datafusion_frame

    split_expr = UnifiedExpr(datafusion.col("text")).str.split(" ")
    missing_sep = UnifiedExpr(datafusion.col("text")).str.split(",")
    expr_sep = UnifiedExpr(datafusion.col("text")).str.split(datafusion.lit(" "))
    result = frame.with_columns(
        split_expr.list.get(5).alias("oob"),
        missing_sep.list.get(0).alias("whole"),
        missing_sep.list.get(1).alias("missing"),
        expr_sep.list.get(1).alias("expr_last"),
    ).collect()

    result_dict = result.to_pydict()
    assert result_dict["oob"] == [None, None]
    assert result_dict["whole"] == ["Alice Smith", "Bob Jones"]
    assert result_dict["missing"] == [None, None]
    assert result_dict["expr_last"] == ["Smith", "Jones"]


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_date_operations_and_string_concat(datafusion_frame):
    _, frame = datafusion_frame

    result = frame.with_columns(
        UnifiedExpr(datafusion.col("d")).dt.year().alias("year"),
        UnifiedExpr(datafusion.col("d")).dt.month().alias("month"),
        UnifiedExpr(datafusion.col("d")).dt.day().alias("day"),
        UnifiedExpr(datafusion.col("dt")).dt.hour().alias("hour"),
        UnifiedExpr(datafusion.col("dt")).dt.minute().alias("minute"),
        UnifiedExpr(datafusion.col("d")).dt.weekday().alias("weekday"),
        (UnifiedExpr(datafusion.col("name")) + "X").alias("suffix_name"),
        ("X" + UnifiedExpr(datafusion.col("name"))).alias("prefix_name"),
        UnifiedExpr(datafusion.col("name")).concat_str("-", UnifiedExpr(datafusion.col("text"))).alias("joined"),
    ).collect()

    result_dict = result.to_pydict()
    assert result_dict["year"] == [2024, 2024]
    assert result_dict["month"] == [1, 1]
    assert result_dict["day"] == [15, 16]
    assert result_dict["hour"] == [10, 11]
    assert result_dict["minute"] == [30, 45]
    assert result_dict["weekday"] == [0, 1]
    assert result_dict["suffix_name"] == ["AliceX", "BobX"]
    assert result_dict["prefix_name"] == ["XAlice", "XBob"]
    assert result_dict["joined"] == ["Alice-Alice Smith", "Bob-Bob Jones"]


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_list_operations(datafusion_frame):
    _, frame = datafusion_frame

    result = frame.with_columns(
        UnifiedExpr(datafusion.col("arr")).list.contains(4).alias("has_four"),
        UnifiedExpr(datafusion.col("arr")).list.len().alias("arr_len"),
        UnifiedExpr(datafusion.col("arr")).list.get(1).alias("second"),
        UnifiedExpr(datafusion.col("arr")).list.unique().alias("unique_vals"),
        UnifiedExpr(datafusion.col("arr")).list.min().alias("min_val"),
        UnifiedExpr(datafusion.col("arr")).list.max().alias("max_val"),
        UnifiedExpr(datafusion.col("arr")).list.sort().alias("sorted_vals"),
        UnifiedExpr(datafusion.col("arr")).list.slice(0, 2).alias("slice_vals"),
    ).collect()

    result_dict = result.to_pydict()
    assert result_dict["has_four"] == [False, True]
    assert result_dict["arr_len"] == [3, 2]
    assert result_dict["second"] == [1, 4]
    assert [sorted(v) for v in result_dict["unique_vals"]] == [[1, 3], [2, 4]]
    assert result_dict["min_val"] == [1, 2]
    assert result_dict["max_val"] == [3, 4]
    assert result_dict["sorted_vals"] == [[1, 3, 3], [2, 4]]
    assert result_dict["slice_vals"] == [[3, 1], [2, 4]]


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_frame_operations(datafusion_frame):
    ctx, frame = datafusion_frame

    sorted_frame = frame.sort(UnifiedExpr(datafusion.col("a")).desc()).collect()
    assert sorted_frame.to_pydict()["a"] == [2, 1]

    stacked = frame.vstack(frame).collect()
    assert stacked.num_rows == 4

    melted = UnifiedLazyFrame(ctx.sql("SELECT a, b FROM t"), adapter=SimpleNamespace(platform_name="DataFusion")).melt(
        [], ["a", "b"]
    )
    melted_rows = melted.collect().to_pydict()
    assert sorted(melted_rows["variable"]) == ["a", "a", "b", "b"]
    assert sorted(melted_rows["value"]) == [1.0, 2.0, 3.0, 4.0]

    exploded = UnifiedLazyFrame(
        ctx.sql("SELECT [1, 2] AS items UNION ALL SELECT [3] AS items"),
        adapter=SimpleNamespace(platform_name="DataFusion"),
    ).explode("items")
    assert sorted(exploded.collect().to_pydict()["items"]) == [1, 2, 3]

    assert frame.collect_column_as_list("a") == [1, 2]
    assert frame.scalar(1, 0) == "Bob"


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_distinct_and_limit(datafusion_frame):
    ctx, frame = datafusion_frame

    doubled = frame.vstack(frame)
    distinct_result = doubled.distinct().collect()
    assert distinct_result.num_rows == 2

    limited = frame.limit(1).collect()
    assert limited.num_rows == 1


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_type_coercions(datafusion_frame):
    import pyarrow as pa

    _, frame = datafusion_frame

    result = frame.with_columns(
        UnifiedExpr(datafusion.col("a")).cast(pa.float64()).alias("a_float"),
        UnifiedExpr(datafusion.col("a")).cast_float().alias("a_cast_float"),
        UnifiedExpr(datafusion.col("a")).cast_string().alias("a_str"),
        UnifiedExpr(datafusion.col("a")).cast_int32().alias("a_int32"),
        UnifiedExpr(datafusion.col("a")).cast_int64().alias("a_int64"),
    ).collect()

    d = result.to_pydict()
    assert d["a_float"] == [1.0, 2.0]
    assert d["a_cast_float"] == [1.0, 2.0]
    assert d["a_str"] == ["1", "2"]
    assert d["a_int32"] == [1, 2]
    assert d["a_int64"] == [1, 2]


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_is_in_is_null_is_not_null(datafusion_frame):
    ctx = datafusion.SessionContext()
    table = pa.table({"v": [1, None, 3], "n": pa.array([None, "b", "c"], type=pa.string())})
    ctx.register_record_batches("t2", [table.to_batches()])
    frame2 = UnifiedLazyFrame(ctx.sql("SELECT * FROM t2"), adapter=SimpleNamespace(platform_name="DataFusion"))

    result = frame2.with_columns(
        UnifiedExpr(datafusion.col("v")).is_in([1, 3]).alias("in_13"),
        UnifiedExpr(datafusion.col("v")).is_null().alias("v_null"),
        UnifiedExpr(datafusion.col("n")).is_not_null().alias("n_notnull"),
    ).collect()

    d = result.to_pydict()
    assert d["in_13"] == [True, None, True]
    assert d["v_null"] == [False, True, False]
    assert d["n_notnull"] == [False, True, True]


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_case_when_otherwise(datafusion_frame):
    from benchbox.platforms.dataframe.unified_frame import UnifiedWhen

    _, frame = datafusion_frame

    when = UnifiedWhen(datafusion.col("a") > datafusion.lit(1), platform="DataFusion")
    case_expr = when.then("big").otherwise("small")

    result = frame.with_columns(case_expr.alias("size_label")).collect()

    d = result.to_pydict()
    assert d["size_label"] == ["small", "big"]


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_case_when_chained(datafusion_frame):
    from benchbox.platforms.dataframe.unified_frame import UnifiedWhen

    _, frame = datafusion_frame

    when = UnifiedWhen(datafusion.col("a") == datafusion.lit(1), platform="DataFusion")
    when_then = when.then("one")
    case_expr = when_then.when(datafusion.col("a") == datafusion.lit(2)).then("two").otherwise("other")

    result = frame.with_columns(case_expr.alias("label")).collect()

    d = result.to_pydict()
    assert d["label"] == ["one", "two"]


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_dt_truncate_and_total_seconds(datafusion_frame):
    _, frame = datafusion_frame

    result = frame.with_columns(
        UnifiedExpr(datafusion.col("dt")).dt.truncate("1d").alias("trunc_day"),
    ).collect()

    d = result.to_pydict()
    trunc0 = d["trunc_day"][0]
    trunc1 = d["trunc_day"][1]
    if hasattr(trunc0, "as_py"):
        trunc0 = trunc0.as_py()
    if hasattr(trunc1, "as_py"):
        trunc1 = trunc1.as_py()
    assert trunc0.date() == datetime.date(2024, 1, 15)
    assert trunc1.date() == datetime.date(2024, 1, 16)


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_arithmetic_operators(datafusion_frame):
    _, frame = datafusion_frame

    result = frame.with_columns(
        (UnifiedExpr(datafusion.col("a")) + UnifiedExpr(datafusion.col("b"))).alias("add"),
        (UnifiedExpr(datafusion.col("b")) - UnifiedExpr(datafusion.col("a"))).alias("sub"),
        (UnifiedExpr(datafusion.col("a")) * UnifiedExpr(datafusion.col("b"))).alias("mul"),
        (UnifiedExpr(datafusion.col("b")) / UnifiedExpr(datafusion.col("a"))).alias("div"),
    ).collect()

    d = result.to_pydict()
    assert d["add"] == [4, 6]
    assert d["sub"] == [2, 2]
    assert d["mul"] == [3, 8]
    assert d["div"] == [3.0, 2.0]


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_concat_str(datafusion_frame):
    _, frame = datafusion_frame

    result = frame.with_columns(
        UnifiedExpr(datafusion.col("name")).concat_str(UnifiedExpr(datafusion.col("name"))).alias("doubled"),
    ).collect()

    d = result.to_pydict()
    assert d["doubled"] == ["AliceAlice", "BobBob"]


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_rename_columns(datafusion_frame):
    _, frame = datafusion_frame

    result = frame.select("a", "b").rename({"a": "col_a", "b": "col_b"}).collect()

    d = result.to_pydict()
    assert "col_a" in d
    assert "col_b" in d
    assert d["col_a"] == [1, 2]
    assert d["col_b"] == [3, 4]


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_drop_columns(datafusion_frame):
    _, frame = datafusion_frame

    result = frame.select("a", "b", "name").drop("name").collect()

    d = result.to_pydict()
    assert "name" not in d
    assert "a" in d
    assert "b" in d


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_limit_and_head(datafusion_frame):
    _, frame = datafusion_frame

    result_limit = frame.select("a").limit(1).collect()
    result_head = frame.select("a").head(1).collect()

    d_limit = result_limit.to_pydict()
    d_head = result_head.to_pydict()
    assert len(d_limit["a"]) == 1
    assert len(d_head["a"]) == 1


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_sort_ascending(datafusion_frame):
    _, frame = datafusion_frame

    result = frame.select("a").sort("a").collect()
    d = result.to_pydict()
    assert d["a"] == [1, 2]


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_sort_descending(datafusion_frame):
    _, frame = datafusion_frame

    result = frame.select("a").sort("a", descending=True).collect()
    d = result.to_pydict()
    assert d["a"] == [2, 1]


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_sort_tuple_syntax(datafusion_frame):
    _, frame = datafusion_frame

    result = frame.select("a").sort([("a", "desc")]).collect()
    d = result.to_pydict()
    assert d["a"] == [2, 1]


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_vstack_union(datafusion_frame):
    _, frame = datafusion_frame

    sub_frame = frame.select("a")
    result = sub_frame.vstack(sub_frame).collect()
    d = result.to_pydict()
    assert len(d["a"]) == 4
    assert sorted(d["a"]) == [1, 1, 2, 2]


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_scalar_extraction(datafusion_frame):
    _, frame = datafusion_frame

    value = frame.select("a").limit(1).scalar(row=0, col=0)
    assert value == 1


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_unique_dedup(datafusion_frame):
    ctx, _ = datafusion_frame

    import pyarrow as pa

    dup_table = pa.table({"x": [1, 1, 2, 2, 3]})
    ctx.register_record_batches("dup_t", [dup_table.to_batches()])
    from types import SimpleNamespace

    dup_frame = UnifiedLazyFrame(
        ctx.sql("SELECT * FROM dup_t"),
        adapter=SimpleNamespace(platform_name="DataFusion"),
    )
    result = dup_frame.unique().collect()
    d = result.to_pydict()
    assert sorted(d["x"]) == [1, 2, 3]


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_columns_property(datafusion_frame):
    _, frame = datafusion_frame

    cols = frame.select("a", "b").columns
    assert set(cols) == {"a", "b"}


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_filter_after_group_by_having(datafusion_frame):
    ctx, _ = datafusion_frame

    from types import SimpleNamespace

    import pyarrow as pa

    grouped_table = pa.table({"cat": ["A", "A", "B", "B", "B"], "val": [1, 2, 3, 4, 5]})
    ctx.register_record_batches("grouped_t", [grouped_table.to_batches()])
    g_frame = UnifiedLazyFrame(
        ctx.sql("SELECT * FROM grouped_t"),
        adapter=SimpleNamespace(platform_name="DataFusion"),
    )

    result = (
        g_frame.group_by("cat")
        .agg(UnifiedExpr(datafusion.col("val")).sum().alias("total"))
        .filter(UnifiedExpr(datafusion.col("total")) > 5)
        .collect()
    )
    d = result.to_pydict()
    assert d["cat"] == ["B"]
    assert d["total"] == [12]


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_string_add_concat(datafusion_frame):
    from datafusion import lit as df_lit

    _, frame = datafusion_frame

    name_expr = UnifiedExpr(datafusion.col("name"), _is_string_literal=True)
    suffix_expr = UnifiedExpr(df_lit("!"), _is_string_literal=True)
    result = frame.with_columns((name_expr + suffix_expr).alias("name_excl")).collect()

    d = result.to_pydict()
    assert d["name_excl"] == ["Alice!", "Bob!"]


_RANK_VALUES = [3.0, None, 1.0, 3.0, 2.0, None, 5.0, 1.0]


_ASCENDING_MIN_RANK_BY_ID = {0: 4, 1: None, 2: 1, 3: 4, 4: 3, 5: None, 6: 6, 7: 1}
_DESCENDING_TOP_TWO_MIN_RANK_BY_ID = [(0, 2), (3, 2), (6, 1)]


def _non_null_ranks(rank_by_id):
    return sorted(rank for rank in rank_by_id.values() if rank is not None)


def _null_rank_ids(rank_by_id):
    return {row_id for row_id, rank in rank_by_id.items() if rank is None}


@pytest.fixture()
def datafusion_rank_frame():
    ctx = datafusion.SessionContext()
    table = pa.table(
        {"id": list(range(len(_RANK_VALUES))), "x": _RANK_VALUES, "g": ["a", "a", "a", "a", "b", "b", "b", "b"]}
    )
    ctx.register_record_batches("rank_t", [table.to_batches()])
    return UnifiedLazyFrame(ctx.sql("SELECT * FROM rank_t"), adapter=SimpleNamespace(platform_name="DataFusion"))


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
@pytest.mark.parametrize("descending", [False, True])
@pytest.mark.parametrize("method", ["min", "max", "dense", "ordinal", "average"])
def test_datafusion_rank_without_over_ranks_the_whole_frame(datafusion_rank_frame, method, descending):
    pl = pytest.importorskip("polars")

    result = datafusion_rank_frame.with_columns(
        UnifiedExpr(datafusion.col("x")).rank(method=method, descending=descending).alias("r")
    ).collect()
    got = dict(zip(result.column("id").to_pylist(), result.column("r").to_pylist(), strict=True))

    expected_series = pl.DataFrame({"x": _RANK_VALUES}).select(pl.col("x").rank(method=method, descending=descending))[
        "x"
    ]
    expected = dict(enumerate(expected_series.to_list()))

    assert len(got) == len(_RANK_VALUES)
    if method == "ordinal":
        assert _non_null_ranks(got) == _non_null_ranks(expected)
        assert _null_rank_ids(got) == _null_rank_ids(expected)
    else:
        assert got == expected


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_rank_without_over_returns_ranks_not_the_input_column(datafusion_rank_frame):
    result = datafusion_rank_frame.with_columns(
        UnifiedExpr(datafusion.col("x")).rank(method="min").alias("r")
    ).collect()
    got = dict(zip(result.column("id").to_pylist(), result.column("r").to_pylist(), strict=True))

    assert got == _ASCENDING_MIN_RANK_BY_ID


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_rank_without_over_column_can_be_filtered_like_an_ordinary_column(datafusion_rank_frame):
    ranked = datafusion_rank_frame.with_columns(
        UnifiedExpr(datafusion.col("x")).rank(method="min", descending=True).alias("rnk")
    )
    result = ranked.filter(UnifiedExpr(datafusion.col("rnk")) <= 2).select(["id", "rnk"]).collect()

    id_and_rank = zip(result.column("id").to_pylist(), result.column("rnk").to_pylist(), strict=True)
    assert sorted(id_and_rank) == _DESCENDING_TOP_TWO_MIN_RANK_BY_ID


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_rank_over_partition_still_partitions():
    ctx = datafusion.SessionContext()
    table = pa.table(
        {"id": [0, 1, 2, 3, 4, 5], "x": [3.0, 1.0, 3.0, 2.0, 9.0, 4.0], "g": ["a", "a", "a", "b", "b", "b"]}
    )
    ctx.register_record_batches("rank_g", [table.to_batches()])
    frame = UnifiedLazyFrame(ctx.sql("SELECT * FROM rank_g"), adapter=SimpleNamespace(platform_name="DataFusion"))

    result = frame.with_columns(UnifiedExpr(datafusion.col("x")).rank(method="min").over("g").alias("r")).collect()
    got = dict(zip(result.column("id").to_pylist(), result.column("r").to_pylist(), strict=True))

    assert got == {1: 1, 0: 2, 2: 2, 3: 1, 5: 2, 4: 3}


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_rank_rejects_unknown_method(datafusion_rank_frame):
    with pytest.raises(ValueError, match="Unsupported rank method"):
        UnifiedExpr(datafusion.col("x")).rank(method="bogus")


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_integer_division_is_true_division(datafusion_frame):
    _, frame = datafusion_frame

    result = frame.select(
        (UnifiedExpr(datafusion.col("a")) / UnifiedExpr(datafusion.col("b"))).alias("col_by_col"),
        (UnifiedExpr(datafusion.col("a")) / 4).alias("col_by_lit"),
        (1 / UnifiedExpr(datafusion.col("b"))).alias("lit_by_col"),
    ).collect()

    assert result.column("col_by_col").to_pylist() == pytest.approx([1 / 3, 2 / 4])
    assert result.column("col_by_lit").to_pylist() == pytest.approx([0.25, 0.5])
    assert result.column("lit_by_col").to_pylist() == pytest.approx([1 / 3, 1 / 4])


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_division_by_zero_is_null(datafusion_frame):
    _, frame = datafusion_frame

    result = frame.select(
        (
            UnifiedExpr(datafusion.col("a")) / (UnifiedExpr(datafusion.col("b")) - UnifiedExpr(datafusion.col("b")))
        ).alias("q")
    ).collect()

    assert result.column("q").to_pylist() == [None, None]


@pytest.fixture()
def agg_frame():
    ctx = datafusion.SessionContext()
    table = pa.table(
        {
            "g": [1, 1, 2, 2, 3],
            "day": ["Sun", "Mon", "Sun", "Sun", "Mon"],
            "x": [1, 2, 3, None, None],
            "y": [1.0, 2.0, 4.0, 4.0, 5.0],
        }
    )
    ctx.register_record_batches("agg_t", [table.to_batches()])
    return UnifiedLazyFrame(ctx.sql("SELECT * FROM agg_t"), adapter=SimpleNamespace(platform_name="DataFusion"))


def _col(name):
    return UnifiedExpr(datafusion.col(name))


def _when(condition):
    from benchbox.platforms.dataframe.unified_frame import UnifiedWhen

    return UnifiedWhen(condition.native, platform="DataFusion")


def _grouped(frame, *exprs):
    result = frame.group_by("g").agg(*exprs).sort("g").collect().to_pydict()
    assert result["g"] == [1, 2, 3]
    return result


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_groupby_case_over_aggregates(agg_frame):
    x = _col("x")
    result = _grouped(agg_frame, _when(x.count() > 0).then(x.sum()).otherwise(None).alias("s"))
    assert result["s"] == [3, 3, None]


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_groupby_case_over_aggregates_of_case(agg_frame):
    sun_x = _when(_col("day") == "Sun").then(_col("x")).otherwise(None)
    result = _grouped(
        agg_frame,
        _when(sun_x.count() > 0).then(sun_x.sum()).otherwise(None).alias("sun_sales"),
    )
    assert result["sun_sales"] == [1, 3, None]


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_groupby_sum_of_case_times_columns(agg_frame):
    result = _grouped(
        agg_frame,
        _when(_col("day") == "Sun").then(_col("x") * _col("y")).otherwise(0).sum().alias("sun_xy"),
    )
    assert result["sun_xy"] == [1.0, 12.0, 0.0]


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_groupby_count_times_sum_and_mixed_arithmetic(agg_frame):
    x, y = _col("x"), _col("y")
    result = _grouped(
        agg_frame,
        (x.count() * x.sum()).alias("cnt_x_sum"),
        (x.count() * x.sum() + y.sum()).alias("mixed"),
        (x.sum() / y.sum()).alias("ratio"),
    )
    assert result["cnt_x_sum"] == [6, 3, None]
    assert result["mixed"] == [9.0, 11.0, None]
    assert result["ratio"] == [1.0, 0.375, None]


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_global_select_case_over_aggregates(agg_frame):
    x, y = _col("x"), _col("y")
    result = (
        agg_frame.select(
            _when(x.count() > 0).then(x.sum()).otherwise(None).alias("s"),
            (y.sum() * 0.5).alias("half_y"),
        )
        .collect()
        .to_pydict()
    )
    assert result == {"s": [6], "half_y": [8.0]}


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
@pytest.mark.parametrize(
    "dtype", [pa.int8(), pa.int32(), pa.int64(), pa.uint32(), pa.uint64()] if HAS_DATAFUSION else []
)
def test_datafusion_computed_integral_division(dtype):
    ctx = datafusion.SessionContext()
    table = pa.table({"a": pa.array([1, 3], type=dtype), "b": pa.array([4, 4], type=dtype)})
    frame = UnifiedLazyFrame(ctx.from_arrow(table), SimpleNamespace(platform_name="DataFusion"))
    a = UnifiedExpr(datafusion.col("a"))
    b = UnifiedExpr(datafusion.col("b"))

    result = frame.select(
        ((a + 1) / (b + 1)).alias("computed"),
        (UnifiedExpr(datafusion.lit(1)) / b).alias("wrapped_literal"),
        ((a / b).cast(int) / b).alias("nested"),
        ((a / b).cast_string().str.len_chars()).alias("length"),
    ).collect()

    assert [float(value) for value in result.column("computed").to_pylist()] == pytest.approx([0.4, 0.8])
    assert result.column("wrapped_literal").to_pylist() == pytest.approx([0.25, 0.25])
    assert result.column("nested").to_pylist() == pytest.approx([0.0, 0.0])
    assert result.column("length").to_pylist() == [4, 4]


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
@pytest.mark.parametrize("dtype", [pa.decimal128(30, 2), pa.decimal256(40, 4)] if HAS_DATAFUSION else [])
def test_datafusion_decimal_division_preserves_native_values_and_types(dtype):
    ctx = datafusion.SessionContext()
    table = pa.table(
        {
            "amount": pa.array([Decimal("9007199254740993.20"), Decimal("1.20"), None], type=dtype),
            "divisor": [4, 0, 4],
            "decimal_divisor": pa.array([Decimal("4.00"), Decimal("0.00"), Decimal("4.00")], type=dtype),
        }
    )
    native_frame = ctx.from_arrow(table)
    frame = UnifiedLazyFrame(native_frame, SimpleNamespace(platform_name="DataFusion"))
    amount = UnifiedExpr(datafusion.col("amount"))
    divisor = UnifiedExpr(datafusion.col("divisor"))
    decimal_divisor = UnifiedExpr(datafusion.col("decimal_divisor"))
    literal = pa.scalar(Decimal("9007199254740993.20"), type=dtype)
    literal_value = Decimal("9007199254740993.20")
    wrapped_literal = UnifiedExpr(datafusion.lit(literal))

    expressions = [
        (amount / divisor).alias("decimal_by_integer"),
        (amount / decimal_divisor).alias("decimal_by_decimal"),
        (amount / 4).alias("decimal_by_literal"),
        (4 / amount).alias("integer_by_decimal"),
        (literal_value / amount).alias("decimal_literal_by_decimal"),
        (wrapped_literal / amount).alias("wrapped_decimal_literal"),
    ]
    f = datafusion.functions
    expected = native_frame.select(
        (datafusion.col("amount") / f.nullif(datafusion.col("divisor"), datafusion.lit(0))).alias("decimal_by_integer"),
        (datafusion.col("amount") / f.nullif(datafusion.col("decimal_divisor"), datafusion.lit(0))).alias(
            "decimal_by_decimal"
        ),
        (datafusion.col("amount") / datafusion.lit(4)).alias("decimal_by_literal"),
        (datafusion.lit(4) / f.nullif(datafusion.col("amount"), datafusion.lit(0))).alias("integer_by_decimal"),
        (datafusion.lit(literal_value) / f.nullif(datafusion.col("amount"), datafusion.lit(0))).alias(
            "decimal_literal_by_decimal"
        ),
        (datafusion.lit(literal) / f.nullif(datafusion.col("amount"), datafusion.lit(0))).alias(
            "wrapped_decimal_literal"
        ),
    ).to_arrow_table()

    result = frame.select(expressions).collect()

    assert result.schema.equals(expected.schema)
    assert result.equals(expected)
    assert all(pa.types.is_decimal(field.type) for field in result.schema)


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_decimal_division_keeps_full_numerator_range():
    ctx = datafusion.SessionContext()
    value = Decimal("110000000000000000000000000000000.00")
    table = pa.table({"amount": pa.array([value], type=pa.decimal128(38, 2)), "divisor": [1000]})
    native_frame = ctx.from_arrow(table)
    frame = UnifiedLazyFrame(native_frame, SimpleNamespace(platform_name="DataFusion"))

    result = frame.select((UnifiedExpr(datafusion.col("amount")) / 1000).alias("ratio")).collect()
    expected = native_frame.select((datafusion.col("amount") / datafusion.lit(1000)).alias("ratio")).to_arrow_table()

    assert result.schema.equals(expected.schema)
    assert result.equals(expected)


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_division_binds_each_input_schema():
    ctx = datafusion.SessionContext()
    expression = (UnifiedExpr(datafusion.col("amount")) / 4).alias("ratio")
    adapter = SimpleNamespace(platform_name="DataFusion")
    integer_frame = UnifiedLazyFrame(ctx.from_pydict({"amount": [1]}), adapter)
    decimal_frame = UnifiedLazyFrame(
        ctx.from_arrow(pa.table({"amount": pa.array([Decimal("9007199254740993.20")], type=pa.decimal128(30, 2))})),
        adapter,
    )

    integer_result = integer_frame.select(expression).collect()
    decimal_result = decimal_frame.select(expression).collect()

    assert integer_result.column("ratio").to_pylist() == [0.25]
    assert integer_result.schema.field("ratio").type == pa.float64()
    assert decimal_result.column("ratio").to_pylist() == [Decimal("2251799813685248.300000")]
    assert decimal_result.schema.field("ratio").type == pa.decimal128(34, 6)


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_deferred_division_captures_literal_lists():
    ctx = datafusion.SessionContext()
    frame = UnifiedLazyFrame(ctx.from_pydict({"a": [1, 3], "b": [4, 4]}), SimpleNamespace(platform_name="DataFusion"))
    values = [0.25]
    expression = (UnifiedExpr(datafusion.col("a")) / UnifiedExpr(datafusion.col("b"))).is_in(values).alias("selected")
    values.append(0.75)

    result = frame.select(expression).collect()

    assert result.column("selected").to_pylist() == [True, False]


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_division_propagates_through_frame_operations(datafusion_frame):
    from benchbox.platforms.dataframe.datafusion_df import DataFusionDataFrameAdapter

    _, frame = datafusion_frame
    a = UnifiedExpr(datafusion.col("a"))
    b = UnifiedExpr(datafusion.col("b"))
    ratio = a / b
    ctx = DataFusionDataFrameAdapter().create_context()

    result = frame.with_columns(
        ctx.when(ratio > 0.4).then(ratio).otherwise(0).alias("conditional"),
        ctx.coalesce(ratio, ctx.lit(0)).alias("coalesced"),
        ctx.struct(ratio.alias("ratio")).struct.field("ratio").alias("struct_ratio"),
    ).collect()
    filtered = frame.filter(ratio > 0.4).select("a").collect()
    sorted_result = frame.sort((b / a).desc()).select("a").collect()
    grouped = frame.group_by(ratio.alias("ratio")).agg(a.sum().alias("total")).collect()
    windowed = frame.with_columns(ratio.sum().over("b").alias("window_ratio")).collect()

    assert result.column("conditional").to_pylist() == pytest.approx([0.0, 0.5])
    assert result.column("coalesced").to_pylist() == pytest.approx([1 / 3, 0.5])
    assert result.column("struct_ratio").to_pylist() == pytest.approx([1 / 3, 0.5])
    assert filtered.column("a").to_pylist() == [2]
    assert sorted_result.column("a").to_pylist() == [1, 2]
    assert sorted(grouped.column("ratio").to_pylist()) == pytest.approx([1 / 3, 0.5])
    assert windowed.column("window_ratio").to_pylist() == pytest.approx([1 / 3, 0.5])


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_division_resolves_join_keys_on_each_side():
    ctx = datafusion.SessionContext()
    adapter = SimpleNamespace(platform_name="DataFusion")
    left = UnifiedLazyFrame(ctx.from_pydict({"x": [1, 2]}), adapter)
    right = UnifiedLazyFrame(
        ctx.from_arrow(pa.table({"y": pa.array([Decimal("1.00"), Decimal("3.00")], type=pa.decimal128(12, 2))})),
        adapter,
    )

    result = left.join(
        right,
        left_on=UnifiedExpr(datafusion.col("x")) / 2,
        right_on=UnifiedExpr(datafusion.col("y")) / 2,
    ).collect()

    assert result.column("x").to_pylist() == [1]
    assert result.column("y").to_pylist() == [Decimal("1.00")]


@pytest.mark.skipif(not HAS_DATAFUSION, reason="datafusion not installed")
def test_datafusion_division_over_aggregate_operands():
    ctx = datafusion.SessionContext()
    table = pa.table(
        {
            "group": ["x", "x"],
            "a": [1, 3],
            "b": [4, 4],
            "amount": pa.array([Decimal("9007199254740993.20"), Decimal("1.20")], type=pa.decimal128(30, 2)),
        }
    )
    native_frame = ctx.from_arrow(table)
    frame = UnifiedLazyFrame(native_frame, SimpleNamespace(platform_name="DataFusion"))
    a = UnifiedExpr(datafusion.col("a"))
    b = UnifiedExpr(datafusion.col("b"))
    amount = UnifiedExpr(datafusion.col("amount"))
    integer_ratio = (a.sum() / b.sum()).alias("integer_ratio")
    decimal_ratio = (amount.sum() / b.sum()).alias("decimal_ratio")
    reverse_ratio = (4 / amount.sum()).alias("reverse_ratio")
    row_ratio_sum = (a / b).filter(a > 1).sum().alias("row_ratio_sum")
    f = datafusion.functions
    native_expressions = [
        (f.sum(datafusion.col("a")).cast(pa.float64()) / f.sum(datafusion.col("b"))).alias("integer_ratio"),
        (f.sum(datafusion.col("amount")) / f.sum(datafusion.col("b"))).alias("decimal_ratio"),
        (datafusion.lit(4) / f.sum(datafusion.col("amount"))).alias("reverse_ratio"),
        f.sum(datafusion.col("a").cast(pa.float64()) / datafusion.col("b"))
        .filter(datafusion.col("a") > datafusion.lit(1))
        .build()
        .alias("row_ratio_sum"),
    ]

    result = frame.select(integer_ratio, decimal_ratio, reverse_ratio, row_ratio_sum).collect()
    expected = native_frame.aggregate([], native_expressions).to_arrow_table()
    grouped = frame.group_by("group").agg(integer_ratio, decimal_ratio, reverse_ratio, row_ratio_sum).collect()
    grouped_expected = native_frame.aggregate([datafusion.col("group")], native_expressions).to_arrow_table()

    assert result.schema.equals(expected.schema)
    assert result.equals(expected)
    assert grouped.schema.equals(grouped_expected.schema)
    assert grouped.equals(grouped_expected)


