# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import MagicMock, call, patch

import pytest

from benchbox.platforms.credentials.shared import prompt_default_output_location

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _make_args(**overrides):
    defaults = {
        "cred_manager": MagicMock(),
        "console": MagicMock(),
        "credentials": {},
        "platform_name": "BigQuery",
        "path_scheme": "gs://",
        "storage_label": "Google Cloud Storage",
        "permission_note": "storage.objects.create permission",
        "bucket": None,
    }
    defaults.update(overrides)
    return defaults


class TestDeclinesToConfigure:
    def test_user_declines_does_not_save_credentials(self, monkeypatch):
        monkeypatch.setattr("benchbox.platforms.credentials.shared.Confirm.ask", lambda *a, **k: False)
        args = _make_args()
        prompt_default_output_location(**args)
        args["cred_manager"].set_platform_credentials.assert_not_called()
        args["cred_manager"].save_credentials.assert_not_called()

    def test_user_declines_shows_tip_message(self, monkeypatch):
        monkeypatch.setattr("benchbox.platforms.credentials.shared.Confirm.ask", lambda *a, **k: False)
        args = _make_args()
        prompt_default_output_location(**args)
        calls = [str(c) for c in args["console"].print.call_args_list]
        assert any("--output" in c for c in calls)


class TestValidCloudPathConfirmed:
    def test_saves_credentials_with_correct_path(self, monkeypatch):
        confirm_answers = iter([True, True])
        monkeypatch.setattr(
            "benchbox.platforms.credentials.shared.Confirm.ask",
            lambda *a, **k: next(confirm_answers),
        )
        monkeypatch.setattr(
            "benchbox.platforms.credentials.shared.Prompt.ask",
            lambda *a, **k: "gs://my-bucket/data",
        )
        monkeypatch.setattr(
            "benchbox.utils.cloud_storage.is_cloud_path",
            lambda path: True,
        )

        args = _make_args()
        creds = {}
        args["credentials"] = creds
        prompt_default_output_location(**args)

        assert creds["default_output_location"] == "gs://my-bucket/data"
        args["cred_manager"].set_platform_credentials.assert_called_once()
        args["cred_manager"].save_credentials.assert_called_once()

    def test_set_platform_credentials_called_with_lowercase_name(self, monkeypatch):
        confirm_answers = iter([True, True])
        monkeypatch.setattr(
            "benchbox.platforms.credentials.shared.Confirm.ask",
            lambda *a, **k: next(confirm_answers),
        )
        monkeypatch.setattr(
            "benchbox.platforms.credentials.shared.Prompt.ask",
            lambda *a, **k: "gs://bucket/path",
        )
        monkeypatch.setattr(
            "benchbox.utils.cloud_storage.is_cloud_path",
            lambda path: True,
        )

        args = _make_args(platform_name="BigQuery")
        creds = {}
        args["credentials"] = creds
        prompt_default_output_location(**args)

        call_args = args["cred_manager"].set_platform_credentials.call_args
        assert call_args[0][0] == "bigquery"


class TestInvalidPathThenValid:
    def test_invalid_path_user_declines_then_retries_with_valid(self, monkeypatch):

        confirm_sequence = iter([True, False, True])
        prompt_sequence = iter(["bad-path", "gs://bucket/path"])

        monkeypatch.setattr(
            "benchbox.platforms.credentials.shared.Confirm.ask",
            lambda *a, **k: next(confirm_sequence),
        )
        monkeypatch.setattr(
            "benchbox.platforms.credentials.shared.Prompt.ask",
            lambda *a, **k: next(prompt_sequence),
        )

        def _is_cloud(path):
            return path.startswith("gs://")

        monkeypatch.setattr("benchbox.utils.cloud_storage.is_cloud_path", _is_cloud)

        args = _make_args()
        creds = {}
        args["credentials"] = creds
        prompt_default_output_location(**args)

        assert creds["default_output_location"] == "gs://bucket/path"


class TestInvalidPathUserProceedsAnyway:
    def test_non_cloud_path_accepted_with_warning(self, monkeypatch):

        confirm_sequence = iter([True, True, True])
        monkeypatch.setattr(
            "benchbox.platforms.credentials.shared.Confirm.ask",
            lambda *a, **k: next(confirm_sequence),
        )
        monkeypatch.setattr(
            "benchbox.platforms.credentials.shared.Prompt.ask",
            lambda *a, **k: "not-a-cloud-path",
        )
        monkeypatch.setattr(
            "benchbox.utils.cloud_storage.is_cloud_path",
            lambda path: False,
        )

        args = _make_args()
        creds = {}
        args["credentials"] = creds
        prompt_default_output_location(**args)

        console_calls = [str(c) for c in args["console"].print.call_args_list]
        assert any("Warning" in c or "doesn't look" in c for c in console_calls)
        assert creds["default_output_location"] == "not-a-cloud-path"


class TestBucketSuggestedDefault:
    def test_bucket_shown_in_suggested_default(self, monkeypatch):

        monkeypatch.setattr(
            "benchbox.platforms.credentials.shared.Confirm.ask",
            lambda *a, **k: True,
        )
        monkeypatch.setattr(
            "benchbox.platforms.credentials.shared.Prompt.ask",
            lambda *a, **k: "",
        )
        monkeypatch.setattr(
            "benchbox.utils.cloud_storage.is_cloud_path",
            lambda path: False,
        )
        args = _make_args(bucket="my-bucket")
        prompt_default_output_location(**args)

        all_calls = " ".join(str(c) for c in args["console"].print.call_args_list)
        assert "my-bucket" in all_calls


class TestEmptyPathInput:
    def test_empty_path_shows_skipping_message(self, monkeypatch):
        confirm_sequence = iter([True])
        monkeypatch.setattr(
            "benchbox.platforms.credentials.shared.Confirm.ask",
            lambda *a, **k: next(confirm_sequence),
        )
        monkeypatch.setattr(
            "benchbox.platforms.credentials.shared.Prompt.ask",
            lambda *a, **k: "",
        )
        monkeypatch.setattr(
            "benchbox.utils.cloud_storage.is_cloud_path",
            lambda path: False,
        )

        args = _make_args()
        creds = {}
        args["credentials"] = creds
        prompt_default_output_location(**args)

        assert "default_output_location" not in creds
        console_calls = [str(c) for c in args["console"].print.call_args_list]
        assert any("skip" in c.lower() for c in console_calls)


