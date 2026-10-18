# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import subprocess
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest

from tests import required_local_cases as required

pytestmark = [pytest.mark.unit, pytest.mark.medium]

MATRIX = required.MATRIX_MODULE
ROOT = Path(__file__).resolve().parents[2]


def test_inventory_is_exact_original_four_plus_sqlite() -> None:
    assert (
        f"{MATRIX}::test_local_platform_benchmark_matrix[tpch-duckdb]",
        f"{MATRIX}::test_local_platform_benchmark_matrix[tpcds-duckdb]",
        f"{MATRIX}::test_local_platform_benchmark_matrix[tpch-datafusion]",
        f"{MATRIX}::test_local_dataframe_platform_benchmark_matrix[tpch-polars-df]",
        f"{MATRIX}::test_sqlite_tpch_fixed_seed_value_parity",
    ) == required.REQUIRED_LOCAL_CASES
    required.check_inventory(required.REQUIRED_LOCAL_CASES)


def test_every_required_case_is_a_node_pytest_collects() -> None:

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-o", "addopts=", "-p", "no:cacheprovider", MATRIX],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    required.check_collected(line.strip() for line in result.stdout.splitlines() if "::" in line)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda nodes: [node for node in nodes if "[tpch-duckdb]" not in node],
        lambda nodes: [node.replace("[tpch-polars-df]", "[tpch-polars]") for node in nodes],
        lambda nodes: [
            node.replace("::test_sqlite_tpch_fixed_seed_value_parity", "::test_sqlite_renamed") for node in nodes
        ],
    ],
    ids=["removed", "reparametrized", "renamed"],
)
def test_a_removed_renamed_or_reparametrized_case_is_rejected(mutation) -> None:
    required.check_collected(required.REQUIRED_LOCAL_CASES)
    with pytest.raises(required.RequiredCaseError, match="not collected"):
        required.check_collected(mutation(required.REQUIRED_LOCAL_CASES))


@pytest.mark.parametrize("compressed", [False, True], ids=["tbl", "tbl-zst"])
def test_duckdb_reference_loader_accepts_apostrophe_path(tmp_path: Path, compressed: bool) -> None:
    import zstandard

    from benchbox.core.tpch.schema import TABLES, DataType
    from tests.integration import test_local_platform_benchmark_matrix as matrix

    datagen_dir = tmp_path / "case's data" / "datagen"
    datagen_dir.mkdir(parents=True)
    values = {
        DataType.INTEGER: "1",
        DataType.DECIMAL: "1.25",
        DataType.CHAR: "A",
        DataType.VARCHAR: "reference row",
        DataType.DATE: "1997-06-01",
    }
    for table in TABLES:
        row = ("|".join(values[column.data_type] for column in table.columns) + "|\n").encode()
        suffix = ".tbl.zst" if compressed else ".tbl"
        if compressed:
            row = zstandard.ZstdCompressor().compress(row)
        (datagen_dir / f"{table.name}{suffix}").write_bytes(row)

    connection = matrix._load_duckdb_from_generated_files(datagen_dir)
    try:
        counts = {
            table.name: connection.execute(f"SELECT COUNT(*) FROM {table.name}").fetchone()[0] for table in TABLES
        }
        assert counts == dict.fromkeys(required.TPCH_TABLE_NAMES, 1)
        assert connection.execute("SELECT l_extendedprice, l_discount FROM lineitem").fetchall() == [
            (Decimal("1.25"), Decimal("1.25"))
        ]
        assert connection.execute("SELECT r_comment FROM region").fetchall() == [("reference row",)]
    finally:
        connection.close()


def test_sqlite_case_rejects_checkout_owned_output(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.integration import test_local_platform_benchmark_matrix as matrix

    monkeypatch.setattr(matrix, "is_platform_available", lambda platform: True)
    checkout = Path(matrix.__file__).resolve().parents[2]
    with pytest.raises(AssertionError, match="outside the checkout"):
        matrix.test_sqlite_tpch_fixed_seed_value_parity(checkout)


def test_sqlite_case_pins_workload_and_owns_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.integration import test_local_platform_benchmark_matrix as matrix

    class CapturedCommand(Exception):
        pass

    captured: dict = {}

    def capture(args, **kwargs):
        captured.update(args=args, **kwargs)
        raise CapturedCommand

    monkeypatch.setattr(matrix, "is_platform_available", lambda platform: True)
    monkeypatch.setattr(matrix, "run_cli_command", capture)
    monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", str(tmp_path / "unrelated"))
    with pytest.raises(CapturedCommand):
        matrix.test_sqlite_tpch_fixed_seed_value_parity(tmp_path)
    args = captured["args"]
    for flag, value in {
        "--platform": "sqlite",
        "--benchmark": "tpch",
        "--scale": "0.01",
        "--phases": "generate,load,power",
        "--queries": "1,6,14",
        "--seed": "42",
        "--iterations": "3",
    }.items():
        assert args[args.index(flag) + 1] == value
    case_dir = tmp_path / "sqlite_tpch_value_parity"
    assert captured["cwd"] == case_dir
    assert captured["env"]["BENCHBOX_OUTPUT_DIR"] == str(case_dir / "benchmark_runs")
    assert not (case_dir / "benchmark_runs").exists()


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda nodes: nodes[:-1], id="missing"),
        pytest.param(lambda nodes: [*nodes, f"{MATRIX}::test_extra"], id="extra"),
        pytest.param(lambda nodes: [*nodes, nodes[0]], id="duplicate"),
        pytest.param(lambda nodes: [n.replace("tpch-duckdb", "tpch-sqlite") for n in nodes], id="substituted"),
        pytest.param(lambda nodes: [], id="empty"),
    ],
)
def test_inventory_rejects_non_conserving_sets(mutate) -> None:
    with pytest.raises(required.RequiredCaseError):
        required.check_inventory(mutate(list(required.REQUIRED_LOCAL_CASES)))


def _measurements(ids: list[str], status: str = "SUCCESS") -> list[dict]:
    return [{"id": query_id, "run_type": "measurement", "status": status} for query_id in ids]


def test_expected_multiset_is_three_iterations_of_q1_q6_q14() -> None:
    assert required.expected_measurement_multiset() == Counter({"1": 3, "6": 3, "14": 3})


def test_measurement_multiset_accepts_exact_and_ignores_warmup() -> None:
    queries = [{"id": "1", "run_type": "warmup", "status": "SUCCESS"}, *_measurements(["1", "6", "14"] * 3)]
    required.check_measurement_multiset(queries, required.expected_measurement_multiset())


@pytest.mark.parametrize(
    "ids",
    [
        pytest.param(["1", "6", "14"] * 2, id="missing-iteration"),
        pytest.param(["1", "6", "14"] * 3 + ["6"], id="extra-duplicate"),
        pytest.param(["1", "1", "6", "14", "6", "14", "1", "6", "6"], id="same-size-wrong-mix"),
        pytest.param(["1", "6", "15"] * 3, id="wrong-id"),
        pytest.param([], id="empty"),
    ],
)
def test_measurement_multiset_rejects_non_conserving_runs(ids: list[str]) -> None:
    with pytest.raises(required.RequiredCaseError):
        required.check_measurement_multiset(_measurements(ids), required.expected_measurement_multiset())


def test_measurement_multiset_rejects_failed_row_even_when_count_restored() -> None:
    queries = [*_measurements(["1", "6", "14"] * 3), *_measurements(["6"], status="FAILED")]
    with pytest.raises(required.RequiredCaseError, match="non-successful"):
        required.check_measurement_multiset(queries, required.expected_measurement_multiset())


def test_population_covers_all_eight_authoritative_tables() -> None:
    assert sorted(required.TPCH_TABLE_NAMES) == sorted(
        ["region", "nation", "supplier", "part", "partsupp", "customer", "orders", "lineitem"]
    )
    required.check_tables_populated(dict.fromkeys(required.TPCH_TABLE_NAMES, 5))


@pytest.mark.parametrize("table", ["region", "lineitem", "partsupp"])
def test_population_rejects_empty_table(table: str) -> None:
    counts = dict.fromkeys(required.TPCH_TABLE_NAMES, 5)
    counts[table] = 0
    with pytest.raises(required.RequiredCaseError, match=table):
        required.check_tables_populated(counts)


def test_population_rejects_missing_table() -> None:
    counts = dict.fromkeys(required.TPCH_TABLE_NAMES, 5)
    del counts["nation"]
    with pytest.raises(required.RequiredCaseError, match="nation"):
        required.check_tables_populated(counts)


REFERENCE = [("A", "F", Decimal("37734107.00"), 56586554400.73), ("N", "O", Decimal("991417.00"), 1487504710.38)]


def test_rows_match_accepts_reordered_and_decimal_vs_float_within_tolerance() -> None:
    actual = [("N", "O", 991417.0, 1487504710.38), ("A", "F", 37734107.0, 56586554400.73 * (1 + 1e-12))]
    required.check_rows_match(REFERENCE, actual, "1")


def test_rows_match_rejects_same_cardinality_wrong_value() -> None:
    actual = [REFERENCE[0], ("N", "O", Decimal("991417.00"), 1487504710.38 * (1 + 1e-6))]
    with pytest.raises(required.RequiredCaseError, match="Q1"):
        required.check_rows_match(REFERENCE, actual, "1")


def test_rows_match_rejects_q6_boundary_exclusion_shape() -> None:

    with pytest.raises(required.RequiredCaseError, match="Q6"):
        required.check_rows_match([(1272913.9338,)], [(771253.5606,)], "6")


def test_rows_match_rejects_wrong_string_and_row_count() -> None:
    with pytest.raises(required.RequiredCaseError):
        required.check_rows_match(REFERENCE, [("A", "X", *REFERENCE[0][2:]), REFERENCE[1]], "1")
    with pytest.raises(required.RequiredCaseError):
        required.check_rows_match(REFERENCE, REFERENCE[:1], "1")


def test_rows_match_rejects_vacuous_empty_reference() -> None:
    with pytest.raises(required.RequiredCaseError, match="vacuous"):
        required.check_rows_match([], [], "14")


def test_tolerance_is_pinned() -> None:
    assert required.VALUE_TOLERANCE == 1e-10
