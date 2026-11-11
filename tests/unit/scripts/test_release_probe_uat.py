from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from tests.uat import _cli

pytestmark = [pytest.mark.unit, pytest.mark.medium]

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
SPEC = importlib.util.spec_from_file_location("release_probe_uat_under_test", SCRIPTS / "release_probe_uat.py")
assert SPEC is not None and SPEC.loader is not None
stub = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = stub
SPEC.loader.exec_module(stub)

HEAD = "a" * 40
TAG = "v0.0.0rc0"


@pytest.fixture
def digest_file(tmp_path: Path) -> Path:
    path = tmp_path / "dist-digest.txt"
    path.write_text("a" * 64 + "  benchbox-0.0.0rc0-py3-none-any.whl\n" + "b" * 64 + "  benchbox-0.0.0rc0.tar.gz\n")
    return path


def _gate(directories: list[Path], output: Path, digest_file: Path) -> tuple[int, dict]:
    expected = hashlib.sha256(digest_file.read_bytes()).hexdigest()
    argv = ["gate-check"]
    for stage, directory in enumerate(directories, start=1):
        argv += [f"--stage{stage}", str(directory / "run")]
    argv += ["--output", str(output), "--expected-distribution-digest", expected]
    status = _cli.main(argv)
    return status, json.loads(output.read_text(encoding="utf-8"))


def test_stub_evidence_passes_the_real_combined_gate_and_the_executed_cell_check(tmp_path, digest_file, capsys):
    written = stub.emit(tmp_path / "evidence", TAG, HEAD, digest_file)
    status, combined = _gate(written, tmp_path / "gate.json", digest_file)
    assert status == 0, capsys.readouterr().err
    assert combined["verdict"] == "green"
    assert combined["source_commit_sha"] == HEAD
    from scripts import release_uat_evidence_check as check

    violations, shards, executed = check.check_evidence(tmp_path / "evidence", TAG)
    assert (violations, shards) == ([], 3)
    assert executed > 0


def test_stub_evidence_is_rejected_for_another_distribution(tmp_path, digest_file):
    written = stub.emit(tmp_path / "evidence", TAG, HEAD, digest_file)
    other = tmp_path / "other-digest.txt"
    other.write_text("c" * 64 + "  other\n")
    status, combined = _gate(written, tmp_path / "gate.json", other)
    assert status == 1
    assert any("distribution digest" in reason for reason in combined["reasons"])


def test_stub_evidence_is_rejected_when_a_planned_cell_is_missing(tmp_path, digest_file):
    written = stub.emit(tmp_path / "evidence", TAG, HEAD, digest_file)
    cells = written[0] / "run" / "cells.jsonl"
    lines = cells.read_text(encoding="utf-8").splitlines(keepends=True)
    cells.write_text("".join(lines[1:]), encoding="utf-8")
    status, combined = _gate(written, tmp_path / "gate.json", digest_file)
    assert status == 1
    assert combined["verdict"] == "red"


def test_stub_evidence_names_match_what_the_release_gate_globs(tmp_path, digest_file):
    written = stub.emit(tmp_path / "evidence", TAG, HEAD, digest_file)
    assert [path.name for path in written] == [f"release-uat-{TAG}-stage{n}-probe" for n in (1, 2, 3)]
    for path in written:
        assert (path / "dist-digest.txt").read_bytes() == digest_file.read_bytes()


def test_stub_docker_stages_start_after_the_native_stage_finished(tmp_path, digest_file):
    written = stub.emit(tmp_path / "evidence", TAG, HEAD, digest_file)
    assert (written[0] / "run" / "uat_lifecycle.log").read_text(encoding="utf-8") == ""
    for path in written[1:]:
        assert "[docker]" in (path / "run" / "uat_lifecycle.log").read_text(encoding="utf-8")


