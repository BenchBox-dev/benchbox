from __future__ import annotations

import hashlib
import json
from datetime import datetime

import pytest

from benchbox.core.results.schema import _tuning_types_present
from benchbox.core.tuning.interface import (
    PlatformOptimizationConfiguration,
    UnifiedTuningConfiguration,
)
from benchbox.core.tuning.metadata import TuningMetadataManager
from benchbox.utils.database_naming import generate_database_name


class _Adapter:
    def __init__(self, platform_name: str = "duckdb"):
        self.platform_name = platform_name
        self.platform_config = {}


pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

_PRE_CHANGE_DEFAULT_DIGEST = "bb837da77fe3b4152fd189ac493c7eab95341ab4ac44733a6e9f9df6c5466668"


def _digest(config: UnifiedTuningConfiguration) -> str:
    canonical = json.dumps(config.to_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _platform_hash(config: UnifiedTuningConfiguration) -> str:
    manager = TuningMetadataManager(_Adapter())
    records = manager._build_section_marker_records(config, "duckdb", datetime(2026, 1, 1))
    marker = next(r for r in records if r.column_name == "platform_optimizations_hash")
    return marker.configuration_hash


def test_empty_and_omitted_spark_match_pre_change_dict():
    omitted = UnifiedTuningConfiguration()
    explicit = UnifiedTuningConfiguration.from_dict({"platform_optimizations": {"spark": {}}})
    null_form = UnifiedTuningConfiguration.from_dict({"platform_optimizations": {"spark": None}})

    assert "spark" not in omitted.platform_optimizations.to_dict()
    assert "spark" not in PlatformOptimizationConfiguration().to_dict()
    assert explicit.platform_optimizations.to_dict() == omitted.platform_optimizations.to_dict()
    assert null_form.platform_optimizations.to_dict() == omitted.platform_optimizations.to_dict()
    assert _digest(omitted) == _PRE_CHANGE_DEFAULT_DIGEST
    assert _digest(explicit) == _digest(UnifiedTuningConfiguration.from_dict({}))


def test_spark_map_round_trips_with_canonical_values():
    config = UnifiedTuningConfiguration.from_dict(
        {
            "platform_optimizations": {
                "spark": {
                    "sql.shuffle.partitions": 200,
                    "spark.sql.adaptive.enabled": True,
                    "spark.sql.adaptive.advisoryPartitionSizeInBytes": "64MB",
                    "spark.sql.broadcastTimeout": 1.5,
                }
            }
        }
    )

    assert config.platform_optimizations.spark == {
        "spark.sql.broadcastTimeout": "1.5",
        "spark.sql.adaptive.advisoryPartitionSizeInBytes": "64MB",
        "spark.sql.adaptive.enabled": "true",
        "spark.sql.shuffle.partitions": "200",
    }

    rebuilt = UnifiedTuningConfiguration.from_dict(config.to_dict())
    assert rebuilt.platform_optimizations.spark == config.platform_optimizations.spark
    assert list(rebuilt.platform_optimizations.spark) == sorted(rebuilt.platform_optimizations.spark)


def test_bare_and_prefixed_forms_share_hash_and_duplicates_rejected():
    bare = UnifiedTuningConfiguration.from_dict(
        {"platform_optimizations": {"spark": {"sql.shuffle.partitions": "200"}}}
    )
    prefixed = UnifiedTuningConfiguration.from_dict(
        {"platform_optimizations": {"spark": {"spark.sql.shuffle.partitions": "200"}}}
    )

    assert bare.get_configuration_hash() == prefixed.get_configuration_hash()

    with pytest.raises(ValueError, match="duplicate key 'spark.sql.shuffle.partitions'"):
        UnifiedTuningConfiguration.from_dict(
            {
                "platform_optimizations": {
                    "spark": {"sql.shuffle.partitions": "200", "spark.sql.shuffle.partitions": "200"}
                }
            }
        )


def test_spark_key_order_stable_for_hash_and_database_name():
    first = UnifiedTuningConfiguration.from_dict(
        {"platform_optimizations": {"spark": {"spark.sql.shuffle.partitions": "2", "spark.sql.cbo.enabled": "1"}}}
    )
    second = UnifiedTuningConfiguration.from_dict(
        {"platform_optimizations": {"spark": {"spark.sql.cbo.enabled": "1", "spark.sql.shuffle.partitions": "2"}}}
    )

    assert first.get_configuration_hash() == second.get_configuration_hash()
    assert generate_database_name("tpch", 1, "spark", first.to_dict()) == generate_database_name(
        "tpch", 1, "spark", second.to_dict()
    )


@pytest.mark.parametrize(
    ("key", "match"),
    [
        ("spark.sql.extensions", "static"),
        ("spark.sql.warehouse.dir", "static"),
        ("spark.sql.catalog.spark_catalog", "static"),
        ("spark.driver.memory", "core deploy"),
        ("spark.executor.cores", "core deploy"),
        ("spark.plugins", "owned by the adapter"),
        ("spark.memory.offHeap.enabled", "owned by the adapter"),
        ("spark.memory.offHeap.size", "owned by the adapter"),
        ("spark.shuffle.manager", "owned by the adapter"),
        ("spark.hadoop.s3a.secret.key", "credential"),
        ("spark.password", "credential"),
        ("spark.token", "credential"),
        ("spark.credential.provider", "credential"),
        ("spark.hadoop.fs.s3a.access.key", "credential"),
        ("spark.hadoop.fs.azure.account.key.store.dfs.core.windows.net", "credential"),
        ("spark.hadoop.fs.azure.sas.container.store.blob.core.windows.net", "credential"),
        ("spark.hadoop.google.cloud.auth.service.account.json.keyfile", "credential"),
        ("spark.hadoop.anything", "credential"),
        ("fs.s3a.endpoint", "credential"),
        ("spark.sql.azure.account.key.store", "credential"),
        ("spark.sql.sas.token", "credential"),
        ("spark.sql.private.key", "credential"),
    ],
)
def test_rejected_key_classes_name_key_and_alternative(key: str, match: str):
    with pytest.raises(ValueError, match=f"{key}.*{match}"):
        PlatformOptimizationConfiguration.from_dict({"spark": {key: "x"}})


@pytest.mark.parametrize(
    "value",
    [None, ["spark.a"], {"nested": "map"}],
    ids=["none-value", "list-value", "dict-value"],
)
def test_unsupported_spark_values_rejected(value):
    with pytest.raises(ValueError, match="unsupported value type"):
        PlatformOptimizationConfiguration.from_dict({"spark": {"spark.a": value}})


def test_non_mapping_spark_rejected():
    with pytest.raises(ValueError, match="expected a mapping"):
        PlatformOptimizationConfiguration.from_dict({"spark": ["spark.a=1"]})


def test_spark_only_change_moves_hash_but_not_drift_marker():
    baseline = UnifiedTuningConfiguration()
    tuned = UnifiedTuningConfiguration.from_dict(
        {"platform_optimizations": {"spark": {"spark.sql.shuffle.partitions": "200"}}}
    )

    assert tuned.get_configuration_hash() != baseline.get_configuration_hash()
    assert _platform_hash(tuned) == _platform_hash(baseline)

    layout_changed = UnifiedTuningConfiguration()
    layout_changed.platform_optimizations.auto_compact_enabled = True
    assert _platform_hash(layout_changed) != _platform_hash(baseline)


def test_non_empty_spark_reports_session_config_tuning_type():
    empty = UnifiedTuningConfiguration().to_dict()
    assert "session_config" not in _tuning_types_present(empty)

    tuned = UnifiedTuningConfiguration.from_dict(
        {"platform_optimizations": {"spark": {"spark.sql.shuffle.partitions": "200"}}}
    )
    assert "session_config" in _tuning_types_present(tuned.to_dict())


@pytest.mark.parametrize("key", ["spark.sql.shuffle.partitoins", "spark.serializer", "spark.sql.catalogImplementation"])
def test_unknown_keys_are_rejected_with_a_suggestion_and_the_override_hint(key: str):
    with pytest.raises(ValueError, match=f"{key}.*not a known runtime-modifiable.*spark_allow_unknown_keys"):
        PlatformOptimizationConfiguration.from_dict({"spark": {key: "x"}})


def test_a_typo_names_the_closest_known_key():
    with pytest.raises(ValueError, match="did you mean 'spark.sql.shuffle.partitions'"):
        PlatformOptimizationConfiguration.from_dict({"spark": {"sql.shuffle.partitoins": "8"}})


def test_the_override_flag_admits_unknown_keys_and_round_trips():
    config = UnifiedTuningConfiguration.from_dict(
        {"platform_optimizations": {"spark": {"spark.custom.vendor.flag": "1"}, "spark_allow_unknown_keys": True}}
    )
    assert config.platform_optimizations.spark == {"spark.custom.vendor.flag": "1"}
    rebuilt = UnifiedTuningConfiguration.from_dict(config.to_dict())
    assert rebuilt.platform_optimizations.spark == config.platform_optimizations.spark
    assert rebuilt.platform_optimizations.spark_allow_unknown_keys is True


@pytest.mark.parametrize(
    "key",
    [
        "spark.sql.extensions",
        "spark.driver.memory",
        "spark.plugins",
        "spark.hadoop.fs.s3a.access.key",
        "spark.hadoop.fs.azure.account.key.store",
        "spark.sql.sas.token",
    ],
)
def test_the_override_flag_never_admits_static_adapter_or_credential_keys(key: str):
    with pytest.raises(ValueError, match=key):
        PlatformOptimizationConfiguration.from_dict({"spark": {key: "x"}, "spark_allow_unknown_keys": True})


def test_the_override_flag_must_be_a_boolean():
    with pytest.raises(ValueError, match="spark_allow_unknown_keys"):
        PlatformOptimizationConfiguration(spark_allow_unknown_keys="yes")


def test_every_allowed_key_is_accepted_and_none_is_a_denied_class():
    from benchbox.core.tuning.interface import SPARK_SESSION_CONFIG_ALLOWED_KEYS

    config = PlatformOptimizationConfiguration.from_dict(
        {"spark": dict.fromkeys(SPARK_SESSION_CONFIG_ALLOWED_KEYS, "1")}
    )
    assert set(config.spark) == set(SPARK_SESSION_CONFIG_ALLOWED_KEYS)


def test_gluten_keys_are_accepted_without_the_override_flag():
    config = PlatformOptimizationConfiguration.from_dict({"spark": {"spark.gluten.sql.columnar.shuffle": "true"}})
    assert config.spark == {"spark.gluten.sql.columnar.shuffle": "true"}
