from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.check_make_targets as make_targets

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.skipif(shutil.which("make") is None or sys.platform == "win32", reason="POSIX make is required"),
]

REPO_ROOT = Path(__file__).resolve().parents[3]
TARGET_CEILING = 190


def _copy_make_files(destination: Path) -> None:
    shutil.copy2(REPO_ROOT / "Makefile", destination / "Makefile")
    shutil.copytree(REPO_ROOT / "make", destination / "make")


@pytest.mark.parametrize(
    ("markdown", "expected"),
    [
        ("Run `make lint typecheck` first.", {"lint", "typecheck"}),
        ("```bash\nmake worktree-create BRANCH=fix/x WORKTREE_PATH=../y\ncd ../y\n```", {"worktree-create"}),
        ("`make pr-ready PR=1 HEAD=$(git rev-parse HEAD)`", {"pr-ready"}),
        ("```make\n\t$(MAKE) skill-sync-check; \\\n```", {"skill-sync-check"}),
        ("`make -C ../other test-fast`", {"test-fast"}),
        ("Make sure to make the change before you make lint.", set()),
        ("`make <target>` is the shape", set()),
    ],
)
def test_the_scanner_reads_make_targets_only_from_code(markdown: str, expected: set[str]) -> None:
    assert make_targets.named_targets(markdown) == expected


def test_every_make_target_named_in_documentation_exists() -> None:
    assert make_targets.missing_documented_targets(REPO_ROOT) == {}


def test_the_check_names_a_missing_target_and_skips_only_decision_records(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "adr" / "checkout"
    root.mkdir(parents=True)
    _copy_make_files(root)
    (root / "docs" / "internal" / "adr").mkdir(parents=True)
    (root / "docs" / "guide.md").write_text("Run `make help`, then `make retired-target`.\n", encoding="utf-8")
    (root / "docs" / "internal" / "adr" / "adr-old.md").write_text(
        "We once ran `make historical-target`.\n", encoding="utf-8"
    )

    assert make_targets.main(["--root", str(root)]) == 1
    errors = capsys.readouterr().err
    assert "make retired-target: no such target, named in docs/guide.md" in errors
    assert "historical-target" not in errors


@pytest.mark.parametrize("damage", ["missing include", "duplicate recipe"])
def test_reading_the_rules_fails_when_make_reports_a_problem(tmp_path: Path, damage: str) -> None:
    _copy_make_files(tmp_path)
    if damage == "missing include":
        (tmp_path / "make" / "help.mk").unlink()
    else:
        with (tmp_path / "Makefile").open("a", encoding="utf-8") as makefile:
            makefile.write("test-fast:\n\techo duplicate\n")

    with pytest.raises(RuntimeError, match="make could not read the rules"):
        make_targets.targets(tmp_path)


_MAKE_3_ENTRY = (
    "timed:\n"
    "#  Phony target (prerequisite of .PHONY).\n"
    "# makefile (from `Makefile', line 2)\n"
    "# SHELL := /bin/sh\n"
    "#  commands to execute (from `Makefile', line 6):\n"
    "\t@echo ran\n"
)
_MAKE_4_ENTRY = (
    "# makefile (from 'Makefile', line 2)\n"
    "timed: SHELL := /bin/sh\n"
    "timed:\n"
    "#  Phony target (prerequisite of .PHONY).\n"
    "#  recipe to execute (from 'Makefile', line 6):\n"
    "\t@echo ran\n"
)
_NOT_A_TARGET = "# Not a target:\nMakefile:\n#  Implicit rule search has not been done.\n"


@pytest.mark.parametrize("entry", [_MAKE_3_ENTRY, _MAKE_4_ENTRY], ids=["make-3", "make-4"])
def test_rules_read_a_target_and_its_variables_in_either_make_layout(
    monkeypatch: pytest.MonkeyPatch, entry: str
) -> None:
    monkeypatch.setattr(make_targets, "_database", lambda root: f"# Files\n\n{entry}\n{_NOT_A_TARGET}\n")

    found = make_targets.rules(REPO_ROOT)

    assert set(found) == {"timed"}
    assert found["timed"].variables["SHELL"] == "/bin/sh"
    assert found["timed"].recipe == ("\t@echo ran",)
    assert make_targets.targets(REPO_ROOT) == ["timed"]


def test_the_make_target_count_stays_within_the_ceiling() -> None:
    count = len(make_targets.targets(REPO_ROOT))

    assert count <= TARGET_CEILING, (
        f"{count} make targets exceed the ceiling of {TARGET_CEILING}: consolidate or remove a target instead of "
        "adding one, or raise TARGET_CEILING with the owner's agreement"
    )


@pytest.mark.parametrize(
    ("arguments", "environment"),
    [
        (("BENCHBOX_MAKEFILE_ROOT=/definitely/missing/", "-n", "help"), None),
        (("-e", "-n", "help"), {**os.environ, "BENCHBOX_MAKEFILE_ROOT": "/definitely/missing/"}),
    ],
)
def test_the_module_root_ignores_command_line_and_environment_overrides(
    tmp_path: Path, arguments: tuple[str, ...], environment: dict[str, str] | None
) -> None:
    _copy_make_files(tmp_path)

    result = subprocess.run(
        ["make", "--no-print-directory", "-C", str(tmp_path), *arguments],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    assert "duplicate-check-delta" in result.stdout
    assert "/definitely/missing" not in result.stderr


def test_a_symlinked_makefile_still_finds_its_modules(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _copy_make_files(repository)
    invocation = tmp_path / "invocation"
    invocation.mkdir()
    (invocation / "BenchBox.mk").symlink_to(repository / "Makefile")

    result = subprocess.run(
        ["make", "--no-print-directory", "-f", str(invocation / "BenchBox.mk"), "-n", "help"],
        cwd=invocation,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0, result.stderr
    assert "duplicate-check-delta" in result.stdout
