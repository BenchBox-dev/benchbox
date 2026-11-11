from __future__ import annotations

import pytest

from benchbox.core.exceptions import ConfigurationError
from benchbox.platforms.gcp._gcs_path import GcsStagingPath, parse_gcs_staging_dir

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class TestParseGcsStagingDir:
    def test_returns_bucket_and_empty_prefix_when_no_path(self) -> None:
        parsed = parse_gcs_staging_dir("gs://my-bucket")
        assert parsed == GcsStagingPath(bucket="my-bucket", prefix="", uri="gs://my-bucket")

    def test_returns_bucket_and_prefix_when_path_given(self) -> None:
        parsed = parse_gcs_staging_dir("gs://my-bucket/staging/bench")
        assert parsed.bucket == "my-bucket"
        assert parsed.prefix == "staging/bench"
        assert parsed.uri == "gs://my-bucket/staging/bench"

    def test_strips_trailing_slash_on_uri(self) -> None:
        parsed = parse_gcs_staging_dir("gs://my-bucket/staging/")
        assert parsed.uri == "gs://my-bucket/staging"
        assert parsed.prefix == "staging/"

    def test_deep_prefix_preserved(self) -> None:
        parsed = parse_gcs_staging_dir("gs://b/a/b/c/d")
        assert parsed.prefix == "a/b/c/d"

    def test_missing_raises_configuration_error(self) -> None:
        with pytest.raises(ConfigurationError, match="gcs_staging_dir is required"):
            parse_gcs_staging_dir(None)

    def test_empty_raises_configuration_error(self) -> None:
        with pytest.raises(ConfigurationError, match="gcs_staging_dir is required"):
            parse_gcs_staging_dir("")

    def test_wrong_scheme_raises_configuration_error(self) -> None:
        with pytest.raises(ConfigurationError, match="Must start with gs://"):
            parse_gcs_staging_dir("s3://my-bucket/path")

    def test_bare_slash_raises_configuration_error(self) -> None:
        with pytest.raises(ConfigurationError, match="Must start with gs://"):
            parse_gcs_staging_dir("/my-bucket/path")


