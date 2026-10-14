from __future__ import annotations

import copy
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from _project.scripts.explorer_pipeline.contract import EXPLORER_READ_MODEL_VERSION
from _project.scripts.explorer_pipeline.duckdb_builder import DuckDBSnapshotBuilder
from _project.scripts.explorer_pipeline.models import ManifestEntry
from benchbox.core.cost.models import DeploymentMetadata, NormalizedCost
from benchbox.core.cost.pricing import PRICING_VERSION

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _make_entry(**overrides) -> ManifestEntry:
    defaults: dict = {
        "result_id": "tpch-duckdb-sf0.1-20260315-abcd1234",
        "benchmark": "tpch",
        "scale_factor": 0.1,
        "platform": "duckdb",
        "driver_version": "1.2.0",
        "run_date": "2026-03-15",
        "power_score": 1234.56,
        "total_duration_s": 45.0,
        "query_count": 2,
        "trust_label": "maintainer-run",
        "visibility": "public-curated",
    }
    defaults.update(overrides)
    return ManifestEntry(**defaults)


class TestDuckDBSnapshotBuilder:
    def test_creates_duckdb_file(self, tmp_path: Path) -> None:
        builder = DuckDBSnapshotBuilder()
        out = tmp_path / "results.duckdb"
        builder.build([_make_entry()], out)

        assert out.exists()
        assert out.stat().st_size > 0

    def test_failed_rebuild_preserves_last_known_good_snapshot(self, tmp_path: Path, monkeypatch) -> None:
        builder = DuckDBSnapshotBuilder()
        out = tmp_path / "results.duckdb"
        builder.build([_make_entry()], out)
        before = out.read_bytes()

        def fail_metadata(*_args, **_kwargs):
            raise RuntimeError("simulated interrupted rebuild")

        monkeypatch.setattr(builder, "_create_metadata", fail_metadata)
        with pytest.raises(RuntimeError, match="simulated interrupted rebuild"):
            builder.build([_make_entry(result_id="new-result")], out)

        assert out.read_bytes() == before
        assert not list(tmp_path.glob(".results.duckdb.*.tmp"))

    def test_writes_read_model_version_metadata(self, tmp_path: Path) -> None:
        builder = DuckDBSnapshotBuilder()
        out = tmp_path / "results.duckdb"
        builder.build([_make_entry()], out)

        with duckdb.connect(str(out), read_only=True) as con:
            version = con.execute("SELECT read_model_version FROM metadata").fetchone()[0]

        assert version == EXPLORER_READ_MODEL_VERSION

    def test_metadata_creation_remains_single_row_when_reused(self, tmp_path: Path) -> None:
        builder = DuckDBSnapshotBuilder()
        out = tmp_path / "results.duckdb"

        with duckdb.connect(str(out)) as con:
            builder._create_metadata(con)
            builder._create_metadata(con)
            rows = con.execute("SELECT read_model_version FROM metadata").fetchall()

        assert rows == [(EXPLORER_READ_MODEL_VERSION,)]

    def test_results_table_has_correct_columns(self, tmp_path: Path) -> None:
        builder = DuckDBSnapshotBuilder()
        out = tmp_path / "results.duckdb"
        builder.build([_make_entry()], out)

        with duckdb.connect(str(out), read_only=True) as con:
            cols = {row[0] for row in con.execute("DESCRIBE results").fetchall()}

        expected = {
            "result_id",
            "benchmark",
            "scale_factor",
            "platform",
            "driver_version",
            "run_date",
            "power_score",
            "total_duration_s",
            "geomean_ms",
            "display_geomean_ms",
            "query_count",
            "logical_query_count",
            "has_display_timing",
            "valid_query_count",
            "missing_query_count",
            "zero_timing_count",
            "display_exclusion_reason",
            "comparison_exclusion_reason",
            "ranking_exclusion_reason",
            "trust_label",
            "visibility",
            "platform_version",
            "execution_mode",
            "tuning_mode",
            "tuning_hash",
            "test_type",
            "validation_status",
            "cost_usd",
            "normalized_cost_usd",
            "cost_model_version",
            "cost_model_source",
            "cost_scope",
            "cost_status",
            "billing_unit",
            "pricing_region",
            "deployment_class",
            "cloud_provider",
            "cloud_region",
            "instance_or_warehouse",
            "instance_type",
            "warehouse_size",
            "node_count",
            "cluster_size",
            "storage_format",
            "storage_tier",
        }
        assert expected.issubset(cols)

    def test_row_count_matches_entries(self, tmp_path: Path) -> None:
        entries = [_make_entry(result_id=f"id-{i}", platform=f"platform-{i}") for i in range(3)]
        builder = DuckDBSnapshotBuilder()
        out = tmp_path / "results.duckdb"
        builder.build(entries, out)

        with duckdb.connect(str(out), read_only=True) as con:
            count = con.execute("SELECT COUNT(*) FROM results").fetchone()[0]

        assert count == 3

    def test_empty_entries_creates_empty_table(self, tmp_path: Path) -> None:
        builder = DuckDBSnapshotBuilder()
        out = tmp_path / "results.duckdb"
        builder.build([], out)

        with duckdb.connect(str(out), read_only=True) as con:
            count = con.execute("SELECT COUNT(*) FROM results").fetchone()[0]

        assert count == 0

    def test_nullable_fields_stored(self, tmp_path: Path) -> None:
        entry = _make_entry(driver_version=None, power_score=None)
        builder = DuckDBSnapshotBuilder()
        out = tmp_path / "results.duckdb"
        builder.build([entry], out)

        with duckdb.connect(str(out), read_only=True) as con:
            row = con.execute("SELECT driver_version, power_score FROM results").fetchone()

        assert row[0] is None
        assert row[1] is None

    def test_extended_fields_round_trip(self, tmp_path: Path) -> None:
        entry = _make_entry(
            geomean_ms=5656.85,
            platform_version="v3",
            execution_mode="sql",
            tuning_mode="tuned",
            tuning_hash="abcd1234",
            test_type="power",
            validation_status="passed",
            cost_usd=0.42,
            deployment_class="cloud",
            cloud_provider="aws",
            cloud_region="us-east-1",
            instance_or_warehouse="XSMALL",
            storage_format="snowflake_native",
            normalized_cost=NormalizedCost(
                normalized_cost_usd="0.42",
                cost_model_version="2026.05.0",
                cost_model_source="benchbox.core.cost.pricing",
                cost_scope="compute_only",
                cost_status="normalized",
                billing_unit="instance_hour",
                pricing_region="us-east-1",
                deployment=DeploymentMetadata(
                    cloud_provider="cost-provider",
                    cloud_region="cost-region",
                    instance_type="r7i.4xlarge",
                    warehouse_size="large",
                    node_count=2,
                    cluster_size="2x",
                    storage_format="cost-format",
                    storage_tier="s3-standard",
                ),
            ),
        )
        builder = DuckDBSnapshotBuilder()
        out = tmp_path / "results.duckdb"
        builder.build([entry], out)

        with duckdb.connect(str(out), read_only=True) as con:
            row = con.execute(
                "SELECT geomean_ms, platform_version, execution_mode, tuning_mode,"
                " tuning_hash, test_type, validation_status, cost_usd,"
                " normalized_cost_usd, cost_model_version, cost_model_source,"
                " cost_scope, cost_status, billing_unit, pricing_region,"
                " deployment_class, cloud_provider, cloud_region, instance_or_warehouse,"
                " instance_type, warehouse_size, node_count, cluster_size,"
                " storage_format, storage_tier FROM results"
            ).fetchone()

        assert row[0] == pytest.approx(5656.85)
        assert row[1] == "v3"
        assert row[2] == "sql"
        assert row[3] == "tuned"
        assert row[4] == "abcd1234"
        assert row[5] == "power"
        assert row[6] == "passed"
        assert row[7] == pytest.approx(0.42)
        assert row[8] == pytest.approx(0.42)
        assert row[9] == "2026.05.0"
        assert row[10] == "benchbox.core.cost.pricing"
        assert row[11] == "compute_only"
        assert row[12] == "normalized"
        assert row[13] == "instance_hour"
        assert row[14] == "us-east-1"
        assert row[15] == "cloud"
        assert row[16] == "aws"
        assert row[17] == "us-east-1"
        assert row[18] == "XSMALL"
        assert row[19] == "r7i.4xlarge"
        assert row[20] == "large"
        assert row[21] == 2
        assert row[22] == "2x"
        assert row[23] == "snowflake_native"
        assert row[24] == "s3-standard"

    def test_estimated_cost_round_trip(self, tmp_path: Path) -> None:
        entry = _make_entry(
            normalized_cost=NormalizedCost(
                normalized_cost_usd=None,
                cost_model_version=PRICING_VERSION,
                cost_model_source="benchbox.core.cost.pricing",
                cost_scope="compute_only",
                cost_status="estimated",
                billing_unit="credit",
                pricing_region="us-east-2",
                deployment=DeploymentMetadata(
                    cloud_provider="aws",
                    cloud_region="us-east-2",
                    warehouse_size="X-Small",
                ),
                estimated_cost_usd="0.000105",
                cost_estimate_method="sum of per-query median runtime hours",
                cost_estimate_source="https://www.snowflake.com/legal-files/CreditConsumptionTable.pdf",
                cost_estimate_pricing_date="2026-10-09",
                cost_estimate_assumptions=("Timing-derived reference scenario.",),
            ),
        )
        builder = DuckDBSnapshotBuilder()
        out = tmp_path / "results.duckdb"
        builder.build([entry], out)

        with duckdb.connect(str(out), read_only=True) as con:
            row = con.execute(
                "SELECT cost_status, normalized_cost_usd, estimated_cost_usd, cost_estimate_method,"
                " cost_estimate_source, cost_estimate_pricing_date, cost_estimate_assumptions FROM results"
            ).fetchone()

        assert row[0] == "estimated"
        assert row[1] is None
        assert row[2] == pytest.approx(0.000105)
        assert row[3] == "sum of per-query median runtime hours"
        assert row[4] == "https://www.snowflake.com/legal-files/CreditConsumptionTable.pdf"
        assert row[5] == "2026-10-09"
        assert row[6] == '["Timing-derived reference scenario."]'

    def test_extended_fields_null_when_absent(self, tmp_path: Path) -> None:
        entry = _make_entry()
        builder = DuckDBSnapshotBuilder()
        out = tmp_path / "results.duckdb"
        builder.build([entry], out)

        with duckdb.connect(str(out), read_only=True) as con:
            row = con.execute(
                "SELECT geomean_ms, platform_version, execution_mode, tuning_mode,"
                " tuning_hash, test_type, validation_status, cost_usd FROM results"
            ).fetchone()

        assert row is not None and all(v is None for v in row)

    def test_normalized_cost_columns_default_to_unavailable(self, tmp_path: Path) -> None:
        entry = _make_entry()
        builder = DuckDBSnapshotBuilder()
        out = tmp_path / "results.duckdb"
        builder.build([entry], out)

        with duckdb.connect(str(out), read_only=True) as con:
            row = con.execute(
                "SELECT normalized_cost_usd, cost_model_version, cost_model_source,"
                " cost_scope, cost_status, billing_unit, pricing_region,"
                " deployment_class, cloud_provider, cloud_region, instance_or_warehouse,"
                " storage_format, instance_type, warehouse_size, node_count,"
                " cluster_size, storage_tier FROM results"
            ).fetchone()

        assert row is not None
        assert row[:7] == (
            None,
            PRICING_VERSION,
            "benchbox.core.cost.pricing",
            "compute_only",
            "unavailable",
            "unknown",
            "unknown",
        )
        assert all(value is None for value in row[7:])

    @pytest.mark.parametrize(
        ("field", "value", "message"),
        [
            ("cost_status", "bogus", "must be one of"),
            ("cost_model_version", "", "must be a non-empty string"),
        ],
    )
    def test_malformed_typed_cost_rejected_by_legacy_builder(
        self,
        tmp_path: Path,
        field: str,
        value: str,
        message: str,
    ) -> None:
        cost = NormalizedCost(
            normalized_cost_usd="0.42",
            cost_model_version="2026.05.0",
            cost_model_source="benchbox.core.cost.pricing",
            cost_scope="compute_only",
            cost_status="normalized",
            billing_unit="instance_hour",
            pricing_region="us-east-1",
        )
        object.__setattr__(cost, field, value)
        entry = _make_entry(normalized_cost=cost)

        builder = DuckDBSnapshotBuilder()
        with pytest.raises(ValueError, match=message):
            builder.build([entry], tmp_path / "results.duckdb")

    def test_non_finite_normalized_cost_usd_rejected(
        self,
        tmp_path: Path,
    ) -> None:
        cost = NormalizedCost(
            normalized_cost_usd="0.42",
            cost_model_version="2026.05.0",
            cost_model_source="benchbox.core.cost.pricing",
            cost_scope="compute_only",
            cost_status="normalized",
            billing_unit="instance_hour",
            pricing_region="us-east-1",
        )
        non_finite = copy.copy(cost)
        object.__setattr__(non_finite, "normalized_cost_usd", Decimal("nan"))
        entry = _make_entry(normalized_cost=non_finite)

        builder = DuckDBSnapshotBuilder()
        with pytest.raises(ValueError, match="must be finite"):
            builder.build([entry], tmp_path / "results.duckdb")

    def test_results_schema_json_not_emitted(self, tmp_path: Path) -> None:
        builder = DuckDBSnapshotBuilder()
        out = tmp_path / "results.duckdb"
        builder.build([_make_entry()], out)

        assert not (tmp_path / "results_schema.json").exists()

    def test_overwrites_existing_file(self, tmp_path: Path) -> None:
        builder = DuckDBSnapshotBuilder()
        out = tmp_path / "results.duckdb"

        builder.build([_make_entry(result_id="id-1")], out)

        builder.build(
            [_make_entry(result_id="id-2"), _make_entry(result_id="id-3", platform="p2")],
            out,
        )

        with duckdb.connect(str(out), read_only=True) as con:
            count = con.execute("SELECT COUNT(*) FROM results").fetchone()[0]

        assert count == 2


class TestQueryIdRepairsColumn:
    REPAIRS = "ualify_cume_dist→qualify_cume_dist; ualify_ntile→qualify_ntile"

    def _published(self, entry: ManifestEntry, sql: str) -> tuple | None:
        builder = DuckDBSnapshotBuilder()
        con = duckdb.connect(":memory:")
        try:
            builder._create_schema(con)
            builder._populate_results(con, [entry], {}, "https://example.test/bundles", {})
            return con.execute(sql, [entry.result_id]).fetchone()
        finally:
            con.close()

    def test_results_table_has_nullable_query_id_repairs_column(self) -> None:
        con = duckdb.connect(":memory:")
        try:
            DuckDBSnapshotBuilder()._create_schema(con)
            row = con.execute(
                "SELECT data_type, is_nullable FROM information_schema.columns"
                " WHERE table_name = 'results' AND column_name = 'query_id_repairs'"
            ).fetchone()
        finally:
            con.close()

        assert row == ("VARCHAR", "YES")

    def test_results_row_and_detail_view_store_query_id_repairs(self) -> None:
        entry = _make_entry(benchmark="read_primitives", query_id_repairs=self.REPAIRS)

        row = self._published(
            entry,
            "SELECT r.query_id_repairs, d.query_id_repairs FROM results r"
            " JOIN result_detail_metrics d USING (result_id) WHERE r.result_id = ?",
        )

        assert row == (self.REPAIRS, self.REPAIRS)

    def test_query_id_repairs_is_null_when_nothing_was_repaired(self) -> None:
        row = self._published(_make_entry(), "SELECT query_id_repairs FROM results WHERE result_id = ?")

        assert row == (None,)
