# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import hashlib
import io
import tarfile
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from benchbox.core.expected_results.download import _COMPLETE_SENTINEL

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _make_tar_gz(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, content in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
    return buf.getvalue()


class TestCacheDir:
    def test_default_cache_dir(self, monkeypatch, tmp_path):
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        from benchbox.core.expected_results import download

        result = download.get_cache_dir()
        assert result == tmp_path / ".cache" / "benchbox" / "answers"

    def test_xdg_cache_home_respected(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))

        from benchbox.core.expected_results import download

        result = download.get_cache_dir()
        assert result == tmp_path / "xdg" / "benchbox" / "answers"

    def test_tpch_cache_dir(self, monkeypatch, tmp_path):
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        from benchbox.core.expected_results import download

        result = download.get_tpch_cache_dir()
        assert result == download.get_cache_dir() / "tpch"

    def test_tpcds_cache_dir(self, monkeypatch, tmp_path):
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        from benchbox.core.expected_results import download

        result = download.get_tpcds_cache_dir()
        assert result == download.get_cache_dir() / "tpcds"


class TestAnswersBaseUrl:
    def test_default_url(self, monkeypatch):
        monkeypatch.delenv("BENCHBOX_ANSWERS_URL", raising=False)

        from benchbox.core.expected_results import download

        url = download.get_answers_base_url()
        assert url.startswith("https://")

    def test_env_var_override(self, monkeypatch):
        monkeypatch.setenv("BENCHBOX_ANSWERS_URL", "https://example.com/answers/")

        from benchbox.core.expected_results import download

        url = download.get_answers_base_url()
        assert url == "https://example.com/answers"

    def test_trailing_slash_stripped(self, monkeypatch):
        monkeypatch.setenv("BENCHBOX_ANSWERS_URL", "https://cdn.example.com/v1/")

        from benchbox.core.expected_results import download

        url = download.get_answers_base_url()
        assert not url.endswith("/")

    def test_default_url_points_to_correct_repo(self, monkeypatch):
        monkeypatch.delenv("BENCHBOX_ANSWERS_URL", raising=False)

        from benchbox.core.expected_results import download

        url = download.get_answers_base_url()
        assert "joeharris76" in url
        assert "BenchBox" in url
        assert "answers-v1" in url


class TestDownloadDisabled:
    @pytest.mark.parametrize("value", ["1", "true", "True", "TRUE", "yes", "YES"])
    def test_disabled_values(self, monkeypatch, value):
        monkeypatch.setenv("BENCHBOX_NO_DOWNLOAD", value)

        from benchbox.core.expected_results import download

        assert download.is_download_disabled() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", ""])
    def test_enabled_values(self, monkeypatch, value):
        monkeypatch.setenv("BENCHBOX_NO_DOWNLOAD", value)

        from benchbox.core.expected_results import download

        assert download.is_download_disabled() is False

    def test_unset(self, monkeypatch):
        monkeypatch.delenv("BENCHBOX_NO_DOWNLOAD", raising=False)

        from benchbox.core.expected_results import download

        assert download.is_download_disabled() is False


class TestChecksumVerification:
    def test_correct_checksum(self, tmp_path):
        from benchbox.core.expected_results.download import _verify_checksum

        content = b"tpch-answers archive bytes"
        f = tmp_path / "tpch-answers.tar.gz"
        f.write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()

        assert _verify_checksum(f, digest) is True

    def test_wrong_checksum(self, tmp_path):
        from benchbox.core.expected_results.download import _verify_checksum

        f = tmp_path / "tpch-answers.tar.gz"
        f.write_bytes(b"tampered content\n")

        assert _verify_checksum(f, "deadbeef" * 8) is False


class TestSafeExtractTar:
    def test_extracts_files(self, tmp_path):
        from benchbox.core.expected_results.download import _safe_extract_tar

        archive_bytes = _make_tar_gz({"q1.out": b"col\nrow\n", "q2.out": b"col\nrow\n"})
        archive = tmp_path / "test.tar.gz"
        archive.write_bytes(archive_bytes)

        dest = tmp_path / "extracted"
        _safe_extract_tar(archive, dest)

        assert (dest / "q1.out").read_bytes() == b"col\nrow\n"
        assert (dest / "q2.out").read_bytes() == b"col\nrow\n"

    def test_path_traversal_rejected(self, tmp_path):
        from benchbox.core.expected_results.download import _safe_extract_tar

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            info = tarfile.TarInfo(name="../../evil.txt")
            content = b"evil"
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
        archive = tmp_path / "evil.tar.gz"
        archive.write_bytes(buf.getvalue())

        dest = tmp_path / "dest"
        with pytest.raises(ValueError, match="Path traversal"):
            _safe_extract_tar(archive, dest)

    def test_sibling_directory_traversal_rejected(self, tmp_path):
        from benchbox.core.expected_results.download import _safe_extract_tar

        dest = tmp_path / "dest"
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            info = tarfile.TarInfo(name="../dest_sibling/evil.txt")
            content = b"evil"
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
        archive = tmp_path / "sibling.tar.gz"
        archive.write_bytes(buf.getvalue())

        with pytest.raises(ValueError, match="Path traversal"):
            _safe_extract_tar(archive, dest)


class TestDownloadWithRetry:
    def test_success_on_first_try(self, tmp_path):
        from benchbox.core.expected_results.download import _download_with_retry

        content = b"archive bytes"
        dest = tmp_path / "tpch-answers.tar.gz"

        with patch("benchbox.core.expected_results.download._fetch_url", return_value=content):
            result = _download_with_retry("https://example.com/tpch-answers.tar.gz", dest)

        assert result is True
        assert dest.read_bytes() == content

    def test_permanent_failure_404(self, tmp_path):
        from benchbox.core.expected_results.download import _download_with_retry

        dest = tmp_path / "missing.tar.gz"
        http_error = urllib.error.HTTPError(None, 404, "Not Found", {}, None)

        with patch("benchbox.core.expected_results.download._fetch_url", side_effect=http_error):
            result = _download_with_retry("https://example.com/missing.tar.gz", dest)

        assert result is False
        assert not dest.exists()

    def test_retries_on_transient_error(self, tmp_path):
        from benchbox.core.expected_results.download import _download_with_retry

        content = b"archive"
        dest = tmp_path / "tpch-answers.tar.gz"
        url_error = urllib.error.URLError("Connection reset")

        call_count = 0

        def side_effect(_url, **_kw):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise url_error
            return content

        with (
            patch("benchbox.core.expected_results.download._fetch_url", side_effect=side_effect),
            patch("benchbox.core.expected_results.download.time.sleep"),
        ):
            result = _download_with_retry("https://example.com/tpch-answers.tar.gz", dest)

        assert result is True
        assert call_count == 2

    def test_raises_after_all_retries(self, tmp_path):
        from benchbox.core.expected_results.download import _download_with_retry

        dest = tmp_path / "tpch-answers.tar.gz"
        url_error = urllib.error.URLError("Timeout")

        with (
            patch("benchbox.core.expected_results.download._fetch_url", side_effect=url_error),
            patch("benchbox.core.expected_results.download.time.sleep"),
        ):
            with pytest.raises(RuntimeError, match="Download failed"):
                _download_with_retry("https://example.com/tpch-answers.tar.gz", dest)


class TestDownloadTpchAnswers:
    def test_disabled_returns_none(self, monkeypatch):
        monkeypatch.setenv("BENCHBOX_NO_DOWNLOAD", "1")

        from benchbox.core.expected_results.download import download_tpch_answers

        result = download_tpch_answers()
        assert result is None

    def test_cache_hit_skips_download(self, monkeypatch, tmp_path):
        monkeypatch.delenv("BENCHBOX_NO_DOWNLOAD", raising=False)

        from benchbox.core.expected_results import download as dl_module

        cache_dir = tmp_path / "tpch"
        cache_dir.mkdir(parents=True)
        (cache_dir / "q1.out").write_text("col\nrow\n")
        (cache_dir / _COMPLETE_SENTINEL).write_text("")

        monkeypatch.setattr(dl_module, "get_tpch_cache_dir", lambda: cache_dir)
        fetch_mock = MagicMock()
        monkeypatch.setattr(dl_module, "_fetch_url", fetch_mock)

        result = dl_module.download_tpch_answers(force=False)

        assert result == cache_dir
        fetch_mock.assert_not_called()

    def test_downloads_and_extracts_archive(self, monkeypatch, tmp_path):
        monkeypatch.delenv("BENCHBOX_NO_DOWNLOAD", raising=False)
        monkeypatch.setenv("BENCHBOX_ANSWERS_URL", "https://example.com/answers")

        from benchbox.core.expected_results import download as dl_module

        cache_dir = tmp_path / "tpch"
        monkeypatch.setattr(dl_module, "get_tpch_cache_dir", lambda: cache_dir)
        monkeypatch.setattr(dl_module, "_load_checksum_manifest", dict)

        archive_bytes = _make_tar_gz({f"q{i}.out": f"col\nrow{i}\n".encode() for i in range(1, 23)})
        monkeypatch.setattr(dl_module, "_fetch_url", lambda _url, **_kw: archive_bytes)

        result = dl_module.download_tpch_answers()

        assert result == cache_dir
        assert (cache_dir / "q1.out").exists()
        assert (cache_dir / _COMPLETE_SENTINEL).exists()

    def test_force_redownloads_when_cache_warm(self, monkeypatch, tmp_path):
        monkeypatch.delenv("BENCHBOX_NO_DOWNLOAD", raising=False)
        monkeypatch.setenv("BENCHBOX_ANSWERS_URL", "https://example.com/answers")

        from benchbox.core.expected_results import download as dl_module

        cache_dir = tmp_path / "tpch"
        cache_dir.mkdir(parents=True)
        (cache_dir / "q1.out").write_text("old data\n")
        (cache_dir / _COMPLETE_SENTINEL).write_text("")

        monkeypatch.setattr(dl_module, "get_tpch_cache_dir", lambda: cache_dir)
        monkeypatch.setattr(dl_module, "_load_checksum_manifest", dict)

        archive_bytes = _make_tar_gz({"q1.out": b"new data\n"})
        monkeypatch.setattr(dl_module, "_fetch_url", lambda _url, **_kw: archive_bytes)

        result = dl_module.download_tpch_answers(force=True)

        assert result == cache_dir
        assert (cache_dir / "q1.out").read_bytes() == b"new data\n"

    def test_checksum_mismatch_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.delenv("BENCHBOX_NO_DOWNLOAD", raising=False)
        monkeypatch.setenv("BENCHBOX_ANSWERS_URL", "https://example.com/answers")

        from benchbox.core.expected_results import download as dl_module

        cache_dir = tmp_path / "tpch"
        monkeypatch.setattr(dl_module, "get_tpch_cache_dir", lambda: cache_dir)
        monkeypatch.setattr(
            dl_module,
            "_load_checksum_manifest",
            lambda: {"tpch-answers.tar.gz": "deadbeef" * 8},
        )
        monkeypatch.setattr(dl_module, "_fetch_url", lambda _url, **_kw: b"archive bytes")

        result = dl_module.download_tpch_answers()

        assert result is None

    def test_not_published_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.delenv("BENCHBOX_NO_DOWNLOAD", raising=False)
        monkeypatch.setenv("BENCHBOX_ANSWERS_URL", "https://example.com/answers")

        from benchbox.core.expected_results import download as dl_module

        cache_dir = tmp_path / "tpch"
        monkeypatch.setattr(dl_module, "get_tpch_cache_dir", lambda: cache_dir)
        http_404 = urllib.error.HTTPError(None, 404, "Not Found", {}, None)
        monkeypatch.setattr(dl_module, "_fetch_url", MagicMock(side_effect=http_404))

        result = dl_module.download_tpch_answers()

        assert result is None


class TestOfflineMode:
    def test_loader_uses_cache_when_available(self, monkeypatch, tmp_path):
        from benchbox.core.expected_results import download as dl_module
        from benchbox.core.expected_results.loader import _find_tpch_answers_dir

        cache_dir = tmp_path / "tpch"
        cache_dir.mkdir(parents=True)
        for i in range(1, 23):
            (cache_dir / f"q{i}.out").write_text(f"col\nrow{i}\n")

        monkeypatch.setattr(dl_module, "get_tpch_cache_dir", lambda: cache_dir)

        import benchbox

        fake_pkg_root = tmp_path / "pkg"
        fake_pkg_root.mkdir()
        monkeypatch.setattr(benchbox, "__file__", str(fake_pkg_root / "benchbox" / "__init__.py"))

        result = _find_tpch_answers_dir()
        assert result == cache_dir

    def test_no_download_triggered_on_cache_hit(self, monkeypatch, tmp_path):
        from benchbox.core.expected_results import download as dl_module

        cache_dir = tmp_path / "tpch"
        cache_dir.mkdir(parents=True)
        for i in range(1, 23):
            (cache_dir / f"q{i}.out").write_text(f"col\nrow{i}\n")

        monkeypatch.setattr(dl_module, "get_tpch_cache_dir", lambda: cache_dir)
        download_mock = MagicMock(return_value=None)
        monkeypatch.setattr(dl_module, "download_tpch_answers", download_mock)

        import benchbox

        fake_pkg_root = tmp_path / "pkg"
        fake_pkg_root.mkdir()
        monkeypatch.setattr(benchbox, "__file__", str(fake_pkg_root / "benchbox" / "__init__.py"))

        from benchbox.core.expected_results.loader import _find_tpch_answers_dir

        result = _find_tpch_answers_dir()
        assert result == cache_dir
        download_mock.assert_not_called()

    def test_benchbox_no_download_prevents_download(self, monkeypatch, tmp_path):
        monkeypatch.setenv("BENCHBOX_NO_DOWNLOAD", "1")

        from benchbox.core.expected_results import download as dl_module

        cache_dir = tmp_path / "tpch"
        monkeypatch.setattr(dl_module, "get_tpch_cache_dir", lambda: cache_dir)

        import benchbox

        fake_pkg_root = tmp_path / "pkg"
        fake_pkg_root.mkdir()
        monkeypatch.setattr(benchbox, "__file__", str(fake_pkg_root / "benchbox" / "__init__.py"))

        from benchbox.core.expected_results.loader import _find_tpch_answers_dir

        with pytest.raises(FileNotFoundError):
            _find_tpch_answers_dir()


class TestPartialCacheNotWarm:
    def test_missing_sentinel_triggers_redownload(self, monkeypatch, tmp_path):
        monkeypatch.delenv("BENCHBOX_NO_DOWNLOAD", raising=False)
        monkeypatch.setenv("BENCHBOX_ANSWERS_URL", "https://example.com/answers")

        from benchbox.core.expected_results import download as dl_module

        cache_dir = tmp_path / "tpch"
        cache_dir.mkdir(parents=True)
        (cache_dir / "q1.out").write_text("partial data\n")

        monkeypatch.setattr(dl_module, "get_tpch_cache_dir", lambda: cache_dir)
        monkeypatch.setattr(dl_module, "_load_checksum_manifest", dict)

        archive_bytes = _make_tar_gz({"q1.out": b"col\nfull row\n"})
        monkeypatch.setattr(dl_module, "_fetch_url", lambda _url, **_kw: archive_bytes)

        result = dl_module.download_tpch_answers(force=False)

        assert result == cache_dir
        assert (cache_dir / "q1.out").read_bytes() == b"col\nfull row\n"
        assert (cache_dir / _COMPLETE_SENTINEL).exists()


class TestDownloadAllAnswers:
    def test_fetches_manifest_once(self, monkeypatch, tmp_path):
        monkeypatch.delenv("BENCHBOX_NO_DOWNLOAD", raising=False)
        monkeypatch.setenv("BENCHBOX_ANSWERS_URL", "https://example.com/answers")

        from benchbox.core.expected_results import download as dl_module

        tpch_dir = tmp_path / "tpch"
        tpcds_dir = tmp_path / "tpcds"
        monkeypatch.setattr(dl_module, "get_tpch_cache_dir", lambda: tpch_dir)
        monkeypatch.setattr(dl_module, "get_tpcds_cache_dir", lambda: tpcds_dir)

        manifest_call_count = 0
        original_load = dl_module._load_checksum_manifest

        def counting_load():
            nonlocal manifest_call_count
            manifest_call_count += 1
            return original_load()

        monkeypatch.setattr(dl_module, "_load_checksum_manifest", counting_load)

        tpch_archive = _make_tar_gz({"q1.out": b"col\nrow\n"})
        tpcds_archive = _make_tar_gz({"1.ans": b"COL\n---\nval\n"})

        def fake_fetch(url, **_kw):
            if "tpch" in url:
                return tpch_archive
            if "tpcds" in url:
                return tpcds_archive
            return b""

        monkeypatch.setattr(dl_module, "_fetch_url", fake_fetch)

        results = dl_module.download_all_answers()

        assert results["tpch"] == tpch_dir
        assert results["tpcds"] == tpcds_dir
        assert manifest_call_count == 1

    def test_disabled_returns_none_for_both(self, monkeypatch):
        monkeypatch.setenv("BENCHBOX_NO_DOWNLOAD", "1")

        from benchbox.core.expected_results.download import download_all_answers

        results = download_all_answers()
        assert results == {"tpch": None, "tpcds": None}


class TestEndToEnd:
    def test_download_verify_extract_cache_cycle(self, monkeypatch, tmp_path):
        monkeypatch.delenv("BENCHBOX_NO_DOWNLOAD", raising=False)
        monkeypatch.setenv("BENCHBOX_ANSWERS_URL", "https://example.com/answers")

        from benchbox.core.expected_results import download as dl_module

        cache_dir = tmp_path / "tpch"
        monkeypatch.setattr(dl_module, "get_tpch_cache_dir", lambda: cache_dir)

        files = {f"q{i}.out": f"col\nrow{i}\n".encode() for i in range(1, 23)}
        archive_bytes = _make_tar_gz(files)
        archive_sha256 = hashlib.sha256(archive_bytes).hexdigest()

        manifest_text = f"{archive_sha256}  tpch-answers.tar.gz\n"

        def fake_fetch(url, **_kw):
            if "checksums.sha256" in url:
                return manifest_text.encode()
            return archive_bytes

        monkeypatch.setattr(dl_module, "_fetch_url", fake_fetch)

        result = dl_module.download_tpch_answers(force=False)
        assert result == cache_dir
        for i in range(1, 23):
            assert (cache_dir / f"q{i}.out").exists()
        assert (cache_dir / _COMPLETE_SENTINEL).exists()

        fetch_mock = MagicMock(side_effect=AssertionError("should not be called"))
        monkeypatch.setattr(dl_module, "_fetch_url", fetch_mock)

        result2 = dl_module.download_tpch_answers(force=False)
        assert result2 == cache_dir
        fetch_mock.assert_not_called()


