from __future__ import annotations

from pathlib import Path

import pytest

from benchbox.core.benchmark_registry import (
    get_all_benchmarks,
    get_presort_table_configs,
    presort_capable_benchmarks,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REPO_ROOT = Path(__file__).resolve().parents[3]


class TestRegistryOwnsPresortDefaults:
    def test_tpch_sorts_lineitem_by_ship_date(self):
        assert get_presort_table_configs("tpch") == {"lineitem": [{"name": "l_shipdate", "order": "asc"}]}

    def test_tpcds_sorts_store_sales_by_sold_date(self):
        assert get_presort_table_configs("tpcds") == {"store_sales": [{"name": "ss_sold_date_sk", "order": "asc"}]}

    def test_capability_is_declared_not_hardcoded(self):
        assert presort_capable_benchmarks() == ("tpcds", "tpch")

    def test_a_benchmark_without_defaults_returns_none(self):
        assert get_presort_table_configs("ssb") is None

    def test_an_unknown_benchmark_returns_none_rather_than_raising(self):
        assert get_presort_table_configs("not_a_benchmark") is None

    def test_lookup_is_case_insensitive(self):
        assert get_presort_table_configs("TPCH") == get_presort_table_configs("tpch")

    def test_callers_cannot_mutate_the_registry_through_the_accessor(self):
        first = get_presort_table_configs("tpch")
        first["lineitem"] = "clobbered"

        assert get_presort_table_configs("tpch") == {"lineitem": [{"name": "l_shipdate", "order": "asc"}]}

    def test_every_declared_config_names_a_real_benchmark(self):
        benchmarks = get_all_benchmarks()

        for benchmark_id in presort_capable_benchmarks():
            assert benchmark_id in benchmarks


class TestCliNoLongerHardcodesSortKeys:
    def test_run_command_carries_no_benchmark_column_literals(self):
        source = (REPO_ROOT / "benchbox/cli/commands/run.py").read_text(encoding="utf-8")

        assert "l_shipdate" not in source
        assert "ss_sold_date_sk" not in source

    def test_run_command_reads_the_registry(self):
        source = (REPO_ROOT / "benchbox/cli/commands/run.py").read_text(encoding="utf-8")

        assert "get_presort_table_configs" in source


class TestPresortPayloadResolution:
    @staticmethod
    def _resolve(benchmark, presort, tuning_payload=None):
        from benchbox.cli.commands.run import _resolve_data_organization_payload

        return _resolve_data_organization_payload(benchmark, presort, tuning_payload)

    @pytest.mark.parametrize(
        ("presort", "output_format"),
        [("parquet-sorted", "parquet"), ("delta-sorted", "delta"), ("iceberg-sorted", "iceberg")],
    )
    def test_tpch_defaults_resolve_per_output_format(self, presort, output_format):
        payload = self._resolve("tpch", presort)

        assert payload["output_format"] == output_format
        assert payload["table_configs"] == {"lineitem": [{"name": "l_shipdate", "order": "asc"}]}

    def test_an_unsupported_benchmark_still_raises(self):
        with pytest.raises(ValueError, match="tpcds, tpch"):
            self._resolve("ssb", "parquet-sorted")

    def test_a_non_sorted_presort_value_passes_the_tuning_payload_through(self):
        tuning = {"table_configs": {"x": []}}

        assert self._resolve("tpch", None, tuning) is tuning

    def test_an_explicit_tuning_payload_wins_over_registry_defaults(self):
        tuning = {"table_configs": {"custom": []}}

        assert self._resolve("tpch", "parquet-sorted", tuning) is tuning

    def test_delta_presort_overrides_the_output_format_on_a_tuning_payload(self):
        payload = self._resolve("tpch", "delta-sorted", {"table_configs": {"custom": []}})

        assert payload["output_format"] == "delta"
        assert payload["table_configs"] == {"custom": []}


