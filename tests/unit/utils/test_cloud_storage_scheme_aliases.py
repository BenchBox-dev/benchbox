# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from pathlib import Path
from types import SimpleNamespace

import pytest

from benchbox.utils.cloud_storage import (
    CloudStagingPath,
    create_path_handler,
    get_cloud_path_info,
    is_adls_path,
    is_cloud_path,
    validate_cloud_credentials,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


ALL_ACCEPTED_SCHEMES = [
    "s3://bucket/prefix",
    "gs://bucket/prefix",
    "gcs://bucket/prefix",
    "az://container/prefix",
    "azure://container/prefix",
    "abfss://container@account.dfs.core.windows.net/prefix",
    "dbfs:/Volumes/catalog/schema/volume",
]

ALIAS_EQUIVALENTS = [
    ("gcs://bucket/a/b", "gs://bucket/a/b"),
    ("azure://container/a/b", "az://container/a/b"),
]


class TestEveryAcceptedSchemeResolves:
    @pytest.mark.parametrize("path", ALL_ACCEPTED_SCHEMES)
    def test_accepted_scheme_produces_a_handler(self, path):
        assert is_cloud_path(path), path
        handler = create_path_handler(path)
        assert handler is not None

    @pytest.mark.parametrize("path", ALL_ACCEPTED_SCHEMES)
    def test_accepted_scheme_never_yields_a_relative_local_path(self, path):
        handler = create_path_handler(path)
        assert not (isinstance(handler, Path) and not handler.is_absolute()), handler


class TestSchemeAliasesAreLossless:
    @pytest.mark.parametrize(("alias", "canonical"), ALIAS_EQUIVALENTS)
    def test_alias_resolves_to_the_same_handler_as_the_canonical_scheme(self, alias, canonical):
        alias_handler = create_path_handler(alias)
        canonical_handler = create_path_handler(canonical)
        assert type(alias_handler) is type(canonical_handler)
        assert str(alias_handler) == str(canonical_handler)

    @pytest.mark.parametrize(("alias", "canonical"), ALIAS_EQUIVALENTS)
    def test_alias_preserves_container_and_key(self, alias, canonical):
        assert str(create_path_handler(alias)).endswith("/a/b")

    def test_uppercase_alias_scheme_is_normalised(self):
        assert str(create_path_handler("GCS://bucket/a")) == str(create_path_handler("gs://bucket/a"))

    def test_alias_rewrite_does_not_touch_other_schemes(self):
        assert str(create_path_handler("s3://bucket/a")) == "s3://bucket/a"

    def test_alias_inside_a_path_is_not_rewritten(self):
        handler = create_path_handler("s3://bucket/azure://nested")
        assert "azure://nested" in str(handler)

    def test_get_cloud_path_info_still_reports_the_original_provider(self):
        assert get_cloud_path_info("gcs://bucket/p")["provider"] == "gcs"
        assert get_cloud_path_info("azure://container/p")["provider"] == "azure"


class TestAdlsPathsStageLocally:
    def test_is_adls_path_matches_only_abfss(self):
        assert is_adls_path("abfss://c@a.dfs.core.windows.net/p")
        assert not is_adls_path("az://c/p")
        assert not is_adls_path("s3://b/p")
        assert not is_adls_path("/local/abfss")
        assert not is_adls_path(None)

    def test_abfss_stages_locally_and_keeps_the_full_uri(self):
        uri = "abfss://container@account.dfs.core.windows.net/path/to/data"
        handler = create_path_handler(uri)
        assert isinstance(handler, CloudStagingPath)
        assert handler.cloud_target == uri
        assert Path(str(handler)).is_absolute()

    def test_abfss_is_not_remapped_to_an_azure_blob_path(self):
        handler = create_path_handler("abfss://container@account.dfs.core.windows.net/p")
        assert "account.dfs.core.windows.net" in handler.cloud_target
        assert not str(handler).startswith("az://")

    def test_abfss_creates_no_directory_in_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        create_path_handler("abfss://c@a.dfs.core.windows.net/p")
        assert list(tmp_path.iterdir()) == []

    def test_cloud_staging_path_delegates_recursive_glob(self, tmp_path):
        stage = tmp_path / "stage"
        nested = stage / "nested"
        nested.mkdir(parents=True)
        expected = nested / "result.json"
        expected.write_text("{}", encoding="utf-8")

        handler = CloudStagingPath(stage, "s3://bucket/results")

        assert list(handler.rglob("*.json")) == [expected]


class TestCredentialValidationHandlesAliases:
    @pytest.mark.parametrize(("alias", "canonical"), ALIAS_EQUIVALENTS)
    def test_alias_validates_identically_to_the_canonical_scheme(self, alias, canonical, monkeypatch):
        for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "GOOGLE_APPLICATION_CREDENTIALS"):
            monkeypatch.setenv(var, "dummy")
        monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_NAME", "acct")
        monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_KEY", "key")

        import benchbox.utils.cloud_storage as cs

        built: list[str] = []

        def build_offline(path, provider, cloud_path_cls):
            built.append(path)
            return SimpleNamespace(exists=lambda: True)

        monkeypatch.setattr(cs, "_build_cloud_path_for_validation", build_offline)

        alias_result = validate_cloud_credentials(alias)
        canonical_result = validate_cloud_credentials(canonical)

        assert built == [alias, canonical]
        assert alias_result["valid"] == canonical_result["valid"]
        assert "does not begin with a known prefix" not in str(alias_result["error"])

    def test_adls_is_not_probed_with_cloudpathlib(self, monkeypatch):
        monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_NAME", "acct")
        monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_KEY", "key")

        import benchbox.utils.cloud_storage as cs

        monkeypatch.setattr(cs, "_load_cloudpathlib", lambda: (_ for _ in ()).throw(AssertionError("must stay lazy")))

        result = validate_cloud_credentials("abfss://c@a.dfs.core.windows.net/p")

        assert result["valid"] is True
        assert result["error"] is None

    def test_adls_still_reports_missing_azure_env_vars(self, monkeypatch):
        monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_NAME", raising=False)
        monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_KEY", raising=False)

        result = validate_cloud_credentials("abfss://c@a.dfs.core.windows.net/p")

        assert result["valid"] is False
        assert "AZURE_STORAGE_ACCOUNT_NAME" in result["error"]

    def test_snowflake_stage_validation_stays_independent_of_cloudpathlib(self, monkeypatch):
        import benchbox.utils.cloud_storage as cs

        monkeypatch.setattr(cs, "_load_cloudpathlib", lambda: (_ for _ in ()).throw(AssertionError("must stay lazy")))

        result = validate_cloud_credentials("@~/benchbox")

        assert result == {
            "valid": True,
            "provider": "snowflake_stage",
            "error": None,
            "env_vars": [],
        }


class TestExistingBehaviourUnchanged:
    def test_dbfs_still_returns_a_databricks_path(self):
        from benchbox.utils.cloud_storage import DatabricksPath

        handler = create_path_handler("dbfs:/Volumes/c/s/v")
        assert isinstance(handler, DatabricksPath)
        assert handler.dbfs_target == "dbfs:/Volumes/c/s/v"

    def test_snowflake_stage_still_returns_a_staging_path(self):
        handler = create_path_handler("@~/benchbox")
        assert isinstance(handler, CloudStagingPath)
        assert handler.cloud_target == "@~/benchbox"

    def test_local_paths_are_untouched(self):
        assert create_path_handler("/local/path") == Path("/local/path")
        assert create_path_handler("./rel") == Path("./rel")

    def test_unknown_scheme_still_classifies_as_local(self):
        assert not is_cloud_path("ftp://host/path")
        assert create_path_handler("ftp://host/path") == Path("ftp://host/path")
