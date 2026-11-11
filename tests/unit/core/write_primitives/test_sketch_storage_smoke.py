from __future__ import annotations

import re
from pathlib import Path

import pytest

from benchbox.core.write_primitives.catalog.loader import load_write_primitives_catalog

pytestmark = [pytest.mark.fast, pytest.mark.unit]


REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_PATH = REPO_ROOT / "scripts" / "sketch_storage_smoke.sh"


def _catalog_sketch_byte_labels() -> set[tuple[str, str]]:
    labels: set[tuple[str, str]] = set()
    catalog = load_write_primitives_catalog()

    for op in catalog.operations.values():
        for validation in op.validation_queries:
            if "sketch_bytes" not in validation.sql:
                continue
            overrides = validation.platform_overrides or {}
            tool = "clickhouse-local" if "duckdb" in overrides and overrides["duckdb"] is None else "duckdb"
            labels.add((tool, op.id))

    return labels


def _script_sketch_byte_labels() -> set[tuple[str, str]]:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    duckdb_ops = set(re.findall(r'op_id="([^"]+)"', script))
    labels = {("duckdb", op_id) for op_id in duckdb_ops}

    clickhouse_block = re.search(r"CLICKHOUSE_PROBE_OP_IDS=\(\n(?P<body>.*?)\n\)", script, re.DOTALL)
    assert clickhouse_block is not None, "CLICKHOUSE_PROBE_OP_IDS must list clickhouse-local smoke labels"
    clickhouse_ops = set(re.findall(r'"([^"]+)"', clickhouse_block.group("body")))
    labels.update(("clickhouse-local", op_id) for op_id in clickhouse_ops)

    return labels


def test_sketch_storage_smoke_covers_catalog_sketch_byte_validations() -> None:

    expected = _catalog_sketch_byte_labels()
    actual = _script_sketch_byte_labels()

    assert actual == expected, (
        "scripts/sketch_storage_smoke.sh must emit one TSV row for each catalog sketch_bytes validation. "
        f"Missing labels: {sorted(expected - actual)}. Extra labels: {sorted(actual - expected)}."
    )


