# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import os
import tempfile
from pathlib import Path, PureWindowsPath

import pytest

from benchbox.utils.cloud_storage import (
    CloudStagingPath,
    create_path_handler,
    get_cloud_path_info,
    is_cloud_path,
    is_snowflake_stage_path,
    validate_cloud_credentials,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


STAGE_PATHS = [
    "@~",
    "@~/benchbox",
    "@~/a/b/c",
    "@%orders",
    "@%orders/part-0",
    '@%"My Table"',
    "@my_stage",
    "@my_stage/data",
    "@my_db.my_schema.my_stage/data",
    '@"My Stage"/data',
    "@stage$name/data",
]

NON_STAGE_PATHS = [
    "abfss://container@account.dfs.core.windows.net/path",
    "azure://container@account/path",
    "s3://bucket/prefix",
    "gs://bucket/prefix",
    "dbfs:/Volumes/catalog/schema/volume",
    "user@host:/remote/path",
    "team@example.com",
    "/tmp/@~/already-spilled",
    "@",
    "@/foo",
    "@.",
    "@%",
    "/local/path",
    "./relative/path",
    "~/home/path",
    "",
]


class TestIsSnowflakeStagePath:
    @pytest.mark.parametrize("path", STAGE_PATHS)
    def test_recognises_stage_paths(self, path):
        assert is_snowflake_stage_path(path), f"{path!r} should classify as a stage"

    @pytest.mark.parametrize("path", NON_STAGE_PATHS)
    def test_rejects_non_stage_paths(self, path):
        assert not is_snowflake_stage_path(path), f"{path!r} should not classify as a stage"

    def test_azure_uri_with_at_sign_is_not_a_stage(self):
        azure = "abfss://container@account.dfs.core.windows.net/path"
        assert not is_snowflake_stage_path(azure)
        assert is_cloud_path(azure)

    def test_accepts_path_objects_and_rejects_non_string_input(self):
        assert is_snowflake_stage_path(Path("@~/benchbox"))
        assert not is_snowflake_stage_path(None)
        assert not is_snowflake_stage_path(123)

    def test_windows_flavoured_path_object_still_classifies_as_a_stage(self):
        assert is_snowflake_stage_path(PureWindowsPath("@~/benchbox"))
        assert is_snowflake_stage_path(PureWindowsPath("@my_stage/sub/dir"))

    def test_windows_flavoured_local_path_is_not_a_stage(self):
        assert not is_snowflake_stage_path(PureWindowsPath(r"C:\data\benchbox"))
        assert not is_snowflake_stage_path(PureWindowsPath(r"C:\data\@notastage"))


class TestStageClassification:
    @pytest.mark.parametrize("path", STAGE_PATHS)
    def test_stage_paths_are_not_local(self, path):
        assert is_cloud_path(path), f"{path!r} still classifies as local"

    def test_existing_schemes_are_unchanged(self):
        for path in [
            "s3://bucket/p",
            "gs://bucket/p",
            "gcs://bucket/p",
            "az://container/p",
            "abfss://container@account.dfs.core.windows.net/p",
            "azure://container/p",
            "dbfs:/Volumes/c/s/v",
        ]:
            assert is_cloud_path(path), path
        for path in ["/local/path", "./rel", "~/home", "", "C:\\Windows\\path"]:
            assert not is_cloud_path(path), path

    def test_get_cloud_path_info_reports_stage_provider(self):
        info = get_cloud_path_info("@my_stage/data/part-0")
        assert info["is_cloud"] is True
        assert info["provider"] == "snowflake_stage"
        assert info["bucket"] == "my_stage"
        assert info["path"] == "data/part-0"
        assert info["stage_info"] == {"stage": "my_stage", "sub_path": "data/part-0"}

    def test_get_cloud_path_info_user_stage_without_sub_path(self):
        info = get_cloud_path_info("@~")
        assert info["provider"] == "snowflake_stage"
        assert info["bucket"] == "~"
        assert info["path"] == ""

    def test_quoted_stage_identifier_supports_escaped_quotes(self):
        info = get_cloud_path_info('@"My ""Stage"""/data')

        assert info["provider"] == "snowflake_stage"
        assert info["bucket"] == '"My ""Stage"""'
        assert info["path"] == "data"

    @pytest.mark.parametrize(
        ("path", "stage", "sub_path"),
        [
            ("@~/benchbox", "~", "benchbox"),
            ("@%orders/part-0", "%orders", "part-0"),
            ("@my_db.my_schema.my_stage/data", "my_db.my_schema.my_stage", "data"),
            ('@"My/Stage"/data', '"My/Stage"', "data"),
            ('@"My Stage"', '"My Stage"', ""),
        ],
    )
    def test_stage_and_sub_path_split_matches_the_grammar(self, path, stage, sub_path):
        info = get_cloud_path_info(path)
        assert info["stage_info"] == {"stage": stage, "sub_path": sub_path}
        assert info["bucket"] == stage
        assert info["path"] == sub_path


class TestStagePathHandler:
    @pytest.mark.parametrize("path", STAGE_PATHS)
    def test_handler_is_never_a_relative_path(self, path):
        handler = create_path_handler(path)
        assert not (isinstance(handler, Path) and not handler.is_absolute()), handler

    def test_handler_is_a_staging_path_carrying_the_stage_target(self):
        handler = create_path_handler("@~/benchbox")
        assert isinstance(handler, CloudStagingPath)
        assert handler.cloud_target == "@~/benchbox"
        assert Path(str(handler)).is_absolute()

    def test_resolving_a_stage_creates_no_directory_in_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        create_path_handler("@~/benchbox")
        assert not (tmp_path / "@~").exists()
        assert list(tmp_path.iterdir()) == []

    def test_stage_handler_does_not_require_cloudpathlib(self, monkeypatch):
        import benchbox.utils.cloud_storage as cs

        def _fail():
            raise AssertionError("cloudpathlib must not be loaded for stage paths")

        monkeypatch.setattr(cs, "_load_cloudpathlib", _fail)
        handler = cs.create_path_handler("@~/benchbox")
        assert isinstance(handler, CloudStagingPath)

    def test_stage_credential_validation_does_not_require_cloudpathlib(self, monkeypatch):
        import benchbox.utils.cloud_storage as cs

        monkeypatch.setattr(cs, "_load_cloudpathlib", lambda: (_ for _ in ()).throw(AssertionError("must stay lazy")))

        result = validate_cloud_credentials("@~/benchbox")

        assert result["valid"] is True
        assert result["provider"] == "snowflake_stage"

    def test_local_paths_still_return_plain_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            assert create_path_handler(tmp) == Path(tmp)
        assert create_path_handler("./rel") == Path("./rel")
        assert create_path_handler(os.path.expanduser("~")) == Path(os.path.expanduser("~"))
