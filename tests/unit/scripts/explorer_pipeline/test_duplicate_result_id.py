from __future__ import annotations

import json
from pathlib import Path

import pytest

from _project.scripts.explorer_pipeline.pipeline import (
    _DIGEST_CHUNK_BYTES,
    DuplicateResultIdError,
    ExplorerPipeline,
    _publication_digest,
)
from _project.scripts.explorer_pipeline.transformer import BundleTransformer
from tests.unit.scripts.explorer_pipeline.conftest import MINIMAL_BUNDLE

pytestmark = [pytest.mark.unit, pytest.mark.fast]

COLLIDING_ID = "tpch-duckdb-sf0.1-20260315-deadbeef"


@pytest.fixture()
def force_id_collision(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        BundleTransformer,
        "result_id_from_bundle",
        lambda *args, **kwargs: COLLIDING_ID,
    )


def _write_bundle(bundles_dir: Path, name: str, *, total_duration_ms: int) -> Path:
    bundles_dir.mkdir(parents=True, exist_ok=True)
    payload = json.loads(json.dumps(MINIMAL_BUNDLE))
    payload["run"]["total_duration_ms"] = total_duration_ms
    path = bundles_dir / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_duplicate_result_id_with_differing_content_fails_the_build(
    tmp_path: Path,
    force_id_collision: None,
) -> None:
    data_dir = tmp_path / "data"
    bundles = data_dir / "bundles"
    _write_bundle(bundles, "first.json", total_duration_ms=45000)
    _write_bundle(bundles, "second.json", total_duration_ms=99000)

    with pytest.raises(DuplicateResultIdError) as excinfo:
        ExplorerPipeline().run(data_dir, tmp_path / "out")

    message = str(excinfo.value)
    assert COLLIDING_ID in message
    assert "first.json" in message
    assert "second.json" in message


def test_duplicate_result_id_error_escapes_the_per_bundle_handler() -> None:
    assert not issubclass(DuplicateResultIdError, ValueError)
    assert not issubclass(DuplicateResultIdError, (json.JSONDecodeError, KeyError, TypeError))


def test_duplicate_result_id_with_identical_content_publishes_once(
    tmp_path: Path,
    force_id_collision: None,
) -> None:
    data_dir = tmp_path / "data"
    bundles = data_dir / "bundles"
    _write_bundle(bundles, "original.json", total_duration_ms=45000)
    _write_bundle(bundles / "nested", "copy.json", total_duration_ms=45000)

    output = tmp_path / "out"
    ExplorerPipeline().run(data_dir, output)

    published = sorted(p.name for p in (output / "bundles").glob("*.json"))
    assert published == [f"{COLLIDING_ID}.json"]


def test_duplicate_result_id_identical_primaries_with_differing_companions_fail(
    tmp_path: Path,
    force_id_collision: None,
) -> None:
    data_dir = tmp_path / "data"
    bundles = data_dir / "bundles"
    _write_bundle(bundles, "first.json", total_duration_ms=45000)
    second = _write_bundle(bundles / "nested", "second.json", total_duration_ms=45000)
    second.with_name("second.plans.json").write_text(json.dumps({"q1": "PLAN"}), encoding="utf-8")

    with pytest.raises(DuplicateResultIdError):
        ExplorerPipeline().run(data_dir, tmp_path / "out")


def test_duplicate_result_id_guard_leaves_the_normal_path_untouched(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    bundles = data_dir / "bundles"
    _write_bundle(bundles, "first.json", total_duration_ms=45000)
    _write_bundle(bundles, "second.json", total_duration_ms=99000)

    output = tmp_path / "out"
    ExplorerPipeline().run(data_dir, output)

    published = sorted(p.name for p in (output / "bundles").glob("*.json"))
    assert len(published) == 2, f"expected both bundles published, got {published}"


def test_duplicate_result_id_digest_streams_companions_larger_than_one_chunk(tmp_path: Path) -> None:
    bundles = tmp_path / "bundles"
    bundle = _write_bundle(bundles, "big.json", total_duration_ms=45000)
    public_raw = bundle.read_bytes()

    baseline = _publication_digest(bundle, public_raw)

    plans = bundle.with_name("big.plans.json")
    plans.write_bytes(b"a" * (_DIGEST_CHUNK_BYTES + 512))
    with_companion = _publication_digest(bundle, public_raw)
    assert with_companion != baseline

    payload = bytearray(b"a" * (_DIGEST_CHUNK_BYTES + 512))
    payload[_DIGEST_CHUNK_BYTES + 100] = ord("b")
    plans.write_bytes(bytes(payload))
    assert _publication_digest(bundle, public_raw) != with_companion


