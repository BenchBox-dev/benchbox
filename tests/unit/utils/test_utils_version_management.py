import pytest

from benchbox.utils import version as version_utils

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.mark.unit
class TestVersionConsistency:
    def test_check_version_consistency_matches_pyproject(self):
        result = version_utils.check_version_consistency()

        assert result.consistent, result.message
        assert result.sources["benchbox.__init__"] == result.sources["pyproject.toml"]

    def test_check_version_consistency_detects_mismatch(self, monkeypatch):

        monkeypatch.setattr(version_utils, "get_pyproject_version", lambda: "9.9.9")

        result = version_utils.check_version_consistency()

        assert not result.consistent
        assert "mismatch" in result.message.lower()
        assert result.sources["pyproject.toml"] == "9.9.9"

    def test_init_source_reads_the_literal_statically(self):
        import benchbox

        literal = version_utils.get_init_version()

        assert literal is not None
        assert literal == benchbox.__version__

        result = version_utils.check_version_consistency()
        assert result.sources["benchbox.__init__"] == literal

    def test_check_version_consistency_detects_init_literal_drift(self, monkeypatch):

        monkeypatch.setattr(version_utils, "_collect_documentation_versions", dict)
        monkeypatch.setattr(version_utils, "get_init_version", lambda: "0.0.0")

        result = version_utils.check_version_consistency()

        assert not result.consistent
        assert "mismatch" in result.message.lower()
        assert result.sources["benchbox.__init__"] == "0.0.0"

    def test_installed_dist_is_a_distinct_checked_source(self, monkeypatch):

        result = version_utils.check_version_consistency()
        assert "installed-dist" in result.sources

        monkeypatch.setattr(version_utils, "get_installed_dist_version", lambda: "8.8.8")
        drifted = version_utils.check_version_consistency()

        assert not drifted.consistent
        assert "installed-dist" in drifted.mismatched_sources

    def test_missing_installed_dist_is_not_an_inconsistency(self, monkeypatch):

        monkeypatch.setattr(version_utils, "_collect_documentation_versions", dict)
        monkeypatch.setattr(version_utils, "get_installed_dist_version", lambda: None)

        result = version_utils.check_version_consistency()

        assert result.consistent, result.message
        assert "installed-dist" not in result.missing_sources


@pytest.mark.unit
class TestVersionCompatibility:
    def test_is_version_compatible_within_bounds(self):
        current = version_utils.get_package_version()

        assert version_utils.is_version_compatible(min_version=current)
        assert version_utils.is_version_compatible(max_version=current)

    def test_is_version_compatible_out_of_bounds(self):
        assert not version_utils.is_version_compatible(max_version="0.0.1")

    def test_is_version_compatible_invalid_version_string(self):
        with pytest.raises(ValueError):
            version_utils.is_version_compatible(min_version="not-a-version")

    def test_ensure_version_compatible_raises_with_clear_message(self):
        with pytest.raises(RuntimeError) as excinfo:
            version_utils.ensure_version_compatible(max_version="0.0.1")

        assert "compatibility" in str(excinfo.value).lower()


@pytest.mark.unit
class TestEnhancedImportErrors:
    def test_create_import_error_includes_dependency_guidance(self):
        error = version_utils.create_import_error(
            benchmark_name="TPCDI",
            missing_dependencies=["tpcdi"],
            original_error=ModuleNotFoundError("No module named 'pandas'", name="pandas"),
        )

        message = str(error)

        assert "'pandas'" in message
        assert "tpcdi" in message
        assert "Version Information" in message
        assert "Original error" in message
