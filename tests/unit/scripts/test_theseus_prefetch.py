from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest

from _project.scripts import theseus_bootstrap as bootstrap, theseus_prefetch as prefetch

pytestmark = [pytest.mark.unit, pytest.mark.fast]

NAME = "theseus-reviewer"
VERSION = "9.9.9"
URLS = [
    "https://example.test/theseus_reviewer-9.9.9-py3-none-any.whl",
    "https://example.test/theseus_reviewer-9.9.9-py3-none-any.retained.whl",
]


def _wheel() -> bytes:
    dist = "theseus_reviewer-9.9.9.dist-info"
    entries = {
        "theseus_reviewer/__init__.py": b"",
        f"{dist}/METADATA": f"Metadata-Version: 2.1\nName: {NAME}\nVersion: {VERSION}\n".encode(),
        f"{dist}/WHEEL": b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    rows = io.StringIO()
    writer = csv.writer(rows, lineterminator="\n")
    for path, content in entries.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).decode().rstrip("=")
        writer.writerow([path, f"sha256={digest}", str(len(content))])
    writer.writerow([f"{dist}/RECORD", "", ""])
    entries[f"{dist}/RECORD"] = rows.getvalue().encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in entries.items():
            archive.writestr(path, content)
    return buffer.getvalue()


def _repo(tmp_path: Path, wheel: bytes) -> Path:
    root = tmp_path / "repo"
    (root / ".github").mkdir(parents=True)
    lock = {
        "schema": 1,
        "package": NAME,
        "version": VERSION,
        "repository": "joeharris76/theseus",
        "source_commit": "a" * 40,
        "source_sha256": "b" * 64,
        "delta_sha256": "c" * 64,
        "artifact": {"sha256": hashlib.sha256(wheel).hexdigest(), "urls": URLS},
        "dependencies": [],
        "workflow_sha": "a" * 40,
    }
    (root / ".github" / "theseus-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    identity = ["-c", "user.name=T", "-c", "user.email=t@example.com"]
    subprocess.run(["git", "init", "-q", str(root)], check=True, env=env)
    subprocess.run(["git", "-C", str(root), "add", ".github/theseus-lock.json"], check=True, env=env)
    subprocess.run(["git", "-C", str(root), *identity, "commit", "-q", "-m", "lock"], check=True, env=env)
    return root


def test_the_install_cache_honours_the_explicit_override_then_the_host_home() -> None:
    assert prefetch.install_cache({"THESEUS_INSTALL_CACHE": "/x/cache"}) == Path("/x/cache")
    assert prefetch.install_cache({"BENCHBOX_TEST_HOST_HOME": "/h"}) == Path("/h/.cache/theseus/installs")


def test_prefetch_verifies_the_locked_wheel_and_places_it_where_the_install_reads_it(tmp_path: Path) -> None:
    wheel = _wheel()
    root = _repo(tmp_path, wheel)
    cache = tmp_path / "cache"
    fetched: list[str] = []

    def fetch(url: str) -> bytes:
        fetched.append(url)
        return wheel

    assert prefetch.run(root, "HEAD", cache, fetch) == 0

    cached = cache / "artifacts" / f"{hashlib.sha256(wheel).hexdigest()}.whl"
    assert cached.read_bytes() == wheel
    assert fetched == [URLS[0]]


def test_a_cached_wheel_is_not_fetched_again(tmp_path: Path) -> None:
    wheel = _wheel()
    root = _repo(tmp_path, wheel)
    cache = tmp_path / "cache"
    assert prefetch.run(root, "HEAD", cache, lambda url: wheel) == 0

    def refuse(url: str) -> bytes:
        raise AssertionError("the cached wheel must be reused")

    assert prefetch.run(root, "HEAD", cache, refuse) == 0


def test_a_wheel_whose_hash_differs_from_the_lock_is_refused_and_never_cached(tmp_path: Path, capsys) -> None:
    wheel = _wheel()
    root = _repo(tmp_path, wheel)
    cache = tmp_path / "cache"

    assert prefetch.run(root, "HEAD", cache, lambda url: wheel + b"tampered") == 1

    assert "refused" in capsys.readouterr().err
    assert list((cache / "artifacts").iterdir()) == []


def test_an_unreachable_source_reports_not_verified_and_caches_nothing(tmp_path: Path, capsys) -> None:
    root = _repo(tmp_path, _wheel())
    cache = tmp_path / "cache"

    def down(url: str) -> bytes:
        raise bootstrap.BootstrapError("cannot download: connection refused")

    assert prefetch.run(root, "HEAD", cache, down) == 0

    out = capsys.readouterr().out
    assert out.startswith(prefetch.WARNING)
    assert "reported as not run locally" in out
    assert list((cache / "artifacts").iterdir()) == []


def test_curl_fetch_refuses_a_non_https_url() -> None:
    with pytest.raises(bootstrap.BootstrapError):
        prefetch.curl_fetch("http://example.test/x.whl")
