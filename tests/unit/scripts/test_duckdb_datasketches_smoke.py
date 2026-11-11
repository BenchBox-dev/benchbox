import duckdb_datasketches_smoke as smoke
import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_known_upstream_drift_allows_reviewed_missing_family() -> None:
    message = "Catalog Error: Scalar Function with name datasketch_theta does not exist!"

    assert smoke._is_known_upstream_drift("2e38607", "theta", message)


def test_known_upstream_drift_requires_reviewed_extension_version() -> None:
    message = "Catalog Error: Scalar Function with name datasketch_theta does not exist!"

    assert not smoke._is_known_upstream_drift("new-build", "theta", message)


def test_known_upstream_drift_requires_allowlisted_family() -> None:
    message = "Catalog Error: Scalar Function with name datasketch_cpc does not exist!"

    assert not smoke._is_known_upstream_drift("2e38607", "cpc", message)


def test_known_upstream_drift_requires_missing_function_error() -> None:
    message = "Catalog Error: binder failed while planning datasketch_theta"

    assert not smoke._is_known_upstream_drift("2e38607", "theta", message)


