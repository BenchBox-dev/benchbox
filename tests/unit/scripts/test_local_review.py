from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest
import theseus_cli

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).resolve().parents[3]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)
    return module


wrapper = _load("local_review")

FAKE_BOOTSTRAP = """\
import os
import sys


def main(argv):
    if not os.environ["FAKE_THESEUS"] or not (sys.flags.isolated and sys.flags.no_site):
        return 1
    print("noise")
    print(os.environ["FAKE_THESEUS"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
"""


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "--quiet", "--initial-branch=develop")
    bootstrap = work / theseus_cli.BOOTSTRAP_PATH
    bootstrap.parent.mkdir(parents=True)
    bootstrap.write_text(FAKE_BOOTSTRAP, encoding="utf-8")
    _git(work, "add", theseus_cli.BOOTSTRAP_PATH)
    _git(work, "commit", "--quiet", "-m", "trusted")
    monkeypatch.chdir(work)
    return work


def _args(*extra: str):
    return wrapper.parser().parse_args(["--head", "HEAD", *extra])


def _launched(args, monkeypatch: pytest.MonkeyPatch, program: Path) -> tuple[list[str], dict[str, str]]:
    monkeypatch.setattr(theseus_cli, "executable", lambda root, ref: program)
    seen: list[tuple[list[str], dict[str, str]]] = []

    def launcher(argv: list[str], env: dict[str, str]) -> int:
        seen.append((argv, env))
        return 7

    assert wrapper.run(args, launcher) == 7
    return seen[0]


def test_the_review_runs_the_verified_install_with_the_trusted_inputs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    program = tmp_path / "theseus"
    argv, _ = _launched(_args("--base-ref", "origin/develop", "--trusted-ref", "origin/develop"), monkeypatch, program)

    assert argv == [
        "theseus",
        "local-review",
        "--policy",
        ".github/review.yml",
        "--base-ref",
        "origin/develop",
        "--trusted-ref",
        "origin/develop",
        "--head",
        "HEAD",
    ]


def test_the_trusted_ref_defaults_to_develop_and_never_follows_the_base_ref(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for extra in ([], ["--base-ref", "origin/feature"], ["--member", "--base-ref", "c" * 40]):
        argv, _ = _launched(_args(*extra), monkeypatch, tmp_path / "theseus")
        assert argv[argv.index("--trusted-ref") + 1] == wrapper.DEVELOP_REF


def test_the_install_is_prepared_from_the_trusted_ref_not_the_base_ref(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prepared = []
    monkeypatch.setattr(wrapper.theseus_cli, "executable", lambda root, ref: prepared.append(ref) or tmp_path / "t")
    monkeypatch.setattr(wrapper.theseus_cli, "launcher_environment", lambda program: {})
    wrapper.run(_args("--base-ref", "origin/feature"), lambda argv, env: 0)
    assert prepared == [wrapper.DEVELOP_REF]


def test_member_mode_passes_the_result_file_and_the_given_trusted_ref(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    args = _args("--member", "--result-json", "out.json", "--base-ref", "a" * 40, "--trusted-ref", "b" * 40)
    argv, _ = _launched(args, monkeypatch, tmp_path / "theseus")

    assert argv[argv.index("--trusted-ref") + 1] == "b" * 40
    assert argv[-3:] == ["--member", "--result-json", "out.json"]


def test_rebuttals_and_adjudication_are_forwarded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    args = _args("--rebuttals", "r.json", "--adjudicate", "owner ruling")
    argv, _ = _launched(args, monkeypatch, tmp_path / "theseus")

    assert argv[argv.index("--rebuttals") + 1] == "r.json"
    assert argv[argv.index("--adjudicate") + 1] == "owner ruling"


def test_the_record_path_and_cache_locations_reach_the_review_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in ("THESEUS_LOCAL_STATE", "THESEUS_OMP_CACHE", "THESEUS_EPHEMERAL_CLONE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BENCHBOX_EPHEMERAL_CLONE", "1")
    _, env = _launched(_args("--record-out", "record.txt"), monkeypatch, tmp_path / "theseus")

    assert env[wrapper.RECORD_OUT_ENV] == "record.txt"
    assert env["PATH"].split(os.pathsep)[0] == str(tmp_path)
    assert env["THESEUS_LOCAL_STATE"] == str(Path.home() / ".cache" / "benchbox" / "local-review")
    assert env["THESEUS_OMP_CACHE"] == str(Path.home() / ".cache" / "benchbox" / "omp")
    assert env["THESEUS_EPHEMERAL_CLONE"] == "1"


def test_the_unavailable_path_reaches_the_review_environment_only_when_given(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, env = _launched(_args("--unavailable-out", "why.txt"), monkeypatch, tmp_path / "theseus")
    assert env[wrapper.UNAVAILABLE_OUT_ENV] == "why.txt"
    monkeypatch.delenv(wrapper.UNAVAILABLE_OUT_ENV, raising=False)
    _, bare = _launched(_args(), monkeypatch, tmp_path / "theseus")
    assert wrapper.UNAVAILABLE_OUT_ENV not in bare


def test_an_explicit_state_directory_is_not_overridden(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("THESEUS_LOCAL_STATE", str(tmp_path / "elsewhere"))
    _, env = _launched(_args(), monkeypatch, tmp_path / "theseus")

    assert env["THESEUS_LOCAL_STATE"] == str(tmp_path / "elsewhere")


def test_a_refused_install_blocks_the_review(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse(root: Path, ref: str) -> Path:
        raise theseus_cli.TheseusError("lock hash mismatch")

    monkeypatch.setattr(theseus_cli, "executable", refuse)

    def never(argv: list[str], env: dict[str, str]) -> int:
        raise AssertionError("the review must not launch")

    assert wrapper.run(_args("--base-ref", "origin/develop"), never) == wrapper.EXIT_UNAVAILABLE
    assert "lock hash mismatch" in capsys.readouterr().err


def test_the_first_stage_comes_from_the_trusted_commit_not_the_working_tree(
    repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    trusted = _git(repo, "rev-parse", "HEAD")
    launcher = tmp_path / "bin" / "theseus"
    launcher.parent.mkdir()
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("FAKE_THESEUS", str(launcher))
    (repo / theseus_cli.BOOTSTRAP_PATH).write_text("raise SystemExit('tampered working tree')\n", encoding="utf-8")

    assert theseus_cli.executable(repo, trusted) == launcher


def test_a_first_stage_failure_is_a_refusal(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    trusted = _git(repo, "rev-parse", "HEAD")
    monkeypatch.setenv("FAKE_THESEUS", "")

    with pytest.raises(theseus_cli.TheseusError, match="was refused"):
        theseus_cli.executable(repo, trusted)


def test_a_ref_without_a_first_stage_is_a_refusal(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    _git(empty, "init", "--quiet")
    _git(empty, "commit", "--quiet", "--allow-empty", "-m", "empty")

    with pytest.raises(theseus_cli.TheseusError):
        theseus_cli.executable(empty, _git(empty, "rev-parse", "HEAD"))


def test_a_non_sha_trusted_commit_is_refused(repo: Path) -> None:
    with pytest.raises(theseus_cli.TheseusError, match="full SHA"):
        theseus_cli.install(repo, "HEAD")


def test_the_environment_does_not_leak_into_the_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("THESEUS_OMP_CACHE", raising=False)
    theseus_cli.environment()

    assert "THESEUS_OMP_CACHE" not in os.environ


