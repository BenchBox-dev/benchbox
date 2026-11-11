from __future__ import annotations

import importlib
from pathlib import Path

import pytest

prefs = importlib.import_module("benchbox.cli.preferences")

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestOutputParameterPersistence:
    def test_output_saved_and_loaded(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(prefs, "get_preferences_dir", lambda: tmp_path)

        prefs.save_last_run_config(
            database="redshift",
            benchmark="tpch",
            scale=0.01,
            tuning_mode="notuning",
            output="s3://benchbox-uploads/benchbox-data",
        )

        loaded = prefs.load_last_run_config()
        assert loaded is not None
        assert loaded["output"] == "s3://benchbox-uploads/benchbox-data"

    def test_output_none_not_saved(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(prefs, "get_preferences_dir", lambda: tmp_path)

        prefs.save_last_run_config(
            database="duckdb",
            benchmark="tpch",
            scale=0.01,
            tuning_mode="tuned",
            output=None,
        )

        loaded = prefs.load_last_run_config()
        assert loaded is not None
        assert "output" not in loaded

    def test_output_empty_string_not_saved(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(prefs, "get_preferences_dir", lambda: tmp_path)

        prefs.save_last_run_config(
            database="duckdb",
            benchmark="tpch",
            scale=0.01,
            tuning_mode="tuned",
            output="",
        )

        loaded = prefs.load_last_run_config()
        assert loaded is not None
        assert "output" not in loaded

    def test_cloud_path_variants_preserved(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(prefs, "get_preferences_dir", lambda: tmp_path)

        for cloud_path in [
            "s3://my-bucket/path/to/data",
            "gs://gcs-bucket/benchbox",
            "abfss://container@account.dfs.core.windows.net/path",
        ]:
            prefs.save_last_run_config(
                database="redshift",
                benchmark="tpch",
                scale=1.0,
                tuning_mode="notuning",
                output=cloud_path,
            )

            loaded = prefs.load_last_run_config()
            assert loaded is not None
            assert loaded["output"] == cloud_path

    def test_output_does_not_collide_with_additional_options(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(prefs, "get_preferences_dir", lambda: tmp_path)

        prefs.save_last_run_config(
            database="redshift",
            benchmark="tpch",
            scale=0.01,
            tuning_mode="notuning",
            output="s3://bucket/data",
            additional_options={"table_mode": "external"},
        )

        loaded = prefs.load_last_run_config()
        assert loaded is not None
        assert loaded["output"] == "s3://bucket/data"
        assert loaded["table_mode"] == "external"


