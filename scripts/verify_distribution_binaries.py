from __future__ import annotations

import argparse
import json
import stat
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

from benchbox.utils.binary_manifest import (
    DEFAULT_ROOT,
    MANIFEST_NAME,
    read_binary_manifest,
    verify_binary_payload,
    verify_binary_tree,
)

CLI_DESCRIPTION = "Verify source, wheel, and sdist bundled generators against one manifest."

DISTRIBUTION_EXCLUDED = frozenset(
    {
        "tpc-di/tpcdi_audit.sql",
        "tpc-di/tpcdi_validation.sql",
        "tpc-di/tpcdi_visibility_1.sql",
        "tpc-di/tpcdi_visibility_2.sql",
    }
)


def _member_name(raw_name: str, seen: dict[str, bool], binary_prefix: str, directory: bool) -> str:
    name = raw_name.removesuffix("/") if directory else raw_name
    path = PurePosixPath(name)
    if (
        not name
        or path.is_absolute()
        or path.as_posix() != name
        or ".." in path.parts
        or any(part.rstrip(" .") != part for part in path.parts)
        or any(character in name for character in ("\\", ":", "\0"))
        or name.casefold() in seen
    ):
        raise ValueError(f"unsafe or duplicate distribution member: {raw_name}")
    key = name.casefold()
    parents = ["/".join(path.parts[:index]).casefold() for index in range(1, len(path.parts))]
    if any(parent in seen and not seen[parent] for parent in parents) or (
        not directory and any(existing.startswith(key + "/") for existing in seen)
    ):
        raise ValueError(f"distribution file/directory conflict: {raw_name}")
    seen[key] = directory
    parts = tuple(part.casefold() for part in path.parts)
    is_bundled = any(parts[index : index + 2] == ("benchbox", "_binaries") for index in range(len(parts)))
    if is_bundled and not (name == binary_prefix.rstrip("/") or name.startswith(binary_prefix)):
        raise ValueError(f"relocated distribution bundled binary: {raw_name}")
    return name


def verify_distribution_binaries(distribution: Path, source_root: Path = DEFAULT_ROOT) -> None:
    verify_binary_tree(source_root)
    trusted_manifest = (source_root / MANIFEST_NAME).read_bytes()
    source_files = {
        path.relative_to(source_root).as_posix(): path.stat().st_size
        for path in source_root.rglob("*")
        if path.is_file() and path.relative_to(source_root).as_posix() not in DISTRIBUTION_EXCLUDED
    }
    source_execute_modes = {name: (source_root / name).stat().st_mode & 0o111 for name in source_files}
    payloads: dict[str, bytes] = {}
    seen: dict[str, bool] = {}

    def store(name: str, size: int, mode: int, payload: bytes) -> None:
        if name not in source_files or name in payloads or size != source_files[name]:
            raise ValueError(f"distribution bundled binary membership or size differs: {name}")
        if mode & 0o111 != source_execute_modes[name]:
            raise ValueError(f"distribution bundled binary execute permissions differ: {name}")
        payloads[name] = payload

    if distribution.name.endswith(".whl"):
        with zipfile.ZipFile(distribution) as archive:
            prefix = "benchbox/_binaries/"
            for entry in archive.infolist():
                member = _member_name(entry.filename, seen, prefix, entry.is_dir())
                mode = entry.external_attr >> 16
                allowed = {0, stat.S_IFDIR} if entry.is_dir() else {0, stat.S_IFREG}
                if stat.S_IFMT(mode) not in allowed:
                    raise ValueError(f"distribution member is not regular: {member}")
                if not member.startswith(prefix) or entry.is_dir():
                    continue
                name = member.removeprefix(prefix)
                if name not in source_files or entry.file_size != source_files[name]:
                    raise ValueError(f"distribution bundled binary membership or size differs: {name}")
                store(name, entry.file_size, mode, archive.read(entry))
    elif distribution.name.endswith(".tar.gz"):
        with tarfile.open(distribution, "r:gz") as archive:
            prefix = distribution.name.removesuffix(".tar.gz") + "/benchbox/_binaries/"
            for entry in archive:
                member = _member_name(entry.name, seen, prefix, entry.isdir())
                if not (entry.isfile() or entry.isdir()):
                    raise ValueError(f"distribution member is not regular: {member}")
                if not member.startswith(prefix) or entry.isdir():
                    continue
                name = member.removeprefix(prefix)
                if name not in source_files or entry.size != source_files[name]:
                    raise ValueError(f"distribution bundled binary membership or size differs: {name}")
                stream = archive.extractfile(entry)
                if stream is None:
                    raise ValueError(f"distribution bundled binary cannot be read: {name}")
                with stream:
                    store(name, entry.size, entry.mode, stream.read())
    else:
        raise ValueError("distribution must be a wheel or .tar.gz sdist")
    manifest = payloads.pop(MANIFEST_NAME, None)
    if manifest != trusted_manifest:
        raise ValueError("distribution manifest differs from the source manifest")
    distribution_files = {
        name: digest
        for name, digest in read_binary_manifest(trusted_manifest).items()
        if name not in DISTRIBUTION_EXCLUDED
    }
    verify_binary_payload(json.dumps({"schema": 1, "files": distribution_files}).encode(), payloads)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("distributions", type=Path, nargs="+")
    parser.add_argument("--source-root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args(argv)
    try:
        for distribution in args.distributions:
            verify_distribution_binaries(distribution, args.source_root)
    except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile) as exc:
        parser.exit(1, f"Distribution binary verification failed: {exc}\n")
    print("Distribution bundled files match the source manifest.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
