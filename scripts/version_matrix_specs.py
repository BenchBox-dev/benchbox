from __future__ import annotations

import random
import re
from dataclasses import dataclass

PRERELEASE_RE = re.compile(r"\.(?:dev\d+|rc\d+|a\d+|b\d+)$", re.IGNORECASE)

SETUP_ENGINES = {"A": "default", "B": "in-memory", "C": "streaming"}
GIB = 1024**3


@dataclass(frozen=True)
class Cell:
    benchmark: str
    version: str
    setup: str

    @property
    def engine(self) -> str:
        return SETUP_ENGINES[self.setup]

    @property
    def cell_id(self) -> str:
        return f"{self.benchmark}/{self.version}/{self.setup}"


def is_prerelease(version: str) -> bool:
    return bool(PRERELEASE_RE.search(version))


def scale_token(scale: float) -> str:
    return str(int(scale)) if scale == int(scale) else str(scale).replace(".", "")


def scale_argument(scale: float) -> str:
    return str(int(scale) if scale == int(scale) else scale)


def version_token(version: str) -> str:
    return version.replace(".", "_")


@dataclass(frozen=True)
class MatrixSpec:
    engine: str
    display_name: str
    platform: str
    driver_package: str
    versions: tuple[str, ...]
    benchmarks: tuple[tuple[str, float], ...]
    repetitions: int
    compression: str
    required_validation: str
    cleared_database_suffixes: tuple[str, ...] = ()
    driver_option_for_prereleases: bool = False
    setup_versions: tuple[tuple[str, tuple[str, ...]], ...] = ()
    setup_engines: tuple[tuple[str, str], ...] = ()
    rounds: int | None = None
    iterations: int | None = None
    thread_count: int | None = None
    cell_timeout_s: float | None = None
    rss_limit_gib: float | None = None
    swap_limit_gib: float | None = None
    safety_swap_gib: float | None = None
    reference_version: str | None = None
    mover_effect: float = 0.10
    qualification_sha256: str | None = None
    expected_queries: tuple[tuple[str, int], ...] = ()
    evidence_validations: tuple[str, ...] = ()

    @property
    def baseline_version(self) -> str:
        return self.versions[0]

    @property
    def benchmark_ids(self) -> tuple[str, ...]:
        return tuple(benchmark for benchmark, _ in self.benchmarks)

    @property
    def scales(self) -> dict[str, float]:
        return dict(self.benchmarks)

    @property
    def analysis_stem(self) -> str:
        return f"{self.engine}-version-matrix"

    @property
    def speedup_field(self) -> str:
        return f"speedup_vs_{self.baseline_version}"

    @property
    def expected_benchmark_runs(self) -> int:
        return len(self.benchmarks) + len(self.versions) * len(self.benchmarks) * (1 + self.repetitions)

    def driver_platform_options(self, version: str | None) -> list[str]:
        if version is None or (is_prerelease(version) and not self.driver_option_for_prereleases):
            return []
        return ["--platform-option", f"driver_version={version}"]

    def bundle_filename(self, benchmark: str, version: str) -> str:
        scale = scale_token(self.scales[benchmark])
        return f"{benchmark}_sf{scale}_{self.engine}_v{version_token(version)}_median.json"

    @property
    def effective_rounds(self) -> int:
        return self.rounds if self.rounds is not None else self.repetitions

    @property
    def setups(self) -> dict[str, tuple[str, ...]]:
        return dict(self.setup_versions)

    @property
    def setup_engine_map(self) -> dict[str, str]:
        return dict(self.setup_engines)

    @property
    def query_counts(self) -> dict[str, int]:
        return dict(self.expected_queries)

    def engine_for_setup(self, setup: str) -> str:
        return dict(self.setup_engines).get(setup, "default")

    def cells(self) -> list[Cell]:
        return [
            Cell(benchmark, version, setup)
            for benchmark in self.benchmark_ids
            for setup, versions in self.setup_versions
            for version in versions
        ]

    @property
    def expected_invocations(self) -> int:
        if not self.setup_versions:
            return self.expected_benchmark_runs
        per_benchmark = sum(len(versions) for _, versions in self.setup_versions)
        return self.effective_rounds * per_benchmark * len(self.benchmarks)

    def snapshot(self) -> dict[str, object]:
        return {
            "platform": self.platform,
            "versions": list(self.versions),
            "setups": {setup: list(versions) for setup, versions in self.setup_versions},
            "engines": dict(self.setup_engines),
            "benchmarks": [{"id": name, "scale": scale} for name, scale in self.benchmarks],
            "expected_queries": dict(self.expected_queries),
            "rounds": self.effective_rounds,
            "iterations": self.iterations,
            "thread_count": self.thread_count,
            "cell_timeout_s": self.cell_timeout_s,
            "rss_limit_gib": self.rss_limit_gib,
            "swap_limit_gib": self.swap_limit_gib,
            "safety_swap_gib": self.safety_swap_gib,
            "reference_version": self.reference_version,
            "mover_effect": self.mover_effect,
            "qualification_sha256": self.qualification_sha256,
        }

    def accepts_without_evidence(self, validation: str | None) -> bool:
        return validation == self.required_validation

    def requires_evidence(self, validation: str | None) -> bool:
        return validation in self.evidence_validations


def round_order(spec: MatrixSpec, shuffle_seed: int, round_index: int) -> list[Cell]:
    cells = spec.cells()
    random.Random(f"{shuffle_seed}:{round_index}").shuffle(cells)
    return cells


DUCKDB = MatrixSpec(
    engine="duckdb",
    display_name="DuckDB",
    platform="duckdb",
    driver_package="duckdb",
    versions=("1.0.0", "1.1.3", "1.2.2", "1.3.2", "1.4.4", "1.5.5", "1.6.0.dev365"),
    benchmarks=(("tpch", 10.0), ("tpcds", 10.0), ("clickbench", 10.0), ("ssb", 10.0)),
    repetitions=3,
    compression="zstd:3",
    required_validation="passed",
    cleared_database_suffixes=(".duckdb", ".wal"),
)

POLARS = MatrixSpec(
    engine="polars",
    display_name="Polars",
    platform="polars-df",
    driver_package="polars",
    versions=("1.31.0", "1.35.2", "1.40.1", "1.44.2", "2.0.0"),
    benchmarks=(("tpch", 10.0), ("tpcds", 10.0), ("clickbench", 10.0), ("ssb", 10.0)),
    repetitions=3,
    compression="zstd:3",
    required_validation="passed",
    setup_versions=(
        ("A", ("1.31.0", "1.35.2", "1.40.1", "1.44.2", "2.0.0")),
        ("B", ("2.0.0",)),
        ("C", ("1.31.0", "1.40.1", "1.44.2", "2.0.0")),
    ),
    setup_engines=(("A", "default"), ("B", "in-memory"), ("C", "streaming")),
    rounds=3,
    iterations=1,
    thread_count=10,
    cell_timeout_s=1800.0,
    rss_limit_gib=14.0,
    swap_limit_gib=1.0,
    safety_swap_gib=6.0,
    reference_version="1.44.2",
    mover_effect=0.10,
    qualification_sha256="da8ea047afdbc880177f6a295dc8d0e9afd1b95a3f044d906e9c6a26b43d2580",
    expected_queries=(("tpch", 22), ("tpcds", 103), ("clickbench", 43), ("ssb", 13)),
    evidence_validations=("uncertain", "not_run"),
)

SPECS: dict[str, MatrixSpec] = {spec.engine: spec for spec in (DUCKDB, POLARS)}
