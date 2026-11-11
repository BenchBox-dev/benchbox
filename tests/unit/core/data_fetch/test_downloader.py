from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable

import pytest
import requests

from benchbox.core.data_fetch import ChecksumMismatchError, DownloadError
from benchbox.core.data_fetch.downloader import download, sha256_of

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _Resp:
    def __init__(self, status_code: int, body: bytes):
        self.status_code = status_code
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, chunk_size: int) -> Iterable[bytes]:
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i : i + chunk_size]


class _PartialThenError(_Resp):
    def __init__(self, status_code: int, body: bytes, fail_after: int):
        super().__init__(status_code, body)
        self._fail_after = fail_after

    def iter_content(self, chunk_size: int) -> Iterable[bytes]:
        sent = 0
        for i in range(0, len(self._body), chunk_size):
            chunk = self._body[i : i + chunk_size]
            yield chunk
            sent += len(chunk)
            if sent >= self._fail_after:
                raise requests.ConnectionError("dropped mid-stream")


class _Session:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def get(self, url, headers=None, stream=False, timeout=None):
        self.calls.append({"url": url, "headers": dict(headers or {})})
        nxt = self._responses.pop(0)
        if isinstance(nxt, BaseException):
            raise nxt
        if isinstance(nxt, _Resp):
            return nxt
        return _Resp(nxt[0], nxt[1])


def test_download_writes_payload_and_returns_path(tmp_path: Path) -> None:
    body = b"hello data_fetch world"
    sha = hashlib.sha256(body).hexdigest()
    sess = _Session([(200, body)])
    out = download("https://x.test/blob", tmp_path / "blob", expected_sha256=sha, session=sess)
    assert out.read_bytes() == body
    assert sha256_of(out) == sha


def test_download_checksum_mismatch_raises(tmp_path: Path) -> None:
    body = b"payload"
    sess = _Session([(200, body)])
    with pytest.raises(ChecksumMismatchError) as excinfo:
        download(
            "https://x.test/blob",
            tmp_path / "blob",
            expected_sha256="0" * 64,
            session=sess,
        )
    err = excinfo.value
    assert err.expected_sha256 == "0" * 64
    assert err.actual_sha256 == hashlib.sha256(body).hexdigest()


def test_download_retries_then_succeeds(tmp_path: Path) -> None:
    sleeps: list[float] = []
    body = b"recovered"
    sess = _Session(
        [
            requests.ConnectionError("boom"),
            (200, body),
        ]
    )
    out = download(
        "https://x.test/blob",
        tmp_path / "blob",
        expected_sha256=None,
        session=sess,
        max_retries=3,
        sleep=lambda s: sleeps.append(s),
    )
    assert out.read_bytes() == body
    assert sleeps == [1.0]


def test_download_retries_exhausted_raises(tmp_path: Path) -> None:
    sleeps: list[float] = []
    sess = _Session(
        [
            requests.ConnectionError("boom1"),
            requests.ConnectionError("boom2"),
            requests.ConnectionError("boom3"),
        ]
    )
    with pytest.raises(DownloadError, match="failed after 3 attempts"):
        download(
            "https://x.test/blob",
            tmp_path / "blob",
            expected_sha256=None,
            session=sess,
            max_retries=3,
            sleep=lambda s: sleeps.append(s),
        )
    assert sleeps == [1.0, 2.0]


def test_download_resumes_within_call_after_dropped_connection(tmp_path: Path) -> None:
    full = b"abcdef" * 100
    dest = tmp_path / "blob"
    sess = _Session(
        [
            _PartialThenError(200, full, fail_after=120),
            (206, full[120:]),
        ]
    )
    out = download(
        "https://x.test/blob",
        dest,
        expected_sha256=hashlib.sha256(full).hexdigest(),
        session=sess,
        chunk_size=60,
        sleep=lambda s: None,
    )
    assert out.read_bytes() == full
    assert "Range" not in sess.calls[0]["headers"]
    assert sess.calls[1]["headers"]["Range"] == "bytes=120-"


def test_download_restarts_when_server_ignores_range(tmp_path: Path) -> None:
    full = b"abcdef" * 100
    dest = tmp_path / "blob"
    sess = _Session(
        [
            _PartialThenError(206, full, fail_after=120),
            (200, full),
        ]
    )
    out = download(
        "https://x.test/blob",
        dest,
        expected_sha256=hashlib.sha256(full).hexdigest(),
        session=sess,
        chunk_size=60,
        sleep=lambda s: None,
    )
    assert out.read_bytes() == full
    assert sess.calls[1]["headers"]["Range"] == "bytes=120-"


def test_download_publishes_atomically_dest_never_partial(tmp_path: Path) -> None:
    full = b"abcdef" * 100
    dest = tmp_path / "blob"
    seen_dest_during_stream: list[bool] = []
    part_seen_during_stream: list[bool] = []

    class _Observing(_Resp):
        def iter_content(self, chunk_size: int):
            for i in range(0, len(self._body), chunk_size):
                seen_dest_during_stream.append(dest.exists())
                part_seen_during_stream.append(any(p.name.endswith(".part") for p in tmp_path.iterdir()))
                yield self._body[i : i + chunk_size]

    sess = _Session([_Observing(200, full)])
    out = download(
        "https://x.test/blob",
        dest,
        expected_sha256=hashlib.sha256(full).hexdigest(),
        session=sess,
        chunk_size=60,
    )
    assert out.read_bytes() == full
    assert seen_dest_during_stream and not any(seen_dest_during_stream)
    assert all(part_seen_during_stream)
    assert [p.name for p in tmp_path.iterdir()] == ["blob"]


def test_download_mismatch_leaves_dest_absent_and_keeps_rejected(tmp_path: Path) -> None:
    body = b"payload"
    dest = tmp_path / "blob"
    sess = _Session([(200, body)])
    with pytest.raises(ChecksumMismatchError) as excinfo:
        download("https://x.test/blob", dest, expected_sha256="0" * 64, session=sess)
    assert not dest.exists()
    rejected = tmp_path / "blob.rejected"
    assert rejected.read_bytes() == body
    assert excinfo.value.path == str(rejected)
    assert not any(p.name.endswith(".part") for p in tmp_path.iterdir())


