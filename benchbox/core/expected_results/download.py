# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import hashlib
import logging
import os
import sys
import tarfile
import tempfile
import time
import urllib.error
from pathlib import Path

from benchbox.utils import tls

logger = logging.getLogger(__name__)

_DEFAULT_ANSWERS_BASE_URL = "https://github.com/joeharris76/BenchBox/releases/download/answers-v1"

_TPCH_ARCHIVE = "tpch-answers.tar.gz"
_TPCDS_ARCHIVE = "tpcds-answers.tar.gz"
_CHECKSUM_FILENAME = "checksums.sha256"

_MAX_RETRIES = 3
_RETRY_DELAY_SECONDS = 1.0
_COMPLETE_SENTINEL = ".benchbox-complete"


def get_cache_dir() -> Path:
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "benchbox" / "answers"


def get_tpch_cache_dir() -> Path:
    return get_cache_dir() / "tpch"


def get_tpcds_cache_dir() -> Path:
    return get_cache_dir() / "tpcds"


def get_answers_base_url() -> str:
    return os.environ.get("BENCHBOX_ANSWERS_URL", _DEFAULT_ANSWERS_BASE_URL).rstrip("/")


def is_download_disabled() -> bool:
    return os.environ.get("BENCHBOX_NO_DOWNLOAD", "").strip().lower() in ("1", "true", "yes")


def _fetch_url(url: str, timeout: int = 60) -> bytes:
    with tls.urlopen(url, timeout=timeout) as response:
        return response.read()


def _download_with_retry(url: str, dest: Path) -> bool:
    last_error: Exception | None = None

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            data = _fetch_url(url)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            return True
        except urllib.error.HTTPError as e:
            if e.code in (404, 403):
                logger.debug("Answer archive not available at %s: HTTP %s", url, e.code)
                return False
            last_error = e
            logger.debug("HTTP %s downloading %s (attempt %s/%s)", e.code, url, attempt, _MAX_RETRIES)
        except urllib.error.URLError as e:
            last_error = e
            logger.debug("Network error downloading %s (attempt %s/%s): %s", url, attempt, _MAX_RETRIES, e)
        except Exception as e:
            last_error = e
            logger.debug("Unexpected error downloading %s (attempt %s/%s): %s", url, attempt, _MAX_RETRIES, e)

        if attempt < _MAX_RETRIES:
            time.sleep(_RETRY_DELAY_SECONDS * attempt)

    logger.warning("Failed to download %s after %s attempts: %s", url, _MAX_RETRIES, last_error)
    raise RuntimeError(f"Download failed for {url}: {last_error}") from last_error


def _load_checksum_manifest() -> dict[str, str]:
    base_url = get_answers_base_url()
    manifest_url = f"{base_url}/{_CHECKSUM_FILENAME}"

    try:
        data = _fetch_url(manifest_url)
        manifest: dict[str, str] = {}
        for line in data.decode().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 1)
            if len(parts) == 2:
                digest, filename = parts
                manifest[filename.lstrip("*").strip()] = digest
        logger.debug("Loaded checksum manifest: %s entries", len(manifest))
        return manifest
    except (urllib.error.HTTPError, urllib.error.URLError):
        logger.debug("No checksum manifest available at %s", manifest_url)
        return {}
    except Exception as e:
        logger.debug("Could not load checksum manifest: %s", e)
        return {}


def _verify_checksum(file_path: Path, expected_hex: str) -> bool:
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest() == expected_hex


def _safe_extract_tar(archive_path: Path, dest_dir: Path) -> None:
    resolved_dest = dest_dir.resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "r:gz") as tf:
        for member in tf.getmembers():
            member_path = (dest_dir / member.name).resolve()
            if not member_path.is_relative_to(resolved_dest):
                raise ValueError(f"Path traversal detected in archive: {member.name!r}")
        if sys.version_info >= (3, 12):
            tf.extractall(dest_dir, filter="data")
        else:
            tf.extractall(dest_dir)


def _is_cache_warm(cache_dir: Path, glob_pattern: str) -> bool:
    return cache_dir.exists() and (cache_dir / _COMPLETE_SENTINEL).exists() and any(cache_dir.glob(glob_pattern))


def _download_and_extract(
    archive_name: str,
    cache_dir: Path,
    force: bool,
    manifest: dict[str, str] | None = None,
) -> Path | None:
    base_url = get_answers_base_url()
    archive_url = f"{base_url}/{archive_name}"

    glob_pattern = "q*.out" if archive_name == _TPCH_ARCHIVE else "*.ans"
    if not force and _is_cache_warm(cache_dir, glob_pattern):
        logger.debug("Cache already warm at %s, skipping download", cache_dir)
        return cache_dir

    if manifest is None:
        manifest = _load_checksum_manifest()

    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        logger.info("Downloading %s...", archive_name)
        if not _download_with_retry(archive_url, tmp_path):
            logger.debug(
                "%s not found at %s. Run 'benchbox download-answers' after the answers-v1 release is published.",
                archive_name,
                archive_url,
            )
            return None

        if archive_name in manifest:
            if not _verify_checksum(tmp_path, manifest[archive_name]):
                logger.warning(
                    "Checksum mismatch for %s: archive may be corrupt. "
                    "Try again or set BENCHBOX_NO_DOWNLOAD=1 to skip.",
                    archive_name,
                )
                return None
            logger.debug("Checksum verified for %s", archive_name)
        else:
            logger.debug("No checksum entry for %s; skipping verification", archive_name)

        _safe_extract_tar(tmp_path, cache_dir)
        (cache_dir / _COMPLETE_SENTINEL).write_text("")
        logger.info("Extracted %s to %s", archive_name, cache_dir)
        return cache_dir

    except Exception as e:
        logger.warning("Failed to download/extract %s: %s. Validation will be skipped.", archive_name, e)
        return None
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def download_tpch_answers(force: bool = False) -> Path | None:
    if is_download_disabled():
        logger.debug("BENCHBOX_NO_DOWNLOAD is set; skipping TPC-H answer download")
        return None

    return _download_and_extract(_TPCH_ARCHIVE, get_tpch_cache_dir(), force)


def download_tpcds_answers(force: bool = False) -> Path | None:
    if is_download_disabled():
        logger.debug("BENCHBOX_NO_DOWNLOAD is set; skipping TPC-DS answer download")
        return None

    return _download_and_extract(_TPCDS_ARCHIVE, get_tpcds_cache_dir(), force)


def download_all_answers(force: bool = False) -> dict[str, Path | None]:
    if is_download_disabled():
        logger.debug("BENCHBOX_NO_DOWNLOAD is set; skipping all answer downloads")
        return {"tpch": None, "tpcds": None}

    manifest = _load_checksum_manifest()
    return {
        "tpch": _download_and_extract(_TPCH_ARCHIVE, get_tpch_cache_dir(), force, manifest),
        "tpcds": _download_and_extract(_TPCDS_ARCHIVE, get_tpcds_cache_dir(), force, manifest),
    }
