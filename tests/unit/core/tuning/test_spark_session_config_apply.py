from __future__ import annotations

import logging
import sys
import types

import pytest

from benchbox.core.tuning.applied_ledger import HARNESS_DEFAULT, NOOP, AppliedTuningLedger
from benchbox.core.tuning.interface import UnifiedTuningConfiguration
from benchbox.platforms._spark_helpers import SparkLikeAdapterMixin, apply_tuned_spark_session_config

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _FakeConf:
    def __init__(self):
        self.store: dict[str, str] = {}
        self.calls: list[tuple[str, str]] = []

    def set(self, key, value):
        self.calls.append((key, value))
        self.store[key] = str(value)

    def get(self, key, default=None):
        return self.store.get(key, default)

    def isModifiable(self, key):
        return True


class _FakeSession:
    def __init__(self):
        self.conf = _FakeConf()


class _ProbeAdapter(SparkLikeAdapterMixin):
    def __init__(self, spark_map=None, tuning_enabled=True):
        self.platform_name = "Spark"
        self.logger = logging.getLogger("spark-config-apply-probe")
        self._applied_tuning_ledger = AppliedTuningLedger()
        self.tuning_enabled = tuning_enabled
        self.spark_config = {}
        self.shuffle_partitions = 200
        self.adaptive_enabled = True
        if spark_map is not None:
            config = UnifiedTuningConfiguration.from_dict({"platform_optimizations": {"spark": dict(spark_map)}})
        else:
            config = UnifiedTuningConfiguration()
        self.unified_tuning_configuration = config

    def get_effective_tuning_configuration(self):
        return self.unified_tuning_configuration


def _applied_statements(adapter) -> list:
    return list(adapter._applied_tuning_ledger.statements)


def test_harness_defaults_are_recorded_with_redacted_values():
    adapter = _ProbeAdapter()
    session = _FakeSession()
    session.conf.store["spark.app.name"] = "private-name"
    session.conf.store["spark.plugins"] = "org.example.Plugin"

    adapter.record_harness_spark_conf(session, ("spark.app.name", "spark.plugins"))

    statements = _applied_statements(adapter)
    assert [(item.statement, item.status, item.mechanism) for item in statements] == [
        ("SET spark.app.name=<redacted>", HARNESS_DEFAULT, "harness_required"),
        ("SET spark.plugins=org.example.Plugin", HARNESS_DEFAULT, "harness_required"),
    ]
    assert adapter._applied_tuning_ledger.overall_status(tuning_enabled=True, has_config=True) == NOOP


def test_tuned_keys_set_and_recorded_once_per_run():
    adapter = _ProbeAdapter({"spark.sql.shuffle.partitions": "8"})
    session = _FakeSession()

    first = apply_tuned_spark_session_config(adapter, session)
    second = apply_tuned_spark_session_config(adapter, session)

    assert first == {"spark.sql.shuffle.partitions": "8"}
    assert second == {"spark.sql.shuffle.partitions": "8"}
    assert session.conf.calls == [("spark.sql.shuffle.partitions", "8")]
    executed = [s for s in _applied_statements(adapter) if s.status == "executed"]
    assert [(s.statement, s.phase, s.mechanism) for s in executed] == [
        ("SET spark.sql.shuffle.partitions=8", "session", "spark_session_config")
    ]


def test_bare_keys_normalized_without_double_prefix():
    adapter = _ProbeAdapter({"sql.shuffle.partitions": "8"})
    session = _FakeSession()

    apply_tuned_spark_session_config(adapter, session)

    assert session.conf.calls == [("spark.sql.shuffle.partitions", "8")]
    assert adapter._applied_tuning_ledger.statements[0].statement == "SET spark.sql.shuffle.partitions=8"


def test_refused_key_recorded_failed_and_warned(caplog):
    adapter = _ProbeAdapter({"spark.sql.shuffle.partitions": "8"})
    session = _FakeSession()
    session.conf.isModifiable = lambda key: False

    with caplog.at_level("WARNING"):
        applied = apply_tuned_spark_session_config(adapter, session)

    assert applied == {}
    assert session.conf.calls == []
    failed = [s for s in _applied_statements(adapter) if s.status == "failed"]
    assert len(failed) == 1
    assert "was refused by the engine" in caplog.text


def test_mismatched_readback_recorded_failed(caplog):
    adapter = _ProbeAdapter({"spark.sql.shuffle.partitions": "8"})
    session = _FakeSession()
    session.conf.get = lambda key, default=None: "200"

    with caplog.at_level("WARNING"):
        applied = apply_tuned_spark_session_config(adapter, session)

    assert applied == {}
    failed = [s for s in _applied_statements(adapter) if s.status == "failed"]
    assert len(failed) == 1
    assert "did not take effect" in caplog.text


def test_shadowed_adapter_option_warns_and_tuning_wins(caplog):
    adapter = _ProbeAdapter({"spark.sql.shuffle.partitions": "8"})
    adapter.spark_config = {"spark.sql.shuffle.partitions": "200"}
    session = _FakeSession()

    with caplog.at_level("WARNING"):
        apply_tuned_spark_session_config(adapter, session)

    assert session.conf.store["spark.sql.shuffle.partitions"] == "8"
    assert "overrides explicit adapter option" in caplog.text


def test_notuning_applies_nothing():
    adapter = _ProbeAdapter({"spark.sql.shuffle.partitions": "8"}, tuning_enabled=False)
    session = _FakeSession()

    assert apply_tuned_spark_session_config(adapter, session) == {}
    assert session.conf.calls == []
    assert _applied_statements(adapter) == []


def test_fresh_and_reused_runs_share_ledger_hash():
    first = _ProbeAdapter({"spark.sql.shuffle.partitions": "8"})
    second = _ProbeAdapter({"spark.sql.shuffle.partitions": "8"})
    apply_tuned_spark_session_config(first, _FakeSession())
    apply_tuned_spark_session_config(second, _FakeSession())

    assert first._applied_tuning_ledger.applied_ledger_hash() == second._applied_tuning_ledger.applied_ledger_hash()


def test_configure_for_benchmark_applies_tuning_after_olap_defaults():
    from unittest.mock import MagicMock, patch

    from benchbox.platforms.spark import SparkAdapter

    with patch.dict(sys.modules, {"pyspark": MagicMock()}):
        adapter = SparkAdapter()
    adapter.tuning_enabled = True
    config = UnifiedTuningConfiguration.from_dict(
        {"platform_optimizations": {"spark": {"spark.sql.adaptive.enabled": "false"}}}
    )
    adapter.unified_tuning_configuration = config
    adapter._applied_tuning_ledger = AppliedTuningLedger()
    session = _FakeSession()
    session.conf.store["spark.sql.adaptive.enabled"] = "true"

    adapter.configure_for_benchmark(session, "olap")

    assert session.conf.store["spark.sql.adaptive.enabled"] == "false"
    info = adapter.get_platform_info()
    assert info["configuration"]["adaptive_enabled"] is False


def test_platform_info_matches_tuned_session():
    from unittest.mock import MagicMock, patch

    from benchbox.platforms.spark import SparkAdapter

    with patch.dict(sys.modules, {"pyspark": MagicMock()}):
        adapter = SparkAdapter()
    adapter._tuned_session_applied = {
        "spark.sql.shuffle.partitions": "8",
        "spark.sql.adaptive.enabled": "false",
    }

    info = adapter.get_platform_info()

    assert info["configuration"]["shuffle_partitions"] == 8
    assert info["configuration"]["adaptive_enabled"] is False


@pytest.mark.parametrize("platform", ["databricks", "duckdb", "DatabricksAdapter", "lakesail", "LakeSail"])
def test_spark_map_warns_on_ignoring_platform(platform):
    _, warnings = _config_with_spark({"spark.sql.shuffle.partitions": "8"}).validate_for_platform_detailed(platform)
    assert any("not applied by platform" in message for message in warnings)


@pytest.mark.parametrize("platform", ["spark", "velox", "Spark", "velox:remote"])
def test_spark_map_quiet_on_applying_platform(platform):
    _, warnings = _config_with_spark({"spark.sql.shuffle.partitions": "8"}).validate_for_platform_detailed(platform)
    assert not any("not applied by platform" in message for message in warnings)


def test_empty_spark_map_warns_nowhere():
    _, warnings = UnifiedTuningConfiguration().validate_for_platform_detailed("databricks")
    assert not any("not applied by platform" in message for message in warnings)


def _config_with_spark(spark_map):
    return UnifiedTuningConfiguration.from_dict({"platform_optimizations": {"spark": spark_map}})


def test_lakesail_warning_explains_why_the_map_is_dropped():
    _, warnings = _config_with_spark({"spark.sql.shuffle.partitions": "8"}).validate_for_platform_detailed("lakesail")
    assert any("records the map as dropped" in message for message in warnings)


def test_drop_records_each_requested_key_and_sets_nothing(caplog):
    from benchbox.platforms._spark_helpers import LAKESAIL_SPARK_MAP_DROP_REASON, drop_tuned_spark_session_config

    adapter = _ProbeAdapter({"sql.shuffle.partitions": "8", "spark.sql.adaptive.enabled": "false"})
    adapter.platform_name = "LakeSail"

    with caplog.at_level("WARNING"):
        dropped = drop_tuned_spark_session_config(adapter, LAKESAIL_SPARK_MAP_DROP_REASON)

    assert dropped == ["spark.sql.adaptive.enabled", "spark.sql.shuffle.partitions"]
    assert [(item.intent, item.reason) for item in adapter._applied_tuning_ledger.dropped] == [
        ("spark_session_config:spark.sql.adaptive.enabled", LAKESAIL_SPARK_MAP_DROP_REASON),
        ("spark_session_config:spark.sql.shuffle.partitions", LAKESAIL_SPARK_MAP_DROP_REASON),
    ]
    assert adapter._applied_tuning_ledger.statements == []
    assert "not applied on LakeSail" in caplog.text


def test_drop_is_silent_without_a_map_or_with_tuning_off(caplog):
    from benchbox.platforms._spark_helpers import drop_tuned_spark_session_config

    for adapter in (_ProbeAdapter(), _ProbeAdapter({"spark.sql.shuffle.partitions": "8"}, tuning_enabled=False)):
        with caplog.at_level("WARNING"):
            assert drop_tuned_spark_session_config(adapter, "reason") == []
        assert adapter._applied_tuning_ledger.dropped == []
    assert caplog.text == ""


def test_spark_family_platforms_have_no_spark_alias():
    from benchbox.core.tuning.capability_registry import PLATFORM_ALIASES, resolve_platform_key
    from benchbox.core.tuning.packaged_templates import list_packaged_templates

    for platform in ("velox", "lakesail"):
        assert platform not in PLATFORM_ALIASES
        assert resolve_platform_key(platform) == platform
        assert not list_packaged_templates(platform).get(platform)
