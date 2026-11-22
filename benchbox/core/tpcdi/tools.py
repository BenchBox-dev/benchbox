from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

from benchbox.core.expected_results.download import get_cache_dir

logger = logging.getLogger(__name__)

TOOLS_ARCHIVE_NAME = "TPC-DI_Tools_v1.1.0.zip"
TOOLS_ARCHIVE_SHA256 = "d9c10de5dafa781ddd4df72453f50af2f0c9c7ccfa0cbae99ed0be70a2ede22b"
TOOLS_ARCHIVE_PATH = Path(__file__).resolve().parents[2] / "_binaries" / "tpc-di" / "tools" / TOOLS_ARCHIVE_NAME
TOOLS_CACHE_NAME = "tpcdi-tools"
TOOLS_ROOT_MEMBER = "Tools"
DIGEN_JAR_NAME = "DIGen.jar"

_COMPLETE_SENTINEL = ".benchbox-complete"


class ToolsArchiveError(RuntimeError):
    pass


def _file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _is_complete(unpack_dir: Path) -> bool:
    sentinel = unpack_dir / _COMPLETE_SENTINEL
    return (
        sentinel.is_file()
        and sentinel.read_text(encoding="utf-8").strip() == TOOLS_ARCHIVE_SHA256
        and (unpack_dir / TOOLS_ROOT_MEMBER / DIGEN_JAR_NAME).is_file()
    )


def _verified_archive() -> Path:
    if not TOOLS_ARCHIVE_PATH.is_file():
        raise ToolsArchiveError(f"TPC-DI tools archive is missing: {TOOLS_ARCHIVE_PATH}")
    actual = _file_sha256(TOOLS_ARCHIVE_PATH)
    if actual != TOOLS_ARCHIVE_SHA256:
        raise ToolsArchiveError(
            f"TPC-DI tools archive is corrupted: SHA-256 {actual} does not match {TOOLS_ARCHIVE_SHA256}"
        )
    return TOOLS_ARCHIVE_PATH


def _extract(archive: Path, destination: Path) -> None:
    resolved = destination.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for name in bundle.namelist():
            if not (destination / name).resolve().is_relative_to(resolved):
                raise ToolsArchiveError(f"path traversal in TPC-DI tools archive: {name!r}")
        bundle.extractall(destination)


def resolve_tools_dir() -> Path:
    unpack_dir = get_cache_dir().parent / TOOLS_CACHE_NAME / TOOLS_ARCHIVE_SHA256
    if _is_complete(unpack_dir):
        return unpack_dir / TOOLS_ROOT_MEMBER

    archive = _verified_archive()
    unpack_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".unpack-", dir=unpack_dir.parent))
    try:
        _extract(archive, staging)
        (staging / _COMPLETE_SENTINEL).write_text(TOOLS_ARCHIVE_SHA256, encoding="utf-8")
        if unpack_dir.exists():
            shutil.rmtree(unpack_dir)
        os.replace(staging, unpack_dir)
    except OSError:
        if not _is_complete(unpack_dir):
            raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    logger.info("Unpacked the TPC-DI tools to %s", unpack_dir)
    return unpack_dir / TOOLS_ROOT_MEMBER
