from __future__ import annotations

from types import SimpleNamespace

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

datafusion = pytest.importorskip("datafusion")
pa = pytest.importorskip("pyarrow")

from benchbox.platforms.dataframe.datafusion_identifiers import (
    datafusion_column,
    datafusion_identifier,
    datafusion_identifiers,
)
from benchbox.platforms.dataframe.unified_frame import UnifiedExpr, UnifiedLazyFrame


@pytest.fixture()
def mixed_case_frames():
    ctx = datafusion.SessionContext()
    ctx.register_record_batches(
        "hits",
        [pa.table({"WatchID": [3, 1, 2, 1], "PageRank": [10, 20, 30, 40], "lower": [1, 1, 1, 1]}).to_batches()],
    )
    ctx.register_record_batches(
        "dim",
        [pa.table({"WatchID": [1, 2, 3], "Label": ["a", "b", "c"]}).to_batches()],
    )
    adapter = SimpleNamespace(platform_name="DataFusion")
    return (
        UnifiedLazyFrame(ctx.table("hits"), adapter=adapter),
        UnifiedLazyFrame(ctx.table("dim"), adapter=adapter),
    )


def _rows(frame: UnifiedLazyFrame) -> list[dict]:
    return frame.collect().to_pylist()


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("watchid", "watchid"),
        ("__join_key_0__", "__join_key_0__"),
        ("WatchID", '"WatchID"'),
        ('"WatchID"', '"WatchID"'),
        ("t.WatchID", "t.WatchID"),
    ],
)
def test_datafusion_identifier_quotes_only_names_that_case_fold(name, expected):
    assert datafusion_identifier(name) == expected


def test_datafusion_identifiers_passes_non_strings_through():
    marker = object()
    assert datafusion_identifiers(["WatchID", marker, "lower"]) == ['"WatchID"', marker, "lower"]


def test_datafusion_column_resolves_mixed_case_column():
    ctx = datafusion.SessionContext()
    ctx.register_record_batches("t", [pa.table({"WatchID": [1, 2]}).to_batches()])
    result = ctx.table("t").select(datafusion_column("WatchID")).collect()
    assert pa.Table.from_batches(result).column("WatchID").to_pylist() == [1, 2]


def test_select_by_mixed_case_name(mixed_case_frames):
    hits, _ = mixed_case_frames
    assert _rows(hits.select("WatchID", "lower")) == [{"WatchID": v, "lower": 1} for v in (3, 1, 2, 1)]


def test_group_by_and_aggregate_mixed_case_columns(mixed_case_frames):
    hits, _ = mixed_case_frames
    total = UnifiedExpr(datafusion_column("PageRank")).sum().alias("total")
    rows = _rows(hits.group_by("WatchID").agg(total).sort("WatchID"))
    assert rows == [
        {"WatchID": 1, "total": 60},
        {"WatchID": 2, "total": 30},
        {"WatchID": 3, "total": 10},
    ]


def test_sort_descending_by_mixed_case_name(mixed_case_frames):
    hits, _ = mixed_case_frames
    rows = _rows(hits.sort("PageRank", descending=True))
    assert [row["PageRank"] for row in rows] == [40, 30, 20, 10]


def test_join_on_mixed_case_key(mixed_case_frames):
    hits, dim = mixed_case_frames
    rows = _rows(hits.join(dim, on="WatchID", how="inner").sort("PageRank"))
    assert [(row["WatchID"], row["Label"]) for row in rows] == [(3, "c"), (1, "a"), (2, "b"), (1, "a")]


def test_rename_and_drop_mixed_case_names(mixed_case_frames):
    hits, _ = mixed_case_frames
    renamed = hits.rename({"WatchID": "watch"}).drop("PageRank")
    assert renamed.collect().column_names == ["watch", "lower"]


def test_unique_subset_on_mixed_case_name(mixed_case_frames):
    hits, _ = mixed_case_frames
    assert len(_rows(hits.unique(subset="WatchID"))) == 3
