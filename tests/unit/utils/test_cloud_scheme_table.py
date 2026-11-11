# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from pathlib import Path

import pytest

from benchbox.utils.cloud_storage import (
    _CLOUD_SCHEMES,
    _SCHEME_BY_NAME,
    CloudStagingPath,
    cloud_provider_family,
    create_path_handler,
    is_adls_path,
    is_cloud_path,
    validate_cloud_credentials,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


ALL_SPELLINGS = [
    ("s3://bucket/x", "aws"),
    ("gs://bucket/x", "gcp"),
    ("gcs://bucket/x", "gcp"),
    ("az://container/x", "azure"),
    ("azure://container/x", "azure"),
    ("abfss://container@account.dfs.core.windows.net/x", "azure"),
    ("abfs://container@account.dfs.core.windows.net/x", "azure"),
    ("dbfs:/Volumes/catalog/schema/volume", "databricks"),
]


class TestEverySchemeIsWiredEverywhere:
    @pytest.mark.parametrize(("path", "family"), ALL_SPELLINGS)
    def test_recognised_scheme_classifies_as_cloud(self, path, family):
        assert is_cloud_path(path), path

    @pytest.mark.parametrize(("path", "family"), ALL_SPELLINGS)
    def test_recognised_scheme_never_yields_a_relative_path(self, path, family):
        handler = create_path_handler(path)
        assert not (isinstance(handler, Path) and not handler.is_absolute()), (path, handler)

    @pytest.mark.parametrize(("path", "family"), ALL_SPELLINGS)
    def test_recognised_scheme_reports_its_family(self, path, family):
        assert cloud_provider_family(path) == family, path

    @pytest.mark.parametrize(("path", "family"), ALL_SPELLINGS)
    def test_scheme_with_credentials_declares_env_vars(self, path, family):
        if family == "databricks":
            pytest.skip("dbfs credentials are checked by the adapter")
        assert validate_cloud_credentials(path)["env_vars"], path


class TestAbfsNoLongerSpillsToCwd:
    ABFS = "abfs://container@account.dfs.core.windows.net/data"

    def test_abfs_is_cloud(self):
        assert is_cloud_path(self.ABFS)

    def test_abfs_stages_locally_like_abfss(self):
        assert is_adls_path(self.ABFS)
        handler = create_path_handler(self.ABFS)
        assert isinstance(handler, CloudStagingPath)
        assert handler.cloud_target == self.ABFS

    def test_abfs_is_not_remapped_to_azure_blob(self):
        handler = create_path_handler(self.ABFS)
        assert "account.dfs.core.windows.net" in handler.cloud_target
        assert not str(handler).startswith("az://")

    def test_abfs_creates_no_directory_in_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        create_path_handler(self.ABFS)
        assert list(tmp_path.iterdir()) == []


class TestAliasesAreLossless:
    @pytest.mark.parametrize(
        ("alias", "canonical"),
        [("gcs://bucket/a/b", "gs://bucket/a/b"), ("azure://container/a/b", "az://container/a/b")],
    )
    def test_alias_matches_canonical_handler(self, alias, canonical):
        assert type(create_path_handler(alias)) is type(create_path_handler(canonical))
        assert str(create_path_handler(alias)) == str(create_path_handler(canonical))

    @pytest.mark.parametrize(
        ("alias", "canonical"),
        [
            ("gcs://b/x", "gs://b/x"),
            ("azure://c/x", "az://c/x"),
            ("abfs://c@a.dfs.core.windows.net/x", "abfss://c@a.dfs.core.windows.net/x"),
        ],
    )
    def test_alias_matches_canonical_family_and_credentials(self, alias, canonical):
        assert cloud_provider_family(alias) == cloud_provider_family(canonical)
        assert validate_cloud_credentials(alias)["env_vars"] == validate_cloud_credentials(canonical)["env_vars"]


class TestTableIsWellFormed:
    def test_no_duplicate_spellings(self):
        spellings = [n for s in _CLOUD_SCHEMES for n in (s.canonical, *s.aliases)]
        assert len(spellings) == len(set(spellings)), spellings

    def test_every_spelling_resolves_to_its_entry(self):
        for scheme in _CLOUD_SCHEMES:
            for name in (scheme.canonical, *scheme.aliases):
                assert _SCHEME_BY_NAME[name] is scheme, name

    def test_unknown_scheme_is_still_local(self):
        assert not is_cloud_path("ftp://host/path")
        assert cloud_provider_family("ftp://host/path") is None
        assert create_path_handler("ftp://host/path") == Path("ftp://host/path")

    def test_snowflake_stage_still_routes_to_staging(self):
        handler = create_path_handler("@~/benchbox")
        assert isinstance(handler, CloudStagingPath)
        assert handler.cloud_target == "@~/benchbox"


