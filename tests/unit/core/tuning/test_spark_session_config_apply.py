from __future__ import annotations

import logging
import types

import pytest

from benchbox.core.tuning.interface import UnifiedTuningConfiguration
from benchbox.platforms._spark_helpers import SparkLikeAdapterMixin

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _FakeConf:
    def __init__(self):
        self.calls = []

    def set(self, key, value):
        self.calls.append((key, value))


class _FakeSession:
    def __init__(self):
        self.conf = _FakeConf()


class _FakeLedger:
    def __init__(self):
        self.statements = []

    def record(self, statement, *args, **kwargs):
        self.statements.append(statement)


class _ProbeAdapter(SparkLikeAdapterMixin):
    def __init__(self):
        self.platform_name = "Spark"
        self.logger = logging.getLogger("spark-config-apply-probe")
        self._applied_tuning_ledger = _FakeLedger()


def _config_with_spark(spark_map):
    return UnifiedTuningConfiguration.from_dict({"platform_optimizations": {"spark": spark_map}})


def test_apply_uses_stored_key_without_double_prefix():
    adapter = _ProbeAdapter()
    session = _FakeSession()
    adapter.apply_platform_optimizations(
        _config_with_spark({"spark.sql.shuffle.partitions": "8"}).platform_optimizations, session
    )
    assert session.conf.calls == [("spark.sql.shuffle.partitions", "8")]
    assert adapter._applied_tuning_ledger.statements == ["SET spark.sql.shuffle.partitions=8"]


def test_apply_normalizes_bare_key_for_conf_and_ledger():
    adapter = _ProbeAdapter()
    session = _FakeSession()
    raw = types.SimpleNamespace(spark={"sql.shuffle.partitions": "8"})
    adapter.apply_platform_optimizations(raw, session)
    assert session.conf.calls == [("spark.sql.shuffle.partitions", "8")]
    assert adapter._applied_tuning_ledger.statements == ["SET spark.sql.shuffle.partitions=8"]


def test_apply_failure_ledger_matches_conf_key():
    adapter = _ProbeAdapter()

    class _FailingConf(_FakeConf):
        def set(self, key, value):
            raise RuntimeError("boom")

    session = _FakeSession()
    session.conf = _FailingConf()
    adapter.apply_platform_optimizations(
        _config_with_spark({"spark.sql.shuffle.partitions": "8"}).platform_optimizations, session
    )
    assert adapter._applied_tuning_ledger.statements == ["SET spark.sql.shuffle.partitions=8"]


@pytest.mark.parametrize("platform", ["databricks", "duckdb", "DatabricksAdapter"])
def test_spark_map_warns_on_ignoring_platform(platform):
    _, warnings = _config_with_spark({"spark.sql.shuffle.partitions": "8"}).validate_for_platform_detailed(platform)
    assert any("not applied by platform" in message for message in warnings)


@pytest.mark.parametrize("platform", ["spark", "velox", "lakesail", "Spark"])
def test_spark_map_quiet_on_applying_platform(platform):
    _, warnings = _config_with_spark({"spark.sql.shuffle.partitions": "8"}).validate_for_platform_detailed(platform)
    assert not any("not applied by platform" in message for message in warnings)


def test_empty_spark_map_warns_nowhere():
    _, warnings = UnifiedTuningConfiguration().validate_for_platform_detailed("databricks")
    assert not any("not applied by platform" in message for message in warnings)
