from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest

from benchbox.core.results.anonymization import AnonymizationManager, find_public_path_leaks
from benchbox.core.results.loader import reconstruct_benchmark_results
from benchbox.core.results.result_factory import build_enhanced_benchmark_result
from benchbox.core.results.schema import build_result_payload
from benchbox.core.tuning.applied_ledger import AppliedTuningLedger
from benchbox.core.tuning.interface import UnifiedTuningConfiguration
from benchbox.platforms._spark_helpers import SparkLikeAdapterMixin, apply_tuned_spark_session_config
from benchbox.platforms.spark_effective_config import (
    CHANGED_BEFORE_RUN,
    EFFECTIVE_SESSION_CONFIG_VERSION,
    EXTERNAL_OVERRIDE,
    EXTERNAL_SUPPLIED,
    PATH_PLACEHOLDER,
    REQUESTED_NOT_EFFECTIVE,
    STATUS_CAPTURED,
    STATUS_NOT_READABLE,
    external_spark_conf,
    redact_spark_conf_value,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

_SECRET = "AKIA-not-a-real-secret-0042"
_HOME_PATH = "/Users/someone/private/warehouse"


class _RuntimeConf:
    def __init__(self, store: dict[str, str], *, refuse: frozenset[str] = frozenset(), override=None):
        self.store = store
        self.refuse = refuse
        self.override = dict(override or {})

    def set(self, key, value):
        self.store[key] = self.override.get(key, str(value))

    def get(self, key, default=None):
        return self.store.get(key, default)

    def isModifiable(self, key):
        return key not in self.refuse

    @property
    def getAll(self):
        return dict(self.store)


class _LegacyRuntimeConf(_RuntimeConf):
    @property
    def getAll(self):
        return lambda: dict(self.store)


class _FakeSession:
    def __init__(self, store: dict[str, str] | None = None, conf_class=_RuntimeConf, **conf_kwargs):
        self.conf = conf_class(dict(store or {}), **conf_kwargs)


class _UnreadableConf:
    def set(self, key, value):
        return None

    def get(self, key, default=None):
        return default

    def isModifiable(self, key):
        return True


class _UnreadableSession:
    def __init__(self):
        self.conf = _UnreadableConf()

    @property
    def sparkContext(self):
        raise RuntimeError("not available in Spark Connect")


class _ProbeAdapter(SparkLikeAdapterMixin):
    def __init__(self, spark_map=None, *, tuning_enabled=True, builder_conf=None):
        self.platform_name = "Spark"
        self.logger = logging.getLogger("spark-effective-config-probe")
        self._applied_tuning_ledger = AppliedTuningLedger()
        self.tuning_enabled = tuning_enabled
        self.spark_config = {}
        self._builder_conf = dict(builder_conf or {"spark.sql.shuffle.partitions": "200"})
        if spark_map is not None:
            config = UnifiedTuningConfiguration.from_dict({"platform_optimizations": {"spark": dict(spark_map)}})
        else:
            config = UnifiedTuningConfiguration()
        self.unified_tuning_configuration = config

    def get_effective_tuning_configuration(self):
        return self.unified_tuning_configuration

    def _get_spark_conf(self):
        return dict(self._builder_conf)


@pytest.fixture(autouse=True)
def _no_external_spark_conf(monkeypatch):
    for name in ("SPARK_CONF_DIR", "SPARK_HOME", "PYSPARK_SUBMIT_ARGS"):
        monkeypatch.delenv(name, raising=False)


def _run(adapter, session, *, between=None):
    adapter.capture_effective_session_config(session, "session_created")
    apply_tuned_spark_session_config(adapter, session)
    adapter.capture_effective_session_config(session, "after_configuration")
    if between is not None:
        between(session)
    adapter.capture_effective_session_config(session, "run_start")
    return adapter.effective_session_config_payload()


def _kinds(payload):
    return [(entry["key"], entry["kind"]) for entry in payload["discrepancies"]]


def test_clean_run_records_three_snapshots_and_no_discrepancies():
    adapter = _ProbeAdapter({"spark.sql.adaptive.enabled": "true"})
    session = _FakeSession({"spark.sql.shuffle.partitions": "200", "spark.app.id": "local-1"})

    payload = _run(adapter, session)

    assert payload["version"] == EFFECTIVE_SESSION_CONFIG_VERSION
    assert payload["engine"] == "Spark"
    assert list(payload["snapshots"]) == ["session_created", "after_configuration", "run_start"]
    assert all(snapshot["status"] == STATUS_CAPTURED for snapshot in payload["snapshots"].values())
    assert payload["snapshots"]["run_start"]["values"]["spark.sql.adaptive.enabled"] == "true"
    assert payload["discrepancies"] == []
    assert adapter.effective_session_config_report() == [
        "Spark session config verified: 3 effective settings, no discrepancies"
    ]


def test_legacy_callable_get_all_is_read():
    adapter = _ProbeAdapter()
    session = _FakeSession({"spark.sql.shuffle.partitions": "200"}, conf_class=_LegacyRuntimeConf)

    payload = _run(adapter, session)

    assert payload["snapshots"]["session_created"]["sources"] == ["runtime_config"]
    assert payload["snapshots"]["session_created"]["values"] == {"spark.sql.shuffle.partitions": "200"}


def test_refused_tuning_key_is_a_discrepancy_on_console_and_in_bundle():
    adapter = _ProbeAdapter({"spark.sql.adaptive.enabled": "true"})
    session = _FakeSession(
        {"spark.sql.shuffle.partitions": "200", "spark.sql.adaptive.enabled": "false"},
        refuse=frozenset({"spark.sql.adaptive.enabled"}),
    )

    payload = _run(adapter, session)

    assert payload["discrepancies"] == [
        {
            "key": "spark.sql.adaptive.enabled",
            "kind": REQUESTED_NOT_EFFECTIVE,
            "snapshot": "after_configuration",
            "expected": "true",
            "actual": "false",
        }
    ]
    report = adapter.effective_session_config_report()
    assert report == [
        "⚠️  Spark session config spark.sql.adaptive.enabled: requested_not_effective (expected true, actual false)"
    ]


def test_engine_override_of_tuning_key_is_a_discrepancy():
    adapter = _ProbeAdapter({"spark.sql.shuffle.partitions": "8"})
    session = _FakeSession({"spark.sql.shuffle.partitions": "200"}, override={"spark.sql.shuffle.partitions": "64"})

    payload = _run(adapter, session)

    assert _kinds(payload) == [("spark.sql.shuffle.partitions", REQUESTED_NOT_EFFECTIVE)]
    assert payload["discrepancies"][0]["actual"] == "64"


def test_builder_value_overridden_outside_benchbox_is_flagged():
    adapter = _ProbeAdapter(builder_conf={"spark.sql.shuffle.partitions": "200", "spark.driver.memory": "4g"})
    session = _FakeSession({"spark.sql.shuffle.partitions": "200", "spark.driver.memory": "1g"})

    payload = _run(adapter, session)

    assert payload["discrepancies"] == [
        {
            "key": "spark.driver.memory",
            "kind": EXTERNAL_OVERRIDE,
            "snapshot": "session_created",
            "expected": "4g",
            "actual": "1g",
        }
    ]


def test_file_scheme_on_read_back_is_not_an_override():
    adapter = _ProbeAdapter(builder_conf={"spark.sql.warehouse.dir": "/tmp/bb-warehouse"})
    session = _FakeSession({"spark.sql.warehouse.dir": "file:/tmp/bb-warehouse/"})

    payload = _run(adapter, session)

    assert payload["discrepancies"] == []


def test_spark_defaults_and_submit_args_are_reported_as_external(tmp_path, monkeypatch):
    (tmp_path / "spark-defaults.conf").write_text(
        "# site defaults\nspark.sql.files.maxPartitionBytes   64m\nspark.sql.shuffle.partitions 999\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SPARK_CONF_DIR", str(tmp_path))
    monkeypatch.setenv("PYSPARK_SUBMIT_ARGS", "--conf spark.sql.ansi.enabled=false pyspark-shell")
    adapter = _ProbeAdapter()
    session = _FakeSession(
        {
            "spark.sql.shuffle.partitions": "200",
            "spark.sql.files.maxPartitionBytes": "64m",
            "spark.sql.ansi.enabled": "false",
        }
    )

    payload = _run(adapter, session)

    assert _kinds(payload) == [
        ("spark.sql.ansi.enabled", EXTERNAL_SUPPLIED),
        ("spark.sql.files.maxPartitionBytes", EXTERNAL_SUPPLIED),
    ]
    assert "expected" not in payload["discrepancies"][0]


def test_external_conf_parses_both_sources(tmp_path):
    conf_dir = tmp_path / "conf"
    conf_dir.mkdir()
    (conf_dir / "spark-defaults.conf").write_text("spark.a 1\nspark.b=2\nnot.spark 3\n", encoding="utf-8")

    values = external_spark_conf({"SPARK_HOME": str(tmp_path), "PYSPARK_SUBMIT_ARGS": "--conf=spark.c=3 -c spark.d=4"})

    assert values == {"spark.a": "1", "spark.b": "2", "spark.c": "3", "spark.d": "4"}


def test_change_between_configuration_and_run_start_is_flagged():
    adapter = _ProbeAdapter()
    session = _FakeSession({"spark.sql.shuffle.partitions": "200"})

    payload = _run(adapter, session, between=lambda s: s.conf.set("spark.sql.shuffle.partitions", "16"))

    assert payload["discrepancies"] == [
        {
            "key": "spark.sql.shuffle.partitions",
            "kind": CHANGED_BEFORE_RUN,
            "snapshot": "run_start",
            "expected": "200",
            "actual": "16",
        }
    ]


def test_unreadable_engine_degrades_explicitly():
    adapter = _ProbeAdapter()
    session = _UnreadableSession()

    payload = _run(adapter, session)

    assert {snapshot["status"] for snapshot in payload["snapshots"].values()} == {STATUS_NOT_READABLE}
    assert all(snapshot["values"] == {} for snapshot in payload["snapshots"].values())
    assert payload["discrepancies"] == []
    report = adapter.effective_session_config_report()
    assert len(report) == 3
    assert all("not readable" in line for line in report)


def test_notuning_run_records_the_real_default_state():
    adapter = _ProbeAdapter({"spark.sql.shuffle.partitions": "8"}, tuning_enabled=False)
    session = _FakeSession({"spark.sql.shuffle.partitions": "200", "spark.sql.adaptive.enabled": "true"})

    payload = _run(adapter, session)

    assert payload["snapshots"]["run_start"]["values"] == {
        "spark.sql.adaptive.enabled": "true",
        "spark.sql.shuffle.partitions": "200",
    }
    assert payload["discrepancies"] == []


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("spark.hadoop.fs.s3a.secret.key", _SECRET, "<redacted>"),
        ("spark.hadoop.fs.s3a.access.key", _SECRET, "<redacted>"),
        ("spark.authenticate.secret", _SECRET, "<redacted>"),
        ("spark.databricks.token", _SECRET, "<redacted>"),
        ("spark.driver.host", "192.168.4.29", "<redacted>"),
        ("spark.sql.warehouse.dir", f"file:{_HOME_PATH}", PATH_PLACEHOLDER),
        ("spark.jars", f"{_HOME_PATH}/a.jar,{_HOME_PATH}/b.jar", f"{PATH_PLACEHOLDER},{PATH_PLACEHOLDER}"),
        ("spark.eventLog.dir", "s3a://private-bucket/logs", PATH_PLACEHOLDER),
        ("spark.driver.extraJavaOptions", f"-Dlog.dir={_HOME_PATH} --add-opens=java.base/java.lang=ALL", None),
        ("spark.sql.shuffle.partitions", "200", "200"),
        ("spark.master", "local[*]", "local[*]"),
    ],
)
def test_redaction_rules(key, value, expected):
    redacted = redact_spark_conf_value(key, value)

    assert _SECRET not in redacted
    assert "/Users/" not in redacted
    if expected is not None:
        assert redacted == expected
    else:
        assert redacted == f"-Dlog.dir={PATH_PLACEHOLDER} --add-opens=java.base/java.lang=ALL"


def _leaky_session():
    return _FakeSession(
        {
            "spark.sql.shuffle.partitions": "200",
            "spark.hadoop.fs.s3a.secret.key": _SECRET,
            "spark.sql.warehouse.dir": f"file:{_HOME_PATH}",
            "spark.driver.host": "192.168.4.29",
        }
    )


def test_no_secret_or_path_reaches_payload_console_or_log(caplog):
    adapter = _ProbeAdapter(builder_conf={"spark.sql.warehouse.dir": "/somewhere/else"})
    session = _leaky_session()

    with caplog.at_level(logging.DEBUG):
        payload = _run(adapter, session, between=lambda s: s.conf.set("spark.hadoop.fs.s3a.secret.key", "rotated-x"))
        report = adapter.effective_session_config_report()

    text = json.dumps(payload) + "\n".join(report) + caplog.text
    assert _SECRET not in text
    assert "rotated-x" not in text
    assert "/Users/" not in text
    assert "192.168.4.29" not in text
    assert ("spark.hadoop.fs.s3a.secret.key", CHANGED_BEFORE_RUN) in _kinds(payload)
    assert find_public_path_leaks(payload) == []


def _result_with(payload):
    benchmark = SimpleNamespace(benchmark_name="tpch", scale_factor=0.01, compliance_class=None)
    result = build_enhanced_benchmark_result(
        benchmark=benchmark,
        platform="spark",
        query_results=[],
        tunings_applied={"platform_optimizations": {"spark": {"spark.sql.shuffle.partitions": "8"}}},
        tuning_config_hash="requested-hash",
    )
    result.effective_session_config = payload
    return result


def test_bundle_round_trip_and_anonymised_export_stay_clean():
    adapter = _ProbeAdapter()
    payload = _run(adapter, _leaky_session())

    bundle = build_result_payload(_result_with(payload))

    assert bundle["platform"]["effective_session_config"] == payload
    reloaded = reconstruct_benchmark_results(json.loads(json.dumps(bundle)))
    assert reloaded.effective_session_config == payload
    anonymised = AnonymizationManager().anonymize_result_payload(bundle)
    exported = json.dumps(anonymised)
    assert _SECRET not in exported
    assert "/Users/" not in exported
    assert find_public_path_leaks(anonymised["platform"]["effective_session_config"]) == []


def test_bundle_without_the_block_still_loads():
    bundle = build_result_payload(_result_with(None))

    assert "effective_session_config" not in bundle["platform"]
    assert reconstruct_benchmark_results(json.loads(json.dumps(bundle))).effective_session_config is None


def test_capture_leaves_requested_hash_and_ledger_hash_unchanged():
    adapter = _ProbeAdapter({"spark.sql.shuffle.partitions": "8"})
    config_hash = adapter.unified_tuning_configuration.get_configuration_hash()
    config_sections = json.dumps(adapter.unified_tuning_configuration.to_dict(), sort_keys=True)
    session = _FakeSession({"spark.sql.shuffle.partitions": "200"})
    adapter.capture_effective_session_config(session, "session_created")
    apply_tuned_spark_session_config(adapter, session)
    ledger_hash = adapter._applied_tuning_ledger.applied_ledger_hash()

    adapter.capture_effective_session_config(session, "after_configuration")
    adapter.capture_effective_session_config(session, "run_start")

    assert adapter.unified_tuning_configuration.get_configuration_hash() == config_hash
    assert json.dumps(adapter.unified_tuning_configuration.to_dict(), sort_keys=True) == config_sections
    assert adapter._applied_tuning_ledger.applied_ledger_hash() == ledger_hash
    with_block = build_result_payload(_result_with(adapter.effective_session_config_payload()))
    without_block = build_result_payload(_result_with(None))
    assert with_block["platform"]["tuning"] == without_block["platform"]["tuning"]


def test_new_session_starts_a_new_capture():
    adapter = _ProbeAdapter()
    _run(adapter, _FakeSession({"spark.sql.shuffle.partitions": "200"}))

    adapter.capture_effective_session_config(_FakeSession({"spark.sql.shuffle.partitions": "200"}), "session_created")

    assert list(adapter.effective_session_config_payload()["snapshots"]) == ["session_created"]


def test_builder_conf_failure_does_not_stop_the_capture():
    adapter = _ProbeAdapter()

    def _raise():
        raise RuntimeError("gluten jar missing")

    adapter._get_spark_conf = _raise

    payload = _run(adapter, _FakeSession({"spark.sql.shuffle.partitions": "200"}))

    assert payload["snapshots"]["session_created"]["status"] == STATUS_CAPTURED
    assert payload["discrepancies"] == []
