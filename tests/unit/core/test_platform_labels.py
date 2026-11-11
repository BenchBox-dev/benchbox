from __future__ import annotations

import pytest

from benchbox.core.labels import disambiguate_platform_labels
from tests.fixtures.result_dict_fixtures import make_normalized_result

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class TestPlatformLabelAgreement:
    def test_same_platform_different_driver_version_cli_matches_pipeline(self) -> None:
        from _project.scripts.explorer_pipeline.models import DetailResult
        from benchbox.core.visualization.result_plotter import ResultPlotter

        base: dict = {
            "benchmark": "tpch",
            "scale_factor": 0.1,
            "platform": "DuckDB",
            "platform_id": "duckdb",
            "run_date": "2026-04-01",
            "total_duration_s": 60.0,
            "geomean_ms": 100.0,
            "display_geomean_ms": 100.0,
            "power_score": 3000.0,
            "environment": {},
            "queries": [],
            "display_timings": [],
            "has_plans": False,
            "has_tuning": False,
            "bundle_download_url": "",
            "trust_label": "maintainer-run",
            "visibility": "public-curated",
        }
        d1 = DetailResult.model_validate({**base, "result_id": "r1", "driver_version": "1.0.0"})
        d2 = DetailResult.model_validate({**base, "result_id": "r2", "driver_version": "1.2.0"})

        pipeline_labels = disambiguate_platform_labels([d1, d2])

        cli_results = [
            make_normalized_result(platform="DuckDB", platform_version="1.0.0"),
            make_normalized_result(platform="DuckDB", platform_version="1.2.0"),
        ]
        plotter = ResultPlotter(cli_results)
        cli_labels = sorted(r.platform for r in plotter.results)

        assert sorted(pipeline_labels) == cli_labels

    def test_single_result_no_suffix_both_paths(self) -> None:
        from _project.scripts.explorer_pipeline.models import DetailResult
        from benchbox.core.visualization.result_plotter import ResultPlotter

        base: dict = {
            "benchmark": "tpch",
            "scale_factor": 0.1,
            "platform": "DuckDB",
            "platform_id": "duckdb",
            "driver_version": None,
            "run_date": "2026-04-01",
            "total_duration_s": 60.0,
            "geomean_ms": 100.0,
            "display_geomean_ms": 100.0,
            "power_score": 3000.0,
            "environment": {},
            "queries": [],
            "display_timings": [],
            "has_plans": False,
            "has_tuning": False,
            "bundle_download_url": "",
            "trust_label": "maintainer-run",
            "visibility": "public-curated",
        }
        d1 = DetailResult.model_validate({**base, "result_id": "r1"})

        assert disambiguate_platform_labels([d1]) == ["DuckDB"]

        plotter = ResultPlotter([make_normalized_result(platform="DuckDB")])
        assert plotter.results[0].platform == "DuckDB"


