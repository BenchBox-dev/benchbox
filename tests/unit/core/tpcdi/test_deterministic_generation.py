# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from benchbox.core.tpcdi.config import TPCDIConfig
from benchbox.core.tpcdi.generator.data import TPCDIDataGenerator
from benchbox.core.tpcdi.generator.facts import FACT_TRADE_GENERATION_ALGORITHM_VERSION

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

SEED = 7
NUM_TRADES = 2000
CHUNK_SIZE = 500
DIMS = {
    "num_accounts": 50,
    "num_securities": 20,
    "num_customers": 60,
    "num_companies": 10,
}


def _make_generator(tmp_path: Path, subdir: str, max_workers: int, seed: int = SEED) -> TPCDIDataGenerator:
    out = tmp_path / subdir
    out.mkdir(parents=True, exist_ok=True)
    gen = TPCDIDataGenerator(
        scale_factor=1.0,
        output_dir=out,
        chunk_size=CHUNK_SIZE,
        buffer_size=512,
        max_workers=max_workers,
        enable_progress=False,
        quiet=True,
        generation_seed=seed,
        compression="none",
    )
    gen.base_customers = DIMS["num_customers"]
    gen.base_companies = DIMS["num_companies"]
    gen.base_securities = DIMS["num_securities"]
    gen.base_accounts = DIMS["num_accounts"]
    gen.base_trades = NUM_TRADES
    gen.compress_existing_file = lambda path, remove_original=True: path
    return gen


def _serial_bytes(tmp_path: Path, seed: int = SEED) -> bytes:
    gen = _make_generator(tmp_path, f"serial-{seed}", max_workers=1, seed=seed)
    return Path(gen._generate_facttrade_data()).read_bytes()


def _parallel_bytes(tmp_path: Path, workers: int, seed: int = SEED, tag: str = "") -> bytes:
    gen = _make_generator(tmp_path, f"parallel-w{workers}-s{seed}{tag}", max_workers=workers, seed=seed)
    out = Path(
        gen._generate_facttrade_parallel(
            gen.output_dir / "FactTrade.tbl",
            num_trades=NUM_TRADES,
            **DIMS,
        )
    )
    return out.read_bytes()


class TestWorkerCountInvariance:
    @pytest.mark.parametrize("workers", [1, 2, 3, 5])
    def test_parallel_worker_counts_match_serial(self, tmp_path: Path, workers: int) -> None:
        assert _parallel_bytes(tmp_path, workers) == _serial_bytes(tmp_path)

    def test_repeated_parallel_runs_are_identical(self, tmp_path: Path) -> None:
        first = _parallel_bytes(tmp_path, 3)
        assert _parallel_bytes(tmp_path, 3, tag="-b") == first
        assert _parallel_bytes(tmp_path, 3, tag="-c") == first

    def test_rows_follow_trade_identity_order(self, tmp_path: Path) -> None:
        content = _serial_bytes(tmp_path).decode("utf-8").splitlines()
        assert len(content) == NUM_TRADES
        assert [int(line.split("|")[0]) for line in content] == list(range(1, NUM_TRADES + 1))


class TestPartitionIndependence:
    def test_split_chunks_concatenate_to_whole(self, tmp_path: Path) -> None:
        gen = _make_generator(tmp_path, "chunks", max_workers=2)
        whole = gen._generate_trade_chunk(1, NUM_TRADES + 1, **DIMS)
        split = gen._generate_trade_chunk(1, 501, **DIMS) + gen._generate_trade_chunk(501, NUM_TRADES + 1, **DIMS)
        assert split == whole

    def test_chunk_rows_match_serial_file(self, tmp_path: Path) -> None:
        gen = _make_generator(tmp_path, "chunk-rows", max_workers=2)
        chunk = gen._generate_trade_chunk(1, 11, **DIMS)
        lines = _serial_bytes(tmp_path).decode("utf-8").splitlines()[:10]
        assert [[str(value) for value in row] == line.split("|") for row, line in zip(chunk, lines)] == [True] * 10


class TestSeedContract:
    def test_different_seed_produces_different_dataset(self, tmp_path: Path) -> None:
        assert _parallel_bytes(tmp_path, 2, seed=SEED + 1) != _serial_bytes(tmp_path)

    def test_no_process_global_rng_mutation(self, tmp_path: Path) -> None:
        before = random.getstate()
        try:
            _serial_bytes(tmp_path)
            _parallel_bytes(tmp_path, 4)
        finally:
            assert random.getstate() == before

    def test_seed_and_version_recorded_in_metadata(self, tmp_path: Path) -> None:
        gen = _make_generator(tmp_path, "manifest", max_workers=2, seed=SEED)
        gen.generate_data(["FactTrade"])
        manifest = json.loads((gen.output_dir / "_datagen_manifest.json").read_text(encoding="utf-8"))
        assert manifest["seed"] == SEED
        assert manifest["generation_algorithm_version"] == FACT_TRADE_GENERATION_ALGORITHM_VERSION
        config = gen.get_generation_config()
        assert config["generation_seed"] == SEED
        assert config["generation_algorithm_version"] == FACT_TRADE_GENERATION_ALGORITHM_VERSION

    def test_legacy_default_seed_is_documented_value(self, tmp_path: Path) -> None:
        gen = TPCDIDataGenerator(scale_factor=1.0, output_dir=tmp_path, quiet=True)
        assert gen.generation_seed == 42


class TestPublicRequestPlumbing:
    def test_config_carries_generation_seed(self, tmp_path: Path) -> None:
        assert TPCDIConfig(output_dir=tmp_path).generation_seed == 42
        assert TPCDIConfig(output_dir=tmp_path, generation_seed=9).generation_seed == 9

    def test_benchmark_forwards_configured_and_explicit_seeds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from benchbox.core.tpcdi.benchmark import TPCDIBenchmark

        config = TPCDIConfig(scale_factor=0.01, output_dir=tmp_path / "bench", generation_seed=9)
        benchmark = TPCDIBenchmark(config=config, quiet=True)
        assert benchmark.data_generator.generation_seed == 9

        calls: dict[str, object] = {}

        def _generate_stub(tables: object) -> dict:
            calls["tables"] = tables
            return {}

        monkeypatch.setattr(benchmark.data_generator, "generate_data", _generate_stub)
        benchmark.generate_data(tables=["DimDate"], seed=11)
        assert benchmark.data_generator.generation_seed == 9
        assert calls["tables"] == ["DimDate"]


