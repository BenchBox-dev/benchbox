from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any

import pytest

from benchbox.core.benchmark_loader import get_core_benchmark_class
from benchbox.core.benchmark_registry import get_all_benchmarks
from benchbox.core.benchmark_table_ownership import (
    MOONCAKE_STAGING_PREFIX,
    table_providers,
    tables_owned_by_benchmark_family,
)
from benchbox.platforms.base import PlatformAdapter

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class _SharedServerAdapter(PlatformAdapter):
    def __init__(self, server_tables: set[str]):
        super().__init__()
        self.server_tables = server_tables
        self.dropped = 0
        self.force_recreate = False

    @staticmethod
    def add_cli_arguments(parser) -> None:
        return None

    @classmethod
    def from_config(cls, config: dict[str, Any]):
        return cls(set())

    @property
    def platform_name(self) -> str:
        return "SharedServer"

    def get_target_dialect(self) -> str | None:
        return None

    def create_connection(self, **connection_config):
        return object()

    def create_schema(self, benchmark, connection):
        return 0.0

    def load_data(self, benchmark, connection, data_dir):
        return {}, 0.0, None

    def configure_for_benchmark(self, connection, benchmark_type):
        return None

    def execute_query(self, connection, query, query_id, **kwargs):
        return {}

    def apply_platform_optimizations(self, platform_config, connection):
        return None

    def apply_constraint_configuration(self, primary_key_config, foreign_key_config, connection):
        return None

    def get_database_path(self, **connection_config) -> str | None:
        return None

    def check_server_database_exists(self, **connection_config) -> bool:
        return True

    def _create_direct_connection(self, **connection_config):
        return object()

    def close_connection(self, connection) -> None:
        return None

    def _get_existing_tables(self, connection) -> list[str]:
        return sorted(self.server_tables)

    def _validate_database_tunings(self, **connection_config):
        return SimpleNamespace(is_valid=True, errors=[], warnings=[])

    def drop_database(self, **connection_config) -> None:
        self.dropped += 1
        self.server_tables.clear()


def _benchmark(name: str, scale_factor: float):
    return get_core_benchmark_class(name)(scale_factor=scale_factor)


def _tables_of(name: str, scale_factor: float = 0.01) -> set[str]:
    return {t.lower() for t in _benchmark(name, scale_factor).get_schema()}


def _run_on(adapter: _SharedServerAdapter, name: str, scale_factor: float) -> None:
    adapter.benchmark_instance = _benchmark(name, scale_factor)
    adapter._existing_db_decided = False
    adapter.handle_existing_database(database="shared")


def test_transaction_primitives_replaces_the_tables_write_primitives_left_in_the_shared_database():
    write_tables = _tables_of("write_primitives") | {"orders_stage", "lineitem_stage", "write_primitives_setup_lock"}
    adapter = _SharedServerAdapter(set(write_tables))

    _run_on(adapter, "transaction_primitives", 0.01)

    assert adapter.dropped == 1
    assert adapter.server_tables == set()


def test_tpcds_obt_replaces_the_tpcds_tables_in_the_shared_database():
    adapter = _SharedServerAdapter(_tables_of("tpcds") | {f"{MOONCAKE_STAGING_PREFIX}store_sales"})

    _run_on(adapter, "tpcds_obt", 1.0)

    assert adapter.dropped == 1
    assert adapter.server_tables == set()


def test_tpch_replaces_leftover_write_primitives_tables_when_its_own_data_is_unusable():
    adapter = _SharedServerAdapter(_tables_of("write_primitives") - {"lineitem"})

    _run_on(adapter, "tpch", 0.01)

    assert adapter.dropped == 1


@pytest.mark.parametrize(
    ("benchmark_id", "scale_factor", "owned"),
    [
        ("transaction_primitives", 0.01, lambda: _tables_of("write_primitives")),
        ("tpcds_obt", 1.0, lambda: _tables_of("tpcds")),
    ],
)
def test_a_table_benchbox_did_not_create_still_blocks_the_drop(benchmark_id, scale_factor, owned):
    adapter = _SharedServerAdapter(owned() | {"customer_notes"})

    with pytest.raises(RuntimeError, match=r"refusing to drop the database.*customer_notes"):
        _run_on(adapter, benchmark_id, scale_factor)

    assert adapter.dropped == 0
    assert "customer_notes" in adapter.server_tables


def test_only_the_unowned_tables_are_named_in_the_refusal():
    adapter = _SharedServerAdapter(_tables_of("tpcds") | {"audit_log"})

    with pytest.raises(RuntimeError) as error:
        _run_on(adapter, "tpcds_obt", 1.0)

    assert "audit_log" in str(error.value)
    assert "store_sales" not in str(error.value)


def test_ownership_is_scoped_to_the_database_family():
    assert "write_ops_log" in tables_owned_by_benchmark_family("tpch")
    assert "write_ops_log" not in tables_owned_by_benchmark_family("tpcds")
    assert "tpcds_sales_returns_obt" in tables_owned_by_benchmark_family("tpcds")
    assert "tpcds_sales_returns_obt" not in tables_owned_by_benchmark_family("tpch")


def test_every_benchmark_sharing_a_data_source_database_has_an_ownership_entry():
    providers = table_providers()
    sharing = {
        name
        for name, metadata in get_all_benchmarks().items()
        if str(metadata.get("data_source") or "").lower() in {"tpch", "tpcds"}
    }

    assert sharing <= set(providers)


def test_ownership_covers_what_each_sibling_benchmark_reports():
    for name in ("read_primitives", "write_primitives", "transaction_primitives", "ai_primitives", "tpch"):
        assert _tables_of(name) <= tables_owned_by_benchmark_family("tpch"), name
    assert _tables_of("tpcds_obt", 1.0) <= tables_owned_by_benchmark_family("tpcds")


def test_a_failing_ownership_lookup_keeps_every_extra_table_foreign(monkeypatch):
    def broken(family: str):
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr("benchbox.core.benchmark_table_ownership.tables_owned_by_benchmark_family", broken)
    adapter = _SharedServerAdapter(_tables_of("tpcds") | {"audit_log"})

    with pytest.raises(RuntimeError, match="refusing to drop the database.*audit_log"):
        _run_on(adapter, "tpcds_obt", 1.0)

    assert adapter.dropped == 0
