from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path

import pytest

from benchbox.core.data_fetch import compute_manifest_hash, fetch_data
from benchbox.core.data_fetch.locking import archive_lock, interprocess_lock

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


ALPHA_PAYLOAD = b"alpha-table-row-bytes"
BETA_PAYLOAD = b"beta-table-row-bytes"
ALPHA_SHA = hashlib.sha256(ALPHA_PAYLOAD).hexdigest()
BETA_SHA = hashlib.sha256(BETA_PAYLOAD).hexdigest()
ARCHIVE_SHA = "00" * 32
MANIFEST_HASH_PLACEHOLDER = "a" * 64


def _write_manifest(tmp: Path) -> Path:
    body = (
        f'dataset_version    = "test-v1"\n'
        f'manifest_hash      = "{MANIFEST_HASH_PLACEHOLDER}"\n'
        f'data_archive_hash  = "{ARCHIVE_SHA}"\n'
        f'url                = "https://example.com/test.tar.zst"\n'
        f'archive_sha256     = "{ARCHIVE_SHA}"\n'
        f'license_file       = "DATA-LICENSE.md"\n\n'
        f"[[tables]]\n"
        f'name      = "alpha"\n'
        f'file      = "alpha.parquet"\n'
        f'sha256    = "{ALPHA_SHA}"\n'
        f"row_count = {len(ALPHA_PAYLOAD)}\n\n"
        f"[[tables]]\n"
        f'name      = "beta"\n'
        f'file      = "beta.parquet"\n'
        f'sha256    = "{BETA_SHA}"\n'
        f"row_count = {len(BETA_PAYLOAD)}\n"
    )
    p = tmp / "data_manifest.toml"
    p.write_text(body)
    p.write_text(body.replace(MANIFEST_HASH_PLACEHOLDER, compute_manifest_hash(p)))
    return p


def test_archive_lock_serializes_across_threads(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "archive.tar.zst"
    events: list[str] = []
    events_lock = threading.Lock()
    barrier = threading.Barrier(2)

    def worker(tag: str) -> None:
        barrier.wait()
        with archive_lock(target):
            with events_lock:
                events.append(f"enter-{tag}")
            time.sleep(0.05)
            with events_lock:
                events.append(f"exit-{tag}")

    threads = [threading.Thread(target=worker, args=(t,)) for t in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(events) == 4
    assert events[0].startswith("enter") and events[1] == f"exit-{events[0].split('-')[1]}"
    assert events[2].startswith("enter") and events[3] == f"exit-{events[2].split('-')[1]}"


def test_archive_lock_remains_the_general_interprocess_lock_alias() -> None:
    assert archive_lock is interprocess_lock


def test_concurrent_fetch_downloads_once_and_reuses(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    call_count = [0]
    count_lock = threading.Lock()
    barrier = threading.Barrier(2)

    def fake_downloader(url, dest, expected_sha256=None):
        with count_lock:
            call_count[0] += 1
        time.sleep(0.05)
        (out_dir / "alpha.parquet").write_bytes(ALPHA_PAYLOAD)
        (out_dir / "beta.parquet").write_bytes(BETA_PAYLOAD)
        Path(dest).write_bytes(b"pretend-archive-bytes")
        return Path(dest)

    results: dict[str, object] = {}
    errors: dict[str, BaseException] = {}

    def worker(tag: str) -> None:
        barrier.wait()
        try:
            results[tag] = fetch_data("test", manifest_path, out_dir, downloader=fake_downloader)
        except BaseException as exc:
            errors[tag] = exc

    threads = [threading.Thread(target=worker, args=(t,)) for t in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"unexpected errors: {errors}"
    assert call_count[0] == 1
    assert results["a"] == out_dir
    assert results["b"] == out_dir


