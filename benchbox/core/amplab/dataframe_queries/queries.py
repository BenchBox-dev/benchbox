# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from csv import reader
from datetime import date
from typing import Any

from benchbox.core.dataframe.context import DataFrameContext
from benchbox.core.dataframe.pandas_ops import where as pandas_where
from benchbox.core.dataframe.query import DataFrameQuery, QueryCategory

from .parameters import get_parameters
from .registry import register_query


def _visit_date(series: Any) -> Any:
    import pandas as pd

    if isinstance(series, pd.Series) and not pd.api.types.is_datetime64_any_dtype(series):
        return pd.to_datetime(series).dt.date
    if isinstance(series, pd.Series):
        return series.dt.date
    return series


def q1_expression_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q1")
    threshold = params.get("pagerank_threshold", 1000)

    rankings = ctx.get_table("rankings")
    col = ctx.col
    lit = ctx.lit

    return rankings.filter(col("pageRank") > lit(threshold)).select("pageURL", "pageRank")


def q1_pandas_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q1")
    threshold = params.get("pagerank_threshold", 1000)

    rankings = ctx.get_table("rankings")
    return rankings[rankings["pageRank"] > threshold][["pageURL", "pageRank"]]


def q1a_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters("Q1a")
    threshold = params.get("pagerank_threshold", 1000)

    rankings = ctx.get_table("rankings")
    col = ctx.col
    lit = ctx.lit

    return rankings.filter(col("pageRank") > lit(threshold)).select(
        col("pageRank").count().alias("total_pages"),
        col("pageRank").mean().alias("avg_pagerank"),
        col("pageRank").max().alias("max_pagerank"),
    )


def q1a_pandas_impl(ctx: DataFrameContext) -> Any:
    import pandas as pd

    params = get_parameters("Q1a")
    threshold = params.get("pagerank_threshold", 1000)

    rankings = ctx.get_table("rankings")
    filtered = rankings[rankings["pageRank"] > threshold]
    return pd.DataFrame(
        {
            "total_pages": [len(filtered)],
            "avg_pagerank": [filtered["pageRank"].mean()],
            "max_pagerank": [filtered["pageRank"].max()],
        }
    )


def q2_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters("Q2")
    start_date = params.get("start_date", date(2000, 1, 1))
    end_date = params.get("end_date", date(2000, 1, 3))
    limit_rows = params.get("limit_rows", 100)

    uservisits = ctx.get_table("uservisits")
    rankings = ctx.get_table("rankings")
    col = ctx.col
    lit = ctx.lit

    visit_date = col("visitDate").cast_date()
    return (
        uservisits.filter((visit_date >= lit(start_date)) & (visit_date <= lit(end_date)))
        .join(rankings, left_on="destURL", right_on="pageURL")
        .group_by("sourceIP")
        .agg(
            col("adRevenue").sum().alias("totalRevenue"),
            col("pageRank").mean().alias("avgPageRank"),
        )
        .sort("totalRevenue", descending=True)
        .limit(limit_rows)
    )


def q2_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters("Q2")
    start_date = params.get("start_date", date(2000, 1, 1))
    end_date = params.get("end_date", date(2000, 1, 3))
    limit_rows = params.get("limit_rows", 100)

    uservisits = ctx.get_table("uservisits")
    rankings = ctx.get_table("rankings")
    visit_date = _visit_date(uservisits["visitDate"])
    filtered = uservisits[(visit_date >= start_date) & (visit_date <= end_date)]
    merged = filtered.merge(rankings, left_on="destURL", right_on="pageURL")
    return (
        merged.groupby(["sourceIP"], as_index=False)
        .agg(totalRevenue=("adRevenue", "sum"), avgPageRank=("pageRank", "mean"))
        .sort_values("totalRevenue", ascending=False)
        .head(limit_rows)
    )


def q2a_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters("Q2a")
    threshold = params.get("pagerank_threshold", 1000)
    start_date = params.get("start_date", date(2000, 1, 1))
    limit_rows = params.get("limit_rows", 100)

    uservisits = ctx.get_table("uservisits")
    rankings = ctx.get_table("rankings")
    col = ctx.col
    lit = ctx.lit

    return (
        uservisits.filter(col("visitDate").cast_date() >= lit(start_date))
        .join(rankings, left_on="destURL", right_on="pageURL")
        .filter(col("pageRank") > lit(threshold))
        .select("destURL", "visitDate", "adRevenue", "pageRank", "avgDuration")
        .sort("pageRank", descending=True)
        .limit(limit_rows)
    )


def q2a_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters("Q2a")
    threshold = params.get("pagerank_threshold", 1000)
    start_date = params.get("start_date", date(2000, 1, 1))
    limit_rows = params.get("limit_rows", 100)

    uservisits = ctx.get_table("uservisits")
    rankings = ctx.get_table("rankings")
    filtered = uservisits[_visit_date(uservisits["visitDate"]) >= start_date]
    merged = filtered.merge(rankings, left_on="destURL", right_on="pageURL")
    merged = merged[merged["pageRank"] > threshold]
    return (
        merged[["destURL", "visitDate", "adRevenue", "pageRank", "avgDuration"]]
        .sort_values("pageRank", ascending=False)
        .head(limit_rows)
    )


def q3_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters("Q3")
    start_date = params.get("start_date", date(2000, 1, 1))
    end_date = params.get("end_date", date(2000, 1, 3))
    search_term = params.get("search_term", "database")
    min_visits = params.get("min_visits", 10)
    limit_rows = params.get("limit_rows", 100)

    uservisits = ctx.get_table("uservisits")
    col = ctx.col
    lit = ctx.lit

    return (
        uservisits.filter(
            (col("visitDate").cast_date() >= lit(start_date))
            & (col("visitDate").cast_date() <= lit(end_date))
            & (col("searchWord").str.contains(search_term))
        )
        .group_by("sourceIP")
        .agg(
            col("sourceIP").count().alias("visit_count"),
            col("adRevenue").sum().alias("total_revenue"),
            col("duration").mean().alias("avg_duration"),
        )
        .filter(col("visit_count") > lit(min_visits))
        .sort("total_revenue", descending=True)
        .limit(limit_rows)
    )


def q3_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters("Q3")
    start_date = params.get("start_date", date(2000, 1, 1))
    end_date = params.get("end_date", date(2000, 1, 3))
    search_term = params.get("search_term", "database")
    min_visits = params.get("min_visits", 10)
    limit_rows = params.get("limit_rows", 100)

    uservisits = ctx.get_table("uservisits")
    visit_date = _visit_date(uservisits["visitDate"])
    filtered = uservisits[
        (visit_date >= start_date)
        & (visit_date <= end_date)
        & (uservisits["searchWord"].str.contains(search_term, na=False))
    ]
    grouped = filtered.groupby(["sourceIP"], as_index=False).agg(
        visit_count=("sourceIP", "count"), total_revenue=("adRevenue", "sum"), avg_duration=("duration", "mean")
    )
    result = grouped[grouped["visit_count"] > min_visits]
    return result.sort_values("total_revenue", ascending=False).head(limit_rows)


def q3a_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters("Q3a")
    keyword1 = params.get("keyword1", "web")
    keyword2 = params.get("keyword2", "data")
    min_content_length = params.get("min_content_length", 1000)
    limit_rows = params.get("limit_rows", 100)

    documents = ctx.get_table("documents")
    col = ctx.col
    lit = ctx.lit

    return (
        documents.with_columns(col("contents").str.len_chars().alias("content_length"))
        .filter(col("content_length") > lit(min_content_length))
        .with_columns(
            ctx.when(col("contents").str.contains(keyword1)).then(lit(1)).otherwise(lit(0)).alias("has_keyword1"),
            ctx.when(col("contents").str.contains(keyword2)).then(lit(1)).otherwise(lit(0)).alias("has_keyword2"),
        )
        .select("url", "content_length", "has_keyword1", "has_keyword2")
        .sort("content_length", descending=True)
        .limit(limit_rows)
    )


def q3a_pandas_impl(ctx: DataFrameContext) -> Any:

    params = get_parameters("Q3a")
    keyword1 = params.get("keyword1", "web")
    keyword2 = params.get("keyword2", "data")
    min_content_length = params.get("min_content_length", 1000)
    limit_rows = params.get("limit_rows", 100)

    documents = ctx.get_table("documents")
    df = documents.copy()
    df["content_length"] = df["contents"].str.len()
    df = df[df["content_length"] > min_content_length]
    df["has_keyword1"] = pandas_where(df["contents"].str.contains(keyword1, na=False), 1, 0)
    df["has_keyword2"] = pandas_where(df["contents"].str.contains(keyword2, na=False), 1, 0)
    return (
        df[["url", "content_length", "has_keyword1", "has_keyword2"]]
        .sort_values("content_length", ascending=False)
        .head(limit_rows)
    )


def q4_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters("Q4")
    start_date = params.get("start_date", date(2000, 1, 1))
    min_revenue = params.get("min_revenue", 1.0)
    min_visits = params.get("min_visits", 10)
    limit_rows = params.get("limit_rows", 100)

    uservisits = ctx.get_table("uservisits")
    col = ctx.col
    lit = ctx.lit

    return (
        uservisits.filter((col("visitDate").cast_date() >= lit(start_date)) & (col("adRevenue") > lit(min_revenue)))
        .group_by("countryCode", "languageCode")
        .agg(
            col("countryCode").count().alias("visit_count"),
            col("adRevenue").sum().alias("total_revenue"),
            col("duration").mean().alias("avg_duration"),
            col("sourceIP").n_unique().alias("unique_visitors"),
        )
        .filter(col("visit_count") > lit(min_visits))
        .sort("total_revenue", descending=True)
        .limit(limit_rows)
    )


def q4_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters("Q4")
    start_date = params.get("start_date", date(2000, 1, 1))
    min_revenue = params.get("min_revenue", 1.0)
    min_visits = params.get("min_visits", 10)
    limit_rows = params.get("limit_rows", 100)

    uservisits = ctx.get_table("uservisits")
    visit_date = _visit_date(uservisits["visitDate"])
    filtered = uservisits[(visit_date >= start_date) & (uservisits["adRevenue"] > min_revenue)]
    grouped = filtered.groupby(["countryCode", "languageCode"], as_index=False).agg(
        visit_count=("countryCode", "count"),
        total_revenue=("adRevenue", "sum"),
        avg_duration=("duration", "mean"),
        unique_visitors=("sourceIP", "nunique"),
    )
    result = grouped[grouped["visit_count"] > min_visits]
    return result.sort_values("total_revenue", ascending=False).head(limit_rows)


def q5_expression_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters("Q5")
    start_date = params.get("start_date", date(2000, 1, 1))
    threshold = params.get("pagerank_threshold", 1000)
    min_visits = params.get("min_visits", 10)
    limit_rows = params.get("limit_rows", 100)

    uservisits = ctx.get_table("uservisits")
    rankings = ctx.get_table("rankings")
    col = ctx.col
    lit = ctx.lit

    return (
        uservisits.filter(col("visitDate").cast_date() >= lit(start_date))
        .join(rankings, left_on="destURL", right_on="pageURL")
        .filter(col("pageRank") > lit(threshold))
        .group_by("countryCode")
        .agg(
            col("destURL").n_unique().alias("unique_pages"),
            col("countryCode").count().alias("total_visits"),
            col("adRevenue").sum().alias("total_revenue"),
            col("pageRank").mean().alias("avg_pagerank"),
            col("duration").mean().alias("avg_duration"),
        )
        .filter(col("total_visits") > lit(min_visits))
        .sort("total_revenue", descending=True)
        .limit(limit_rows)
    )


def q5_pandas_impl(ctx: DataFrameContext) -> Any:
    params = get_parameters("Q5")
    start_date = params.get("start_date", date(2000, 1, 1))
    threshold = params.get("pagerank_threshold", 1000)
    min_visits = params.get("min_visits", 10)
    limit_rows = params.get("limit_rows", 100)

    uservisits = ctx.get_table("uservisits")
    rankings = ctx.get_table("rankings")
    filtered = uservisits[_visit_date(uservisits["visitDate"]) >= start_date]
    merged = filtered.merge(rankings, left_on="destURL", right_on="pageURL")
    merged = merged[merged["pageRank"] > threshold]
    grouped = merged.groupby(["countryCode"], as_index=False).agg(
        unique_pages=("destURL", "nunique"),
        total_visits=("countryCode", "count"),
        total_revenue=("adRevenue", "sum"),
        avg_pagerank=("pageRank", "mean"),
        avg_duration=("duration", "mean"),
    )
    result = grouped[grouped["total_visits"] > min_visits]
    return result.sort_values("total_revenue", ascending=False).head(limit_rows)


_CATEGORY_CODES = {
    "AG": QueryCategory.AGGREGATE,
    "AN": QueryCategory.ANALYTICAL,
    "FI": QueryCategory.FILTER,
    "GB": QueryCategory.GROUP_BY,
    "JO": QueryCategory.JOIN,
    "PR": QueryCategory.PROJECTION,
    "SC": QueryCategory.SCAN,
    "SO": QueryCategory.SORT,
}

_QUERY_METADATA = """\
Q1|Scan Query|Filter rankings by pageRank threshold|SC,FI|q1
Q1a|Scan Aggregation|COUNT, AVG, MAX of pageRank with threshold filter|FI,AG|q1a
Q2|Join Query|Join uservisits to rankings with date range, GROUP BY sourceIP|FI,JO,GB,SO|q2
Q2a|Join with Filter|Join uservisits to rankings with pageRank filter|FI,JO,SO|q2a
Q3|Text Search|Text search on searchWord with HAVING on visit_count|FI,GB,AG,AN|q3
Q3a|Document Analysis|Text analysis on documents with keyword matching via CASE WHEN|FI,PR,AN|q3a
Q4|Country/Language Analytics|Country and language analytics with HAVING and COUNT DISTINCT|FI,GB,AG,AN|q4
Q5|Cross-Table Analytics|Join uservisits+rankings, GROUP BY country with HAVING and COUNT DISTINCT|FI,JO,GB,AG,AN|q5
"""


def _impl_for(stem: str, family: str) -> Any:
    return globals()[f"{stem}_{family}_impl"]


def _register_all_queries() -> None:
    for query_id, query_name, description, category_codes, impl_stem in reader(
        _QUERY_METADATA.splitlines(), delimiter="|"
    ):
        register_query(
            DataFrameQuery(
                query_id=query_id,
                query_name=query_name,
                description=description,
                categories=[_CATEGORY_CODES[code] for code in category_codes.split(",")],
                expression_impl=_impl_for(impl_stem, "expression"),
                pandas_impl=_impl_for(impl_stem, "pandas"),
            )
        )


_register_all_queries()
