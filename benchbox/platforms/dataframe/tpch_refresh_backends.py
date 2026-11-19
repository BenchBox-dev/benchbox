from __future__ import annotations

from pathlib import Path
from typing import Any


class PolarsRefreshBackend:
    def __init__(self, adapter: Any) -> None:
        self._adapter = adapter

    def parse_delimited(self, path: Path, like: Any) -> Any:
        import polars as pl

        schema = like.collect_schema()
        raw = pl.read_csv(path, separator="|", has_header=False, infer_schema=False, quote_char=None)
        if raw.width < len(schema):
            raise ValueError(f"{path.name} has {raw.width} fields; expected {len(schema)}")
        columns = []
        for position, (name, dtype) in enumerate(schema.items()):
            source = pl.col(raw.columns[position])
            if dtype == pl.Date:
                columns.append(source.str.to_date("%Y-%m-%d").alias(name))
            else:
                columns.append(source.cast(dtype).alias(name))
        return raw.select(columns).lazy()

    def concat(self, current: Any, delta: Any) -> Any:
        import polars as pl

        return pl.concat([current, delta], how="vertical")

    def row_count(self, frame: Any) -> int:
        import polars as pl

        return int(self._adapter.materialize(frame.select(pl.len())).item())

    def count_keys(self, frame: Any, column: str, keys: list[int]) -> tuple[int, int]:
        import polars as pl

        matched = frame.filter(pl.col(column).is_in(keys)).select(
            pl.len().alias("rows"), pl.col(column).n_unique().alias("distinct")
        )
        row = self._adapter.materialize(matched).row(0)
        return int(row[0]), int(row[1])

    def exclude_keys(self, frame: Any, column: str, keys: list[int]) -> Any:
        import polars as pl

        return frame.filter(~pl.col(column).is_in(keys))


class DataFusionRefreshBackend:
    def __init__(self, adapter: Any) -> None:
        self._adapter = adapter

    def parse_delimited(self, path: Path, like: Any) -> Any:
        import pyarrow as pa
        from pyarrow import csv

        from benchbox.platforms.dataframe.datafusion_identifiers import datafusion_column

        schema = like.schema()
        names = [field.name for field in schema]
        with path.open(encoding="utf-8") as source:
            first_line = source.readline().rstrip("\r\n")
        trailing = [] if not first_line else ["__trailing_delimiter"] if first_line.endswith("|") else []
        read_options = csv.ReadOptions(column_names=[*names, *trailing])
        parse_options = csv.ParseOptions(delimiter="|", quote_char=False)
        convert_options = csv.ConvertOptions(
            column_types={name: pa.string() for name in names},
            include_columns=names,
        )
        raw = csv.read_csv(
            path, read_options=read_options, parse_options=parse_options, convert_options=convert_options
        )
        target = pa.schema(schema)
        portable = pa.schema([pa.field(field.name, _portable_type(field.type)) for field in target])
        delta = self._adapter.session_ctx.from_arrow(raw.cast(portable))
        if target == portable:
            return delta
        return delta.select(
            *[
                datafusion_column(field.name)
                if field.type == source.type
                else datafusion_column(field.name).cast(field.type).alias(field.name)
                for field, source in zip(target, portable, strict=True)
            ]
        )

    def concat(self, current: Any, delta: Any) -> Any:
        return current.union(delta)

    def row_count(self, frame: Any) -> int:
        return int(frame.count())

    def count_keys(self, frame: Any, column: str, keys: list[int]) -> tuple[int, int]:
        from datafusion import functions as f, lit

        from benchbox.platforms.dataframe.datafusion_identifiers import datafusion_column

        key = datafusion_column(column)
        matched = frame.filter(f.in_list(key, [lit(item) for item in keys], negated=False)).aggregate(
            [], [f.count(lit(1)).alias("matched_rows"), f.count(key, distinct=True).alias("matched_keys")]
        )
        row = self._adapter.collect(matched).to_pylist()[0]
        return int(row["matched_rows"]), int(row["matched_keys"])

    def exclude_keys(self, frame: Any, column: str, keys: list[int]) -> Any:
        from datafusion import functions as f, lit

        from benchbox.platforms.dataframe.datafusion_identifiers import datafusion_column

        return frame.filter(f.in_list(datafusion_column(column), [lit(item) for item in keys], negated=True))


def _portable_type(arrow_type: Any) -> Any:
    import pyarrow as pa

    if arrow_type in (pa.string_view(), pa.large_string()):
        return pa.string()
    if arrow_type in (pa.binary_view(), pa.large_binary()):
        return pa.binary()
    return arrow_type
