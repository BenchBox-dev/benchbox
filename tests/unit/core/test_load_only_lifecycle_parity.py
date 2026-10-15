from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from benchbox.core.runner.runner import ValidationOptions, _execute_load_only_mode
from benchbox.core.tuning.applied_ledger import AppliedTuningLedger
from benchbox.core.tuning.interface import TableTuning, TuningColumn, UnifiedTuningConfiguration

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _StubBenchmark:
    name = "parity-probe"

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.captured: dict = {}

    def get_create_tables_sql(self, dialect: str = "standard", tuning_config=None) -> str:
        pk = ""
        if tuning_config is not None and tuning_config.primary_keys.enabled:
            pk = ", PRIMARY KEY (a)"
        return f"CREATE TABLE t (a INTEGER, b VARCHAR{pk})"

    @property
    def tables(self) -> dict:
        return {"t": self.output_dir / "t.parquet"}

    def create_enhanced_benchmark_result(self, **kwargs):
        self.captured = dict(kwargs)
        return SimpleNamespace(execution_id="parity-probe", **kwargs)


def _benchmark_config() -> SimpleNamespace:
    return SimpleNamespace(name="parity-probe", scale_factor=0.01)


def _tuned_sort_config() -> UnifiedTuningConfiguration:
    config = UnifiedTuningConfiguration()
    config.disable_all_constraints()
    config.table_tunings["t"] = TableTuning(
        table_name="t",
        sorting=[TuningColumn(name="a", type="INTEGER", order=1)],
    )
    return config


def _seed_parquet(data_dir: Path) -> None:
    duckdb = pytest.importorskip("duckdb")
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(":memory:")
    conn.execute("CREATE TABLE seed (a INTEGER, b VARCHAR)")
    conn.execute("INSERT INTO seed VALUES (2, 'y'), (1, 'x'), (3, 'z')")
    conn.execute(f"COPY seed TO '{data_dir}/t.parquet' (FORMAT PARQUET)")
    conn.close()


def _duckdb_adapter(database_path: Path, config: UnifiedTuningConfiguration | None):
    from benchbox.platforms.duckdb import DuckDBAdapter

    adapter = DuckDBAdapter(database_path=str(database_path))
    adapter.tuning_enabled = config is not None
    adapter.unified_tuning_configuration = config
    adapter._applied_tuning_ledger = AppliedTuningLedger()
    return adapter


def _ledger_statements(adapter) -> list[tuple[str, str | None, str]]:
    return sorted(
        (statement.phase, statement.mechanism, statement.statement)
        for statement in adapter._applied_tuning_ledger.statements
        if statement.status == "executed"
    )


class TestLoadOnlyLifecycleParity:
    @pytest.fixture(autouse=True)
    def _require_duckdb(self):
        pytest.importorskip("duckdb")

    def test_load_only_tuned_run_matches_full_run_ledger(self, tmp_path: Path) -> None:
        from benchbox.core.results.schema import build_tuning_payload

        _seed_parquet(tmp_path / "data")
        config = _tuned_sort_config()

        full_adapter = _duckdb_adapter(tmp_path / "full.duckdb", config)
        full_benchmark = _StubBenchmark(tmp_path / "data")
        full_connection = full_adapter.create_connection()
        try:
            full_adapter._setup_fresh_database_phases(full_benchmark, full_connection, config)
            full_adapter._fold_layout_operations_into_ledger()
            full_adapter._reconcile_requested_tuning(config)
        finally:
            full_connection.close()

        load_adapter = _duckdb_adapter(tmp_path / "load.duckdb", config)
        load_benchmark = _StubBenchmark(tmp_path / "data")
        result, _postload = _execute_load_only_mode(
            benchmark=load_benchmark,
            benchmark_config=_benchmark_config(),
            adapter=load_adapter,
            platform_config={},
            validation_opts=ValidationOptions(),
            table_mode="native",
        )

        assert _ledger_statements(load_adapter) == _ledger_statements(full_adapter)
        assert result.tunings_applied is not None
        assert result.applied_tuning_ledger is not None
        payload = build_tuning_payload(result)
        assert payload is not None

    def test_load_only_tuned_run_is_refused_without_full_lifecycle(self, tmp_path: Path) -> None:
        config = _tuned_sort_config()
        adapter = SimpleNamespace(
            platform_name="legacy",
            tuning_enabled=True,
            unified_tuning_configuration=config,
            get_effective_tuning_configuration=lambda: config,
            create_connection=Mock(return_value="connection"),
            close_connection=Mock(),
        )
        benchmark = _StubBenchmark(tmp_path)

        with pytest.raises(RuntimeError, match="Load-only mode cannot run a tuned configuration"):
            _execute_load_only_mode(
                benchmark=benchmark,
                benchmark_config=_benchmark_config(),
                adapter=adapter,
                platform_config={},
                validation_opts=ValidationOptions(),
                table_mode="native",
            )

    def test_load_only_untuned_run_carries_no_tuning(self, tmp_path: Path) -> None:
        adapter = SimpleNamespace(
            platform_name="duckdb",
            tuning_enabled=False,
            create_connection=Mock(return_value="connection"),
            close_connection=Mock(),
            create_schema=Mock(return_value=0.25),
            load_data=Mock(return_value=({"t": 3}, 0.5, None)),
            _calculate_data_size=Mock(return_value=1.5),
        )
        benchmark = _StubBenchmark(tmp_path)

        result, _postload = _execute_load_only_mode(
            benchmark=benchmark,
            benchmark_config=_benchmark_config(),
            adapter=adapter,
            platform_config={},
            validation_opts=ValidationOptions(),
            table_mode="native",
        )

        assert result.tunings_applied is None
        assert result.applied_tuning_ledger is None
        assert result.tuning_validation_status is None
