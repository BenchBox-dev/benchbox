# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from csv import reader
from datetime import date
from typing import Any

from benchbox.core.dataframe.context import DataFrameContext
from benchbox.core.dataframe.pandas_ops import is_lazy_frame, where as pandas_where
from benchbox.core.dataframe.query import DataFrameQuery, QueryCategory

from .registry import register_query

_DATE_FROM, _DATE_TO = "2013-07-01", "2013-07-31"


def _hits(ctx: DataFrameContext) -> Any:
    return ctx.get_table("hits")


def _expr_july_mask(ctx: DataFrameContext, start: str = _DATE_FROM, end: str = _DATE_TO) -> Any:
    col, lit = ctx.col, ctx.lit
    start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
    return (col("CounterID") == lit(62)) & (col("EventDate") >= lit(start_date)) & (col("EventDate") <= lit(end_date))


def _expr_filter(ctx: DataFrameContext, hits: Any, code: str) -> Any:
    col, lit = ctx.col, ctx.lit
    if not code:
        return hits
    if code == "adv_ne_0":
        return hits.filter(col("AdvEngineID") != lit(0))
    if code == "phone_model_nonempty":
        return hits.filter(col("MobilePhoneModel") != lit(""))
    if code == "search_nonempty":
        return hits.filter(col("SearchPhrase") != lit(""))
    if code == "url_google":
        return hits.filter(col("URL").str.contains(lit("google")))
    if code == "google_search":
        return hits.filter((col("URL").str.contains(lit("google"))) & (col("SearchPhrase") != lit("")))
    if code == "google_title":
        return hits.filter(
            (col("Title").str.contains(lit("Google")))
            & (~col("URL").str.contains(lit("\\.google\\.")))
            & (col("SearchPhrase") != lit(""))
        )
    if code == "user_lookup":
        return hits.filter(col("UserID") == lit(435090932899640449))
    if code == "minute_range":
        return hits.filter(_expr_july_mask(ctx, "2013-07-14", "2013-07-15") & _expr_no_refresh(ctx))
    mask = _expr_july_mask(ctx)
    if code == "july_url_pageviews":
        return hits.filter(mask & _expr_no_refresh(ctx) & (col("URL") != lit("")))
    if code == "july_title_pageviews":
        return hits.filter(mask & _expr_no_refresh(ctx) & (col("Title") != lit("")))
    if code == "july_link_views":
        return hits.filter(
            mask & (col("IsRefresh") == lit(0)) & (col("IsLink") != lit(0)) & (col("IsDownload") == lit(0))
        )
    if code == "july_source":
        return hits.filter(mask & (col("IsRefresh") == lit(0)))
    if code == "july_referer_hash":
        return hits.filter(
            mask
            & (col("IsRefresh") == lit(0))
            & (col("TraficSourceID").is_in([-1, 6]))
            & (col("RefererHash") == lit(3594120000172545465))
        )
    if code == "july_url_hash":
        return hits.filter(mask & _expr_no_refresh(ctx) & (col("URLHash") == lit(2868770270353813622)))
    raise ValueError(f"Unknown ClickBench expression filter: {code}")


def _expr_no_refresh(ctx: DataFrameContext) -> Any:
    col, lit = ctx.col, ctx.lit
    return (col("DontCountHits") == lit(0)) & (col("IsRefresh") == lit(0))


def _pandas_july_mask(hits: Any, start: str = _DATE_FROM, end: str = _DATE_TO) -> Any:
    start_value = date.fromisoformat(start) if isinstance(start, str) else start
    end_value = date.fromisoformat(end) if isinstance(end, str) else end
    return (hits["CounterID"] == 62) & (hits["EventDate"] >= start_value) & (hits["EventDate"] <= end_value)


def _pandas_filter(hits: Any, code: str) -> Any:
    if not code:
        return hits
    if code == "adv_ne_0":
        return hits[hits["AdvEngineID"] != 0]
    if code == "phone_model_nonempty":
        return hits[hits["MobilePhoneModel"].notnull() & (hits["MobilePhoneModel"] != "")]
    if code == "search_nonempty":
        return hits[hits["SearchPhrase"].notnull() & (hits["SearchPhrase"] != "")]
    if code == "url_google":
        return hits[hits["URL"].str.contains("google", na=False)]
    if code == "google_search":
        return hits[
            hits["URL"].str.contains("google", na=False) & hits["SearchPhrase"].notnull() & (hits["SearchPhrase"] != "")
        ]
    if code == "google_title":
        return hits[
            hits["Title"].str.contains("Google", na=False)
            & ~hits["URL"].str.contains(".google.", na=False, regex=False)
            & hits["SearchPhrase"].notnull()
            & (hits["SearchPhrase"] != "")
        ]
    if code == "user_lookup":
        return hits[hits["UserID"] == 435090932899640449]
    if code == "minute_range":
        return hits[_pandas_july_mask(hits, "2013-07-14", "2013-07-15") & _pandas_no_refresh(hits)]
    mask = _pandas_july_mask(hits)
    if code == "july_url_pageviews":
        return hits[mask & _pandas_no_refresh(hits) & (hits["URL"] != "")]
    if code == "july_title_pageviews":
        return hits[mask & _pandas_no_refresh(hits) & (hits["Title"] != "")]
    if code == "july_link_views":
        return hits[mask & (hits["IsRefresh"] == 0) & (hits["IsLink"] != 0) & (hits["IsDownload"] == 0)]
    if code == "july_source":
        return hits[mask & (hits["IsRefresh"] == 0)]
    if code == "july_referer_hash":
        return hits[
            mask
            & (hits["IsRefresh"] == 0)
            & (hits["TraficSourceID"].isin([-1, 6]))
            & (hits["RefererHash"] == 3594120000172545465)
        ]
    if code == "july_url_hash":
        return hits[mask & _pandas_no_refresh(hits) & (hits["URLHash"] == 2868770270353813622)]
    raise ValueError(f"Unknown ClickBench pandas filter: {code}")


def _pandas_no_refresh(hits: Any) -> Any:
    return (hits["DontCountHits"] == 0) & (hits["IsRefresh"] == 0)


def _expr_derive(ctx: DataFrameContext, hits: Any, code: str) -> Any:
    col, lit = ctx.col, ctx.lit
    if not code:
        return hits
    if code == "minute":
        return hits.with_columns(col("EventTime").dt.minute().alias("m"))
    if code == "one":
        return hits.with_columns(lit(1).alias("one"))
    if code == "ip_offsets":
        return hits.with_columns(
            (col("ClientIP") - lit(1)).alias("ip_m1"),
            (col("ClientIP") - lit(2)).alias("ip_m2"),
            (col("ClientIP") - lit(3)).alias("ip_m3"),
        )
    if code == "source":
        return hits.with_columns(
            ctx.when((col("SearchEngineID") == lit(0)) & (col("AdvEngineID") == lit(0)))
            .then(col("Referer"))
            .otherwise(lit(""))
            .alias("Src")
        )
    if code == "trunc_minute":
        return hits.with_columns(col("EventTime").dt.truncate("1m").alias("M"))
    raise ValueError(f"Unknown ClickBench expression derived column: {code}")


def _pandas_derive(hits: Any, code: str) -> Any:
    if not code:
        return hits
    hits = hits.copy()
    if code == "minute":
        hits["m"] = hits["EventTime"].dt.minute
    elif code == "one":
        hits["one"] = 1
    elif code == "ip_offsets":
        hits["ip_m1"] = hits["ClientIP"] - 1
        hits["ip_m2"] = hits["ClientIP"] - 2
        hits["ip_m3"] = hits["ClientIP"] - 3
    elif code == "source":
        hits["Src"] = pandas_where((hits["SearchEngineID"] == 0) & (hits["AdvEngineID"] == 0), hits["Referer"], "")
    elif code == "trunc_minute":
        hits["M"] = hits["EventTime"].dt.floor("min")
    else:
        raise ValueError(f"Unknown ClickBench pandas derived column: {code}")
    return hits


def _agg_parts(token: str) -> tuple[str, str, str, str]:
    alias, rhs = token.split("=")
    parts = rhs.split(":")
    return alias, parts[0], parts[1], parts[2] if len(parts) > 2 else parts[1]


def _expr_agg(ctx: DataFrameContext, token: str) -> Any:
    alias, source, op, _ = _agg_parts(token)
    expr = ctx.col(source)
    if op == "nunique":
        expr = expr.n_unique()
    else:
        expr = getattr(expr, op)()
    return expr.alias(alias)


def _partition_extreme(part: Any, op: str) -> Any:
    import pandas as pd

    return pd.Series([getattr(part.dropna(), op)() if part.notna().any() else None], dtype=object)


def _pandas_value(hits: Any, source: str, op: str) -> Any:
    if op == "len":
        value = len(hits)
    elif op in ("min", "max") and is_lazy_frame(hits) and str(hits[source].dtype).startswith("date"):
        import pandas as pd

        partials = hits[source].map_partitions(_partition_extreme, op, meta=pd.Series([], dtype=object)).compute()
        return getattr(partials.dropna(), op)() if partials.notna().any() else None
    else:
        value = getattr(hits[source], op)()
    return value.compute() if hasattr(value, "compute") else value


def _sort_parts(sort_spec: str) -> tuple[str | list[str], bool]:
    sort_cols, direction = sort_spec.split(":")
    cols = sort_cols.split(",")
    return cols[0] if len(cols) == 1 else cols, direction == "desc"


def _make_scalar_expression(filter_code: str, agg_spec: str) -> Any:
    def impl(ctx: DataFrameContext) -> Any:
        hits = _expr_filter(ctx, _hits(ctx), filter_code)
        return hits.select(*[_expr_agg(ctx, token) for token in agg_spec.split(";")])

    return impl


def _make_scalar_pandas(filter_code: str, agg_spec: str) -> Any:
    def impl(ctx: DataFrameContext) -> Any:
        import pandas as pd

        hits = _pandas_filter(_hits(ctx), filter_code)
        values = {}
        for token in agg_spec.split(";"):
            alias, source, _, pandas_op = _agg_parts(token)
            values[alias] = [_pandas_value(hits, source, pandas_op)]
        return pd.DataFrame(values)

    return impl


def _make_group_expression(
    filter_code: str,
    derive_code: str,
    group_spec: str,
    agg_spec: str,
    sort_spec: str,
    limit_spec: str,
    offset_spec: str,
) -> Any:
    groups, limit, offset = group_spec.split(","), int(limit_spec or 0), int(offset_spec or 0)

    def impl(ctx: DataFrameContext) -> Any:
        result = _expr_filter(ctx, _hits(ctx), filter_code)
        result = _expr_derive(ctx, result, derive_code)
        result = result.group_by(*groups).agg(*[_expr_agg(ctx, token) for token in agg_spec.split(";")])
        if sort_spec:
            sort_cols, descending = _sort_parts(sort_spec)
            result = result.sort(sort_cols, descending=descending)
        if offset:
            return result.slice(offset, limit)
        return result.limit(limit) if limit else result

    return impl


def _make_group_pandas(
    filter_code: str,
    derive_code: str,
    group_spec: str,
    agg_spec: str,
    sort_spec: str,
    limit_spec: str,
    offset_spec: str,
) -> Any:
    groups, limit, offset = group_spec.split(","), int(limit_spec or 0), int(offset_spec or 0)
    agg_kwargs = {
        alias: (source, "nunique" if pandas_op == "nunique" else pandas_op)
        for alias, source, _, pandas_op in (_agg_parts(token) for token in agg_spec.split(";"))
    }

    def impl(ctx: DataFrameContext) -> Any:
        result = _pandas_derive(_pandas_filter(_hits(ctx), filter_code), derive_code)
        result = result.groupby(groups, as_index=False).agg(**agg_kwargs)
        if sort_spec:
            sort_cols, descending = _sort_parts(sort_spec)
            result = result.sort_values(sort_cols, ascending=not descending)
        if offset:
            if is_lazy_frame(result):
                return getattr(result, "native", result).head(offset + limit, npartitions=-1).iloc[offset:]
            return result.iloc[offset : offset + limit]
        return result.head(limit) if limit else result

    return impl


def _make_select_expression(filter_code: str, sort_spec: str, limit_spec: str, select_spec: str) -> Any:
    def impl(ctx: DataFrameContext) -> Any:
        result = _expr_filter(ctx, _hits(ctx), filter_code)
        if sort_spec:
            sort_cols, descending = _sort_parts(sort_spec)
            result = result.sort(sort_cols, descending=descending)
        if limit_spec:
            result = result.limit(int(limit_spec))
        return result.select(*select_spec.split(",")) if select_spec else result

    return impl


def _make_select_pandas(filter_code: str, sort_spec: str, limit_spec: str, select_spec: str) -> Any:
    def impl(ctx: DataFrameContext) -> Any:
        result = _pandas_filter(_hits(ctx), filter_code)
        if sort_spec:
            sort_cols, descending = _sort_parts(sort_spec)
            result = result.sort_values(sort_cols, ascending=not descending)
        if limit_spec:
            result = result.head(int(limit_spec))
        return result[select_spec.split(",")] if select_spec else result

    return impl


def q28_expression_impl(ctx: DataFrameContext) -> Any:
    col, lit = ctx.col, ctx.lit
    return (
        _hits(ctx)
        .filter(col("URL") != lit(""))
        .with_columns(col("URL").str.len_chars().alias("url_len"))
        .group_by("CounterID")
        .agg(col("url_len").mean().alias("l"), col("CounterID").count().alias("c"))
        .filter(col("c") > lit(100000))
        .sort("l", descending=True)
        .limit(25)
    )


def q28_pandas_impl(ctx: DataFrameContext) -> Any:
    filtered = _hits(ctx)[_hits(ctx)["URL"] != ""].copy()
    filtered["url_len"] = filtered["URL"].str.len()
    grouped = filtered.groupby(["CounterID"], as_index=False).agg(l=("url_len", "mean"), c=("url_len", "count"))
    return grouped[grouped["c"] > 100000].sort_values("l", ascending=False).head(25)


def q29_expression_impl(ctx: DataFrameContext) -> Any:
    col, lit = ctx.col, ctx.lit
    return (
        _hits(ctx)
        .filter(col("Referer") != lit(""))
        .with_columns(
            col("Referer").str.replace(lit(r"^https?://(?:www\.)?([^/]+)/.*$"), lit("$1")).alias("k"),
            col("Referer").str.len_chars().alias("ref_len"),
        )
        .group_by("k")
        .agg(col("ref_len").mean().alias("l"), col("k").count().alias("c"), col("Referer").min().alias("min_referer"))
        .filter(col("c") > lit(100000))
        .sort("l", descending=True)
        .limit(25)
    )


def q29_pandas_impl(ctx: DataFrameContext) -> Any:
    filtered = _hits(ctx)[_hits(ctx)["Referer"] != ""].copy()
    filtered["k"] = filtered["Referer"].str.replace(r"^https?://(?:www\.)?([^/]+)/.*$", r"\1", regex=True)
    filtered["ref_len"] = filtered["Referer"].str.len()
    grouped = filtered.groupby(["k"], as_index=False).agg(
        l=("ref_len", "mean"), c=("ref_len", "count"), min_referer=("Referer", "min")
    )
    return grouped[grouped["c"] > 100000].sort_values("l", ascending=False).head(25)


def q30_expression_impl(ctx: DataFrameContext) -> Any:
    col, lit = ctx.col, ctx.lit
    return _hits(ctx).select(*[(col("ResolutionWidth") + lit(i)).sum().alias(f"sum_{i}") for i in range(90)])


def q30_pandas_impl(ctx: DataFrameContext) -> Any:
    import pandas as pd

    base = _hits(ctx)["ResolutionWidth"]
    return pd.DataFrame({f"sum_{i}": [(base + i).sum()] for i in range(90)})


_SCALAR_ROWS = """1||count=WatchID:count:len
2|adv_ne_0|count=WatchID:count:len
3||sum_adv=AdvEngineID:sum;count=AdvEngineID:count:len;avg_resolution=ResolutionWidth:mean
4||avg_user_id=UserID:mean
5||uniq_users=UserID:nunique
6||uniq_search=SearchPhrase:nunique
7||min_date=EventDate:min;max_date=EventDate:max
21|url_google|count=WatchID:count:len
"""

_GROUP_ROWS = """8|adv_ne_0||AdvEngineID|c=AdvEngineID:count|c:desc||
9|||RegionID|u=UserID:nunique|u:desc|10|
10|||RegionID|sum_adv=AdvEngineID:sum;c=RegionID:count;avg_res=ResolutionWidth:mean;uniq_users=UserID:nunique|c:desc|10|
11|phone_model_nonempty||MobilePhoneModel|u=UserID:nunique|u:desc|10|
12|phone_model_nonempty||MobilePhone,MobilePhoneModel|u=UserID:nunique|u:desc|10|
13|search_nonempty||SearchPhrase|c=SearchPhrase:count|c:desc|10|
14|search_nonempty||SearchPhrase|u=UserID:nunique|u:desc|10|
15|search_nonempty||SearchEngineID,SearchPhrase|c=SearchPhrase:count|c:desc|10|
16|||UserID|c=UserID:count|c:desc|10|
17|||UserID,SearchPhrase|c=UserID:count|c:desc|10|
18|||UserID,SearchPhrase|c=UserID:count||10|
19||minute|UserID,m,SearchPhrase|c=UserID:count|c:desc|10|
22|google_search||SearchPhrase|min_url=URL:min;c=SearchPhrase:count|c:desc|10|
23|google_title||SearchPhrase|min_url=URL:min;min_title=Title:min;c=SearchPhrase:count;uniq_users=UserID:nunique|c:desc|10|
31|search_nonempty||SearchEngineID,ClientIP|c=ClientIP:count;sum_refresh=IsRefresh:sum;avg_res=ResolutionWidth:mean|c:desc|10|
32|search_nonempty||WatchID,ClientIP|c=ClientIP:count;sum_refresh=IsRefresh:sum;avg_res=ResolutionWidth:mean|c:desc|10|
33|||WatchID,ClientIP|c=ClientIP:count;sum_refresh=IsRefresh:sum;avg_res=ResolutionWidth:mean|c:desc|10|
34|||URL|c=URL:count|c:desc|10|
35||one|one,URL|c=URL:count|c:desc|10|
36||ip_offsets|ClientIP,ip_m1,ip_m2,ip_m3|c=ClientIP:count|c:desc|10|
37|july_url_pageviews||URL|PageViews=URL:count|PageViews:desc|10|
38|july_title_pageviews||Title|PageViews=Title:count|PageViews:desc|10|
39|july_link_views||URL|PageViews=URL:count|PageViews:desc|10|1000
40|july_source|source|TraficSourceID,SearchEngineID,AdvEngineID,Src,URL|PageViews=URL:count|PageViews:desc|10|1000
41|july_referer_hash||URLHash,EventDate|PageViews=URLHash:count|PageViews:desc|10|100
42|july_url_hash||WindowClientWidth,WindowClientHeight|PageViews=WindowClientWidth:count|PageViews:desc|10|10000
43|minute_range|trunc_minute|M|PageViews=M:count|M:asc||
"""

_SELECT_ROWS = """20|user_lookup|||
24|url_google|EventTime:asc|10|
25|search_nonempty|EventTime:asc|10|SearchPhrase
26|search_nonempty|SearchPhrase:asc|10|SearchPhrase
27|search_nonempty|EventTime,SearchPhrase:asc|10|SearchPhrase
"""


def _install_generated_impls() -> None:
    for row in reader(_SCALAR_ROWS.splitlines(), delimiter="|"):
        q_num, filter_code, agg_spec = row
        _install_impl(q_num, _make_scalar_expression(filter_code, agg_spec), _make_scalar_pandas(filter_code, agg_spec))
    for row in reader(_GROUP_ROWS.splitlines(), delimiter="|"):
        q_num, filter_code, derive_code, group_spec, agg_spec, sort_spec, limit_spec, offset_spec = row
        _install_impl(
            q_num,
            _make_group_expression(filter_code, derive_code, group_spec, agg_spec, sort_spec, limit_spec, offset_spec),
            _make_group_pandas(filter_code, derive_code, group_spec, agg_spec, sort_spec, limit_spec, offset_spec),
        )
    for row in reader(_SELECT_ROWS.splitlines(), delimiter="|"):
        q_num, filter_code, sort_spec, limit_spec, select_spec = row
        _install_impl(
            q_num,
            _make_select_expression(filter_code, sort_spec, limit_spec, select_spec),
            _make_select_pandas(filter_code, sort_spec, limit_spec, select_spec),
        )


def _install_impl(q_num: str, expression_impl: Any, pandas_impl: Any) -> None:
    for family, impl in (("expression", expression_impl), ("pandas", pandas_impl)):
        impl.__name__ = f"q{q_num}_{family}_impl"
        globals()[impl.__name__] = impl


_install_generated_impls()

_CATEGORY_CODES = {
    "AG": QueryCategory.AGGREGATE,
    "AN": QueryCategory.ANALYTICAL,
    "FI": QueryCategory.FILTER,
    "GB": QueryCategory.GROUP_BY,
    "PR": QueryCategory.PROJECTION,
    "SC": QueryCategory.SCAN,
    "SO": QueryCategory.SORT,
}

_QUERY_METADATA = """Q1|Full Scan Count|COUNT(*) full table scan - baseline performance test|SC,AG
Q2|Filtered Count|COUNT(*) with single equality filter on AdvEngineID|FI,AG
Q3|Multi-Aggregate Scan|SUM, COUNT, AVG on full table scan - multiple aggregate functions|SC,AG
Q4|Average UserID|AVG on BIGINT column - numeric precision test|SC,AG
Q5|Unique Users|COUNT(DISTINCT UserID) - cardinality estimation|SC,AG
Q6|Unique Search Phrases|COUNT(DISTINCT SearchPhrase) - string cardinality|SC,AG
Q7|Date Range|MIN/MAX on EventDate - date column statistics|SC,AG
Q8|AdvEngine Distribution|GROUP BY AdvEngineID with COUNT, filtered and sorted|FI,GB,SO
Q9|Users by Region|COUNT(DISTINCT UserID) by RegionID, top 10|GB,SO,AG
Q10|Region Multi-Aggregate|Multi-aggregate by RegionID - SUM, COUNT, AVG, COUNT(DISTINCT)|GB,SO,AG
Q11|Mobile Phone Models|Unique users by phone model, filtered on non-empty|FI,GB,SO
Q12|Phone + Model Unique Users|Unique users by phone and model, multi-column grouping|FI,GB,SO
Q13|Top Search Phrases|Most frequent non-empty search phrases|FI,GB,SO
Q14|Search Phrase Unique Users|Top search phrases by unique user count|FI,GB,SO
Q15|Search Engine + Phrase|Search engine and phrase combination counts|FI,GB,SO
Q16|Top Users|Most active users by event count|GB,SO
Q17|User Search Activity|Top user + search phrase combinations by count|GB,SO
Q18|User Search Unordered|User + search phrase group without ordering - nondeterministic|GB
Q19|User Minute Activity|User + minute + search phrase grouping with datetime extraction|GB,SO,AN
Q20|User Point Lookup|Single user lookup by exact UserID value|FI,SC
Q21|URL Google Count|Count rows where URL contains 'google' - string containment test|FI,AG
Q22|Google URL Search Phrases|Top search phrases for google URLs with MIN(URL)|FI,GB,SO
Q23|Google Title Analysis|Search phrases for Google titles excluding .google. URLs - case-sensitive LIKE|FI,GB,SO,AN
Q24|Google URL Full Rows|All columns for google URLs sorted by EventTime - wide output|FI,SO,PR
Q25|Search Phrase by Time|Non-empty search phrases sorted by EventTime|FI,SO,PR
Q26|Search Phrase Lexicographic|Non-empty search phrases sorted lexicographically|FI,SO,PR
Q27|Search Phrase Compound Sort|Non-empty search phrases with compound sort (EventTime, SearchPhrase)|FI,SO,PR
Q28|URL Length Analysis|Average URL length by counter with HAVING > 100000|GB,AG,AN
Q29|Domain Extraction|Regex domain extraction from Referer with HAVING - complex string operation|GB,AG,AN
Q30|Wide Aggregation|90-column SUM(ResolutionWidth + N) - wide aggregation test|SC,AG
Q31|Search Engine Client Analysis|Multi-agg by SearchEngineID + ClientIP, filtered on non-empty search|FI,GB,SO
Q32|Watch Client Analysis (Filtered)|Multi-agg by WatchID + ClientIP, filtered - high cardinality|FI,GB,SO
Q33|Watch Client Analysis (Unfiltered)|Multi-agg by WatchID + ClientIP, unfiltered - high cardinality full scan|GB,SO
Q34|Top URLs|Top URLs by count - high cardinality text GROUP BY|GB,SO
Q35|Literal Column URL Group|GROUP BY literal constant column plus URL - tests derived column grouping|GB,SO,AN
Q36|Derived Column Group|GROUP BY ClientIP and derived arithmetic columns - expression grouping|GB,SO,AN
Q37|July URL Page Views|Top URLs for CounterID=62, July 2013 with multiple filters|FI,GB,SO
Q38|July Title Page Views|Top titles for CounterID=62, July 2013 with multiple filters|FI,GB,SO
Q39|URL Views with Offset|URL page views with OFFSET 1000 - pagination test|FI,GB,SO
Q40|Traffic Source CASE WHEN|Traffic source analysis with CASE WHEN conditional and OFFSET|FI,GB,SO,AN
Q41|URL Hash IN Clause|URLHash + EventDate with IN clause filter and OFFSET 100|FI,GB,SO
Q42|Window Dimensions|Window dimensions for specific URL hash with large OFFSET 10000|FI,GB,SO
Q43|Minute Granularity|Page views by minute with DATE_TRUNC - temporal aggregation|FI,GB,SO,AN
"""


def _impl_for(query_id: str, family: str) -> Any:
    return globals()[f"q{query_id[1:]}_{family}_impl"]


def _register_all_queries() -> None:
    for query_id, query_name, description, category_codes in reader(_QUERY_METADATA.splitlines(), delimiter="|"):
        register_query(
            DataFrameQuery(
                query_id=query_id,
                query_name=query_name,
                description=description,
                categories=[_CATEGORY_CODES[code] for code in category_codes.split(",")],
                expression_impl=_impl_for(query_id, "expression"),
                pandas_impl=_impl_for(query_id, "pandas"),
            )
        )


_register_all_queries()
