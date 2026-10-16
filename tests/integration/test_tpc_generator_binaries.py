# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import hashlib
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
]

TPCH_TABLES = ["customer", "lineitem", "nation", "orders", "part", "partsupp", "region", "supplier"]


def _require_binary(name: str) -> Path:

    from benchbox.utils.tpc_compilation import ensure_tpc_binaries

    results = ensure_tpc_binaries([name], auto_compile=False)
    result = results.get(name)
    path = getattr(result, "binary_path", None)
    if path is None or not Path(path).exists():
        pytest.skip(f"no precompiled {name} binary for this platform")
    return Path(path)


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


class TestDbgenAvailability:
    def test_dbgen_binary_resolves(self):
        binary = _require_binary("dbgen")
        assert binary.name.startswith("dbgen")

    def test_dsdgen_binary_resolves(self):
        binary = _require_binary("dsdgen")
        assert binary.name.startswith("dsdgen")


class TestRealDbgenGeneration:
    def test_tiny_scale_produces_all_tbl_files(self, tmp_path: Path):
        _require_binary("dbgen")
        from benchbox.core.tpch.generator import TPCHDataGenerator

        tables = TPCHDataGenerator(scale_factor=0.01, output_dir=tmp_path, force_regenerate=True).generate()

        assert sorted(tables) == TPCH_TABLES
        for table, path in tables.items():
            assert isinstance(path, Path), f"{table} unexpectedly sharded at SF 0.01"
            assert path.exists(), f"{table} file missing: {path}"
            assert path.stat().st_size > 0, f"{table} file is empty"

    def test_tbl_pipe_format(self, tmp_path: Path):
        _require_binary("dbgen")
        from benchbox.core.tpch.generator import TPCHDataGenerator

        tables = TPCHDataGenerator(scale_factor=0.01, output_dir=tmp_path, force_regenerate=True).generate()

        lineitem = Path(tables["lineitem"])
        first = lineitem.read_text(encoding="utf-8").splitlines()[0]
        fields = first.split("|")

        assert len(fields) == 16
        assert fields[0].isdigit()

    def test_generation_writes_manifest(self, tmp_path: Path):
        _require_binary("dbgen")
        import json

        from benchbox.core.tpch.generator import TPCHDataGenerator

        TPCHDataGenerator(scale_factor=0.01, output_dir=tmp_path, force_regenerate=True).generate()

        manifest_path = tmp_path / "_datagen_manifest.json"
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["benchmark"] == "tpch"
        assert manifest["scale_factor"] == 0.01
        assert sorted(manifest["tables"]) == TPCH_TABLES

    def test_generation_is_byte_deterministic(self, tmp_path: Path):
        _require_binary("dbgen")
        from benchbox.core.tpch.generator import TPCHDataGenerator

        first_dir = tmp_path / "first"
        second_dir = tmp_path / "second"
        TPCHDataGenerator(scale_factor=0.01, output_dir=first_dir, force_regenerate=True).generate()
        TPCHDataGenerator(scale_factor=0.01, output_dir=second_dir, force_regenerate=True).generate()

        for table in TPCH_TABLES:
            assert _md5(first_dir / f"{table}.tbl") == _md5(second_dir / f"{table}.tbl"), (
                f"{table}.tbl differs between identical real-dbgen runs"
            )

    def test_generated_lineitem_loads_into_duckdb(self, tmp_path: Path):

        duckdb = pytest.importorskip("duckdb")
        _require_binary("dbgen")
        from benchbox.core.tpch.generator import TPCHDataGenerator

        tables = TPCHDataGenerator(scale_factor=0.01, output_dir=tmp_path, force_regenerate=True).generate()

        conn = duckdb.connect(":memory:")
        try:
            count = conn.execute(
                f"SELECT COUNT(*) FROM read_csv('{tables['lineitem']}', delim='|', header=False, nullstr='')"
            ).fetchone()[0]
        finally:
            conn.close()
        assert count > 0


class TestOrchestratedForceRegenerate:
    @staticmethod
    def _generate(tmp_path: Path, force_regenerate: bool) -> dict[str, int]:
        from benchbox.cli.orchestrator import BenchmarkOrchestrator
        from benchbox.core.schemas import BenchmarkConfig

        output_dir = tmp_path / "datagen"
        config = BenchmarkConfig(
            name="tpch", display_name="TPC-H", scale_factor=0.01, options={"force_regenerate": force_regenerate}
        )
        orchestrator = BenchmarkOrchestrator(base_dir=str(tmp_path))
        orchestrator.set_custom_output_dir(str(output_dir))
        orchestrator._get_benchmark_instance(config, system_profile=None).generate_data()
        return {path.name: path.stat().st_mtime_ns for path in output_dir.glob("*.tbl*")}

    def test_force_regenerate_rewrites_valid_existing_data(self, tmp_path: Path):
        _require_binary("dbgen")

        first = self._generate(tmp_path, force_regenerate=False)
        reused = self._generate(tmp_path, force_regenerate=False)
        forced = self._generate(tmp_path, force_regenerate=True)

        assert first
        assert reused == first
        assert forced.keys() == first.keys()
        assert all(forced[name] > first[name] for name in first)
