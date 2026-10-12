from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from benchbox.core.execution_engine import ExecutionEngineReceipt
from benchbox.core.execution_variant import VariantIdError, derive_variant_id, resolve_variant_platform_id
from benchbox.core.platform_registry import PlatformRegistry
from benchbox.core.results.anonymization import AnonymizationManager
from benchbox.core.results.builder import RunConfigInput
from benchbox.core.results.environment import PlatformComputeMetadata, PlatformDeploymentMetadata
from benchbox.core.results.loader import load_result_file, reconstruct_benchmark_results
from benchbox.core.results.models import QueryExecution
from benchbox.core.results.schema import SchemaV2Validator, build_result_payload
from tests.fixtures.result_dict_fixtures import make_benchmark_results, make_v2_result_dict

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.mark.parametrize(
    ("deployment", "engine", "gateway", "expected"),
    [
        (None, "default", None, "snowflake"),
        (None, "photon", None, "snowflake+photon"),
        ("other", "default", None, "snowflake~other"),
        (None, "default", "greybeam", "snowflake@greybeam"),
        ("other", "photon", None, "snowflake~other+photon"),
        ("other", "default", "greybeam", "snowflake~other@greybeam"),
        (None, "photon", "greybeam", "snowflake+photon@greybeam"),
        ("other", "photon", "greybeam", "snowflake~other+photon@greybeam"),
    ],
)
def test_variant_components(deployment, engine, gateway, expected):
    assert derive_variant_id("snowflake", deployment, engine, gateway) == expected


def test_manifest_default_and_native_gateway_do_not_change_identity():
    default = PlatformRegistry.get_default_deployment("clickhouse")
    assert default is not None
    assert derive_variant_id("clickhouse", default, "default", "native") == "clickhouse"
    assert resolve_variant_platform_id(["Polars (DataFrame)"], "dataframe") == "polars-df"


@pytest.mark.parametrize("field", ["platform", "deployment", "requested_engine", "gateway"])
@pytest.mark.parametrize("separator", ["~", "+", "@"])
def test_variant_rejects_ambiguous_components(field, separator):
    values = {"platform": "snowflake", field: f"bad{separator}value"}
    with pytest.raises(VariantIdError):
        derive_variant_id(**values)


def test_schema_fields_are_ordered_and_round_trip():
    receipt = ExecutionEngineReceipt(
        requested="streaming",
        applied="streaming",
        applied_class="streaming",
        applied_native={"collect": {"engine": "streaming"}},
        resolution="explicit",
        observed="streaming",
        observed_source="explain",
    )
    result = make_benchmark_results(
        platform="polars-df",
        platform_info={
            "platform_type": "polars",
            "execution_mode": "dataframe",
            "execution_engine": receipt,
            "gateway": {"routed": True, "name": "custom"},
        },
        platform_compute={"source": "requested", "size": "small", "resource_kind": "cluster", "resource": "private"},
        platform_deployment={"source": "requested", "selected_class": "self_hosted", "selected": "other"},
        execution_metadata={"run_config": RunConfigInput(execution_engine="streaming").to_dict()},
        query_results=[QueryExecution(query_id="1", execution_time_ms=10, rows_returned=1, execution_engine="unknown")],
    )
    payload = build_result_payload(result)
    SchemaV2Validator().validate(payload)
    assert payload["result_schema_version"] == payload["version"] == "2.3"
    platform = payload["platform"]
    assert list(platform)[:6] == ["name", "version", "client_version", "variant", "execution_engine", "gateway"]
    assert list(platform["execution_engine"]) == [
        "requested",
        "applied",
        "applied_class",
        "applied_native",
        "resolution",
        "observed",
        "observed_source",
    ]
    assert list(platform["gateway"]) == ["name", "routed"]
    assert list(platform["compute"])[:3] == ["resource", "resource_kind", "size"]
    assert list(platform["deployment"])[:2] == ["selected", "selected_class"]
    assert platform["variant"] == "polars-df~other+streaming@custom"
    assert payload["config"]["execution_engine"] == "streaming"
    assert list(payload["config"]) == ["mode", "execution_engine"]
    assert payload["queries"][0]["execution_engine"] == "unknown"
    assert list(payload["queries"][0]).index("execution_engine") == list(payload["queries"][0]).index("status") + 1
    loaded = reconstruct_benchmark_results(payload)
    assert loaded.execution_engine == platform["execution_engine"]
    assert build_result_payload(loaded)["queries"][0]["execution_engine"] == "unknown"
    assert build_result_payload(loaded)["platform"]["execution_engine"] == platform["execution_engine"]


def test_absent_producers_do_not_fill_new_fields():
    result = make_benchmark_results(
        platform="polars-df", query_results=[QueryExecution(query_id="1", execution_time_ms=1)]
    )
    payload = build_result_payload(result)
    assert payload["platform"]["variant"] == "polars-df"
    assert "execution_engine" not in payload["platform"]
    assert "gateway" not in payload["platform"]
    assert "execution_engine" not in payload["config"]
    assert "execution_engine" not in payload["queries"][0]
    assert "resource" not in PlatformComputeMetadata().to_dict()
    assert "selected" not in PlatformDeploymentMetadata().to_dict()
    assert "execution_engine" not in RunConfigInput().to_dict()


@pytest.mark.parametrize("engine", ["default", "in-memory", "streaming"])
def test_current_polars_producer_request_defines_variant(engine, tmp_path):
    from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

    adapter = PolarsDataFrameAdapter(execution_engine=engine, working_dir=tmp_path)
    result = make_benchmark_results(
        platform="polars-df",
        platform_info={"configuration": adapter.get_platform_info()},
    )
    payload = build_result_payload(result)
    expected = "polars-df" if engine == "default" else f"polars-df+{engine}"
    assert payload["platform"]["variant"] == expected
    assert payload["platform"]["execution_engine"]["requested"] == engine


@pytest.mark.parametrize("engine", ["default", "in-memory", "streaming"])
def test_config_only_engine_request_survives_load_export(engine):
    bundle = make_v2_result_dict(version="2.3", platform="polars-df", config={"execution_engine": engine})
    original = copy.deepcopy(bundle)
    loaded = reconstruct_benchmark_results(bundle)
    exported = build_result_payload(loaded)
    SchemaV2Validator().validate(exported)
    assert exported["config"]["execution_engine"] == engine
    assert "execution_engine" not in exported["platform"]
    assert reconstruct_benchmark_results(exported).execution_engine == loaded.execution_engine
    assert bundle == original


@pytest.mark.parametrize("recorded_variant", [None, "polars-df"])
def test_legacy_engine_request_keeps_existing_identity(recorded_variant):
    bundle = make_v2_result_dict(version="2.2", platform="polars-df")
    bundle["platform"]["config"] = {"engine_requested": "streaming"}
    if recorded_variant:
        bundle["platform"]["variant"] = recorded_variant
    else:
        bundle["platform"].pop("variant", None)
    original = copy.deepcopy(bundle)
    loaded = reconstruct_benchmark_results(bundle)
    assert loaded.execution_engine["requested"] == "streaming"
    assert build_result_payload(loaded)["platform"]["variant"] == "polars-df"
    assert bundle == original


def test_native_receipt_cannot_leak_credentials_at_producer_boundary():
    result = make_benchmark_results(
        platform="polars-df",
        platform_info={"execution_engine": {"requested": "streaming", "applied_native": {"password": "SENTINEL"}}},
    )
    payload = build_result_payload(result)
    assert "SENTINEL" not in json.dumps(payload)
    assert payload["platform"]["execution_engine"]["requested"] == "streaming"


@pytest.mark.parametrize(
    ("platform_config", "options", "requested", "resolution"),
    [
        ({}, {}, "default", None),
        ({}, {"streaming": True}, "streaming", "legacy_option"),
        ({}, {"streaming": "true"}, "streaming", "legacy_option"),
        ({}, {"streaming": False}, "default", None),
        ({"engine_requested": "in-memory"}, {}, "in-memory", "explicit"),
        ({"engine_requested": "streaming"}, {}, "streaming", "explicit"),
        ({"engine_requested": "default"}, {}, "default", "version_default"),
        ({"engine_requested": "default"}, {"streaming": True}, "default", "version_default"),
    ],
)
def test_legacy_requested_mapping_does_not_invent_execution(platform_config, options, requested, resolution):
    bundle = make_v2_result_dict(version="2.2", config={"platform_options": options})
    bundle["platform"]["config"] = platform_config
    original = copy.deepcopy(bundle)
    loaded = reconstruct_benchmark_results(bundle)
    assert loaded.execution_engine["requested"] == requested
    assert loaded.execution_engine["applied"] == loaded.execution_engine["observed"] == "unknown"
    assert loaded.execution_engine.get("resolution") == resolution
    assert bundle == original


def test_existing_corpus_copies_load_without_changing_bytes(tmp_path):
    corpus = Path(__file__).resolve().parents[4] / "results-data" / "bundles"
    selected = []
    for path in sorted(corpus.glob("*.json")):
        if path.name.endswith((".manifest.json", ".plans.json", ".tuning.json", ".applied.json", ".override.json")):
            continue
        data = json.loads(path.read_bytes())
        if data.get("result_schema_version", data.get("version")) == "2.2" and not data["platform"].get(
            "config", {}
        ).get("engine_requested"):
            selected.append(path)
        if len(selected) == 3:
            break
    assert len(selected) == 3
    for source in selected:
        before = source.read_bytes()
        target = tmp_path / source.name
        target.write_bytes(before)
        loaded, _ = load_result_file(target)
        assert loaded.execution_engine == {"requested": "default", "applied": "unknown", "observed": "unknown"}
        assert target.read_bytes() == source.read_bytes() == before


def test_athena_legacy_product_mapping():
    bundle = make_v2_result_dict(version="2.2", platform="Athena")
    bundle["platform"]["compute"] = {"engine": "athena", "workgroup": "private"}
    loaded = reconstruct_benchmark_results(bundle)
    assert loaded.platform_compute == {"product": "athena", "workgroup": "private"}
    assert bundle["platform"]["compute"]["engine"] == "athena"


def test_public_vocabulary_privacy_and_fixed_point():
    payload = {
        "config": {
            "execution_engine": "streaming",
            "compute_resource": "private",
            "gateway_host": "private.example.com",
        },
        "platform": {
            "execution_engine": {
                "requested": "streaming",
                "applied": "streaming",
                "applied_class": "streaming",
                "applied_native": {"host": "private.example.com", "engine": "private-engine"},
            },
            "gateway": {"name": "greybeam", "routed": True},
            "compute": {"resource": "private", "resource_kind": "warehouse", "size": "small"},
            "config": {"engine": "private-engine", "enginename": "private-name"},
        },
    }
    manager = AnonymizationManager()
    published = manager.anonymize_result_payload(payload)
    assert published["config"]["execution_engine"] == "streaming"
    assert "compute_resource" not in published["config"]
    assert published["config"]["gateway_host"].startswith("endpoint_")
    engine = published["platform"]["execution_engine"]
    assert engine["requested"] == engine["applied"] == engine["applied_class"] == "streaming"
    assert engine["applied_native"]["host"].startswith("host_")
    assert engine["applied_native"]["engine"].startswith("engine_")
    assert published["platform"]["gateway"] == payload["platform"]["gateway"]
    assert published["platform"]["compute"] == {"resource_kind": "warehouse", "size": "small"}
    assert all(value.startswith("engine_") for value in published["platform"]["config"].values())
    assert manager.anonymize_result_payload(published) == published
