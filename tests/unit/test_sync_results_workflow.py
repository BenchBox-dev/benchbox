from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "sync-results-data-to-published.yml"
DRIFT_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "corpus-drift-check.yml"
BUNDLES_DIR = "results-data/bundles"


def _run(cmd: list[str], *, cwd: Path) -> str:
    return subprocess.check_output(cmd, cwd=cwd, text=True).strip()


def _init_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _run(["git", "init", "-b", "main"], cwd=root)
    _run(["git", "config", "user.email", "mirror@example.com"], cwd=root)
    _run(["git", "config", "user.name", "Mirror Test"], cwd=root)
    _run(["git", "config", "core.autocrlf", "false"], cwd=root)


def _commit_files(root: Path, files: dict[str, str], message: str) -> str:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8"))
        _run(["git", "add", rel], cwd=root)
    _run(["git", "commit", "--allow-empty", "-m", message], cwd=root)
    return _run(["git", "rev-parse", "HEAD"], cwd=root)


def _tracked_under(root: Path, prefix: str, ref: str = "HEAD") -> set[str]:
    raw = _run(["git", "ls-tree", "-r", "--name-only", ref, prefix], cwd=root)
    return {line for line in raw.splitlines() if line}


def _build_step_run() -> str:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    for step in workflow["jobs"]["mirror"]["steps"]:
        if step.get("name") == "Build mirror branch":
            run = step.get("run", "")
            assert run, "Build mirror branch step has empty run body"
            return run
    raise AssertionError("sync workflow has no Build mirror branch step")


FIXED_POINT_GATE_STEP = "Gate corpus publication fixed point"
BUILD_MIRROR_STEP = "Build mirror branch"
FIXED_POINT_TEST_FRAGMENT = "rederived_corpus_publishes_byte_identically"


def test_sync_results_workflow_sources_triggering_commit() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "ref: ${{ github.sha }}" in workflow
    assert 'git diff --name-only origin/published-results "${GITHUB_SHA}"' in workflow
    assert 'SOURCE_REF="${GITHUB_SHA}"' in workflow
    assert 'git checkout "${SOURCE_REF}" -- "${path}"' in workflow
    assert "origin/develop" not in workflow


def test_sync_workflow_captures_and_compares_publication_revision() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    build = _build_step_run()

    assert "PUBLISHED_BASE_SHA=$(git rev-parse --verify origin/published-results^{commit})" in workflow
    assert 'echo "published_base_sha=${PUBLISHED_BASE_SHA}" >> "$GITHUB_OUTPUT"' in workflow
    assert 'git switch --force-create "${MIRROR_BRANCH}" "${PUBLISHED_BASE_SHA}"' in build
    assert "CURRENT_PUBLISHED_SHA=$(git rev-parse --verify origin/published-results^{commit})" in build
    assert '"${CURRENT_PUBLISHED_SHA}" != "${PUBLISHED_BASE_SHA}"' in build
    assert "No mirror PR was opened" in build
    assert "Re-run this workflow" in build
    assert build.rfind('echo "has_changes=true"') > build.index("CURRENT_PUBLISHED_SHA")
    assert build.rfind('echo "mirror_head_sha=${MIRROR_HEAD_SHA}"') > build.index("CURRENT_PUBLISHED_SHA")

    assert "published_base_sha" in workflow
    assert "mirror_head_sha" in workflow
    assert "built from published-results@${PUBLISHED_BASE_SHA}" in workflow


def test_sync_workflow_revalidates_publication_after_pr_creation() -> None:
    steps = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))["jobs"]["mirror"]["steps"]
    names = [step.get("name") for step in steps]
    open_step = next(step for step in steps if step.get("name") == "Open or update draft PR")
    verify_step = next(step for step in steps if step.get("name") == "Verify mirror PR publication freshness")

    assert open_step["id"] == "pr"
    assert 'echo "number=${PR_NUMBER}" >> "$GITHUB_OUTPUT"' in open_step["run"]
    assert names.index("Open or update draft PR") < names.index("Verify mirror PR publication freshness")
    assert names.index("Verify mirror PR publication freshness") < names.index(
        "Fail the run if mirrored content failed validation"
    )
    assert "git/ref/heads/published-results" in verify_step["run"]
    assert "baseRefOid" in verify_step["run"]
    assert '"${CURRENT_PUBLISHED_SHA}" != "${PUBLISHED_BASE_SHA}"' in verify_step["run"]
    assert 'gh pr close "${PR_NUMBER}"' in verify_step["run"]
    assert "Re-run the mirror workflow" in verify_step["run"]
    assert 'echo "::error::${STALE_REASON}' in verify_step["run"]
    assert "exit 1" in verify_step["run"]


def test_sync_workflow_references_publication_fixed_point_gate() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert FIXED_POINT_TEST_FRAGMENT in workflow, (
        "sync workflow does not reference the publication fixed-point gate "
        f"({FIXED_POINT_TEST_FRAGMENT}); a non-fixed-point corpus could still be mirrored"
    )
    assert "AnonymizationManager" in workflow
    assert "canonical_json_bytes" in workflow
    assert "publication fixed point" in workflow.lower() or "not at the publication fixed point" in workflow


def test_sync_workflow_fixed_point_gate_runs_before_mirror_build() -> None:
    steps = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))["jobs"]["mirror"]["steps"]
    names = [step.get("name") for step in steps]
    assert FIXED_POINT_GATE_STEP in names, f"missing step {FIXED_POINT_GATE_STEP!r}"
    assert BUILD_MIRROR_STEP in names, f"missing step {BUILD_MIRROR_STEP!r}"
    assert names.index(FIXED_POINT_GATE_STEP) < names.index(BUILD_MIRROR_STEP), (
        "publication fixed-point gate runs after Build mirror branch; "
        "the mirror could be built from a corpus that publishing would rewrite"
    )

    gate = next(step for step in steps if step.get("name") == FIXED_POINT_GATE_STEP)
    run = gate.get("run", "")
    assert "Refusing to open a mirror" in run or "not at the publication fixed point" in run
    assert "SystemExit(1)" in run or "raise SystemExit" in run


def test_sync_workflow_does_not_wipe_bundles_directory() -> None:
    run = _build_step_run()
    assert "union overlay" in run.lower() or "Union overlay" in run
    executable = "\n".join(line for line in run.splitlines() if line.strip() and not line.lstrip().startswith("#"))
    assert "git rm -rf" not in executable
    assert 'git checkout "${SOURCE_REF}" -- "${BUNDLES_DIR}"' in executable
    assert "BUNDLES_DIR=" in executable or "BUNDLES_DIR='" in executable or 'BUNDLES_DIR="' in executable
    assert "published-only" in run


def test_wipe_then_checkout_deletes_published_only_when_develop_ahead(tmp_path: Path) -> None:
    repo = tmp_path / "wipe-proof"
    _init_repo(repo)

    published = {
        f"{BUNDLES_DIR}/shared.json": '{"id": "shared-v1"}\n',
        f"{BUNDLES_DIR}/community_only.json": '{"id": "community"}\n',
        "results-data/corpus-inventory.json": '{"version": 1}\n',
    }
    pub_sha = _commit_files(repo, published, "published tip")
    _run(["git", "branch", "published-results", pub_sha], cwd=repo)

    _run(["git", "rm", "-rf", "--quiet", BUNDLES_DIR], cwd=repo)
    develop = {
        f"{BUNDLES_DIR}/shared.json": '{"id": "shared-v2-sanitized"}\n',
        f"{BUNDLES_DIR}/develop_only.json": '{"id": "new-on-develop"}\n',
        "results-data/corpus-inventory.json": '{"version": 1}\n',
    }
    dev_sha = _commit_files(repo, develop, "develop tip")

    _run(["git", "switch", "--force-create", "mirror-wipe", "published-results"], cwd=repo)
    _run(["git", "rm", "-rf", "--quiet", BUNDLES_DIR], cwd=repo)
    _run(["git", "checkout", dev_sha, "--", BUNDLES_DIR], cwd=repo)
    _run(["git", "add", "-A"], cwd=repo)
    _run(["git", "commit", "-m", "wipe mirror"], cwd=repo)

    tracked = _tracked_under(repo, BUNDLES_DIR)
    assert f"{BUNDLES_DIR}/shared.json" in tracked
    assert f"{BUNDLES_DIR}/develop_only.json" in tracked
    assert f"{BUNDLES_DIR}/community_only.json" not in tracked, (
        "wipe-then-checkout deleted published-only community_only.json — the defect this TODO fixes"
    )


def test_union_overlay_preserves_published_only_when_develop_ahead(tmp_path: Path) -> None:
    repo = tmp_path / "overlay"
    _init_repo(repo)

    published = {
        f"{BUNDLES_DIR}/shared.json": '{"id": "shared-v1"}\n',
        f"{BUNDLES_DIR}/community_only.json": '{"id": "community"}\n',
        "results-data/corpus-inventory.json": '{"version": 1}\n',
    }
    pub_sha = _commit_files(repo, published, "published tip")
    _run(["git", "branch", "published-results", pub_sha], cwd=repo)

    _run(["git", "rm", "-rf", "--quiet", BUNDLES_DIR], cwd=repo)
    develop = {
        f"{BUNDLES_DIR}/shared.json": '{"id": "shared-v2-sanitized"}\n',
        f"{BUNDLES_DIR}/develop_only.json": '{"id": "new-on-develop"}\n',
        "results-data/corpus-inventory.json": '{"version": 1}\n',
    }
    dev_sha = _commit_files(repo, develop, "develop tip")

    _run(["git", "switch", "--force-create", "mirror-overlay", "published-results"], cwd=repo)
    _run(["git", "checkout", dev_sha, "--", BUNDLES_DIR], cwd=repo)
    _run(["git", "add", "-A"], cwd=repo)
    _run(["git", "commit", "-m", "overlay mirror"], cwd=repo)

    tracked = _tracked_under(repo, BUNDLES_DIR)
    assert f"{BUNDLES_DIR}/community_only.json" in tracked
    assert f"{BUNDLES_DIR}/develop_only.json" in tracked
    assert f"{BUNDLES_DIR}/shared.json" in tracked
    shared_body = (repo / f"{BUNDLES_DIR}/shared.json").read_text(encoding="utf-8")
    assert "shared-v2-sanitized" in shared_body
    community_body = (repo / f"{BUNDLES_DIR}/community_only.json").read_text(encoding="utf-8")
    assert "community" in community_body


def test_build_mirror_regenerates_inventory_after_union_overlay() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "generate_corpus_inventory.py --write" in workflow
    build = workflow.split("name: Build mirror branch", 1)[1].split("name: ", 1)[0]
    assert "generate_corpus_inventory.py --write" in build
    assert "FILE_PATHS=" in build
    file_paths_block = build.split("FILE_PATHS=", 1)[1].split(")", 1)[0]
    assert "corpus-inventory.json" not in file_paths_block


def test_build_mirror_carries_shared_query_status_policy() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert '"benchbox/core/results/query_status.py"' in workflow
    build = workflow.split("name: Build mirror branch", 1)[1].split("name: ", 1)[0]
    assert "'benchbox/core/results/query_status.py'" in build


def test_build_mirror_carries_shared_schema_policy() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert '"benchbox/core/results/schema_policy.py"' in workflow
    build = workflow.split("name: Build mirror branch", 1)[1].split("name: ", 1)[0]
    assert "'benchbox/core/results/schema_policy.py'" in build


def _mirrored_runtime_paths() -> tuple[list[str], list[str], list[str]]:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)
    triggers = workflow.get("on", workflow.get(True))["push"]["paths"]
    drift = next(
        step["run"]
        for step in workflow["jobs"]["mirror"]["steps"]
        if step.get("name") == "Detect drift between develop and published-results"
    )
    comparison = drift.split("mapfile -t DIFFED < <(", 1)[1].split(")\n", 1)[0].replace("\\\n", "")
    compare_paths = shlex.split(comparison)[shlex.split(comparison).index("--") + 1 :]
    copy_paths = shlex.split(_build_step_run().split("FILE_PATHS=(", 1)[1].split(")", 1)[0])
    return triggers, compare_paths, copy_paths


def test_parity_runtime_triggers_is_compared_and_is_copied() -> None:
    for paths in _mirrored_runtime_paths():
        assert "scripts/publication/validator_parity.py" in paths


def test_runtime_drift_copies_exact_source_and_preserves_published_only_archive(tmp_path: Path) -> None:
    repo = tmp_path / "runtime-mirror"
    _init_repo(repo)
    helper = "scripts/publication/validator_parity.py"
    community = f"{BUNDLES_DIR}/community.json"
    archive_bytes = '{"archive": "published-only"}\n'
    published = _commit_files(repo, {helper: "old helper\n", community: archive_bytes}, "published runtime")
    source_bytes = "new trusted helper\n"
    _run(["git", "rm", "--quiet", community], cwd=repo)
    source = _commit_files(repo, {helper: source_bytes}, "source runtime")
    assert community not in _tracked_under(repo, BUNDLES_DIR, source)
    _, compare_paths, copy_paths = _mirrored_runtime_paths()
    changed = _run(["git", "diff", "--name-only", published, source, "--", *compare_paths], cwd=repo)
    assert set(changed.splitlines()) == {helper, community}
    _run(["git", "switch", "--create", "mirror", published], cwd=repo)
    for path in copy_paths:
        exists = subprocess.run(["git", "cat-file", "-e", f"{source}:{path}"], cwd=repo, capture_output=True)
        if exists.returncode == 0:
            _run(["git", "checkout", source, "--", path], cwd=repo)
    assert (repo / helper).read_bytes() == source_bytes.encode()
    assert (repo / community).read_bytes() == archive_bytes.encode()
    assert _run(["git", "diff", "--cached", "--name-only"], cwd=repo) == helper


def test_drift_check_ignores_derived_inventory_difference() -> None:
    workflow = yaml.safe_load(DRIFT_WORKFLOW_PATH.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["corpus-drift"]["steps"]
    compare = next(step for step in steps if step.get("name") == "Compare the mirrored corpus paths (directional)")
    run = compare["run"]
    path_specs = run.split("PATHSPECS=(", 1)[1].split(")", 1)[0]
    assert "results-data/bundles" in path_specs
    assert "results-data/corpus-inventory.json" not in path_specs
