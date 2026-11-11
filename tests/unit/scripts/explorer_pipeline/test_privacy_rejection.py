from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from _project.scripts.explorer_pipeline.pipeline import (
    ExplorerPipeline,
    PrivacyRejectionError,
)
from _project.scripts.explorer_publish import explorer_publish
from tests.unit.scripts.explorer_pipeline.conftest import MINIMAL_BUNDLE

pytestmark = [pytest.mark.unit, pytest.mark.fast]

LEAKING_PATH = "/Users/alice/secret/db"


def _write_bundle(bundles_dir: Path, name: str, *, leaking: bool = False) -> Path:
    bundles_dir.mkdir(parents=True, exist_ok=True)
    payload = json.loads(json.dumps(MINIMAL_BUNDLE))
    if leaking:
        payload["config"] = {LEAKING_PATH: 1.2}
    path = bundles_dir / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_privacy_rejection_in_a_bundle_fails_the_build(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_bundle(data_dir / "bundles", "leaky.json", leaking=True)

    with pytest.raises(PrivacyRejectionError) as excinfo:
        ExplorerPipeline().run(data_dir, tmp_path / "out")

    message = str(excinfo.value)
    assert "privacy check failed" in message
    assert LEAKING_PATH not in message


def test_privacy_rejection_error_escapes_the_per_bundle_handler() -> None:
    assert not issubclass(PrivacyRejectionError, ValueError)
    assert not issubclass(PrivacyRejectionError, (json.JSONDecodeError, KeyError, TypeError))


def test_privacy_rejection_in_a_plans_companion_fails_the_build(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    bundle = _write_bundle(data_dir / "bundles", "clean.json")
    bundle.with_name("clean.plans.json").write_text(
        json.dumps({"per_path_timings": {LEAKING_PATH: 1.2}}),
        encoding="utf-8",
    )

    with pytest.raises(PrivacyRejectionError) as excinfo:
        ExplorerPipeline().run(data_dir, tmp_path / "out")

    assert "plans privacy check failed" in str(excinfo.value)


def test_privacy_rejection_in_an_applied_receipt_fails_the_build(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    bundle = _write_bundle(data_dir / "bundles", "clean.json")
    bundle.with_name("clean.applied.json").write_text(
        json.dumps({"receipt": {"per_path_timings": {LEAKING_PATH: 1.2}}}),
        encoding="utf-8",
    )

    with pytest.raises(PrivacyRejectionError) as excinfo:
        ExplorerPipeline().run(data_dir, tmp_path / "out")

    assert "applied receipt privacy check failed" in str(excinfo.value)


def test_privacy_rejection_leaves_a_clean_corpus_publishing(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_bundle(data_dir / "bundles", "first.json")

    output = tmp_path / "out"
    ExplorerPipeline().run(data_dir, output)

    assert list((output / "bundles").glob("*.json")), "clean corpus published nothing"


def test_privacy_rejection_still_tolerates_a_malformed_companion(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    bundle = _write_bundle(data_dir / "bundles", "clean.json")
    bundle.with_name("clean.plans.json").write_text("{not json", encoding="utf-8")

    output = tmp_path / "out"
    ExplorerPipeline().run(data_dir, output)

    assert list((output / "bundles").glob("*.json")), "a malformed companion should not stop publication"


def test_privacy_rejection_makes_the_publish_command_exit_non_zero(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_bundle(data_dir / "bundles", "leaky.json", leaking=True)

    result = CliRunner().invoke(
        explorer_publish,
        ["build", "--data-dir", str(data_dir), "--output", str(tmp_path / "out")],
    )

    assert result.exit_code != 0, "a leaking bundle produced a successful publish"
    normalized_output = " ".join(result.output.split())
    assert "privacy check failed" in normalized_output
    assert LEAKING_PATH not in result.output


