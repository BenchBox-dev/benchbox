from __future__ import annotations

import hashlib
import json
import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

from scripts.sync_sqlglot_runtime import NOTICE, PREFIX

CLI_DESCRIPTION = "Verify BenchBox distributions bundle only the pinned SQLGlot runtime wheel."


def _bundled(distribution: Path) -> dict[str, bytes]:
    if distribution.name.endswith(".whl"):
        with zipfile.ZipFile(distribution) as archive:
            names = [name for name in archive.namelist() if not name.endswith("/")]
            read = archive.read
            root = "benchbox/_vendor/"
            return {name.removeprefix(root): read(name) for name in names if name.startswith(root)}
    with tarfile.open(distribution) as archive:
        members = [member for member in archive.getmembers() if member.isfile()]
        marker = "/benchbox/_vendor/"
        result = {}
        for member in members:
            if marker in member.name:
                extracted = archive.extractfile(member)
                assert extracted is not None
                result[member.name.split(marker, 1)[1]] = extracted.read()
        return result


def verify_distribution(distribution: Path) -> None:
    files = _bundled(distribution)
    pin = json.loads(files["sqlglot.json"])
    archive_name = pin["archive"]
    expected = {"__init__.py", "sqlglot.json", NOTICE, archive_name}
    unexpected = sorted(set(files) - expected)
    missing = sorted(expected - set(files))
    if unexpected or missing or any(PurePosixPath(name).parts[0] == "sqlglot" for name in files):
        raise ValueError(f"{distribution.name}: unexpected {unexpected}, missing {missing}")
    archive_bytes = files[archive_name]
    if hashlib.sha256(archive_bytes).hexdigest() != pin["sha256"]:
        raise ValueError(f"{distribution.name}: bundled runtime bytes differ from the pin")
    nested = distribution.parent / f".{distribution.name}.runtime"
    nested.write_bytes(archive_bytes)
    try:
        with zipfile.ZipFile(nested) as runtime:
            if files[NOTICE] != runtime.read(PREFIX + "LICENSE"):
                raise ValueError(f"{distribution.name}: plain-text notice differs from the bundled license")
            if json.loads(runtime.read(PREFIX + "benchbox_provenance.json"))["fork_sha"] != pin["fork_sha"]:
                raise ValueError(f"{distribution.name}: runtime provenance differs from the pin")
    finally:
        nested.unlink()


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(CLI_DESCRIPTION, file=sys.stderr)
        return 2
    try:
        for name in argv[1:]:
            verify_distribution(Path(name))
            print(f"SQLGlot runtime packaging verified: {name}")
    except (OSError, ValueError, KeyError, tarfile.TarError, zipfile.BadZipFile) as error:
        print(f"SQLGlot runtime packaging: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
