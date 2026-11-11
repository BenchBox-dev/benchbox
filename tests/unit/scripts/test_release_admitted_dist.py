from __future__ import annotations

import ast
import hashlib
import json
import shutil
import types
from pathlib import Path

import pytest

from scripts import release_admitted_dist as gate, release_artifact_consumer as consumer
from tests.unit.scripts.test_release_artifact_consumer import (
    ROOT,
    SDIST,
    SHA,
    WHEEL,
    distributions as distributions,
    metadata as metadata,
)

pytestmark = [pytest.mark.unit, pytest.mark.medium]
TAG = "v0.4.2"
SECOND_WHEEL = "benchbox-0.4.2-py3-none-manylinux_2_17_x86_64.whl"


@pytest.fixture
def admitted(distributions):
    producer = json.loads((distributions / consumer.PRODUCER_RECEIPT).read_text())
    receipt = {
        "tag": TAG,
        "tag_object": "c" * 40,
        "head_sha": SHA,
        "version": "0.4.2",
        "producer": producer,
        "artifact_id": 11,
        "artifact_digest": "sha256:" + "b" * 64,
        "expires_at": "2099-01-01T00:00:00Z",
    }
    (distributions / gate.ADMISSION_RECEIPT).write_text(json.dumps(receipt))
    return distributions


def rewrite(directory: Path, name: str, change) -> None:
    path = directory / name
    value = json.loads(path.read_text())
    change(value)
    path.write_text(json.dumps(value))


def test_accepts_the_admitted_directory_and_stages_only_distributions(admitted, tmp_path):
    receipt, payloads = gate.verify_admitted(admitted, TAG, SHA)
    assert receipt["artifact_id"] == 11
    assert set(payloads) == {WHEEL, SDIST}
    stage = tmp_path / "publish"
    gate.stage_distributions(payloads, stage)
    assert {path.name for path in stage.iterdir()} == {WHEEL, SDIST}
    assert all((stage / name).read_bytes() == (admitted / name).read_bytes() for name in (WHEEL, SDIST))


def test_refuses_to_stage_into_an_existing_directory(admitted, tmp_path):
    _, payloads = gate.verify_admitted(admitted, TAG, SHA)
    stage = tmp_path / "publish"
    stage.mkdir()
    with pytest.raises(FileExistsError):
        gate.stage_distributions(payloads, stage)


@pytest.mark.parametrize(
    "tag,sha,message",
    [
        ("v0.4.3", SHA, "another tag"),
        (TAG, "d" * 40, "another commit"),
        ("0.4.2", SHA, "invalid version tag"),
        (TAG, "A" * 40, "invalid head SHA"),
    ],
)
def test_refuses_a_release_that_is_not_the_admitted_one(admitted, tag, sha, message):
    with pytest.raises(ValueError, match=message):
        gate.verify_admitted(admitted, tag, sha)


def test_refuses_altered_distribution_bytes(admitted):
    (admitted / WHEEL).write_bytes(b"original wheel, edited")
    with pytest.raises(ValueError, match="differs from the receipt"):
        gate.verify_admitted(admitted, TAG, SHA)


def test_refuses_altered_same_length_bytes(admitted):
    (admitted / SDIST).write_bytes(b"X" + (admitted / SDIST).read_bytes()[1:])
    with pytest.raises(ValueError, match="hash differs"):
        gate.verify_admitted(admitted, TAG, SHA)


def test_refuses_an_edited_checksum_file(admitted):
    path = admitted / gate.SHA256SUMS
    digest, name = path.read_text().splitlines()[0].split("  ")
    path.write_text(path.read_text().replace(digest, "0" * 64, 1))
    with pytest.raises(ValueError, match="SHA256SUMS"):
        gate.verify_admitted(admitted, TAG, SHA)


@pytest.mark.parametrize("name", [gate.ADMISSION_RECEIPT, consumer.PRODUCER_RECEIPT, gate.SHA256SUMS, WHEEL])
def test_refuses_a_missing_member(admitted, name):
    (admitted / name).unlink()
    with pytest.raises((OSError, ValueError)):
        gate.verify_admitted(admitted, TAG, SHA)


def test_refuses_an_extra_member(admitted):
    (admitted / "notes.txt").write_text("not a distribution")
    with pytest.raises(ValueError, match="unexpected admitted directory contents"):
        gate.verify_admitted(admitted, TAG, SHA)


def _symlink(link: Path, target: Path, *, directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable here")


def test_refuses_a_symlinked_distribution(admitted, tmp_path):
    outside = tmp_path / "outside.whl"
    shutil.move(admitted / WHEEL, outside)
    _symlink(admitted / WHEEL, outside)
    with pytest.raises(ValueError, match="not a regular file"):
        gate.verify_admitted(admitted, TAG, SHA)


def test_refuses_a_symlinked_admitted_directory(admitted, tmp_path):
    link = tmp_path / "link"
    _symlink(link, admitted, directory=True)
    with pytest.raises(ValueError, match="not a directory"):
        gate.verify_admitted(link, TAG, SHA)


@pytest.mark.parametrize("name", [consumer.PRODUCER_RECEIPT, gate.SHA256SUMS, WHEEL])
def test_refuses_a_member_that_is_not_a_regular_file(admitted, name):
    (admitted / name).unlink()
    (admitted / name).mkdir()
    with pytest.raises(ValueError, match="not a regular file"):
        gate.verify_admitted(admitted, TAG, SHA)


def test_refuses_a_member_larger_than_the_size_limit(admitted, monkeypatch):
    monkeypatch.setattr(gate._consumer, "MAX_PAYLOAD_BYTES", 8)
    with pytest.raises(ValueError, match="exceeds the size limit"):
        gate.verify_admitted(admitted, TAG, SHA)


@pytest.mark.parametrize("renamed", ["../escape.whl", "sub/dir.whl", "/absolute.whl", "..\\escape.whl"])
def test_refuses_a_receipt_file_name_that_leaves_the_directory(admitted, renamed):

    def rename(value):
        value["files"][renamed] = value["files"].pop(WHEEL)

    rewrite(admitted, consumer.PRODUCER_RECEIPT, rename)
    rewrite(admitted, gate.ADMISSION_RECEIPT, lambda value: rename(value["producer"]))
    with pytest.raises(ValueError, match="unexpected admitted directory contents"):
        gate.verify_admitted(admitted, TAG, SHA)
    assert not (admitted.parent / "escape.whl").exists()


def test_refuses_more_than_one_wheel_and_one_sdist(admitted):
    extra = "benchbox-0.4.2-py3-none-manylinux_2_17_x86_64.whl"
    (admitted / extra).write_bytes((admitted / WHEEL).read_bytes())

    def add(value):
        value["files"][extra] = dict(value["files"][WHEEL])

    rewrite(admitted, consumer.PRODUCER_RECEIPT, add)
    rewrite(admitted, gate.ADMISSION_RECEIPT, lambda value: add(value["producer"]))
    with pytest.raises(ValueError, match="exactly one wheel and one sdist"):
        gate.verify_admitted(admitted, TAG, SHA)


def test_refuses_a_producer_receipt_that_differs_from_the_admission_receipt(admitted):
    rewrite(admitted, consumer.PRODUCER_RECEIPT, lambda value: value.update(run_id=8))
    with pytest.raises(ValueError, match="differs from the admission receipt"):
        gate.verify_admitted(admitted, TAG, SHA)


def test_refuses_duplicate_json_keys(admitted):
    path = admitted / gate.ADMISSION_RECEIPT
    path.write_text(path.read_text().replace('"tag":', '"tag": "v9.9.9", "tag":', 1))
    with pytest.raises(ValueError, match="duplicate JSON key"):
        gate.verify_admitted(admitted, TAG, SHA)


@pytest.mark.parametrize(
    "change,message",
    [
        (lambda value: value.update(extra=1), "unexpected admission receipt fields"),
        (lambda value: value.update(tag_object="short"), "invalid tag object"),
        (lambda value: value.update(artifact_id=0), "invalid artifact ID"),
        (lambda value: value.update(artifact_id=True), "invalid artifact ID"),
        (lambda value: value.update(artifact_digest="b" * 64), "invalid artifact digest"),
        (lambda value: value.update(version="0.4.3"), "version differs from the tag"),
        (lambda value: value["producer"].update(schema=True), "not for this repository"),
        (lambda value: value["producer"].update(schema=1.0), "not for this repository"),
        (lambda value: value["producer"].update(repository="elsewhere/BenchBox"), "not for this repository"),
        (
            lambda value: value["producer"].update(workflow_path=".github/workflows/other.yml"),
            "not for this repository",
        ),
        (lambda value: value["producer"].update(artifact_name="dist-other"), "artifact name"),
        (lambda value: value["producer"].update(head_repository_id=2), "invalid producer identity"),
    ],
)
def test_refuses_a_receipt_that_does_not_bind_this_release(admitted, change, message):
    def both(value):
        change(value)
        producer_only = value["producer"]
        (admitted / consumer.PRODUCER_RECEIPT).write_text(json.dumps(producer_only))

    rewrite(admitted, gate.ADMISSION_RECEIPT, both)
    with pytest.raises(ValueError, match=message):
        gate.verify_admitted(admitted, TAG, SHA)


def reseal(directory: Path, sums: bytes | None = None) -> None:
    producer = json.loads((directory / gate.ADMISSION_RECEIPT).read_text())["producer"]
    if sums is None:
        sums = "".join(f"{record['sha256']}  {name}\n" for name, record in producer["files"].items()).encode("ascii")
    (directory / gate.SHA256SUMS).write_bytes(sums)
    digest = hashlib.sha256(sums).hexdigest()
    rewrite(directory, consumer.PRODUCER_RECEIPT, lambda value: value.update(sha256sums_sha256=digest))
    rewrite(directory, gate.ADMISSION_RECEIPT, lambda value: value["producer"].update(sha256sums_sha256=digest))


def edit_producer(directory: Path, change) -> None:
    rewrite(directory, consumer.PRODUCER_RECEIPT, change)
    rewrite(directory, gate.ADMISSION_RECEIPT, lambda value: change(value["producer"]))


@pytest.mark.parametrize("renamed", ["benchbox-0.4.3-py3-none-any.whl", "other-0.4.2-py3-none-any.whl"])
def test_refuses_distribution_names_for_another_version_or_package(admitted, renamed):
    (admitted / WHEEL).rename(admitted / renamed)
    edit_producer(admitted, lambda value: value["files"].update({renamed: value["files"].pop(WHEEL)}))
    reseal(admitted)
    with pytest.raises(ValueError, match="package or version differs from the tag"):
        gate.verify_admitted(admitted, TAG, SHA)


def test_refuses_a_checksum_file_whose_bytes_differ_from_the_receipt(admitted):
    path = admitted / gate.SHA256SUMS
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="SHA256SUMS differs from receipt"):
        gate.verify_admitted(admitted, TAG, SHA)


@pytest.mark.parametrize(
    "content,message",
    [
        (b"not a checksum record\n", "malformed SHA256SUMS record"),
        (b"\xff\xfe\n", "ascii"),
        (None, "duplicate SHA256SUMS record"),
        (
            b"0" * 64 + b"  " + WHEEL.encode() + b"\n" + b"0" * 64 + b"  " + SDIST.encode() + b"\n",
            "differs from the producer files",
        ),
    ],
    ids=["malformed", "not-ascii", "duplicate", "wrong-digests"],
)
def test_refuses_an_inconsistent_checksum_file_even_when_the_receipts_match_it(admitted, content, message):
    if content is None:
        record = (admitted / gate.SHA256SUMS).read_bytes().splitlines()[0] + b"\n"
        content = (admitted / gate.SHA256SUMS).read_bytes() + record
    reseal(admitted, content)
    with pytest.raises(ValueError, match=message):
        gate.verify_admitted(admitted, TAG, SHA)


def test_refuses_a_producer_receipt_with_an_extra_field(admitted):
    edit_producer(admitted, lambda value: value.update(extra="field"))
    with pytest.raises(ValueError, match="unexpected producer receipt fields"):
        gate.verify_admitted(admitted, TAG, SHA)


@pytest.mark.parametrize(
    "change,message,extra_member",
    [
        (lambda files: files.update({"extra.txt": dict(files[WHEEL])}), "exactly one wheel and one sdist", "extra.txt"),
        (
            lambda files: (files.update({SECOND_WHEEL: dict(files[WHEEL])}), files.pop(SDIST)),
            "exactly one wheel and one sdist",
            SECOND_WHEEL,
        ),
        (lambda files: files[WHEEL].update(extra=1), "invalid producer file record", None),
        (lambda files: files[WHEEL].update(size="11"), "invalid file record", None),
        (lambda files: files[WHEEL].update(sha256=files[WHEEL]["sha256"].upper()), "invalid file record", None),
        (lambda files: files[WHEEL].update(size=files[WHEEL]["size"] + 1), "size differs from the receipt", None),
    ],
    ids=["third-file", "two-wheels", "record-extra-key", "size-not-int", "digest-not-lowercase", "size-wrong"],
)
def test_refuses_an_invalid_file_record_in_the_receipt(admitted, change, message, extra_member):
    if extra_member is not None:
        (admitted / extra_member).write_bytes((admitted / WHEEL).read_bytes())
    if extra_member == SECOND_WHEEL:
        (admitted / SDIST).unlink()
    edit_producer(admitted, lambda value: change(value["files"]))
    with pytest.raises(ValueError, match=message):
        gate.verify_admitted(admitted, TAG, SHA)


def test_a_file_that_changes_while_it_is_read_is_refused(admitted, monkeypatch):
    real_lstat = Path.lstat

    def lstat(self, *args, **kwargs):
        status = real_lstat(self, *args, **kwargs)
        if self.name == WHEEL:
            return types.SimpleNamespace(st_mode=status.st_mode, st_size=status.st_size - 1)
        return status

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(ValueError, match="changed while it was read"):
        gate.verify_admitted(admitted, TAG, SHA)


def test_a_staged_file_that_reads_back_differently_is_refused(admitted, tmp_path, monkeypatch):
    _, payloads = gate.verify_admitted(admitted, TAG, SHA)
    stage = tmp_path / "publish"
    real_read = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda self: b"changed" if self.parent == stage else real_read(self))
    with pytest.raises(ValueError, match="changed after it was staged"):
        gate.stage_distributions(payloads, stage)


def test_command_line_stages_on_success_and_exits_nonzero_on_refusal(admitted, tmp_path, capsys):
    stage = tmp_path / "publish"
    assert gate.main(["--admitted", str(admitted), "--tag", TAG, "--sha", SHA, "--stage", str(stage)]) == 0
    assert json.loads(capsys.readouterr().out)["files"] == sorted([WHEEL, SDIST])
    assert {path.name for path in stage.iterdir()} == {WHEEL, SDIST}
    (admitted / WHEEL).write_bytes(b"tampered")
    with pytest.raises(SystemExit) as refusal:
        gate.main(["--admitted", str(admitted), "--tag", TAG, "--sha", SHA, "--stage", str(tmp_path / "second")])
    assert refusal.value.code == 1
    assert not (tmp_path / "second").exists()
    assert "Admitted distribution verification failed" in capsys.readouterr().err


def test_gate_never_builds_downloads_or_publishes():
    tree = ast.parse((ROOT / "scripts/release_admitted_dist.py").read_text())
    imported = {
        alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    } | {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    assert not imported & {"subprocess", "socket", "urllib", "http", "requests", "build", "shutil"}
