from __future__ import annotations

import dataclasses
from hashlib import sha256
from pathlib import Path
from typing import Any

from benchbox.core.equivalence.builders.base import CrossSurfaceData

TPCDS_GATE_QUERY_IDS = tuple(str(number) for number in range(1, 100)) + ("14b", "23b", "24b", "39b")
_MULTIPART_QUERIES = frozenset({14, 23, 24, 39})


def validate_tpcds_gate_data(data: CrossSurfaceData) -> None:
    from benchbox.core.tpcds.dataframe_queries.production_binding import power_parameter_seed, query_number

    if len(data.query_ids) != len(TPCDS_GATE_QUERY_IDS) or set(data.query_ids) != set(TPCDS_GATE_QUERY_IDS):
        raise ValueError("TPC-DS requires all 99 base queries and the four b statements")
    bindings = getattr(data.dataframe_query, "bindings", {})
    if set(bindings) != set(range(1, 100)):
        raise ValueError("TPC-DS requires all 99 parameter bindings")
    metadata = data.query_parameters
    required = {"seed", "power_stream_id", "rngseed", "dsqgen_stream_id", "scale_factor", "dsqgen_sha256"}
    if not required.issubset(metadata):
        raise ValueError("TPC-DS requires a complete recorded draw identity")
    if metadata["scale_factor"] != data.benchmark.scale_factor:
        raise ValueError("TPC-DS draw scale disagrees with its generated data")
    if set(metadata.get("bindings", {})) != {str(number) for number in range(1, 100)}:
        raise ValueError("TPC-DS requires all 99 canonical binding records")
    if set(metadata.get("sql_sha256", {})) != set(TPCDS_GATE_QUERY_IDS):
        raise ValueError("TPC-DS requires all 103 SQL statement hashes")
    rngseed = power_parameter_seed(metadata["seed"], metadata["power_stream_id"])
    if metadata["rngseed"] != rngseed or metadata["dsqgen_stream_id"] != 0:
        raise ValueError("TPC-DS draw identity disagrees with the Power stream")
    for number, binding in bindings.items():
        if (
            binding.query_id != number
            or binding.scale_factor != metadata["scale_factor"]
            or binding.seed != rngseed
            or binding.stream_id != metadata["dsqgen_stream_id"]
            or binding.dsqgen_sha256 != metadata["dsqgen_sha256"]
            or dataclasses.asdict(binding) != metadata["bindings"][str(number)]
        ):
            raise ValueError(f"TPC-DS Q{number} binding disagrees with its recorded draw")
    for query_id in data.query_ids:
        sql = data.reference_sql(query_id)
        if not sql.strip() or sha256(sql.encode("utf-8")).hexdigest() != metadata["sql_sha256"][query_id]:
            raise ValueError(f"TPC-DS {query_id} SQL disagrees with its recorded statement")
        query = data.dataframe_query(query_id)
        if query.query_id != f"Q{query_id}":
            raise ValueError(f"TPC-DS {query_id} is missing its intended DataFrame statement")
        number = query_number(query_id)
        if number is None:
            raise ValueError(f"TPC-DS {query_id} is not a valid statement ID")
        binding = bindings[number]
        for family in ("expression", "pandas"):
            implementation = query.get_impl_for_family(family)
            if implementation is None:
                raise ValueError(f"TPC-DS {query_id} is missing its {family} implementation")
            if getattr(implementation, "parameter_binding", None) is not binding:
                raise ValueError(f"TPC-DS {query_id} {family} is missing its captured binding")


def build_tpcds_duckdb(
    scale_factor: float, output_dir: Path, *, seed: int | None = None, stream_id: int = 0
) -> CrossSurfaceData:
    from benchbox._vendor import sqlglot
    from benchbox._vendor.sqlglot import exp
    from benchbox.core.equivalence.builders.base import _load_duckdb_cell
    from benchbox.core.tpcds.dataframe_queries import TPCDS_DATAFRAME_QUERIES
    from benchbox.core.tpcds.dataframe_queries.parameter_adapters import ADAPTERS
    from benchbox.core.tpcds.dataframe_queries.production_binding import (
        bind_power_stream,
        bind_queries,
        power_parameter_seed,
        query_number,
    )
    from benchbox.core.tpcds.schema.registry import TABLES
    from benchbox.tpcds import TPCDS

    if stream_id < 0 or (seed is not None and seed < 0):
        raise ValueError("TPC-DS seed and Power stream must be nonnegative")
    if set(ADAPTERS) != set(range(1, 100)):
        raise ValueError("TPC-DS requires parameter adapters for every query from 1 through 99")
    if set(TPCDS_DATAFRAME_QUERIES.get_query_ids()) != {f"Q{query_id}" for query_id in TPCDS_GATE_QUERY_IDS}:
        raise ValueError("TPC-DS DataFrame registry must contain exactly the 103 intended statements")

    benchmark = TPCDS(scale_factor=scale_factor, output_dir=Path(output_dir))
    dsqgen = benchmark._impl.query_manager.dsqgen
    bindings = bind_power_stream(
        range(1, 100), scale_factor=scale_factor, seed=seed, stream_id=stream_id, dsqgen=dsqgen
    )
    if set(bindings) != set(range(1, 100)):
        raise ValueError("TPC-DS requires all 99 parameter bindings")
    queries = dict(
        zip(
            TPCDS_GATE_QUERY_IDS,
            bind_queries(
                (TPCDS_DATAFRAME_QUERIES.get_or_raise(f"Q{query_id}") for query_id in TPCDS_GATE_QUERY_IDS), bindings
            ),
            strict=True,
        )
    )
    sql_queries = {}
    for query_id in TPCDS_GATE_QUERY_IDS:
        number = query_number(query_id)
        if number is None:
            raise ValueError(f"TPC-DS {query_id} is not a valid statement ID")
        binding = bindings[number]
        variant = query_id[-1] if query_id.endswith("b") else "a" if number in _MULTIPART_QUERIES else None
        template_id = f"{number}{variant or ''}"
        raw = dsqgen.generate_with_parameters(
            template_id, dict(binding.logged), scale_factor=scale_factor, seed=binding.seed
        )
        translated = benchmark._impl.translate_query_text(raw, "netezza", "duckdb")
        sql = benchmark._impl._apply_target_dialect_overrides(number, translated, "duckdb", variant=variant)
        statements = sqlglot.parse(sql, read="duckdb")
        if len(statements) != 1 or not isinstance(statements[0], exp.Query):
            raise ValueError(f"TPC-DS {query_id} must render exactly one SQL query")
        sql_queries[query_id] = sql

    def dataframe_query(query_id: str) -> Any:
        return queries[query_id]

    dataframe_query.bindings = bindings
    data = CrossSurfaceData(
        connection=None,
        query_ids=TPCDS_GATE_QUERY_IDS,
        reference_sql=sql_queries.__getitem__,
        dataframe_query=dataframe_query,
        benchmark=benchmark,
        data_dir=Path(output_dir),
        query_parameters={
            "seed": seed,
            "power_stream_id": stream_id,
            "rngseed": power_parameter_seed(seed, stream_id),
            "dsqgen_stream_id": 0,
            "scale_factor": scale_factor,
            "dsqgen_sha256": sha256(dsqgen.dsqgen_path.read_bytes()).hexdigest(),
            "bindings": {str(number): dataclasses.asdict(binding) for number, binding in bindings.items()},
            "sql_sha256": {query_id: sha256(sql.encode("utf-8")).hexdigest() for query_id, sql in sql_queries.items()},
        },
    )
    validate_tpcds_gate_data(data)
    benchmark.generate_data()
    connection = _load_duckdb_cell(benchmark, Path(output_dir), [table.name for table in TABLES], label="TPC-DS")
    return dataclasses.replace(data, connection=connection)
