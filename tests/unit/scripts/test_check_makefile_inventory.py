from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "make" / "check_makefile_inventory.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_makefile_inventory", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _copy_make_contract(destination: Path) -> None:
    shutil.copy2(REPO_ROOT / "Makefile", destination / "Makefile")
    shutil.copytree(REPO_ROOT / "make", destination / "make", ignore=shutil.ignore_patterns("inventory.json"))


def _run_make(root: Path, *arguments: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "--no-print-directory", "-C", str(root), *arguments],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


def test_check_builds_in_memory_and_never_writes_the_manifest(tmp_path: Path) -> None:
    module = _load_module()
    _copy_make_contract(tmp_path)
    manifest = tmp_path / module.MANIFEST_PATH
    assert not manifest.exists()

    assert module.main(["--root", str(tmp_path)]) == 0

    assert not manifest.exists()
    assert module.validate_migration_proof(REPO_ROOT) == []


def test_check_leaves_an_existing_manifest_untouched(tmp_path: Path) -> None:
    module = _load_module()
    _copy_make_contract(tmp_path)
    manifest = tmp_path / module.MANIFEST_PATH
    manifest.write_text("{}\n", encoding="utf-8")

    assert module.main(["--root", str(tmp_path)]) == 0

    assert manifest.read_text(encoding="utf-8") == "{}\n"


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="needs enforced POSIX directory permissions")
def test_check_succeeds_on_a_read_only_tree(tmp_path: Path) -> None:
    module = _load_module()
    _copy_make_contract(tmp_path)
    directories = [tmp_path / "make", tmp_path]
    for directory in directories:
        directory.chmod(0o555)
    try:
        assert module.main(["--root", str(tmp_path)]) == 0
        assert not (tmp_path / module.MANIFEST_PATH).exists()
    finally:
        for directory in directories:
            directory.chmod(0o755)


def test_write_mode_writes_the_manifest(tmp_path: Path) -> None:
    module = _load_module()
    _copy_make_contract(tmp_path)

    assert module.main(["--root", str(tmp_path), "--write"]) == 0

    manifest = tmp_path / module.MANIFEST_PATH
    assert json.loads(manifest.read_text(encoding="utf-8")) == module.build_inventory(tmp_path)


def test_checked_monolith_baseline_has_expected_contract() -> None:
    module = _load_module()
    baseline = module._load_inventory(REPO_ROOT / module.BASELINE_PATH)

    assert baseline["target_count"] == 198
    assert baseline["public_target_count"] == 195
    assert baseline["default_goal"] == "test"


def test_public_target_removal_is_reflected_in_the_evaluated_inventory(tmp_path: Path) -> None:
    module = _load_module()
    _copy_make_contract(tmp_path)
    makefile = tmp_path / "Makefile"
    text = makefile.read_text(encoding="utf-8")
    assert "test-fast:\n" in text
    makefile.write_text(text.replace("test-fast:\n", "test-fast-removed:\n", 1), encoding="utf-8")

    inventory, problems = module.evaluate_inventory(tmp_path)

    assert problems == []
    assert inventory is not None
    assert "test-fast" not in inventory["targets"]
    assert "test-fast-removed" in inventory["targets"]


def test_required_include_removal_fails_closed(tmp_path: Path) -> None:
    module = _load_module()
    _copy_make_contract(tmp_path)
    (tmp_path / "make" / "help.mk").unlink()

    assert module.evaluate_inventory(tmp_path) == (None, ["required Make include is missing: make/help.mk"])
    assert module.main(["--root", str(tmp_path)]) == 1


def test_semantic_assignment_reorder_changes_gnu_make_evaluation_and_the_inventory(tmp_path: Path) -> None:
    module = _load_module()
    _copy_make_contract(tmp_path)
    platform_makefile = tmp_path / "make" / "platform-tests.mk"
    original = "CONTAINER_ENGINE ?= docker\nCOMPOSE := $(CONTAINER_ENGINE) compose"
    reordered = "COMPOSE := $(CONTAINER_ENGINE) compose\nCONTAINER_ENGINE ?= docker"
    text = platform_makefile.read_text(encoding="utf-8")
    assert original in text
    before_inventory, _ = module.evaluate_inventory(tmp_path)

    before = _run_make(tmp_path, "-pn", "test")
    assert before.returncode == 0
    assert "COMPOSE := docker compose" in before.stdout.splitlines()

    platform_makefile.write_text(text.replace(original, reordered, 1), encoding="utf-8")
    after = _run_make(tmp_path, "-pn", "test")
    assert after.returncode == 0
    assert "COMPOSE :=  compose" in after.stdout.splitlines()

    after_inventory, problems = module.evaluate_inventory(tmp_path)
    assert problems == []
    assert before_inventory is not None and after_inventory is not None
    assert after_inventory["semantic_sha256"] != before_inventory["semantic_sha256"]


def test_writer_adds_future_target_without_rewriting_migration_proof(tmp_path: Path) -> None:
    module = _load_module()
    _copy_make_contract(tmp_path)
    maintenance = tmp_path / "make" / "worktree-maintenance.mk"
    maintenance.write_text(
        maintenance.read_text(encoding="utf-8")
        + "\n.PHONY: future-contract-probe\nfuture-contract-probe:\n\t@echo future\n",
        encoding="utf-8",
    )
    baseline = tmp_path / module.BASELINE_PATH
    proof = tmp_path / module.MIGRATION_PROOF_PATH
    original_baseline = baseline.read_bytes()
    original_proof = proof.read_bytes()

    result = module.main(["--root", str(tmp_path), "--write"])

    assert result == 0
    manifest = json.loads((tmp_path / module.MANIFEST_PATH).read_text(encoding="utf-8"))
    assert "future-contract-probe" in manifest["targets"]
    assert baseline.read_bytes() == original_baseline
    assert proof.read_bytes() == original_proof


@pytest.mark.parametrize(
    ("arguments", "environment"),
    [
        (("BENCHBOX_MAKEFILE_ROOT=/definitely/missing/", "-n", "help"), None),
        (
            ("-e", "-n", "help"),
            {**os.environ, "BENCHBOX_MAKEFILE_ROOT": "/definitely/missing/"},
        ),
    ],
)
def test_reserved_include_root_rejects_cli_and_environment_override(
    tmp_path: Path,
    arguments: tuple[str, ...],
    environment: dict[str, str] | None,
) -> None:
    _copy_make_contract(tmp_path)

    result = _run_make(tmp_path, *arguments, env=environment)

    assert result.returncode == 0, result.stderr
    assert "makefile-inventory-check" in result.stdout
    assert "/definitely/missing" not in result.stderr


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="creating a symlink on Windows requires elevation or developer mode",
)
def test_absolute_symlink_makefile_preserves_module_resolution(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _copy_make_contract(repository)
    invocation = tmp_path / "invocation"
    invocation.mkdir()
    makefile_link = invocation / "BenchBox.mk"
    makefile_link.symlink_to(repository / "Makefile")

    result = subprocess.run(
        ["make", "--no-print-directory", "-f", str(makefile_link), "-n", "help"],
        cwd=invocation,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0, result.stderr
    assert "makefile-inventory-check" in result.stdout


AGENT_MAKE_INTERFACES = [
    "agent-write-preflight",
    "ci-lint",
    "ci-linux",
    "comment-policy-check",
    "compat-docs-check",
    "duplicate-check-delta",
    "duplicate-check-verbose",
    "help",
    "makefile-inventory-check",
    "oracle-review-recover",
    "platform-manifest-check",
    "pr-arm",
    "pr-open",
    "pr-preflight",
    "pr-ready",
    "skill-sync",
    "test",
    "test-fast",
    "worktree-create",
]


def test_make_targets_agents_rely_on_stay_public() -> None:
    module = _load_module()
    public = set(module.build_inventory(REPO_ROOT)["public_targets"])

    assert sorted(set(AGENT_MAKE_INTERFACES) - public) == []


@pytest.mark.skipif(
    not (REPO_ROOT / "AGENTS.md").is_file(), reason="agent instructions are not part of a curated release"
)
def test_every_make_target_named_in_agent_instructions_exists() -> None:
    module = _load_module()
    public = set(module.build_inventory(REPO_ROOT)["public_targets"])
    instructions = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    named = set(re.findall(r"`make ([a-z][a-z0-9_-]*)", instructions))
    named |= set(re.findall(r"^make ([a-z][a-z0-9_-]*)", instructions, flags=re.MULTILINE))

    assert named
    assert sorted(named - public) == []
