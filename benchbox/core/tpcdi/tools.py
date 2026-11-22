from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
import uuid
import zipfile
import zlib
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


def _file_crc32(path: Path) -> int:
    crc = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1 << 20):
            crc = zlib.crc32(chunk, crc)
    return crc


def _members_intact(unpack_dir: Path) -> bool:
    if not TOOLS_ARCHIVE_PATH.is_file():
        raise ToolsArchiveError(f"TPC-DI tools archive is missing: {TOOLS_ARCHIVE_PATH}")
    with zipfile.ZipFile(TOOLS_ARCHIVE_PATH) as bundle:
        for info in bundle.infolist():
            member = unpack_dir / info.filename
            if info.is_dir():
                if not member.is_dir():
                    return False
            elif not member.is_file() or member.stat().st_size != info.file_size or _file_crc32(member) != info.CRC:
                return False
    return True


def _is_complete(unpack_dir: Path) -> bool:
    sentinel = unpack_dir / _COMPLETE_SENTINEL
    return (
        sentinel.is_file()
        and sentinel.read_text(encoding="utf-8").strip() == TOOLS_ARCHIVE_SHA256
        and (unpack_dir / TOOLS_ROOT_MEMBER / DIGEN_JAR_NAME).is_file()
        and _members_intact(unpack_dir)
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


def _publish(staging: Path, unpack_dir: Path) -> bool:
    if _is_complete(unpack_dir):
        return False
    if unpack_dir.exists():
        stale = unpack_dir.with_name(f".stale-{uuid.uuid4().hex}")
        try:
            os.replace(unpack_dir, stale)
        except OSError:
            pass
        else:
            shutil.rmtree(stale, ignore_errors=True)
    try:
        os.replace(staging, unpack_dir)
    except OSError:
        if not _is_complete(unpack_dir):
            raise
        return False
    return True


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
        if _publish(staging, unpack_dir):
            logger.info("Unpacked the TPC-DI tools to %s", unpack_dir)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return unpack_dir / TOOLS_ROOT_MEMBER
