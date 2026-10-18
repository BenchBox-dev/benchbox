from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import math
import os
import re
import signal
import subprocess
import sys
import time
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from benchbox._vendor import sqlglot
from benchbox.core.expected_results.loader import TpcdsAnswerBlock, parse_tpcds_answer_values
from benchbox.core.tpcds.qualification.classification import (
    CHAR_PADDING,
    DISPLAY_PRECISION,
    FLOAT_DETAIL,
    HALF_BOUNDARY_ROUNDING,
    KNOWN,
    MALFORMED_OFFICIAL_ANSWER,
    NULL_ORDER_VARIANT,
    RETURNS_DIFFERENCE,
    TIED_ORDER,
    KnownDifference,
    gate,
    printed_classification,
    rows_digest,
)
from benchbox.utils.clock import elapsed_seconds, mono_time

_INPUTS = Path(__file__).with_name("inputs.json")
_PARAMETERS = Path(__file__).parents[1] / "dataframe_queries" / "qualification_values.json"
_MULTIPART = {14, 23, 24, 39}
STATEMENTS = tuple(str(n) + suffix for n in range(1, 100) for suffix in (("a", "b") if n in _MULTIPART else ("",)))
_ENGINES = {
    "polars": ("benchbox.platforms.dataframe.polars_df", "PolarsDataFrameAdapter", "expression"),
    "pandas": ("benchbox.platforms.dataframe.pandas_df", "PandasDataFrameAdapter", "pandas"),
    "datafusion": ("benchbox.platforms.dataframe.datafusion_df", "DataFusionDataFrameAdapter", "expression"),
}


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def parameter_hash(values: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def qualification_parameters(path: Path = _PARAMETERS, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    inputs = manifest if manifest is not None else json.loads(_INPUTS.read_text(encoding="utf-8"))
    if sha256(path) != inputs["parameters_sha256"]:
        raise ValueError("Qualification parameter file differs from the pinned specification values")
    document = json.loads(path.read_text(encoding="utf-8"))
    if set(document["values"]) != {str(n) for n in range(1, 100)}:
        raise ValueError("Qualification parameter inventory must contain exactly queries 1 through 99")
    if document["not_in_template"] != {"71": ["MANAGER.01"]}:
        raise ValueError("Unknown qualification template exception")
    receipt = inputs["specification_audit"]
    if receipt["queries"] != 99 or receipt["canonical_values_sha256"] != parameter_hash(document["values"]):
        raise ValueError("Qualification values differ from the complete Appendix B audit")
    if set(receipt["query_pdf_pages"]) != set(document["values"]):
        raise ValueError("Incomplete specification audit provenance")
    return document


def mapped_blocks(
    query: int, blocks: tuple[TpcdsAnswerBlock, ...], inputs: dict[str, Any]
) -> tuple[TpcdsAnswerBlock, ...]:
    if str(query) in inputs["block_rows"]:
        if [len(block.rows) for block in blocks] != inputs["block_rows"][str(query)]:
            raise ValueError(f"Q{query}: unknown official block shape")
        if len({block.columns for block in blocks}) != 1:
            raise ValueError(f"Q{query}: inconsistent columns across official blocks")
    if query == 98:
        blocks = (TpcdsAnswerBlock(blocks[0].columns, tuple(row for block in blocks for row in block.rows)),)
    expected = 2 if query in _MULTIPART else 1
    if len(blocks) != expected:
        raise ValueError(f"Q{query}: expected {expected} statement blocks, found {len(blocks)}")
    return blocks


def official_inventory(directory: Path, inputs: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    names = {path.name for path in directory.glob("*.ans")}
    if names != set(inputs["answers"]):
        raise ValueError(
            f"Official answer inventory differs: missing={sorted(set(inputs['answers']) - names)}, extra={sorted(names - set(inputs['answers']))}"
        )
    result: dict[str, list[dict[str, Any]]] = {query: [] for query in STATEMENTS}
    for number in range(1, 100):
        available = [
            name for name in (f"{number}.ans", f"{number}_NULLS_FIRST.ans", f"{number}_NULLS_LAST.ans") if name in names
        ]
        if not available:
            raise ValueError(f"Q{number}: missing official answer")
        for name in available:
            pin = inputs["answers"][name]
            if sha256(directory / name) != pin["sha256"]:
                raise ValueError(f"Official answer hash differs: {name}")
            raw = parse_tpcds_answer_values(directory / name)
            blocks = mapped_blocks(number, raw, inputs)
            for index, block in enumerate(blocks):
                query = str(number) + ("ab"[index] if number in _MULTIPART else "")
                result[query].append(
                    {
                        "file": name,
                        "sha256": pin["sha256"],
                        "null_tokens": pin["null_tokens"],
                        "raw_blocks": len(raw),
                        "columns": block.columns,
                        "rows": block.rows,
                        "null_order": "first" if "FIRST" in name else "last" if "LAST" in name else "unspecified",
                    }
                )
    if set(result) != set(STATEMENTS) or any(not values for values in result.values()):
        raise ValueError("Official answers do not cover all 103 statements")
    return result


def convert_cell(text: str | None, sql_type: str, null_tokens: list[str]) -> Any:
    if text is None or text in null_tokens:
        return None
    kind = sql_type.upper()
    if kind in {"VARCHAR", "CHAR", "TEXT", "STRING"}:
        return text
    if kind.startswith("DECIMAL("):
        value = Decimal(text)
        if not value.is_finite():
            raise ValueError(f"Nonfinite DECIMAL answer: {text!r}")
        return value
    if kind in {
        "TINYINT",
        "SMALLINT",
        "INTEGER",
        "BIGINT",
        "HUGEINT",
        "UTINYINT",
        "USMALLINT",
        "UINTEGER",
        "UBIGINT",
        "UHUGEINT",
    }:
        if not re.fullmatch(r"[+-]?\d+", text):
            raise ValueError(f"Invalid integer answer: {text!r}")
        return int(text)
    if kind in {"FLOAT", "DOUBLE", "REAL"}:
        return float(text)
    if kind == "DATE":
        return date.fromisoformat(text)
    if kind.startswith("TIMESTAMP"):
        return datetime.fromisoformat(text)
    if kind == "BOOLEAN" and text.lower() in {"true", "false"}:
        return text.lower() == "true"
    raise ValueError(f"Unsupported official answer conversion: {sql_type!r}, {text!r}")


def convert_rows(answer: dict[str, Any], sql_types: list[str]) -> list[tuple[Any, ...]]:
    if len(answer["columns"]) != len(sql_types):
        raise ValueError("Official columns differ from SQL reference width")
    if any(len(row) != len(sql_types) for row in answer["rows"]):
        raise ValueError("Malformed official row width")
    return [
        tuple(convert_cell(cell, kind, answer["null_tokens"]) for cell, kind in zip(row, sql_types))
        for row in answer["rows"]
    ]


def display_rounded(
    actual: list[tuple[Any, ...]], answer: dict[str, Any], sql_types: list[str]
) -> list[tuple[Any, ...]]:
    if len(actual) != len(answer["rows"]):
        return actual
    return [
        tuple(
            _display_cell(value, text, kind, answer["null_tokens"])
            for value, text, kind in zip(row, printed, sql_types, strict=True)
        )
        for row, printed in zip(actual, answer["rows"], strict=True)
    ]


def _display_cell(value: Any, text: str | None, sql_type: str, null_tokens: list[str]) -> Any:
    if text is None or text in null_tokens or isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return value
    if not sql_type.upper().startswith(("DECIMAL(", "DOUBLE", "FLOAT", "REAL")):
        return value
    if isinstance(value, float) and not math.isfinite(value):
        return value
    try:
        printed = Decimal(text)
    except InvalidOperation:
        return value
    if not printed.is_finite():
        return value
    exponent = printed.as_tuple().exponent
    if not isinstance(exponent, int):
        return value
    step = Decimal(1).scaleb(exponent)
    actual = Decimal(str(value))
    if printed in {actual.quantize(step, ROUND_HALF_UP), actual.quantize(step, ROUND_HALF_EVEN)}:
        return convert_cell(text, sql_type, null_tokens)
    return value


_HALF_BOUNDARY_RELATIVE = Decimal("1e-12")
_FLOAT_DETAIL_RELATIVE = Decimal("1e-8")
_CHAR_PADDING_WIDTH = 30


def _printed_step(text: str) -> tuple[Decimal, Decimal] | None:
    try:
        printed = Decimal(text)
    except InvalidOperation:
        return None
    exponent = printed.as_tuple().exponent
    if not printed.is_finite() or not isinstance(exponent, int):
        return None
    return printed, Decimal(1).scaleb(exponent)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool) and math.isfinite(value)


def _half_boundary_cell(value: Any, text: str | None, sql_type: str, null_tokens: list[str]) -> Any:
    if (
        text is None
        or text in null_tokens
        or not _is_number(value)
        or not sql_type.upper().startswith(("DECIMAL(", "DOUBLE"))
    ):
        return value
    parsed = _printed_step(text)
    if parsed is None:
        return value
    printed, step = parsed
    actual = Decimal(str(value))
    if abs(abs(actual - printed) - step / 2) <= _HALF_BOUNDARY_RELATIVE * max(abs(actual), Decimal(1)):
        return convert_cell(text, sql_type, null_tokens)
    return value


def _float_detail_cell(value: Any, text: str | None, sql_type: str, null_tokens: list[str]) -> Any:
    if text is None or text in null_tokens or not _is_number(value) or not sql_type.upper().startswith("DOUBLE"):
        return value
    parsed = _printed_step(text)
    if parsed is None or parsed[0] == 0:
        return value
    printed = parsed[0]
    if abs(Decimal(str(value)) - printed) <= _FLOAT_DETAIL_RELATIVE * abs(printed):
        return convert_cell(text, sql_type, null_tokens)
    return value


def _char_padding_cell(value: Any, text: str | None) -> Any:
    if not isinstance(value, str) or text is None or ", " not in value:
        return value
    last, first = value.split(", ", 1)
    if text.rstrip() == f"{last.ljust(_CHAR_PADDING_WIDTH)}, {first}".rstrip():
        return text
    return value


def _half_up_ratio_cell(row: tuple[Any, ...], text: str | None, ratio: tuple[int, int, int, int]) -> Any:
    target, numerator, denominator, places = ratio
    if text is None or not (_is_number(row[numerator]) and _is_number(row[denominator])) or not row[denominator]:
        return row[target]
    exact = (Decimal(str(row[numerator])) / Decimal(str(row[denominator]))).quantize(
        Decimal(1).scaleb(-places), ROUND_HALF_UP
    )
    parsed = _printed_step(text)
    if parsed is not None and parsed[0] == exact:
        return float(exact)
    return row[target]


def _tie_distance(actual: tuple[Any, ...], expected: tuple[Any, ...], key: tuple[int, ...]) -> float:
    total = 0.0
    for index, (left, right) in enumerate(zip(actual, expected, strict=True)):
        if index in key:
            continue
        if _is_number(left) and _is_number(right):
            total += abs(float(left) - float(right)) / (1 + abs(float(right)))
        elif left != right:
            total += 1.0
    return total


def _align_tied_runs(
    rows: list[tuple[Any, ...]], expected: list[tuple[Any, ...]], key: tuple[int, ...]
) -> list[tuple[Any, ...]]:
    aligned = list(rows)
    start = 0
    while start < len(expected):
        end = start + 1
        while end < len(expected) and all(expected[end][i] == expected[start][i] for i in key):
            end += 1
        if end - start > 1 and all(rows[p][i] == expected[start][i] for p in range(start, end) for i in key):
            pool = list(range(start, end))
            for target in range(start, end):
                best = min(pool, key=lambda p: _tie_distance(rows[p], expected[target], key))
                pool.remove(best)
                aligned[target] = rows[best]
        start = end
    return aligned


def classified_rows(
    rows: list[tuple[Any, ...]],
    expected: list[tuple[Any, ...]],
    answer: dict[str, Any],
    sql_types: list[str],
    entry: KnownDifference,
    classes: tuple[str, ...],
) -> list[tuple[Any, ...]]:
    if len(rows) != len(answer["rows"]):
        return rows
    if TIED_ORDER in classes:
        rows = _align_tied_runs(rows, expected, entry.tie_key)
    null_tokens = answer["null_tokens"]
    result = []
    for row, printed in zip(rows, answer["rows"], strict=True):
        cells = []
        for index, (value, text, kind) in enumerate(zip(row, printed, sql_types, strict=True)):
            replaced = value
            if DISPLAY_PRECISION in classes:
                replaced = _display_cell(replaced, text, kind, null_tokens)
            if replaced is value and index in entry.columns:
                if HALF_BOUNDARY_ROUNDING in classes:
                    replaced = _half_boundary_cell(replaced, text, kind, null_tokens)
                if FLOAT_DETAIL in classes and replaced is value:
                    replaced = _float_detail_cell(replaced, text, kind, null_tokens)
                if CHAR_PADDING in classes and replaced is value:
                    replaced = _char_padding_cell(replaced, text)
            if replaced is value and HALF_BOUNDARY_ROUNDING in classes and entry.ratio and index == entry.ratio[0]:
                replaced = _half_up_ratio_cell(row, text, entry.ratio)
            cells.append(replaced)
        result.append(tuple(cells))
    return result


def side_classification(
    entry: KnownDifference | None,
    item: dict[str, Any],
    side: str,
    rows: list[tuple[Any, ...]],
    expected: list[tuple[Any, ...]],
    answer: dict[str, Any],
    sql_types: list[str],
    query: str,
    sql: str,
    columns: list[tuple[str, str]],
) -> dict[str, str] | None:
    if entry is None or item[f"{side}_to_printed"]["status"] == "match":
        return None
    if entry.result_digest is not None and answer["null_order"] != "first" and rows_digest(rows) == entry.result_digest:
        return {"class": RETURNS_DIFFERENCE}
    if item[f"{side}_to_printed_display"]["status"] == "match" and DISPLAY_PRECISION in entry.classes:
        return {"class": DISPLAY_PRECISION}
    for count in range(1, len(entry.classes) + 1):
        classes = entry.classes[:count]
        if RETURNS_DIFFERENCE in classes or MALFORMED_OFFICIAL_ANSWER in classes:
            break
        transformed = classified_rows(rows, expected, answer, sql_types, entry, classes)
        if compare_rows(expected, transformed, query, sql, columns)["status"] == "match":
            return {"class": classes[-1]}
    if answer["file"] in entry.variant_files and answer["null_order"] == "first":
        return {"class": NULL_ORDER_VARIANT}
    return None


def compare_rows(
    expected: list[tuple[Any, ...]],
    actual: list[tuple[Any, ...]],
    query: str,
    sql: str,
    columns: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    from benchbox.core.equivalence.cross_surface import (
        _derived_order_violation,
        _is_truncated_top_n,
        _order_by_result_key,
    )
    from benchbox.core.equivalence.dataframe_surface import _normalize_value
    from benchbox.core.tpchavoc.validation import ResultValidator, ValidationError

    order_by = _order_by_result_key(sql)
    try:
        ResultValidator(strip_strings=True).validate_results_exact(
            [tuple(_normalize_value(value) for value in row) for row in expected],
            [tuple(_normalize_value(value) for value in row) for row in actual],
            int(re.sub(r"\D", "", query)),
            0,
            order_aware=bool(order_by),
            order_by=order_by,
            tie_aware=_is_truncated_top_n(sql),
        )
        violation = _derived_order_violation(sql, columns, actual) if order_by is None and columns else None
        if violation is not None:
            raise ValidationError(f"Q{query}: {violation}")
        return {"status": "match", "rows": len(actual), "order_by": order_by}
    except ValidationError as exc:
        return {
            "status": "mismatch",
            "rows": len(actual),
            "expected_rows": len(expected),
            "detail": str(exc),
            "order_by": order_by,
        }


def emit(path: Path, event: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, default=str, allow_nan=False) + "\n")
        stream.flush()


def _bundled_sqlglot_identity() -> str:
    from benchbox import _vendor

    pin = json.loads(Path(_vendor.__file__).with_name("sqlglot.json").read_text(encoding="utf-8"))
    return f"{pin['version']}+{pin['fork_sha'][:12]}.{pin['sha256'][:12]}"


def prepare(answers_dir: Path) -> dict[str, Any]:
    from benchbox.core.tpcds.c_tools import DSQGenBinary
    from benchbox.core.tpcds.dataframe_queries import TPCDS_DATAFRAME_QUERIES
    from benchbox.core.tpcds.dataframe_queries.parameter_adapters import ADAPTERS
    from benchbox.tpcds import TPCDS

    inputs = json.loads(_INPUTS.read_text(encoding="utf-8"))
    if set(inputs["templates"]) != {f"query{number}.tpl" for number in range(1, 100)}:
        raise ValueError("Qualification template inventory must contain exactly queries 1 through 99")
    specification = Path(__file__).parents[4] / "_sources/tpc-ds/specification/specification_4.0.0.pdf"
    if sha256(specification) != inputs["specification_audit"]["pdf_sha256"]:
        raise ValueError("Qualification specification hash differs")
    document = qualification_parameters(manifest=inputs)
    answers = official_inventory(answers_dir, inputs)
    if set(ADAPTERS) != set(range(1, 100)):
        raise ValueError("Qualification adapters must cover all 99 queries")
    dsqgen = DSQGenBinary()
    benchmark = TPCDS(scale_factor=1.0)
    dsdgen = benchmark.generator.dsdgen_exe
    if dsdgen is None:
        raise ValueError("Missing dsdgen binary")
    binaries = {
        "dsqgen": {"path": str(dsqgen.dsqgen_path), "sha256": sha256(dsqgen.dsqgen_path)},
        "dsdgen": {"path": str(dsdgen), "sha256": sha256(dsdgen)},
    }
    for name, digest in inputs["templates"].items():
        if sha256(dsqgen.templates_dir / name) != digest:
            raise ValueError(f"Qualification template hash differs: {name}")
    sqls: dict[str, str] = {}
    for query in STATEMENTS:
        number = int(re.sub(r"\D", "", query))
        registry_id = f"Q{number}" + ("b" if query.endswith("b") else "")
        definition = TPCDS_DATAFRAME_QUERIES.get_or_raise(registry_id)
        if definition.expression_impl is None or definition.pandas_impl is None:
            raise ValueError(f"Missing qualification implementation: {registry_id}")
        ADAPTERS[number](document["values"][str(number)])
        raw = dsqgen.generate_with_parameters(query, document["values"][str(number)], scale_factor=1.0, seed=1)
        translated = benchmark._impl.translate_query_text(raw, "netezza", "duckdb")
        sqls[query] = benchmark._impl._apply_target_dialect_overrides(
            number, translated, "duckdb", variant="b" if query.endswith("b") else None
        )
    if len(sqls) != 103:
        raise ValueError("Qualification SQL inventory must contain all 103 statements")
    return {
        "specification": inputs["specification"],
        "specification_audit": inputs["specification_audit"],
        "specification_url": inputs["specification_url"],
        "parameters": document,
        "parameters_sha256": sha256(_PARAMETERS),
        "parameter_values_hash": parameter_hash(document["values"]),
        "sql": sqls,
        "answers": answers,
        "binaries": binaries,
        "templates": inputs["templates"],
        "answer_manifest_sha256": sha256(_INPUTS),
        "source_head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "dependencies": {
            **{
                name: importlib.metadata.version(name)
                for name in ("duckdb", "polars", "pandas", "datafusion", "pyarrow")
            },
            "sqlglot": sqlglot.__version__,
            "benchbox-sqlglot": _bundled_sqlglot_identity(),
        },
    }


def _table_paths(value: Any) -> list[Path]:
    return [Path(path) for path in (value if isinstance(value, list) else [value])]


def worker(job: Path, engine: str, events: Path, data_dir: Path) -> int:
    from benchbox.core.equivalence.builders.base import _load_duckdb_cell
    from benchbox.core.equivalence.dataframe_surface import materialize_rows
    from benchbox.core.tpcds.dataframe_queries import TPCDS_DATAFRAME_QUERIES
    from benchbox.core.tpcds.dataframe_queries.parameter_adapters import ADAPTERS, ParameterBinding
    from benchbox.core.tpcds.dataframe_queries.production_binding import bind_queries
    from benchbox.core.tpcds.schema.registry import TABLES
    from benchbox.core.tpcds.schema.tables import DBGEN_VERSION
    from benchbox.tpcds import TPCDS

    prepared = json.loads(job.read_text(encoding="utf-8"))
    for binary in prepared["binaries"].values():
        if sha256(Path(binary["path"])) != binary["sha256"]:
            raise ValueError("Input binary changed after qualification preflight")
    benchmark = TPCDS(scale_factor=1.0, output_dir=data_dir, compress_data=False)
    generated = benchmark.generate_data()
    data_hashes = {}
    paths = [path for group in generated for path in _table_paths(group)]
    for path in sorted(paths, key=str):
        key = path.resolve().relative_to(data_dir.resolve()).as_posix()
        if key in data_hashes:
            raise ValueError(f"Duplicate qualification data path: {key}")
        data_hashes[key] = sha256(path)
    if not data_hashes:
        raise ValueError("Qualification data inventory is empty")
    table_data_hashes = {}
    for table in (table for table in TABLES if table.name != DBGEN_VERSION.name):
        if table.name not in benchmark.tables:
            raise ValueError(f"Missing qualification schema table: {table.name}")
        table_paths = _table_paths(benchmark.tables[table.name])
        if not table_paths:
            raise ValueError(f"Empty qualification schema table: {table.name}")
        keys = [path.resolve().relative_to(data_dir.resolve()).as_posix() for path in table_paths]
        table_data_hashes[table.name] = {key: data_hashes[key] for key in keys}
    table_keys = {key for files in table_data_hashes.values() for key in files}
    metadata_hashes = {key: value for key, value in data_hashes.items() if key not in table_keys}
    connection = _load_duckdb_cell(benchmark, data_dir, [table.name for table in TABLES], label="TPC-DS qualification")
    connection.execute("SET threads = 2")
    connection.execute("SET memory_limit = '2GB'")
    module, name, family = _ENGINES[engine]
    adapter = getattr(importlib.import_module(module), name)()
    context = adapter.load_benchmark_into_context(benchmark, data_dir, scale_factor=1.0)
    emit(
        events,
        {
            "event": "loaded",
            "engine": engine,
            "data_sha256": data_hashes,
            "table_data_sha256": table_data_hashes,
            "generator_metadata_sha256": metadata_hashes,
            "duckdb_null_order": connection.execute("SELECT current_setting('default_null_order')").fetchone()[0],
        },
    )
    failed = False
    try:
        for query in STATEMENTS:
            emit(events, {"event": "query_start", "engine": engine, "query": query})
            started = mono_time()
            try:
                number = int(re.sub(r"\D", "", query))
                values = prepared["parameters"]["values"][str(number)]
                binding = ParameterBinding(
                    number, 1.0, None, 0, prepared["binaries"]["dsqgen"]["sha256"], values, ADAPTERS[number](values)
                )
                registry_id = f"Q{number}" + ("b" if query.endswith("b") else "")
                definition = bind_queries([TPCDS_DATAFRAME_QUERIES.get_or_raise(registry_id)], {number: binding})[0]
                sql = prepared["sql"][query]
                cursor = connection.execute(sql)
                types = [str(column[1]) for column in cursor.description]
                columns = [(str(column[0]), str(column[1])) for column in cursor.description]
                reference = cursor.fetchall()
                result = definition.get_impl_for_family(family)(context)
                candidate = materialize_rows(result)
                parity = compare_rows(reference, candidate, query, sql, columns)
                comparisons = []
                answer_error = False
                entry = KNOWN.get(query)
                for answer in prepared["answers"][query]:
                    try:
                        expected = convert_rows(answer, types)
                    except (ValueError, ArithmeticError) as exc:
                        answer_error = True
                        comparisons.append(
                            {
                                "file": answer["file"],
                                "sha256": answer["sha256"],
                                "null_order": answer["null_order"],
                                "status": "error",
                                "detail": str(exc),
                                "official_columns": len(answer["columns"]),
                                "sql_columns": len(types),
                            }
                        )
                        continue
                    item = {
                        "file": answer["file"],
                        "sha256": answer["sha256"],
                        "null_order": answer["null_order"],
                        "sql_to_printed": compare_rows(expected, reference, query, sql, columns),
                        "dataframe_to_printed": compare_rows(expected, candidate, query, sql, columns),
                    }
                    for side, rows in (("sql", reference), ("dataframe", candidate)):
                        if item[f"{side}_to_printed"]["status"] != "match":
                            item[f"{side}_to_printed_display"] = compare_rows(
                                expected, display_rounded(rows, answer, types), query, sql, columns
                            )
                            label = side_classification(
                                entry, item, side, rows, expected, answer, types, query, sql, columns
                            )
                            if label is not None:
                                item[f"{side}_classification"] = label
                    comparisons.append(item)
                matched = (
                    not answer_error
                    and parity["status"] == "match"
                    and any(
                        item["sql_to_printed"]["status"] == "match"
                        and item["dataframe_to_printed"]["status"] == "match"
                        for item in comparisons
                    )
                )
                status = "error" if answer_error else "match" if matched else "mismatch"
                classification = printed_classification(query, status, comparisons)
                nonempty = any(value is not None for row in reference for value in row)
                verdict = gate(query, status, parity, classification, nonempty)
                failed |= verdict["status"] == "fail"
                emit(
                    events,
                    {
                        "event": "query_result",
                        "engine": engine,
                        "query": query,
                        "sql_types": types,
                        "sql_sha256": hashlib.sha256(sql.encode()).hexdigest(),
                        "parameters": values,
                        "parameter_hash": parameter_hash(values),
                        "df_parameters": binding.parameters,
                        "elapsed_seconds": elapsed_seconds(started),
                        "dataframe_to_sql": parity,
                        "official_files": comparisons,
                        "status": status,
                        "sql_nonempty": nonempty,
                        "printed_classification": classification,
                        "gate": verdict,
                    },
                )
            except Exception as exc:
                failed = True
                emit(
                    events,
                    {
                        "event": "query_result",
                        "engine": engine,
                        "query": query,
                        "status": "error",
                        "detail": str(exc),
                        "elapsed_seconds": elapsed_seconds(started),
                        "gate": {"status": "fail", "failures": ["execution_error"]},
                    },
                )
    finally:
        connection.close()
    return int(failed)


def supervise(
    command: list[str],
    events: Path,
    log: Path,
    query_seconds: float,
    overall_seconds: float,
    max_rss_bytes: int = 6 * 1024**3,
) -> int:
    started = mono_time()
    deadline = started + overall_seconds
    offset = 0
    active: str | None = None
    import psutil

    peak_rss = 0
    with log.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            while process.poll() is None:
                try:
                    owned = psutil.Process(process.pid)
                    rss = sum(member.memory_info().rss for member in [owned, *owned.children(recursive=True)])
                    peak_rss = max(peak_rss, rss)
                    if rss > max_rss_bytes:
                        emit(events, {"event": "memory_limit", "status": "error", "rss_bytes": rss})
                        _kill_worker(process)
                        return 1
                except psutil.NoSuchProcess:
                    pass
                if events.exists():
                    with events.open(encoding="utf-8") as stream:
                        stream.seek(offset)
                        while True:
                            position = stream.tell()
                            line = stream.readline()
                            if not line.endswith("\n"):
                                stream.seek(position)
                                break
                            event = json.loads(line)
                            if event["event"] == "query_start":
                                active = event["query"]
                                deadline = min(started + overall_seconds, mono_time() + query_seconds)
                            elif event["event"] == "query_result":
                                active = None
                                deadline = started + overall_seconds
                        offset = stream.tell()
                if mono_time() >= deadline:
                    emit(events, {"event": "timeout", "query": active, "status": "error"})
                    _kill_worker(process)
                    return 1
                time.sleep(0.1)
            emit(events, {"event": "worker_exit", "returncode": process.returncode, "peak_rss_bytes": peak_rss})
            return process.returncode
        finally:
            if process.poll() is None:
                _kill_worker(process)


def _kill_worker(process: subprocess.Popen[Any]) -> None:
    try:
        if hasattr(signal, "SIGKILL"):
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass
    process.wait()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--answers-dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--query-seconds", type=float, default=120)
    parser.add_argument("--overall-seconds", type=float, default=1200)
    parser.add_argument("--max-rss-gib", type=float, default=6)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--worker", choices=tuple(_ENGINES))
    parser.add_argument("--prepare-worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not (0 < args.query_seconds <= 1200 and 0 < args.overall_seconds <= 1200):
        parser.error("Query and overall bounds must be positive and at most 1200 seconds")
    if not 0 < args.max_rss_gib <= 9:
        parser.error("Resident memory bound must be positive and at most 9 GiB")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.worker:
        return worker(
            args.output_dir / "inputs.json",
            args.worker,
            args.output_dir / f"{args.worker}.jsonl",
            args.output_dir / "data",
        )
    private = args.output_dir / "cache"
    private.mkdir(exist_ok=True)
    os.environ["BENCHBOX_CACHE_DIR"] = str(private.resolve())
    os.environ["XDG_CACHE_HOME"] = str(private.resolve())
    started = mono_time()
    try:
        if not args.prepare_worker:
            if (args.output_dir / "inputs.json").exists() or (args.output_dir / "summary.jsonl").exists():
                raise ValueError("Qualification output directory must be fresh")
            emit(
                args.output_dir / "summary.jsonl",
                {
                    "event": "limits",
                    "query_seconds": args.query_seconds,
                    "overall_seconds": args.overall_seconds,
                    "max_rss_bytes": int(args.max_rss_gib * 1024**3),
                },
            )
            command = [
                sys.executable,
                "-m",
                "benchbox.core.tpcds.qualification.runner",
                "--prepare-worker",
                "--output-dir",
                str(args.output_dir),
            ]
            if args.answers_dir is not None:
                command.extend(["--answers-dir", str(args.answers_dir)])
            result = supervise(
                command,
                args.output_dir / "summary.jsonl",
                args.output_dir / "preflight.log",
                args.query_seconds,
                args.overall_seconds,
                int(args.max_rss_gib * 1024**3),
            )
            if result or not (args.output_dir / "inputs.json").exists():
                raise ValueError("Qualification preflight failed; see preflight.log and summary.jsonl")
        else:
            return prepare_worker(args)
        if args.preflight_only:
            return 0
        failures = []
        data_identity = None
        metadata_identity = None
        for engine in _ENGINES:
            events = args.output_dir / f"{engine}.jsonl"
            if events.exists():
                raise ValueError("Qualification output directory must be fresh")
            remaining = args.overall_seconds - elapsed_seconds(started)
            if remaining <= 0:
                raise TimeoutError("Qualification overall timeout")
            command = [
                sys.executable,
                "-m",
                "benchbox.core.tpcds.qualification.runner",
                "--worker",
                engine,
                "--output-dir",
                str(args.output_dir),
            ]
            result = supervise(
                command,
                events,
                args.output_dir / f"{engine}.log",
                args.query_seconds,
                remaining,
                int(args.max_rss_gib * 1024**3),
            )
            records = (
                [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines()]
                if events.exists()
                else []
            )
            completed = [record["query"] for record in records if record["event"] == "query_result"]
            if completed != list(STATEMENTS):
                result = 1
            loaded = [record for record in records if record["event"] == "loaded"]
            if len(loaded) != 1:
                result = 1
            elif data_identity is None:
                data_identity = loaded[0]["table_data_sha256"]
                metadata_identity = loaded[0]["generator_metadata_sha256"]
            elif loaded[0]["table_data_sha256"] != data_identity:
                result = 1
                emit(events, {"event": "input_drift", "status": "error", "detail": "Data differs across engines"})
            if loaded and loaded[0]["generator_metadata_sha256"] != metadata_identity:
                emit(
                    events,
                    {
                        "event": "generation_metadata_difference",
                        "reference": metadata_identity,
                        "current": loaded[0]["generator_metadata_sha256"],
                    },
                )
            failures.append(bool(result))
            emit(
                args.output_dir / "summary.jsonl",
                {
                    "event": "engine_result",
                    "engine": engine,
                    "status": "failure" if result else "match",
                    "completed": len(completed),
                },
            )
        return int(any(failures))
    except Exception as exc:
        emit(args.output_dir / "summary.jsonl", {"event": "error", "status": "failure", "detail": str(exc)})
        return 1


def prepare_worker(args: argparse.Namespace) -> int:
    try:
        if args.answers_dir is None:
            from benchbox.core.expected_results.loader import _find_tpcds_answers_dir

            args.answers_dir = _find_tpcds_answers_dir()
        prepared = prepare(args.answers_dir)
        (args.output_dir / "inputs.json").write_text(json.dumps(prepared, indent=2), encoding="utf-8")
        emit(
            args.output_dir / "summary.jsonl",
            {
                "event": "preflight",
                "status": "complete",
                "queries": 99,
                "statements": 103,
                "raw_blocks_per_null_order": 104,
                "parameter_values_hash": prepared["parameter_values_hash"],
            },
        )
        return 0
    except Exception as exc:
        emit(args.output_dir / "summary.jsonl", {"event": "error", "status": "failure", "detail": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
