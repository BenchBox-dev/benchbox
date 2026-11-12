from __future__ import annotations

import json
from datetime import datetime

import pytest

from benchbox.core.results.anonymization import (
    PUBLIC_PSEUDONYM_SALT_ENV,
    AnonymizationConfig,
    AnonymizationManager,
    MissingPublicPseudonymSaltError,
    require_public_pseudonym_salt,
    resolve_public_pseudonym_salt,
)
from benchbox.core.results.exporter import ResultExporter
from benchbox.core.results.models import BenchmarkResults

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_resolve_prefers_explicit_over_env() -> None:
    assert resolve_public_pseudonym_salt(explicit="  a  ", environ={PUBLIC_PSEUDONYM_SALT_ENV: "b"}) == "a"


def test_resolve_reads_env() -> None:
    assert resolve_public_pseudonym_salt(environ={PUBLIC_PSEUDONYM_SALT_ENV: "deploy-secret"}) == "deploy-secret"


def test_resolve_empty_is_unset() -> None:
    assert resolve_public_pseudonym_salt(explicit="  ") is None
    assert resolve_public_pseudonym_salt(environ={PUBLIC_PSEUDONYM_SALT_ENV: ""}) is None


def test_require_raises_when_missing() -> None:
    with pytest.raises(MissingPublicPseudonymSaltError, match=PUBLIC_PSEUDONYM_SALT_ENV):
        require_public_pseudonym_salt(environ={})


def test_from_public_environ_require_salt() -> None:
    cfg = AnonymizationConfig.from_public_environ(
        environ={PUBLIC_PSEUDONYM_SALT_ENV: "secret"},
        require_salt=True,
    )
    assert cfg.machine_id_salt == "secret"
    with pytest.raises(MissingPublicPseudonymSaltError):
        AnonymizationConfig.from_public_environ(environ={}, require_salt=True)


def test_from_public_environ_soft_read_allows_empty() -> None:
    cfg = AnonymizationConfig.from_public_environ(environ={})
    assert cfg.machine_id_salt is None


_RAW_ENDPOINT = "https://warehouse.example.invalid/query"
_RAW_DATABASE = "analytics_db"


def _minimal_result_with_endpoint() -> BenchmarkResults:
    return BenchmarkResults(
        benchmark_name="TPC-H",
        platform="duckdb",
        scale_factor=0.01,
        execution_id="salt-export-test",
        timestamp=datetime(2026, 1, 1, 12, 0, 0),
        duration_seconds=1.0,
        total_queries=1,
        successful_queries=1,
        failed_queries=0,
        query_results=[
            {
                "query_id": "Q1",
                "execution_time_seconds": 0.1,
                "rows_returned": 1,
                "status": "SUCCESS",
                "run_type": "measurement",
            }
        ],
        platform_info={
            "name": "duckdb",
            "version": "1.0",
            "endpoint": _RAW_ENDPOINT,
            "database_name": _RAW_DATABASE,
        },
    )


def test_exporter_soft_reads_env_salt_for_public_hash(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    salt = "deployment-private-export-salt"
    monkeypatch.setenv(PUBLIC_PSEUDONYM_SALT_ENV, salt)

    expected = AnonymizationManager(AnonymizationConfig(machine_id_salt=salt))._hash_public_identifier(
        _RAW_ENDPOINT,
        "endpoint",
    )
    empty_expected = AnonymizationManager(AnonymizationConfig())._hash_public_identifier(
        _RAW_ENDPOINT,
        "endpoint",
    )
    assert expected != empty_expected, "salt must change retained-field digest"

    exporter = ResultExporter(output_dir=tmp_path, anonymize=True)
    assert exporter.anonymization_manager is not None
    assert exporter.anonymization_manager.config.machine_id_salt == salt

    exported = exporter.export_result(_minimal_result_with_endpoint(), formats=["json"])
    with open(exported["json"], encoding="utf-8") as handle:
        payload = json.load(handle)

    endpoint = payload["platform"]["config"]["endpoint"]
    assert endpoint == expected
    assert endpoint != empty_expected


def test_exporter_without_env_salt_uses_private_salt_file(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(PUBLIC_PSEUDONYM_SALT_ENV, raising=False)

    empty_expected = AnonymizationManager(AnonymizationConfig())._hash_public_identifier(
        _RAW_ENDPOINT,
        "endpoint",
    )

    exporter = ResultExporter(output_dir=tmp_path, anonymize=True)
    assert exporter.anonymization_manager is not None
    assert exporter.anonymization_manager.config.machine_id_salt

    exported = exporter.export_result(_minimal_result_with_endpoint(), formats=["json"])
    with open(exported["json"], encoding="utf-8") as handle:
        payload = json.load(handle)

    assert payload["platform"]["config"]["endpoint"] != empty_expected
    assert payload["export"]["anonymized"] is True


def test_exporter_explicit_config_overrides_env(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(PUBLIC_PSEUDONYM_SALT_ENV, "from-env")
    explicit = AnonymizationConfig(machine_id_salt="from-arg")
    exporter = ResultExporter(output_dir=tmp_path, anonymize=True, anonymization_config=explicit)
    assert exporter.anonymization_manager is not None
    assert exporter.anonymization_manager.config.machine_id_salt == "from-arg"
