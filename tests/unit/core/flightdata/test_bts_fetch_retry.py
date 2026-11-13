from __future__ import annotations

import http.client
import io
import urllib.error

import pytest

from benchbox.core.flightdata import downloader as dl

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _make(tmp_path):
    return dl.FlightDataDownloader(scale_factor=0.1, output_dir=tmp_path)


def _script(monkeypatch, outcomes):
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return _Response(outcome)

    monkeypatch.setattr(dl.tls, "urlopen", fake_urlopen)
    monkeypatch.setattr(dl, "_sleep", lambda _s: None)
    return calls


def test_transient_failure_is_retried(tmp_path, monkeypatch):
    calls = _script(monkeypatch, [urllib.error.URLError("reset"), b"zip-bytes"])

    assert _make(tmp_path)._fetch_bts_zip("https://example.test/a.zip") == b"zip-bytes"
    assert len(calls) == 2


def test_server_error_is_retried_until_attempts_exhausted(tmp_path, monkeypatch):
    err = urllib.error.HTTPError("https://example.test/a.zip", 503, "unavailable", {}, None)
    calls = _script(monkeypatch, [err] * dl.BTS_FETCH_ATTEMPTS)

    with pytest.raises(urllib.error.HTTPError):
        _make(tmp_path)._fetch_bts_zip("https://example.test/a.zip")
    assert len(calls) == dl.BTS_FETCH_ATTEMPTS


def test_client_error_fails_at_once(tmp_path, monkeypatch):
    err = urllib.error.HTTPError("https://example.test/a.zip", 404, "missing", {}, None)
    calls = _script(monkeypatch, [err, b"zip-bytes"])

    with pytest.raises(urllib.error.HTTPError):
        _make(tmp_path)._fetch_bts_zip("https://example.test/a.zip")
    assert len(calls) == 1


def _record_sleeps(monkeypatch, jitter):
    sleeps: list[float] = []
    monkeypatch.setattr(dl, "_sleep", sleeps.append)
    monkeypatch.setattr(dl, "_jitter", jitter)
    return sleeps


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.URLError("reset"),
        ConnectionResetError("reset"),
        TimeoutError("timed out"),
        http.client.IncompleteRead(b"abc", 10),
        urllib.error.HTTPError("https://example.test/a.zip", 500, "error", {}, None),
        urllib.error.HTTPError("https://example.test/a.zip", 503, "unavailable", {}, None),
        urllib.error.HTTPError("https://example.test/a.zip", 429, "slow down", {}, None),
    ],
)
def test_retryable_errors_recover_on_the_last_attempt(tmp_path, monkeypatch, error):
    calls = _script(monkeypatch, [error] * (dl.BTS_FETCH_ATTEMPTS - 1) + [b"zip-bytes"])

    assert _make(tmp_path)._fetch_bts_zip("https://example.test/a.zip") == b"zip-bytes"
    assert len(calls) == dl.BTS_FETCH_ATTEMPTS


@pytest.mark.parametrize("code", [400, 401, 403, 404, 410])
def test_other_client_errors_fail_at_once(tmp_path, monkeypatch, code):
    err = urllib.error.HTTPError("https://example.test/a.zip", code, "no", {}, None)
    calls = _script(monkeypatch, [err, b"zip-bytes"])

    with pytest.raises(urllib.error.HTTPError):
        _make(tmp_path)._fetch_bts_zip("https://example.test/a.zip")
    assert len(calls) == 1


def test_size_mismatch_fails_at_once(tmp_path, monkeypatch):
    class _Short(_Response):
        headers = {"Content-Length": "99"}

    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        return _Short(b"zip-bytes")

    monkeypatch.setattr(dl.tls, "urlopen", fake_urlopen)
    monkeypatch.setattr(dl, "_sleep", lambda _s: None)

    with pytest.raises(ValueError, match="size mismatch"):
        _make(tmp_path)._fetch_bts_zip("https://example.test/a.zip")
    assert len(calls) == 1


def test_checksum_mismatch_fails_at_once(tmp_path, monkeypatch):
    url = "https://example.test/a.zip"
    calls = _script(monkeypatch, [b"zip-bytes", b"zip-bytes"])
    monkeypatch.setitem(dl.PINNED_SOURCE_SHA256, url, "0" * 64)

    with pytest.raises(dl.ChecksumMismatchError):
        _make(tmp_path)._fetch_bts_zip(url)
    assert len(calls) == 1


def test_backoff_doubles_between_attempts_and_totals_at_most_two_minutes(tmp_path, monkeypatch):
    sleeps = _record_sleeps(monkeypatch, lambda _low, high: high)
    err = urllib.error.URLError("reset")
    calls = _script(monkeypatch, [err] * dl.BTS_FETCH_ATTEMPTS)
    monkeypatch.setattr(dl, "_sleep", sleeps.append)

    with pytest.raises(urllib.error.URLError):
        _make(tmp_path)._fetch_bts_zip("https://example.test/a.zip")

    assert len(calls) == dl.BTS_FETCH_ATTEMPTS
    assert sleeps == [4.0, 8.0, 16.0, 32.0, 64.0]
    assert sum(sleeps) <= 125


def test_backoff_jitter_never_drops_below_half_the_nominal_delay(tmp_path, monkeypatch):
    sleeps = _record_sleeps(monkeypatch, lambda low, _high: low)
    err = urllib.error.URLError("reset")
    _script(monkeypatch, [err] * dl.BTS_FETCH_ATTEMPTS)
    monkeypatch.setattr(dl, "_sleep", sleeps.append)

    with pytest.raises(urllib.error.URLError):
        _make(tmp_path)._fetch_bts_zip("https://example.test/a.zip")

    assert sleeps == [2.0, 4.0, 8.0, 16.0, 32.0]


def test_final_error_names_the_month_and_the_cause(tmp_path, monkeypatch):
    err = urllib.error.HTTPError("https://example.test/a.zip", 503, "unavailable", {}, None)
    _script(monkeypatch, [err] * dl.BTS_FETCH_ATTEMPTS)
    downloader = _make(tmp_path)

    with pytest.raises(RuntimeError, match=r"2024-12 \(HTTPError: HTTP Error 503: unavailable\)"):
        downloader._process_month(None, 2024, 12, 1)
