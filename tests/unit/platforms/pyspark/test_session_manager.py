from __future__ import annotations

import logging
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from benchbox.platforms.pyspark import session as session_module
from benchbox.platforms.pyspark.session import (
    SparkConfigurationError,
    SparkSessionConfig,
    SparkSessionManager,
    suppress_window_exec_warning,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.usefixtures("spark_runtime_environment"),
]


def _patch_classmethod(monkeypatch: pytest.MonkeyPatch, attr: str, func):
    monkeypatch.setattr(SparkSessionManager, attr, classmethod(func))


@pytest.fixture(autouse=True)
def reset_manager():
    SparkSessionManager.close()
    yield
    SparkSessionManager.close()


def test_get_or_create_returns_singleton(monkeypatch: pytest.MonkeyPatch) -> None:

    fake_session = SimpleNamespace(stop=lambda: None)

    monkeypatch.setattr(session_module, "PYSPARK_AVAILABLE", True)
    monkeypatch.setattr(session_module, "_validate_java_version", lambda: None)
    _patch_classmethod(monkeypatch, "_create_session", lambda cls, config: fake_session)

    session = SparkSessionManager.get_or_create(
        master="local[*]",
        app_name="BenchBox-Tests",
        driver_memory="2g",
        executor_memory=None,
        shuffle_partitions=8,
        enable_aqe=True,
        extra_configs={"spark.sql.warehouse.dir": "/tmp/warehouse"},
    )

    assert session is fake_session
    assert SparkSessionManager._config is not None
    assert SparkSessionManager._config.master == "local[*]"
    assert SparkSessionManager._config.shuffle_partitions == 8

    SparkSessionManager.release()
    assert SparkSessionManager._session is None


def test_baseline_session_sets_only_harness_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    builder = MagicMock()
    builder.master.return_value = builder
    builder.appName.return_value = builder
    builder.config.return_value = builder
    session = MagicMock()
    builder.getOrCreate.return_value = session
    monkeypatch.setattr(session_module, "SparkSession", SimpleNamespace(builder=builder))
    monkeypatch.setattr(session_module, "suppress_window_exec_warning", lambda _: None)

    SparkSessionManager._create_session(
        SparkSessionConfig(
            master="local[1]",
            app_name="baseline",
            driver_memory=None,
            executor_memory=None,
            shuffle_partitions=None,
            enable_aqe=None,
            extra_configs=(),
        )
    )

    configured_keys = [call.args[0] for call in builder.config.call_args_list]
    assert configured_keys == ["spark.driver.extraJavaOptions", "spark.sql.inMemoryColumnarStorage.enabled"]


def test_get_or_create_redacts_azure_sas_config_in_debug_log(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake_session = SimpleNamespace(stop=lambda: None)
    sas_key = "spark.hadoop.fs.azure.sas.container.account.blob.core.windows.net"

    monkeypatch.setattr(session_module, "PYSPARK_AVAILABLE", True)
    monkeypatch.setattr(session_module, "_validate_java_version", lambda: None)
    _patch_classmethod(monkeypatch, "_create_session", lambda cls, config: fake_session)

    with caplog.at_level(logging.DEBUG, logger="benchbox.platforms.pyspark.session"):
        SparkSessionManager.get_or_create(
            master="local[*]",
            app_name="BenchBox-Tests",
            driver_memory="2g",
            executor_memory=None,
            shuffle_partitions=8,
            enable_aqe=True,
            extra_configs={sas_key: "SAS-SENTINEL"},
        )

    messages = [record.getMessage() for record in caplog.records if "Creating SparkSession" in record.getMessage()]
    assert messages
    assert all("SAS-SENTINEL" not in message for message in messages)
    assert any(sas_key in message for message in messages)


def test_configuration_mismatch_raises(monkeypatch: pytest.MonkeyPatch) -> None:

    fake_session = SimpleNamespace(stop=lambda: None)

    monkeypatch.setattr(session_module, "PYSPARK_AVAILABLE", True)
    monkeypatch.setattr(session_module, "_validate_java_version", lambda: None)
    _patch_classmethod(monkeypatch, "_create_session", lambda cls, config: fake_session)

    first = SparkSessionManager.get_or_create(
        master="local[2]",
        app_name="BenchBox-Tests",
        driver_memory="1g",
        executor_memory=None,
        shuffle_partitions=4,
        enable_aqe=True,
        extra_configs={},
    )
    assert first is fake_session

    with pytest.raises(SparkConfigurationError):
        SparkSessionManager.get_or_create(
            master="local[4]",
            app_name="BenchBox-Tests",
            driver_memory="1g",
            executor_memory=None,
            shuffle_partitions=4,
            enable_aqe=True,
            extra_configs={},
        )


def test_release_stops_session_once(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_session = SimpleNamespace()
    stop_called = {"count": 0}

    def fake_stop_session(cls):
        stop_called["count"] += 1
        cls._session = None

    monkeypatch.setattr(session_module, "PYSPARK_AVAILABLE", True)
    monkeypatch.setattr(session_module, "_validate_java_version", lambda: None)
    _patch_classmethod(monkeypatch, "_create_session", lambda cls, config: fake_session)
    _patch_classmethod(monkeypatch, "_stop_session", fake_stop_session)

    SparkSessionManager.get_or_create(
        master="local[*]",
        app_name="BenchBox",
        driver_memory="1g",
        executor_memory=None,
        shuffle_partitions=2,
        enable_aqe=False,
        extra_configs={},
    )
    SparkSessionManager.release()

    assert stop_called["count"] == 1
    assert SparkSessionManager._session is None


def test_java_home_auto_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(session_module, "PYSPARK_AVAILABLE", True)

    SparkSessionManager._java_validated = False

    call_count = {"n": 0}

    def fake_detect(java_home: str | None = None) -> int | None:
        call_count["n"] += 1
        if java_home == "/fake/jdk17":
            return 17
        return 25

    def fake_find_supported():
        return ("/fake/jdk17", 17)

    monkeypatch.setattr(session_module, "detect_java_version", fake_detect)
    monkeypatch.setattr(session_module, "find_supported_java_home", fake_find_supported)

    fake_session = SimpleNamespace(stop=lambda: None)
    _patch_classmethod(monkeypatch, "_create_session", lambda cls, config: fake_session)

    monkeypatch.delenv("JAVA_HOME", raising=False)
    monkeypatch.delenv("BENCHBOX_JAVA_HOME", raising=False)

    SparkSessionManager.get_or_create(
        master="local[*]",
        app_name="BenchBox",
        driver_memory="1g",
        executor_memory=None,
        shuffle_partitions=2,
        enable_aqe=True,
        extra_configs={},
    )

    assert os.environ.get("JAVA_HOME") == "/fake/jdk17"


class TestSuppressWindowExecWarning:
    def test_calls_configurator_with_logger_object(self) -> None:

        mock_spark = SimpleNamespace(sparkContext=SimpleNamespace(_jvm=MagicMock()))
        mock_jvm = mock_spark.sparkContext._jvm
        mock_logger = MagicMock()
        mock_jvm.org.apache.logging.log4j.LogManager.getLogger.return_value = mock_logger

        suppress_window_exec_warning(mock_spark)

        mock_jvm.org.apache.logging.log4j.LogManager.getLogger.assert_called_once_with(
            "org.apache.spark.sql.execution.window.WindowExec"
        )
        mock_jvm.org.apache.logging.log4j.core.config.Configurator.setLevel.assert_called_once_with(
            mock_logger,
            mock_jvm.org.apache.logging.log4j.Level.ERROR,
        )

    def test_does_not_raise_on_jvm_error(self) -> None:
        mock_spark = SimpleNamespace(sparkContext=SimpleNamespace(_jvm=MagicMock()))
        mock_spark.sparkContext._jvm.org.apache.logging.log4j.core.config.Configurator.setLevel.side_effect = (
            RuntimeError("JVM not available")
        )

        suppress_window_exec_warning(mock_spark)

    def test_does_not_raise_when_jvm_is_none(self) -> None:
        mock_spark = SimpleNamespace(sparkContext=SimpleNamespace(_jvm=None))

        suppress_window_exec_warning(mock_spark)


class _RecordingBuilder:
    def __init__(self) -> None:
        self.entries: dict[str, str] = {}
        self.session = MagicMock()

    def master(self, _value: str) -> _RecordingBuilder:
        return self

    def appName(self, _value: str) -> _RecordingBuilder:
        return self

    def config(self, key: str, value: str) -> _RecordingBuilder:
        self.entries[key] = value
        return self

    def getOrCreate(self) -> MagicMock:
        return self.session


def _create_session_with_fake_builder(monkeypatch: pytest.MonkeyPatch, config: SparkSessionConfig) -> dict[str, str]:
    builder = _RecordingBuilder()
    fake_spark_session = SimpleNamespace(builder=builder)
    monkeypatch.setattr(session_module, "SparkSession", fake_spark_session)
    SparkSessionManager._create_session(config)
    assert isinstance(builder.entries, dict)
    return builder.entries


def _session_config(**overrides) -> SparkSessionConfig:
    params = {
        "master": "local[*]",
        "app_name": "BenchBox-Tests",
        "driver_memory": "2g",
        "executor_memory": None,
        "shuffle_partitions": 8,
        "enable_aqe": True,
        "extra_configs": (),
    }
    params.update(overrides)
    return SparkSessionConfig(**params)


class TestCreateSessionAqeKeys:
    def test_aqe_enabled_sets_all_keys_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        entries = _create_session_with_fake_builder(monkeypatch, _session_config(enable_aqe=True))

        assert entries["spark.sql.adaptive.enabled"] == "true"
        assert entries["spark.sql.adaptive.coalescePartitions.enabled"] == "true"
        assert entries["spark.sql.adaptive.skewJoin.enabled"] == "true"

    def test_aqe_disabled_sets_all_keys_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        entries = _create_session_with_fake_builder(monkeypatch, _session_config(enable_aqe=False))

        assert entries["spark.sql.adaptive.enabled"] == "false"
        assert entries["spark.sql.adaptive.coalescePartitions.enabled"] == "false"
        assert entries["spark.sql.adaptive.skewJoin.enabled"] == "false"

    def test_extra_configs_override_aqe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        entries = _create_session_with_fake_builder(
            monkeypatch,
            _session_config(
                enable_aqe=True,
                extra_configs=(("spark.sql.adaptive.enabled", "false"),),
            ),
        )

        assert entries["spark.sql.adaptive.enabled"] == "false"
        assert entries["spark.sql.adaptive.skewJoin.enabled"] == "true"
