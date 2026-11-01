from __future__ import annotations

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
    monkeypatch.setattr(dl.time, "sleep", lambda _s: None)
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
