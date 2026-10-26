from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/check_docs_freeze.py"
pytestmark = [pytest.mark.unit, pytest.mark.fast]

MANIFEST = "quality/docs-ownership.json"
OWNERSHIP = {
    "docs/guides/public.md": "site-owned",
    "docs/guides/other.md": "site-owned",
    "docs/internal/private.md": "core-only",
    "docs/compat/matrix.md": "core-generated",
    "docs/benchmarks/queries/q1.md": "site-owned",
}
GENERATED = [
    {"path": "docs/compat/matrix.md", "generator": "scripts/generate_compat_docs.py"},
    {"path": "docs/benchmarks/queries/", "generator": "scripts/generate_query_docs.py"},
]


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def write(repo: Path, path: str, text: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def write_manifest(repo: Path, files: dict[str, str]) -> None:
    write(repo, MANIFEST, json.dumps({"schema": 1, "files": files, "generated": GENERATED, "transitions": {}}))


def commit(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "--quiet", "-m", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "--quiet", "-b", "main")
    git(root, "config", "user.email", "t@example.invalid")
    git(root, "config", "user.name", "Test")
    git(root, "config", "commit.gpgsign", "false")
    for path in OWNERSHIP:
        write(root, path, "base\n")
    write_manifest(root, OWNERSHIP)
    write(root, "scripts/check_docs_freeze.py", "gate\n")
    write(root, ".github/workflows/ci.yml", "run: python check_docs_freeze.py\n")
    write(root, "make/documentation.mk", "\tpython scripts/check_docs_freeze.py\n")
    write(root, "src/core.py", "base\n")
    commit(root, "base")
    return root


def run_gate(repo: Path, base: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(repo), "--base", base, "--head", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )


def gate_after(repo: Path, mutate) -> subprocess.CompletedProcess[str]:
    base = git(repo, "rev-parse", "HEAD")
    mutate(repo)
    commit(repo, "change")
    return run_gate(repo, base)


def test_site_owned_edit_fails(repo: Path) -> None:
    result = gate_after(repo, lambda r: write(r, "docs/guides/public.md", "edited\n"))
    assert result.returncode == 1
    assert "docs/guides/public.md: changed" in result.stderr


def test_site_owned_deletion_fails(repo: Path) -> None:
    result = gate_after(repo, lambda r: (r / "docs/guides/public.md").unlink())
    assert result.returncode == 1
    assert "docs/guides/public.md: deleted" in result.stderr


def test_site_owned_rename_fails_on_both_sides(repo: Path) -> None:
    result = gate_after(repo, lambda r: git(r, "mv", "docs/guides/public.md", "docs/guides/moved.md"))
    assert result.returncode == 1
    assert "docs/guides/public.md: deleted" in result.stderr


def test_core_only_edit_passes(repo: Path) -> None:
    result = gate_after(repo, lambda r: write(r, "docs/internal/private.md", "edited\n"))
    assert result.returncode == 0, result.stderr


def test_core_generated_and_query_paths_are_not_frozen(repo: Path) -> None:
    def mutate(r: Path) -> None:
        write(r, "docs/compat/matrix.md", "regenerated\n")
        write(r, "docs/benchmarks/queries/q1.md", "regenerated\n")

    result = gate_after(repo, mutate)
    assert result.returncode == 0, result.stderr


def test_non_docs_change_is_unaffected(repo: Path) -> None:
    result = gate_after(repo, lambda r: write(r, "src/core.py", "edited\n"))
    assert result.returncode == 0, result.stderr


def test_new_core_only_entry_and_file_pass(repo: Path) -> None:
    def mutate(r: Path) -> None:
        write(r, "docs/internal/new.md", "new\n")
        write_manifest(r, {**OWNERSHIP, "docs/internal/new.md": "core-only"})

    result = gate_after(repo, mutate)
    assert result.returncode == 0, result.stderr


def test_new_site_owned_file_fails(repo: Path) -> None:
    def mutate(r: Path) -> None:
        write(r, "docs/guides/new.md", "new\n")
        write_manifest(r, {**OWNERSHIP, "docs/guides/new.md": "site-owned"})

    result = gate_after(repo, mutate)
    assert result.returncode == 1
    assert "docs/guides/new.md: site-owned source added" in result.stderr


def test_reowning_a_frozen_path_fails(repo: Path) -> None:
    result = gate_after(repo, lambda r: write_manifest(r, {**OWNERSHIP, "docs/guides/public.md": "core-only"}))
    assert result.returncode == 1
    assert "changes the frozen site-owned set" in result.stderr


def test_deleting_the_manifest_fails(repo: Path) -> None:
    result = gate_after(repo, lambda r: (r / MANIFEST).unlink())
    assert result.returncode == 1


def retire(repo: Path) -> None:
    (repo / "scripts/check_docs_freeze.py").unlink()
    write(repo, ".github/workflows/ci.yml", "run: true\n")
    write(repo, "make/documentation.mk", "\ttrue\n")


def test_atomic_removal_passes_with_deletion_and_ownership_transition(repo: Path) -> None:
    def mutate(r: Path) -> None:
        retire(r)
        (r / "docs/guides/public.md").unlink()
        write_manifest(r, {k: v for k, v in OWNERSHIP.items() if k != "docs/guides/public.md"})

    result = gate_after(repo, mutate)
    assert result.returncode == 0, result.stderr


def test_removal_does_not_allow_editing_a_frozen_source(repo: Path) -> None:
    def mutate(r: Path) -> None:
        retire(r)
        write(r, "docs/guides/public.md", "edited\n")

    result = gate_after(repo, mutate)
    assert result.returncode == 1
    assert "docs/guides/public.md: changed" in result.stderr


@pytest.mark.parametrize(
    "survivor", ["scripts/check_docs_freeze.py", ".github/workflows/ci.yml", "make/documentation.mk"]
)
def test_partial_removal_does_not_unlock_deletion(repo: Path, survivor: str) -> None:
    def mutate(r: Path) -> None:
        retire(r)
        if survivor == "scripts/check_docs_freeze.py":
            write(r, survivor, "gate\n")
        else:
            write(r, survivor, "check_docs_freeze\n")
        (r / "docs/guides/public.md").unlink()

    result = gate_after(repo, mutate)
    assert result.returncode == 1
    assert "docs/guides/public.md: deleted" in result.stderr


def test_missing_base_manifest_is_an_error(repo: Path) -> None:
    (repo / MANIFEST).unlink()
    base = commit(repo, "drop manifest")
    write(repo, "src/core.py", "edited\n")
    commit(repo, "change")
    result = run_gate(repo, base)
    assert result.returncode == 2
    assert "missing at the base revision" in result.stderr
