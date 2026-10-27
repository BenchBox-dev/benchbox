from __future__ import annotations

import ast
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath

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


def _drift_check_pathspecs() -> list[str]:
    workflow = yaml.safe_load(DRIFT_WORKFLOW_PATH.read_text(encoding="utf-8"))
    compare = next(
        step
        for step in workflow["jobs"]["corpus-drift"]["steps"]
        if step.get("name") == "Compare the mirrored corpus paths (directional)"
    )
    return shlex.split(compare["run"].split("PATHSPECS=(", 1)[1].split(")", 1)[0])


def test_drift_check_pathspec_matches_sync_list_except_derived_inventory() -> None:
    _, compare_paths, copy_paths = _mirrored_runtime_paths()
    drift_paths = _drift_check_pathspecs()

    assert sorted(drift_paths) == sorted(set(drift_paths))
    assert sorted(drift_paths) == sorted(set(compare_paths) - {"results-data/corpus-inventory.json"})
    assert sorted(drift_paths) == sorted([BUNDLES_DIR, *copy_paths])
    assert not [path for path in drift_paths if "*" in path]


def test_mirror_allowlist_excludes_modules_the_variant_identity_import_needs() -> None:
    triggers, compare_paths, copy_paths = _mirrored_runtime_paths()
    unmirrored = {
        "benchbox/core/results/execution_variant.py",
        "benchbox/core/execution_variant.py",
        "benchbox/core/platform_manifest.py",
    }

    for paths in (triggers, compare_paths, copy_paths, _drift_check_pathspecs()):
        assert not unmirrored & set(paths)


def test_validator_fails_closed_when_the_identity_module_is_not_mirrored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle = {
        "benchmark": {"id": "tpch", "scale_factor": 1.0, "test_type": "power"},
        "platform": {"name": "DuckDB"},
        "run": {"timestamp": "2026-08-01T12:00:00"},
        "export": {"anonymized": True},
        "summary": {
            "queries": {"total": 2, "passed": 2, "failed": 0},
            "validation": "passed",
            "tpc_metrics": {"power_at_size": 1.0},
        },
        "queries": [
            {"id": "Q1", "ms": 10.0, "run_type": "measurement", "status": "SUCCESS"},
            {"id": "Q2", "ms": 12.0, "run_type": "measurement", "status": "SUCCESS"},
        ],
    }
    (tmp_path / "rankable.json").write_text(json.dumps(bundle), encoding="utf-8")
    for name in [name for name in sys.modules if name == "benchbox" or name.startswith("benchbox.")]:
        monkeypatch.setitem(sys.modules, name, None)
    monkeypatch.setitem(sys.modules, "benchbox.core.results.execution_variant", None)
    spec = importlib.util.spec_from_file_location("validate_corpus", REPO_ROOT / "results-data" / "validate_corpus.py")
    assert spec and spec.loader
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)

    assert validator.main(tmp_path) == 1
    output = capsys.readouterr().out
    assert "needs the full benchbox package" in output
    assert "Cohorts:" not in output


VALIDATOR_ROOTS = ("results-data/validate_corpus.py", "scripts/validate_submission.py")


def _path_chain(node: ast.expr) -> list[ast.expr]:
    parts: list[ast.expr] = []
    while isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        parts.append(node.right)
        node = node.left
    parts.append(node)
    return parts[::-1]


def _chain_base(head: ast.expr, module: str) -> str | None:
    if isinstance(head, ast.Name) and head.id == "CHECKOUT_ROOT":
        return ""
    if isinstance(head, ast.Subscript) and isinstance(head.slice, ast.Constant):
        value = head.value
        if isinstance(value, ast.Attribute) and value.attr == "parents":
            return str(PurePosixPath(module).parents[head.slice.value])
    return None


def _repo_modules_loaded_by_path(module: str) -> set[str]:
    tree = ast.parse((REPO_ROOT / module).read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_load_repo_module"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
        ):
            found.add(str(node.args[1].value))
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            chain = _path_chain(node)
            base = _chain_base(chain[0], module)
            tail = chain[1:]
            if (
                base is None
                or not tail
                or not all(isinstance(p, ast.Constant) and isinstance(p.value, str) for p in tail)
            ):
                continue
            rel = PurePosixPath(base, *(p.value for p in tail))
            if rel.suffix == ".py":
                found.add(str(rel))
    return {path for path in found if (REPO_ROOT / path).is_file()}


def _validator_module_closure() -> set[str]:
    seen: set[str] = set()
    pending = list(VALIDATOR_ROOTS)
    while pending:
        module = pending.pop()
        if module in seen:
            continue
        seen.add(module)
        pending.extend(_repo_modules_loaded_by_path(module))
    return seen


def _git_add_targets() -> list[str]:
    build = _build_step_run()
    line = next(raw for raw in build.splitlines() if raw.strip().startswith("git add results-data "))
    return [token for token in shlex.split(line.strip())[2:] if not token.startswith("|")]


def _covered_by_git_add(path: str, targets: list[str]) -> bool:
    return any(path == target or path.startswith(f"{target}/") for target in targets)


def test_validator_path_loader_discovery_finds_known_modules() -> None:
    closure = _validator_module_closure()

    assert set(VALIDATOR_ROOTS) <= closure
    assert "benchbox/validation/bundle.py" in closure
    assert "benchbox/core/results/schema_policy.py" in closure
    assert "benchbox/core/results/query_status.py" in closure


def test_mirror_file_lists_cover_every_module_the_validators_load_by_path() -> None:
    required = _validator_module_closure()
    triggers, compare_paths, copy_paths = _mirrored_runtime_paths()
    add_targets = _git_add_targets()

    assert sorted(required - set(triggers)) == []
    assert sorted(required - set(compare_paths)) == []
    assert sorted(required - set(copy_paths)) == []
    assert sorted(path for path in required if not _covered_by_git_add(path, add_targets)) == []


def test_mirror_file_lists_name_only_repo_files_without_wildcards() -> None:
    triggers, compare_paths, copy_paths = _mirrored_runtime_paths()

    for path in copy_paths:
        assert "*" not in path
        assert (REPO_ROOT / path).is_file(), path
    for path in compare_paths:
        assert "*" not in path
        assert (REPO_ROOT / path).exists(), path
    assert not [path for path in _git_add_targets() if "*" in path]


REPO_IMPORT_ROOTS = frozenset({"benchbox", "scripts"})
IMPORT_ERROR_NAMES = frozenset({"ImportError", "ModuleNotFoundError", "Exception", "BaseException"})
DYNAMIC_IMPORT_NAMES = frozenset({"import_module", "__import__"})


def _repo_module_file(module: str) -> str | None:
    rel = PurePosixPath(*module.split("."))
    for candidate in (rel.with_suffix(".py"), rel / "__init__.py"):
        if (REPO_ROOT / candidate).is_file():
            return str(candidate)
    return None


def _handler_catches_import_error(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    names = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(
        (isinstance(name, ast.Name) and name.id in IMPORT_ERROR_NAMES)
        or (isinstance(name, ast.Attribute) and name.attr in IMPORT_ERROR_NAMES)
        for name in names
    )


def _guarded_import_nodes(tree: ast.AST) -> set[int]:
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Try) and any(_handler_catches_import_error(h) for h in node.handlers):
            for stmt in node.body:
                guarded.update(id(child) for child in ast.walk(stmt))
    return guarded


def _imported_repo_modules(node: ast.Import | ast.ImportFrom) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names if alias.name.split(".")[0] in REPO_IMPORT_ROOTS]
    if node.level or not node.module or node.module.split(".")[0] not in REPO_IMPORT_ROOTS:
        return []
    submodules = [
        f"{node.module}.{alias.name}" for alias in node.names if _repo_module_file(f"{node.module}.{alias.name}")
    ]
    return submodules or [node.module]


def _unguarded_unmirrored_imports(source: str, mirrored: set[str]) -> list[str]:
    tree = ast.parse(source)
    guarded = _guarded_import_nodes(tree)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
            if name in DYNAMIC_IMPORT_NAMES and id(node) not in guarded:
                offenders.append(f"line {node.lineno}: dynamic {name}()")
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        for module in _imported_repo_modules(node):
            if _repo_module_file(module) in mirrored or id(node) in guarded:
                continue
            offenders.append(f"line {node.lineno}: {module}")
    return offenders


def _mirrored_python_files() -> set[str]:
    _, _, copy_paths = _mirrored_runtime_paths()
    return {path for path in copy_paths if path.endswith(".py")}


def test_mirrored_python_files_never_import_unmirrored_repo_modules_without_a_guard() -> None:
    mirrored = _mirrored_python_files()
    assert "benchbox/validation/bundle.py" in mirrored
    assert "scripts/generate_corpus_inventory.py" in mirrored

    offenders = {
        path: found
        for path in sorted(mirrored)
        if (found := _unguarded_unmirrored_imports((REPO_ROOT / path).read_text(encoding="utf-8"), mirrored))
    }

    assert offenders == {}, (
        "mirrored files import repo modules that are not copied to published-results without an ImportError guard"
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import json\nfrom pathlib import Path\n", []),
        ("from benchbox.validation.bundle import discover_bundles\n", []),
        (
            "from benchbox.core.results.provenance import FUNDING_SOURCES\n",
            ["line 1: benchbox.core.results.provenance"],
        ),
        ("def f():\n    from benchbox.core.schemas import X\n", ["line 2: benchbox.core.schemas"]),
        ("import benchbox.core.benchmark_registry\n", ["line 1: benchbox.core.benchmark_registry"]),
        ("from benchbox.core.results import provenance\n", ["line 1: benchbox.core.results.provenance"]),
        ("import importlib\nimportlib.import_module('benchbox.core.schemas')\n", ["line 2: dynamic import_module()"]),
        (
            "try:\n    from benchbox.core.schemas import X\nexcept ImportError:\n    X = 2\n",
            [],
        ),
        (
            "def f():\n    try:\n        from benchbox.core.schemas import X\n    except ModuleNotFoundError as exc:\n        raise SystemExit(str(exc))\n",
            [],
        ),
        (
            "try:\n    pass\nexcept ImportError:\n    from benchbox.core.schemas import X\n",
            ["line 4: benchbox.core.schemas"],
        ),
        (
            "try:\n    from benchbox.core.schemas import X\nexcept KeyError:\n    X = 2\n",
            ["line 2: benchbox.core.schemas"],
        ),
    ],
)
def test_unguarded_import_detector(source: str, expected: list[str]) -> None:
    mirrored = {"benchbox/validation/bundle.py"}

    assert _unguarded_unmirrored_imports(source, mirrored) == expected


def _close_superseded_step_run() -> str:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    step = next(s for s in workflow["jobs"]["mirror"]["steps"] if s.get("name") == "Close superseded mirror PRs")
    return step["run"]


@pytest.mark.skipif(sys.platform == "win32", reason="runs a bash step body with a fake gh on PATH")
@pytest.mark.skipif(shutil.which("jq") is None, reason="the fake gh applies the step's --jq filter with jq")
def test_superseded_mirror_prs_close_only_when_their_commit_is_an_ancestor(tmp_path: Path) -> None:
    repo = tmp_path / "supersede"
    _init_repo(repo)
    older = _commit_files(repo, {"a.txt": "1"}, "older develop commit")
    current = _commit_files(repo, {"a.txt": "2"}, "current develop commit")
    _run(["git", "checkout", "-b", "side", older], cwd=repo)
    sibling = _commit_files(repo, {"b.txt": "3"}, "develop commit not contained in current")
    _run(["git", "checkout", "main"], cwd=repo)

    def pr(number: int, head: str, cross: bool = False) -> dict[str, object]:
        return {"number": number, "headRefName": head, "isCrossRepository": cross}

    listing = [
        pr(10, f"auto/results-mirror-{older[:8]}"),
        pr(11, f"auto/results-mirror-{current[:8]}"),
        pr(12, f"auto/results-mirror-{sibling[:8]}"),
        pr(13, "auto/results-mirror-deadbeef"),
        pr(14, f"auto/results-mirror-{older[:8]}", cross=True),
        pr(15, "feature/unrelated"),
    ]
    listing_file = tmp_path / "listing.json"
    listing_file.write_text(json.dumps(listing), encoding="utf-8")
    closed_log = tmp_path / "closed.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_gh = bin_dir / "gh"
    fake_gh.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1 $2" == "pr list" ]]; then\n'
        "  while [[ $# -gt 0 ]]; do\n"
        '    if [[ "$1" == "--jq" ]]; then jq -r "$2" "$LISTING_FILE"; exit 0; fi\n'
        "    shift\n"
        "  done\n"
        "fi\n"
        'if [[ "$1 $2" == "pr close" ]]; then echo "$3" >> "$CLOSED_LOG"; exit 0; fi\n'
        "exit 1\n",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "LISTING_FILE": str(listing_file),
        "CLOSED_LOG": str(closed_log),
        "PR_NUMBER": "11",
        "GITHUB_SHA": current,
    }
    script = tmp_path / "close_superseded.sh"
    script.write_text(_close_superseded_step_run(), encoding="utf-8")
    subprocess.run(["bash", "-c", '. "$1"', "bash", str(script)], cwd=repo, env=env, check=True)

    assert closed_log.read_text(encoding="utf-8").split() == ["10"]
