from __future__ import annotations

import argparse
import email
import hashlib
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "benchbox" / "_vendor"
NAMESPACE = "benchbox._vendor.sqlglot"
REPOSITORY = "https://github.com/BenchBox-dev/benchbox-sqlglot"
PREFIX = NAMESPACE.replace(".", "/") + "/"
NOTICE = "SQLGLOT_LICENSE.txt"


def inspect_wheel(path: Path, wheel_name: str | None = None) -> dict[str, Any]:
    wheel_name = wheel_name or path.name
    if not re.fullmatch(r"benchbox_sqlglot-\d+\.\d+\.\d+\.[1-9]\d*-py3-none-any\.whl", wheel_name):
        raise ValueError("Expected the pure-Python BenchBox SQLGlot runtime wheel")
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate wheel members")
        for name in names:
            parts = PurePosixPath(name).parts
            if ".." in parts or name.startswith("/") or "\\" in name:
                raise ValueError("Unsafe wheel member")
            if not (
                name.startswith((PREFIX, "benchbox_sqlglot-"))
                or name in {"benchbox/__init__.py", "benchbox/_vendor/__init__.py"}
            ):
                raise ValueError("Unexpected runtime wheel namespace")
            if name.endswith((".so", ".pyd", ".pyc", "/entry_points.txt")):
                raise ValueError("Compiled code and entry points are not supported")
        metadata = email.message_from_bytes(archive.read(next(n for n in names if n.endswith("/METADATA"))))
        wheel_metadata = email.message_from_bytes(archive.read(next(n for n in names if n.endswith("/WHEEL"))))
        provenance = json.loads(archive.read(PREFIX + "benchbox_provenance.json"))
        version = provenance["version"]
        if (
            provenance["schema"] != 1
            or provenance["dirty"] is not False
            or provenance["repository"] != REPOSITORY
            or provenance["namespace"] != NAMESPACE
            or provenance["distribution"] != "benchbox-sqlglot"
            or not re.fullmatch(r"[a-f0-9]{40}", provenance["fork_sha"])
            or not re.fullmatch(r"[a-f0-9]{40}", provenance["upstream_sha"])
            or provenance["manifest"]["version"] != version
            or provenance["manifest"]["upstream"]["sha"] != provenance["upstream_sha"]
        ):
            raise ValueError("Invalid or development runtime provenance")
        if (
            metadata["Name"] != "benchbox-sqlglot"
            or metadata["Version"] != version
            or wheel_name != f"benchbox_sqlglot-{version}-py3-none-any.whl"
            or metadata.get_all("Requires-Dist")
            or metadata.get_all("Provides-Extra")
            or wheel_metadata.get_all("Tag") != ["py3-none-any"]
            or wheel_metadata["Root-Is-Purelib"] != "true"
        ):
            raise ValueError("Inconsistent or non-pure runtime wheel metadata")
        payload = {
            name.removeprefix(PREFIX): hashlib.sha256(archive.read(name)).hexdigest()
            for name in names
            if name.startswith(PREFIX) and not name.endswith("/benchbox_provenance.json") and not name.endswith("/")
        }
        if (
            provenance["files"] != payload
            or not {"LICENSE", "py.typed", "_version.py", "__init__.py"} <= payload.keys()
        ):
            raise ValueError("Runtime payload hashes or required resources disagree")
    return {
        "schema": 1,
        "distribution": "benchbox-sqlglot",
        "namespace": NAMESPACE,
        "repository": REPOSITORY,
        "version": version,
        "fork_sha": provenance["fork_sha"],
        "upstream_sha": provenance["upstream_sha"],
        "wheel": wheel_name,
        "archive": wheel_name + ".zip",
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def check_runtime(directory: Path = RUNTIME) -> dict[str, Any]:
    manifest = json.loads((directory / "sqlglot.json").read_text(encoding="utf-8"))
    name = manifest["wheel"]
    if not isinstance(name, str) or Path(name).name != name:
        raise ValueError("Runtime pin must name one local wheel")
    archive = name + ".zip"
    if sorted(p.name for p in directory.iterdir() if p.suffix in {".whl", ".zip"}) != [archive]:
        raise ValueError("Exactly the pinned runtime wheel archive must be bundled")
    if (directory / "sqlglot").exists():
        raise ValueError("Unpacked SQLGlot sources must not be bundled")
    actual = inspect_wheel(directory / archive, name)
    if manifest != actual:
        raise ValueError("Bundled wheel differs from its immutable pin")
    with zipfile.ZipFile(directory / archive) as bundle:
        if (directory / NOTICE).read_bytes() != bundle.read(PREFIX + "LICENSE"):
            raise ValueError("Plain-text SQLGlot notice differs from the bundled wheel license")
    return actual


def adopt_wheel(path: Path, directory: Path = RUNTIME) -> dict[str, Any]:
    manifest = inspect_wheel(path)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / manifest["archive"]
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != manifest["sha256"]:
        raise ValueError("A runtime version cannot be reused for different wheel bytes")
    if path.resolve() != target.resolve():
        shutil.copyfile(path, target)
    with zipfile.ZipFile(path) as bundle:
        (directory / NOTICE).write_bytes(bundle.read(PREFIX + "LICENSE"))
    (directory / "sqlglot.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    for previous in directory.iterdir():
        if previous.suffix in {".whl", ".zip"} and previous != target:
            previous.unlink()
    return check_runtime(directory)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pin or verify the bundled SQLGlot runtime wheel without extracting it."
    )
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    try:
        manifest = adopt_wheel(arguments.wheel) if arguments.wheel else check_runtime()
    except (OSError, ValueError, KeyError, TypeError, StopIteration, zipfile.BadZipFile) as error:
        print(f"SQLGlot runtime: {error}", file=sys.stderr)
        return 1
    print(f"SQLGlot runtime {manifest['version']} source {manifest['fork_sha']} SHA256 {manifest['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
