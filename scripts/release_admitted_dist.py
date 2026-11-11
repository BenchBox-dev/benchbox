from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import stat
import sys
from pathlib import Path
from typing import Any

ADMISSION_RECEIPT = "admission-receipt.json"
SHA256SUMS = "SHA256SUMS"

_SHA1 = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_SUMS_RECORD = re.compile(r"([0-9a-f]{64})  ([^/\\]+)")
_ADMISSION_KEYS = {
    "tag",
    "tag_object",
    "head_sha",
    "version",
    "producer",
    "artifact_id",
    "artifact_digest",
    "expires_at",
}
_PRODUCER_KEYS = {
    "schema",
    "repository",
    "repository_id",
    "head_repository_id",
    "workflow_path",
    "head_sha",
    "run_id",
    "run_attempt",
    "job_id",
    "job_name",
    "artifact_name",
    "sha256sums_sha256",
    "files",
}


def _load_consumer():
    spec = importlib.util.spec_from_file_location(
        "_release_artifact_consumer", Path(__file__).with_name("release_artifact_consumer.py")
    )
    if spec is None or spec.loader is None:
        raise ImportError("release_artifact_consumer.py cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_consumer = _load_consumer()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _positive(value: Any) -> bool:
    return type(value) is int and value > 0


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_regular(path: Path, limit: int) -> bytes:
    status = path.lstat()
    _require(stat.S_ISREG(status.st_mode), f"{path.name} is not a regular file")
    _require(status.st_size <= limit, f"{path.name} exceeds the size limit")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    _require(len(data) == status.st_size, f"{path.name} changed while it was read")
    return data


def _sums(data: bytes) -> dict[str, str]:
    records: dict[str, str] = {}
    for line in data.decode("ascii").splitlines():
        match = _SUMS_RECORD.fullmatch(line)
        _require(match is not None, "malformed SHA256SUMS record")
        assert match is not None
        digest, name = match.groups()
        _require(name not in records, "duplicate SHA256SUMS record")
        records[name] = digest
    return records


def verify_admitted(directory: Path, tag: str, head_sha: str) -> tuple[dict[str, Any], dict[str, bytes]]:
    from packaging.utils import parse_sdist_filename, parse_wheel_filename
    from packaging.version import Version

    _require(bool(re.fullmatch(_consumer.TAG_PATTERN, tag)), "invalid version tag")
    _require(bool(_SHA1.fullmatch(head_sha)), "invalid head SHA")
    _require(directory.is_dir() and not directory.is_symlink(), "admitted directory is not a directory")
    limit = _consumer.MAX_PAYLOAD_BYTES

    receipt = json.loads(_read_regular(directory / ADMISSION_RECEIPT, limit), object_pairs_hook=_object)
    _require(isinstance(receipt, dict) and set(receipt) == _ADMISSION_KEYS, "unexpected admission receipt fields")
    _require(receipt["tag"] == tag, "admission receipt is for another tag")
    _require(receipt["head_sha"] == head_sha, "admission receipt is for another commit")
    _require(
        isinstance(receipt["tag_object"], str) and bool(_SHA1.fullmatch(receipt["tag_object"])), "invalid tag object"
    )
    _require(_positive(receipt["artifact_id"]), "invalid artifact ID")
    _require(
        isinstance(receipt["artifact_digest"], str) and bool(_DIGEST.fullmatch(receipt["artifact_digest"])),
        "invalid artifact digest",
    )

    producer = receipt["producer"]
    _require(isinstance(producer, dict) and set(producer) == _PRODUCER_KEYS, "unexpected producer receipt fields")
    on_disk = json.loads(_read_regular(directory / _consumer.PRODUCER_RECEIPT, limit), object_pairs_hook=_object)
    _require(on_disk == producer, "producer receipt differs from the admission receipt")
    _require(
        type(producer["schema"]) is int
        and producer["schema"] == 1
        and producer["repository"] == _consumer.REPOSITORY
        and producer["workflow_path"] == _consumer.WORKFLOW
        and producer["job_name"] == _consumer.JOB_NAME
        and producer["head_sha"] == head_sha,
        "producer receipt is not for this repository, workflow and commit",
    )
    _require(
        all(
            _positive(producer[key])
            for key in ("repository_id", "head_repository_id", "run_id", "run_attempt", "job_id")
        )
        and producer["repository_id"] == producer["head_repository_id"],
        "invalid producer identity",
    )
    _require(
        producer["artifact_name"] == f"dist-{head_sha}-attempt-{producer['run_attempt']}",
        "producer artifact name does not match the attempt",
    )

    files = producer["files"]
    _require(isinstance(files, dict) and len(files) == 2, "expected exactly one wheel and one sdist")
    wheels = [name for name in files if name.endswith(".whl")]
    sdists = [name for name in files if name.endswith(".tar.gz")]
    _require(len(wheels) == 1 and len(sdists) == 1, "expected exactly one wheel and one sdist")
    expected_names = set(files) | {SHA256SUMS, _consumer.PRODUCER_RECEIPT, ADMISSION_RECEIPT}
    _require({entry.name for entry in directory.iterdir()} == expected_names, "unexpected admitted directory contents")

    payloads: dict[str, bytes] = {}
    for name, record in files.items():
        _require(isinstance(record, dict) and set(record) == {"size", "sha256"}, "invalid producer file record")
        _require(_positive(record["size"]) and bool(_SHA256.fullmatch(str(record["sha256"]))), "invalid file record")
        data = _read_regular(directory / name, limit)
        _require(len(data) == record["size"], f"{name} size differs from the receipt")
        _require(hashlib.sha256(data).hexdigest() == record["sha256"], f"{name} hash differs from the receipt")
        payloads[name] = data

    sums_bytes = _read_regular(directory / SHA256SUMS, limit)
    _require(hashlib.sha256(sums_bytes).hexdigest() == producer["sha256sums_sha256"], "SHA256SUMS differs from receipt")
    _require(
        _sums(sums_bytes) == {name: record["sha256"] for name, record in files.items()},
        "SHA256SUMS differs from the producer files",
    )

    expected = Version(tag[1:])
    _require(Version(receipt["version"]) == expected, "admission receipt version differs from the tag")
    for name in files:
        package, version = parse_wheel_filename(name)[:2] if name.endswith(".whl") else parse_sdist_filename(name)
        _require(package == "benchbox" and version == expected, "distribution package or version differs from the tag")
    return receipt, payloads


def stage_distributions(payloads: dict[str, bytes], destination: Path) -> None:
    destination.mkdir(mode=0o700)
    for name, data in payloads.items():
        with open(destination / name, "xb") as stream:
            stream.write(data)
    for name, data in payloads.items():
        _require((destination / name).read_bytes() == data, f"{name} changed after it was staged")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify an admitted release directory against its admission receipt before publication."
    )
    parser.add_argument(
        "--admitted", type=Path, required=True, help="output directory of release_artifact_consumer admit"
    )
    parser.add_argument("--tag", required=True, help="tag being released (github.ref_name)")
    parser.add_argument("--sha", required=True, help="commit being released (github.sha)")
    parser.add_argument("--stage", type=Path, help="new directory that receives only the wheel and sdist")
    args = parser.parse_args(argv)
    try:
        receipt, payloads = verify_admitted(args.admitted, args.tag, args.sha)
        if args.stage is not None:
            stage_distributions(payloads, args.stage)
    except (OSError, ValueError, KeyError, TypeError, UnicodeError) as exc:
        parser.exit(1, f"Admitted distribution verification failed: {exc}\n")
    print(json.dumps({"tag": receipt["tag"], "head_sha": receipt["head_sha"], "files": sorted(payloads)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
