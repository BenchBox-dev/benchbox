from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from benchbox.core.run_service import begin_run
from benchbox.platforms.adapter_factory import get_adapter
from benchbox.platforms.dataframe import dask_df

pytestmark = [pytest.mark.unit, pytest.mark.fast]

NOTICE_LOGGER = "benchbox.core.option_notices"
CLUSTER_ONLY = ("n_workers", "threads_per_worker", "memory_limit", "spill_directory")


@pytest.fixture(autouse=True)
def _fresh_run() -> Iterator[None]:
    begin_run()
    yield
    begin_run()


def _messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.name == NOTICE_LOGGER]


def _dask_options(tmp_path: Path) -> dict[str, Any]:
    return {
        "n_workers": 2,
        "threads_per_worker": 4,
        "memory_limit": "1GB",
        "spill_directory": str(tmp_path / "spill"),
    }


@pytest.mark.skipif(not dask_df.DASK_AVAILABLE, reason="Dask not installed")
class TestDaskThreadedClusterOptions:
    def test_each_cluster_only_option_warns_and_is_not_recorded(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING, logger=NOTICE_LOGGER)

        adapter = get_adapter(
            "dask-df",
            working_dir=tmp_path,
            execution_engine="threaded",
            **_dask_options(tmp_path),
        )
        info = adapter.get_platform_info()
        adapter.close()

        messages = _messages(caplog)
        assert len(messages) == len(CLUSTER_ONLY)
        for option in CLUSTER_ONLY:
            assert sum(f"Option '{option}' for 'dask' has no effect" in message for message in messages) == 1
        assert "distributed" in messages[0]
        assert info["execution_engine"]["applied"] == "threaded"
        assert info["use_distributed"] is False
        for key in (*CLUSTER_ONLY, "spill_to_disk"):
            assert key not in info

    def test_threaded_without_cluster_options_is_silent_and_records_no_cluster_keys(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING, logger=NOTICE_LOGGER)

        adapter = get_adapter("dask-df", working_dir=tmp_path, execution_engine="threaded")
        info = adapter.get_platform_info()
        adapter.close()

        assert _messages(caplog) == []
        assert {"n_workers", "threads_per_worker", "memory_limit"}.isdisjoint(info)

    def test_warns_once_per_run(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING, logger=NOTICE_LOGGER)

        for _ in range(2):
            adapter = get_adapter("dask-df", working_dir=tmp_path, execution_engine="threaded", threads_per_worker=4)
            adapter.close()
        assert len(_messages(caplog)) == 1

        begin_run()
        adapter = get_adapter("dask-df", working_dir=tmp_path, execution_engine="threaded", threads_per_worker=4)
        adapter.close()
        assert len(_messages(caplog)) == 2

    def test_legacy_use_distributed_false_warns_the_same_way(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING, logger=NOTICE_LOGGER)

        adapter = get_adapter("dask-df", working_dir=tmp_path, use_distributed=False, threads_per_worker=4)
        info = adapter.get_platform_info()
        adapter.close()

        assert any("Option 'threads_per_worker' for 'dask'" in message for message in _messages(caplog))
        assert "threads_per_worker" not in info

    def test_distributed_engine_records_and_does_not_warn(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        caplog.set_level(logging.WARNING, logger=NOTICE_LOGGER)
        monkeypatch.setattr(dask_df.DaskDataFrameAdapter, "_setup_distributed", lambda self: None)

        adapter = get_adapter(
            "dask-df",
            working_dir=tmp_path,
            execution_engine="distributed",
            **_dask_options(tmp_path),
        )
        info = adapter.get_platform_info()

        assert _messages(caplog) == []
        assert info["use_distributed"] is True
        assert info["n_workers"] == 2
        assert info["threads_per_worker"] == 4
        assert info["memory_limit"] == "1GB"


class TestLakeSailWorkers:
    @pytest.fixture(autouse=True)
    def _pyspark(self) -> None:
        pytest.importorskip("pyspark")

    @pytest.mark.parametrize("platform", ["lakesail", "lakesail:distributed"])
    def test_sail_workers_warns_and_is_not_recorded(self, platform: str, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING, logger=NOTICE_LOGGER)

        adapter = get_adapter(platform, benchmark="tpch", scale_factor=0.01, sail_workers=4)
        info = adapter.get_platform_info()

        messages = _messages(caplog)
        assert len(messages) == 1
        assert "Option 'sail_workers' for 'lakesail' has no effect" in messages[0]
        assert "sail_workers" not in info["configuration"]
        assert info["configuration"]["sail_mode"] == (platform.partition(":")[2] or "local")

    def test_reason_names_the_mode(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING, logger=NOTICE_LOGGER)

        get_adapter("lakesail", benchmark="tpch", scale_factor=0.01, sail_workers=4)
        begin_run()
        get_adapter("lakesail:distributed", benchmark="tpch", scale_factor=0.01, sail_workers=4)

        local, external = _messages(caplog)
        assert "local Sail server" in local
        assert "existing Sail server" in external

    def test_no_notice_without_sail_workers(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING, logger=NOTICE_LOGGER)

        adapter = get_adapter("lakesail:distributed", benchmark="tpch", scale_factor=0.01)

        assert _messages(caplog) == []
        assert adapter.get_platform_info()["connection_mode"] == "distributed"
