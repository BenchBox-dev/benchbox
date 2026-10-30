from __future__ import annotations

from csv import reader
from typing import Any

from benchbox.core.dataframe.context import DataFrameContext
from benchbox.core.dataframe.pandas_ops import where as pandas_where
from benchbox.core.dataframe.query import DataFrameQuery, QueryCategory

from .parameters import get_parameters
from .registry import register_query


def _items(value: str) -> list[str]:
    return [item for item in value.split(",") if item]


def _params(query_id: str) -> Any:
    return get_parameters(query_id)


def _expr_window(ctx: DataFrameContext, query_id: str, table: str, *, host: bool = False) -> tuple[Any, Any, Any]:
    params = _params(query_id)
    col, lit = ctx.col, ctx.lit
    cond = (col("time") >= lit(params.get("start_time"))) & (col("time") < lit(params.get("end_time")))
    if host:
        cond = (col("hostname") == lit(params.get("hostname", "host_0"))) & cond
    return ctx.get_table(table).filter(cond), col, lit


def _pandas_window(ctx: DataFrameContext, query_id: str, table: str, *, host: bool = False, copy: bool = False) -> Any:
    params = _params(query_id)
    frame = ctx.get_table(table)
    cond = (frame["time"] >= params.get("start_time")) & (frame["time"] < params.get("end_time"))
    if host:
        cond &= frame["hostname"] == params.get("hostname", "host_0")
    filtered = frame[cond]
    return filtered.copy() if copy else filtered


def _expr_derive(ctx: DataFrameContext, frame: Any, names: str) -> Any:
    col, lit = ctx.col, ctx.lit
    for name in _items(names):
        if name == "minute":
            frame = frame.with_columns(col("time").dt.truncate("1m").alias("minute"))
        elif name == "second":
            frame = frame.with_columns(col("time").dt.truncate("1s").alias("second"))
        elif name == "disk_latency":
            frame = frame.with_columns(
                ctx.when(col("reads_completed") > lit(0))
                .then(col("read_time_ms").cast_float() / col("reads_completed").cast_float())
                .otherwise(lit(0.0))
                .alias("read_latency_ms"),
                ctx.when(col("writes_completed") > lit(0))
                .then(col("write_time_ms").cast_float() / col("writes_completed").cast_float())
                .otherwise(lit(0.0))
                .alias("write_latency_ms"),
            )
    return frame


def _pandas_derive(frame: Any, names: str) -> Any:
    for name in _items(names):
        if name == "minute":
            frame["minute"] = frame["time"].dt.floor("min")
        elif name == "second":
            frame["second"] = frame["time"].dt.floor("s")
        elif name == "disk_latency":
            frame["read_latency_ms"] = pandas_where(
                frame["reads_completed"] > 0, frame["read_time_ms"] / frame["reads_completed"], 0.0
            )
            frame["write_latency_ms"] = pandas_where(
                frame["writes_completed"] > 0, frame["write_time_ms"] / frame["writes_completed"], 0.0
            )
        elif name == "net_errors":
            frame["err_in+err_out"] = frame["err_in"] + frame["err_out"]
            frame["drop_in+drop_out"] = frame["drop_in"] + frame["drop_out"]
    return frame


def _expr_aggs(col: Any, aggs: str) -> list[Any]:
    out = []
    for name, source, func in (item.split(":") for item in aggs.split(";") if item):
        expr = (
            (col(source.split("+")[0]) + col(source.split("+")[1])).sum()
            if "+" in source
            else getattr(col(source), func)()
        )
        out.append(expr.alias(name))
    return out


def _parse_aggs(aggs: str) -> dict[str, tuple[str, str]]:
    return {name: (source, func) for name, source, func in (item.split(":") for item in aggs.split(";") if item)}


def _sort_expr(frame: Any, sort: str, desc: str) -> Any:
    if not sort:
        return frame
    columns = _items(sort)
    return frame.sort(columns if len(columns) > 1 else columns[0], descending=desc == "True")


def _sort_pandas(frame: Any, sort: str, desc: str) -> Any:
    if not sort:
        return frame
    columns = _items(sort)
    return frame.sort_values(columns if len(columns) > 1 else columns[0], ascending=desc != "True")


def _having_expr(col: Any, lit: Any, having: str) -> Any:
    if having == "high_cpu_count>10":
        return col("high_cpu_count") > lit(10)
    if having == "min_avail_pct<10":
        return col("min_avail_pct") < lit(10)
    if having == "errors>0":
        return (col("total_errors") + col("total_drops")) > lit(0)
    raise ValueError(f"Unsupported TSBS having clause: {having}")


def _having_pandas(frame: Any, having: str) -> Any:
    if having == "high_cpu_count>10":
        return frame[frame["high_cpu_count"] > 10]
    if having == "min_avail_pct<10":
        return frame[frame["min_avail_pct"] < 10]
    if having == "errors>0":
        return frame[(frame["total_errors"] + frame["total_drops"]) > 0]
    raise ValueError(f"Unsupported TSBS having clause: {having}")


def _make_query(row: list[str]) -> tuple[Any, Any]:
    qid, table, host, select, predicate, derives, group, aggs, having, sort, desc = row

    def expression_impl(ctx: DataFrameContext) -> Any:
        frame, col, lit = _expr_window(ctx, qid, table, host=host == "host")
        if predicate in {"usage_user>90", "usage_user>90_distinct"}:
            frame = frame.filter(col("usage_user") > lit(90))
        if select:
            frame = frame.select(*_items(select))
            return _sort_expr(frame.unique() if predicate.endswith("distinct") else frame, sort, desc)
        frame = _expr_derive(ctx, frame, derives).group_by(*_items(group)).agg(*_expr_aggs(col, aggs))
        if having:
            frame = frame.filter(_having_expr(col, lit, having))
        return _sort_expr(frame, sort, desc)

    def pandas_impl(ctx: DataFrameContext) -> Any:
        frame = _pandas_window(ctx, qid, table, host=host == "host", copy=bool(derives))
        if predicate in {"usage_user>90", "usage_user>90_distinct"}:
            frame = frame[frame["usage_user"] > 90]
        if select:
            frame = frame[_items(select)]
            frame = frame.drop_duplicates() if predicate.endswith("distinct") else frame
            return _sort_pandas(frame, sort, desc)
        frame = _pandas_derive(frame, derives).groupby(_items(group), as_index=False).agg(**_parse_aggs(aggs))
        if having:
            frame = _having_pandas(frame, having)
        return _sort_pandas(frame, sort, desc)

    expression_impl.__name__ = f"q{qid[1:]}_expression_impl"
    pandas_impl.__name__ = f"q{qid[1:]}_pandas_impl"
    expression_impl.__qualname__ = expression_impl.__name__
    pandas_impl.__qualname__ = pandas_impl.__name__
    return expression_impl, pandas_impl


_QUERY_SPECS = """\
Q1|cpu|host|time,usage_user,usage_system,usage_idle||||||time|
Q2|cpu|host|time,usage_user,usage_system,usage_idle,usage_iowait||||||time|
Q3|cpu|||||hostname|max_user:usage_user:max;max_system:usage_system:max||max_user|True
Q4|cpu|||||hostname|max_user:usage_user:max;max_system:usage_system:max;max_iowait:usage_iowait:max||max_user|True
Q5|cpu||||minute|hostname,minute|avg_user:usage_user:mean;max_user:usage_user:max||hostname,minute|
Q6|cpu||||second|hostname,second|avg_user:usage_user:mean;avg_system:usage_system:mean||hostname,second|
Q7|cpu||hostname|usage_user>90_distinct||||||
Q8|cpu|||usage_user>90||hostname|high_cpu_count:hostname:count|high_cpu_count>10|high_cpu_count|True
Q9|mem|||||hostname|avg_used_pct:used_percent:mean;max_used_pct:used_percent:max;min_available:available:min||avg_used_pct|True
Q10|mem|||||hostname|min_avail_pct:available_percent:min|min_avail_pct<10|min_avail_pct|
Q11|disk|||||hostname,device|total_reads:reads_completed:sum;total_writes:writes_completed:sum||total_writes|True
Q12|disk||||disk_latency|hostname,device|avg_read_latency_ms:read_latency_ms:mean;avg_write_latency_ms:write_latency_ms:mean||avg_write_latency_ms|True
Q13|net|||||hostname,interface|total_bytes_sent:bytes_sent:sum;total_bytes_recv:bytes_recv:sum||total_bytes_sent|True
Q14|net||||net_errors|hostname,interface|total_errors:err_in+err_out:sum;total_drops:drop_in+drop_out:sum|errors>0|total_errors|True
"""

for _row in reader(_QUERY_SPECS.splitlines(), delimiter="|"):
    globals()[f"q{_row[0][1:]}_expression_impl"], globals()[f"q{_row[0][1:]}_pandas_impl"] = _make_query(_row)


def q15_expression_impl(ctx: DataFrameContext) -> Any:
    params = _params("Q15")
    col, lit = ctx.col, ctx.lit
    cpu = (
        ctx.get_table("cpu")
        .filter((col("time") >= lit(params.get("start_time"))) & (col("time") < lit(params.get("end_time"))))
        .with_columns(col("time").dt.truncate("1m").alias("minute"))
        .group_by("hostname", "minute")
        .agg((col("usage_user") + col("usage_system")).mean().alias("avg_cpu_total"))
    )
    mem = (
        ctx.get_table("mem")
        .filter((col("time") >= lit(params.get("start_time"))) & (col("time") < lit(params.get("end_time"))))
        .with_columns(col("time").dt.truncate("1m").alias("minute"))
        .group_by("hostname", "minute")
        .agg(col("used_percent").mean().alias("avg_mem_used"))
    )
    return (
        cpu.join(mem, on=["hostname", "minute"])
        .group_by("hostname")
        .agg(col("avg_cpu_total").mean().alias("avg_cpu_total"), col("avg_mem_used").mean().alias("avg_mem_used"))
        .sort("avg_cpu_total", descending=True)
    )


def q15_pandas_impl(ctx: DataFrameContext) -> Any:
    params = _params("Q15")
    cpu = ctx.get_table("cpu")
    mem = ctx.get_table("mem")
    cpu_f = cpu[(cpu["time"] >= params.get("start_time")) & (cpu["time"] < params.get("end_time"))].copy()
    cpu_f["minute"] = cpu_f["time"].dt.floor("min")
    cpu_f["cpu_total"] = cpu_f["usage_user"] + cpu_f["usage_system"]
    mem_f = mem[(mem["time"] >= params.get("start_time")) & (mem["time"] < params.get("end_time"))].copy()
    mem_f["minute"] = mem_f["time"].dt.floor("min")
    merged = (
        cpu_f.groupby(["hostname", "minute"], as_index=False)
        .agg(avg_cpu_total=("cpu_total", "mean"))
        .merge(
            mem_f.groupby(["hostname", "minute"], as_index=False).agg(avg_mem_used=("used_percent", "mean")),
            on=["hostname", "minute"],
        )
    )
    return (
        merged.groupby(["hostname"], as_index=False)
        .agg(avg_cpu_total=("avg_cpu_total", "mean"), avg_mem_used=("avg_mem_used", "mean"))
        .sort_values("avg_cpu_total", ascending=False)
    )


def q16_expression_impl(ctx: DataFrameContext) -> Any:
    params = _params("Q16")
    cpu, col, lit = ctx.get_table("cpu"), ctx.col, ctx.lit
    last_times = (
        cpu.filter(col("time") >= lit(params.get("start_time")))
        .group_by("hostname")
        .agg(col("time").max().alias("max_time"))
    )
    return (
        cpu.join(last_times, on="hostname")
        .filter(col("time") == col("max_time"))
        .select("hostname", "time", "usage_user", "usage_system", "usage_idle")
        .sort("hostname")
    )


def q16_pandas_impl(ctx: DataFrameContext) -> Any:
    params = _params("Q16")
    filtered = ctx.get_table("cpu")[ctx.get_table("cpu")["time"] >= params.get("start_time")]
    result = filtered.merge(
        filtered.groupby("hostname")["time"].max().reset_index().rename(columns={"time": "max_time"}), on="hostname"
    )
    return result[result["time"] == result["max_time"]][
        ["hostname", "time", "usage_user", "usage_system", "usage_idle"]
    ].sort_values("hostname")


def q17_expression_impl(ctx: DataFrameContext) -> Any:
    params = _params("Q17")
    frame, col, lit = _expr_window(ctx, "Q17", "cpu")
    return (
        frame.join(ctx.get_table("tags"), on="hostname")
        .filter(col("region") == lit(params.get("region", "us-east-1")))
        .group_by("region")
        .agg(col("usage_user").mean().alias("avg_cpu_user"), col("usage_system").mean().alias("avg_cpu_system"))
    )


def q17_pandas_impl(ctx: DataFrameContext) -> Any:
    params = _params("Q17")
    merged = _pandas_window(ctx, "Q17", "cpu").merge(ctx.get_table("tags"), on="hostname")
    return (
        merged[merged["region"] == params.get("region", "us-east-1")]
        .groupby(["region"], as_index=False)
        .agg(avg_cpu_user=("usage_user", "mean"), avg_cpu_system=("usage_system", "mean"))
    )


def q18_expression_impl(ctx: DataFrameContext) -> Any:
    frame, col, _ = _expr_window(ctx, "Q18", "cpu")
    return (
        frame.join(ctx.get_table("tags"), on="hostname")
        .group_by("service")
        .agg(
            col("hostname").n_unique().alias("host_count"),
            (col("usage_user") + col("usage_system")).mean().alias("avg_cpu_total"),
        )
        .sort("avg_cpu_total", descending=True)
    )


def q18_pandas_impl(ctx: DataFrameContext) -> Any:
    merged = _pandas_window(ctx, "Q18", "cpu").merge(ctx.get_table("tags"), on="hostname")
    merged["cpu_total"] = merged["usage_user"] + merged["usage_system"]
    return (
        merged.groupby(["service"], as_index=False)
        .agg(host_count=("hostname", "nunique"), avg_cpu_total=("cpu_total", "mean"))
        .sort_values("avg_cpu_total", ascending=False)
    )


_CATEGORY_CODES = {
    "AG": QueryCategory.AGGREGATE,
    "AN": QueryCategory.ANALYTICAL,
    "FI": QueryCategory.FILTER,
    "GB": QueryCategory.GROUP_BY,
    "JO": QueryCategory.JOIN,
    "PR": QueryCategory.PROJECTION,
    "SC": QueryCategory.SCAN,
    "SO": QueryCategory.SORT,
    "SQ": QueryCategory.SUBQUERY,
}

_QUERY_METADATA = """\
Q1|Single Host 12hr|CPU usage for a single host over 12 hours (time-series scan)|SC,FI,SO|q1
Q2|Single Host 1hr|CPU usage with iowait for a single host over 1 hour|SC,FI,SO|q2
Q3|CPU Max All 1hr|Max CPU user/system across all hosts in 1 hour window|FI,GB,AG|q3
Q4|CPU Max All 8hr|Max CPU user/system/iowait across all hosts in 8 hour window|FI,GB,AG|q4
Q5|Double GroupBy 1hr|CPU avg/max grouped by hostname and minute bucket using DATE_TRUNC|FI,GB,AG,AN|q5
Q6|Double GroupBy 5min|CPU avg grouped by hostname and second bucket (fine-grained)|FI,GB,AG,AN|q6
Q7|High CPU 1hr|DISTINCT hostnames with CPU usage > 90% in 1 hour|FI,PR|q7
Q8|High CPU 12hr Sustained|Hosts with sustained high CPU (>90%) using HAVING COUNT > 10|FI,GB,AG|q8
Q9|Memory by Host|Memory AVG/MAX used% and MIN available per host|FI,GB,AG|q9
Q10|Low Memory Hosts|Hosts with MIN available_percent < 10 using HAVING|FI,GB,AG|q10
Q11|Disk IOPS|Disk read/write IOPS per host and device|FI,GB,AG|q11
Q12|Disk Latency|Average disk latency with CASE WHEN safe division|FI,GB,AG,AN|q12
Q13|Network Throughput|Network bytes sent/received per host and interface|FI,GB,AG|q13
Q14|Network Errors|Network errors and drops with HAVING SUM > 0|FI,GB,AG|q14
Q15|Resource Utilization|Combined CPU+memory utilization via JOIN on hostname and minute|FI,JO,GB,AG|q15
Q16|Last Point per Host|Most recent CPU metrics per host using self-join subquery pattern|FI,JO,SQ|q16
Q17|Metrics by Region|CPU metrics filtered by region via tags JOIN|FI,JO,GB,AG|q17
Q18|Metrics by Service|CPU metrics grouped by service with host count via tags JOIN|FI,JO,GB,AG|q18
"""


def _impl_for(stem: str, family: str) -> Any:
    return globals()[f"{stem}_{family}_impl"]


for _query_id, _query_name, _description, _category_codes, _stem in reader(_QUERY_METADATA.splitlines(), delimiter="|"):
    register_query(
        DataFrameQuery(
            query_id=_query_id,
            query_name=_query_name,
            description=_description,
            categories=[_CATEGORY_CODES[code] for code in _category_codes.split(",")],
            expression_impl=_impl_for(_stem, "expression"),
            pandas_impl=_impl_for(_stem, "pandas"),
        )
    )
