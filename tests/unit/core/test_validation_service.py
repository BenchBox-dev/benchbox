from unittest.mock import Mock

import pytest

from benchbox.core.validation import (
    PlatformValidationResult,
    ValidationResult,
    ValidationService,
    ValidationSummary,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture()
def service():
    return ValidationService()


def test_run_preflight(service, tmp_path):
    data_engine = Mock()
    db_engine = Mock()
    svc = ValidationService(data_engine=data_engine, db_engine=db_engine)

    result = ValidationResult(is_valid=True, errors=[], warnings=[])
    data_engine.validate_preflight_conditions.return_value = result

    outcome = svc.run_preflight("tpcds", 1.0, tmp_path)

    data_engine.validate_preflight_conditions.assert_called_once_with("tpcds", 1.0, tmp_path)
    assert outcome is result


def test_run_manifest(service, tmp_path):
    data_engine = Mock()
    svc = ValidationService(data_engine=data_engine)

    result = ValidationResult(is_valid=True, errors=[], warnings=[])
    data_engine.validate_generated_data.return_value = result

    manifest = tmp_path / "manifest.json"
    outcome = svc.run_manifest(manifest)

    data_engine.validate_generated_data.assert_called_once_with(manifest)
    assert outcome is result


def test_run_database(service):
    db_engine = Mock()
    svc = ValidationService(db_engine=db_engine)
    connection = object()
    result = ValidationResult(is_valid=True, errors=[], warnings=[])
    db_engine.validate_loaded_data.return_value = result

    outcome = svc.run_database(connection, "tpcds", 1.0)

    db_engine.validate_loaded_data.assert_called_once_with(connection, "tpcds", 1.0)
    assert outcome is result


def test_run_platform_with_health(service):
    adapter = Mock()
    connection = object()
    capability = ValidationResult(is_valid=True, errors=[], warnings=[])
    health = ValidationResult(is_valid=True, errors=[], warnings=["warn"])
    adapter.validate_platform_capabilities.return_value = capability
    adapter.validate_connection_health.return_value = health

    result = service.run_platform(adapter, "tpcds", connection=connection)

    adapter.validate_platform_capabilities.assert_called_once_with("tpcds")
    adapter.validate_connection_health.assert_called_once_with(connection)
    assert isinstance(result, PlatformValidationResult)
    assert result.capabilities is capability
    assert result.connection_health is health


def test_run_comprehensive_builds_results(tmp_path):
    data_engine = Mock()
    db_engine = Mock()
    svc = ValidationService(data_engine=data_engine, db_engine=db_engine)

    preflight = ValidationResult(is_valid=True, errors=[], warnings=[])
    manifest_res = ValidationResult(is_valid=True, errors=[], warnings=[])
    db_res = ValidationResult(is_valid=False, errors=["err"], warnings=[])
    platform_res = ValidationResult(is_valid=True, errors=[], warnings=[])
    data_engine.validate_preflight_conditions.return_value = preflight
    data_engine.validate_generated_data.return_value = manifest_res
    db_engine.validate_loaded_data.return_value = db_res

    adapter = Mock()
    adapter.validate_platform_capabilities.return_value = platform_res
    adapter.configure_mock(validate_connection_health=None)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}")

    results = svc.run_comprehensive(
        benchmark_type="tpcds",
        scale_factor=1.0,
        output_dir=tmp_path,
        manifest_path=manifest_path,
        connection=object(),
        platform_adapter=adapter,
    )

    assert results == [preflight, platform_res, manifest_res, db_res]


def test_summarize(service):
    results = [
        ValidationResult(is_valid=True, errors=[], warnings=[]),
        ValidationResult(is_valid=False, errors=["err"], warnings=[]),
        ValidationResult(is_valid=True, errors=[], warnings=["warn"]),
    ]

    summary = service.summarize(results)

    assert isinstance(summary, ValidationSummary)
    assert summary.total_validations == 3
    assert summary.passed_validations == 2
    assert summary.failed_validations == 1
    assert summary.warnings_count == 1


