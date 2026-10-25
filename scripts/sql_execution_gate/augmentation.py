from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from scripts.sql_execution_gate.engines import Dataset

AUGMENTED = "tpcds-augmented"
BASE = "tpcds"
QUERY_IDS = ("16", "94", "95")
SEEDS = ("default", "1001")
_STATE = re.compile(r"ca_state\"?\s*=\s*'([A-Z]{2})'")
_COMPANY = re.compile(r"web_company_name\"?\s*=\s*'([^']+)'")


def query_key(query: str, seed: str) -> str:
    return f"{query}.{seed}"


def base_query(key: str) -> str:
    return key.split(".", 1)[0]


def _column_index(table: str, column: str) -> int:
    from benchbox.core.tpcds.schema import TABLES

    names = [c.name for c in next(t for t in TABLES if t.name == table).columns]
    return names.index(column)


def _rewrite(source: Path, target: Path, edits: dict[int, Callable]) -> None:
    with (
        source.open("r", encoding="utf-8", errors="replace", newline="") as reader,
        target.open("w", encoding="utf-8", newline="") as writer,
    ):
        for line in reader:
            body = line.rstrip("\r\n")
            fields = body.split("|")
            for index, edit in edits.items():
                fields[index] = edit(fields)
            writer.write("|".join(fields) + "\n")


def _warehouse_split(table: str, warehouse: str, item: str):
    warehouse_index, item_index = _column_index(table, warehouse), _column_index(table, item)
    return {
        warehouse_index: lambda fields: (
            str(int(fields[item_index]) % 2 + 1) if fields[item_index] else fields[warehouse_index]
        )
    }


def _cycle(index: int, values: list[str], key: int):
    return {index: lambda fields: values[int(fields[key]) % len(values)] if fields[key] else fields[index]}


def parameters(queries: dict[str, dict[str, str]]) -> tuple[list[str], list[str]]:
    states: list[str] = []
    companies: list[str] = []
    for per_dialect in queries.values():
        for sql in per_dialect.values():
            states += _STATE.findall(sql)
            companies += _COMPANY.findall(sql)
    return sorted(set(states)), sorted(set(companies))


def augment(dataset: Dataset, queries: dict[str, dict[str, str]], scratch: Path) -> Dataset:
    states, companies = parameters(queries)
    if not states or not companies:
        raise ValueError("augmented queries name no customer_address state or web_site company to populate")
    directory = scratch / "data-tpcds-augmented"
    directory.mkdir(exist_ok=True)
    tables = dict(dataset.tables)
    plans = {
        "catalog_sales": _warehouse_split("catalog_sales", "cs_warehouse_sk", "cs_item_sk"),
        "web_sales": _warehouse_split("web_sales", "ws_warehouse_sk", "ws_item_sk"),
        "customer_address": _cycle(
            _column_index("customer_address", "ca_state"), states, _column_index("customer_address", "ca_address_sk")
        ),
        "web_site": _cycle(
            _column_index("web_site", "web_company_name"), companies, _column_index("web_site", "web_site_sk")
        ),
    }
    for table, edits in plans.items():
        target = directory / tables[table].name
        _rewrite(tables[table], target, edits)
        tables[table] = target
    warehouse = directory / tables["warehouse"].name
    first = tables["warehouse"].read_text(encoding="utf-8", errors="replace").splitlines()[0].split("|")
    second = list(first)
    second[_column_index("warehouse", "w_warehouse_sk")] = "2"
    second[_column_index("warehouse", "w_warehouse_id")] = "AAAAAAAACAAAAAAA"
    warehouse.write_text("|".join(first) + "\n" + "|".join(second) + "\n", encoding="utf-8")
    tables["warehouse"] = warehouse
    return Dataset(AUGMENTED, dataset.ddl, tables)
