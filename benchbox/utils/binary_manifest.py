from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath

MANIFEST_NAME = "SHA256MANIFEST.json"
DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "_binaries"
DISTRIBUTION_EXCLUDED = frozenset(
    {
        "tpc-di/tpcdi_audit.sql",
        "tpc-di/tpcdi_validation.sql",
        "tpc-di/tpcdi_visibility_1.sql",
        "tpc-di/tpcdi_visibility_2.sql",
    }
)


CLI_DESCRIPTION = "Verify the complete bundled-generator tree shipped inside BenchBox."


def _unique_object(pairs: Iterable[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate manifest key: {key}")
        result[key] = value
    return result


def read_binary_manifest(payload: bytes) -> dict[str, str]:
    manifest = json.loads(payload, object_pairs_hook=_unique_object)
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"schema", "files"}
        or type(manifest["schema"]) is not int
        or manifest["schema"] != 1
        or not isinstance(manifest["files"], dict)
        or not manifest["files"]
    ):
        raise ValueError("invalid bundled binary manifest schema")
    for name, digest in manifest["files"].items():
        path = PurePosixPath(name)
        if (
            not name
            or name == MANIFEST_NAME
            or path.is_absolute()
            or path.as_posix() != name
            or ".." in path.parts
            or any(character in name for character in ("\\", ":", "\0"))
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            raise ValueError(f"invalid bundled binary manifest entry: {name}")
    return manifest["files"]


def _tree_files(root: Path) -> dict[str, Path]:
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f"bundled binary root is not a regular directory: {root}")
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError(f"non-regular bundled binary path: {path}")
        if path.is_file() and path.relative_to(root).as_posix() != MANIFEST_NAME:
            files[path.relative_to(root).as_posix()] = path
    if not files:
        raise ValueError("bundled binary tree is empty")
    return files


def build_binary_manifest(root: Path) -> bytes:
    hashes = {}
    for name, path in _tree_files(root).items():
        with path.open("rb") as stream:
            hashes[name] = hashlib.file_digest(stream, "sha256").hexdigest()
    payload = json.dumps({"schema": 1, "files": hashes}, indent=2, sort_keys=True).encode() + b"\n"
    read_binary_manifest(payload)
    return payload


def verify_binary_payload(manifest: bytes, files: Mapping[str, bytes]) -> None:
    expected = read_binary_manifest(manifest)
    missing = sorted(set(expected) - set(files) - DISTRIBUTION_EXCLUDED)
    extra = sorted(set(files) - set(expected))
    if missing or extra:
        raise ValueError(f"bundled binary membership differs: missing={missing}, extra={extra}")
    for name, digest in expected.items():
        if name in files and hashlib.sha256(files[name]).hexdigest() != digest:
            raise ValueError(f"bundled binary hash differs: {name}")


def verify_binary_tree(root: Path = DEFAULT_ROOT) -> None:
    files = _tree_files(root)
    manifest_path = root / MANIFEST_NAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("bundled binary manifest is missing or not a regular file")
    expected = read_binary_manifest(manifest_path.read_bytes())
    if set(files) - set(expected) or set(expected) - set(files) - DISTRIBUTION_EXCLUDED:
        raise ValueError("bundled binary membership differs from the manifest")
    for name, path in files.items():
        with path.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != expected[name]:
            raise ValueError(f"bundled binary hash differs: {name}")


def main(argv: list[str] | None = None) -> int:
    from benchbox.utils.printing import emit

    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args(argv)
    try:
        verify_binary_tree(args.root)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Bundled binary verification failed: {exc}\n")
    emit("Bundled binary membership and SHA-256 hashes verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
