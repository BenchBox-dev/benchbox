from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from scripts import release_artifact_consumer as consumer

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).resolve().parents[3]

NOT_CARRIED = {
    "tests/integration/worktree/batch_loop_support.py": "builds a scratch repository from fixtures",
    "tests/unit/scripts/test_changelog_fragments.py": "tests the changelog fragment script",
    "tests/unit/scripts/test_changelog_gate_cli.py": "tests the changelog gate script",
    "tests/unit/scripts/test_comment_policy.py": "tests the comment-policy scripts",
    "tests/unit/scripts/test_heavy_tier_needed.py": "tests the heavy-tier selector",
    "tests/unit/scripts/test_inert_paths.py": "checks every tracked path of the tree against the inert manifest",
    "tests/unit/scripts/batch_loop_world.py": "a batch-loop fixture; passes unchanged against the carried files on a v0.5.1 tree",
    "tests/unit/scripts/test_agent_instruction_audit.py": "audits agent instructions; passes unchanged on a v0.5.1 tree",
    "tests/unit/scripts/test_batch_integration.py": "tests batch integration; passes unchanged on a v0.5.1 tree",
    "tests/unit/scripts/test_dev_loop_pr_metrics.py": "tests the metrics script; passes unchanged on a v0.5.1 tree",
    "tests/unit/scripts/test_make_timing.py": "tests the timing recorder; passes unchanged against the carried wrapper",
    "tests/unit/scripts/test_release_line_carried_tests.py": "tests the carry declarations themselves",
    "tests/unit/scripts/test_release_line_landing_tooling.py": "tests the creation commit",
    "tests/unit/scripts/test_sqlglot_delivery.py": "tests the sqlglot delivery script, which the line keeps",
    "tests/unit/scripts/test_trunk_revert.py": "tests the trunk revert script; passes unchanged on a v0.5.1 tree",
    "tests/unit/test_agent_write_preflight.py": "reads the line's own agent documents",
    "tests/integration/worktree/test_worktree_lifecycle_metadata.py": "tests the worktree lifecycle script through the line's Makefile; passes unchanged on a v0.5.1 tree",
    "tests/unit/scripts/test_worktree_session_ownership.py": "tests worktree ownership; passes unchanged against the carried script on a v0.5.1 tree",
    "tests/unit/test_primary_clone_write_guard.py": "tests the write guard, which the line keeps; passes unchanged against the carried script on a v0.5.1 tree",
    "tests/unit/test_standardized_test_commands.py": "tests the standard test commands; passes unchanged on a v0.5.1 tree",
    "tests/unit/workflows/test_ci_text_edit_refresh_workflow.py": "tests a workflow the line keeps",
    "tests/unit/workflows/test_ci_windows_pr_leg.py": "tests a workflow the line keeps",
    "tests/unit/workflows/test_pr_hold_workflow.py": "tests a workflow the line keeps",
    "tests/unit/workflows/test_release_v2_workflow_reuse_step.py": "tests a workflow the line keeps",
    "tests/unit/scripts/test_preflight_targets.py": "tests the preflight selector",
    "tests/unit/scripts/test_release_flow.py": "tests the release flow script",
    "tests/unit/scripts/test_release_maintenance_admission.py": "tests the creation and admission scripts",
    "tests/unit/scripts/test_soundness_merge_digest.py": "tests the merge digest script",
    "tests/unit/scripts/test_soundness_merge_digest_dispatch.py": "tests the merge digest dispatch",
    "tests/unit/test_cloud_builder_scripts.py": "tests the cloud builder scripts",
    "tests/unit/workflows/test_batch_loop_e2e_workflow.py": "tests the batch-loop workflow",
    "tests/unit/workflows/test_ci_changelog_gate.py": "tests the changelog gate workflow",
    "tests/unit/workflows/test_omp_pin_bump_workflow.py": "tests the pin-bump workflow",
    "tests/unit/workflows/test_omp_pin_watch_workflow.py": "tests the pin-watch workflow",
}


def _carried_names() -> set[str]:
    names = {Path(path).name for path in (*consumer.CREATION_REVIEW_PATHS, *consumer.CREATION_LANDING_PATHS)}
    workflows = (path for path in (ROOT / ".github" / "workflows").iterdir() if path.is_file())
    names.update(
        path.name for path in workflows if consumer.CREATION_REVIEW_WORKFLOW.fullmatch(f".github/workflows/{path.name}")
    )
    return names


def _referencing_tests() -> set[str]:
    names = _carried_names()
    pattern = re.compile("|".join(rf"(?<![\w.-]){re.escape(name)}(?![\w-])" for name in sorted(names)))
    found = set()
    for path in (ROOT / "tests").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if pattern.search(path.read_text(encoding="utf-8", errors="replace")):
            found.add(path.relative_to(ROOT).as_posix())
    return found


def test_every_test_that_names_a_carried_file_is_carried_or_declared_unrelated() -> None:
    carried_tests = set(consumer.CREATION_REVIEW_TESTS) | set(consumer.CREATION_LANDING_TESTS)
    unclassified = _referencing_tests() - carried_tests - set(NOT_CARRIED)
    assert not unclassified, (
        f"{sorted(unclassified)} name a file a release line carries from develop; add each to "
        "CREATION_REVIEW_TESTS in scripts/release_artifact_consumer.py when it pins that file's content, "
        "or to NOT_CARRIED here when its subject is something else"
    )


def test_the_declarations_do_not_overlap_or_go_stale() -> None:
    referencing = _referencing_tests()
    carried = set(consumer.CREATION_REVIEW_TESTS)
    assert len(carried) == len(consumer.CREATION_REVIEW_TESTS)
    assert not carried & set(NOT_CARRIED)
    carried_tests = {path for path in carried if path.endswith(".py")}
    assert carried_tests <= referencing, sorted(carried_tests - referencing)
    assert set(NOT_CARRIED) <= referencing, sorted(set(NOT_CARRIED) - referencing)


def test_every_carried_fixture_is_read_by_a_carried_test() -> None:
    carried = set(consumer.CREATION_REVIEW_TESTS)
    texts = [(ROOT / path).read_text(encoding="utf-8") for path in carried if path.endswith(".py")]
    unread = [path for path in carried if not path.endswith(".py") and not any(Path(path).name in t for t in texts)]
    assert not unread, unread


def test_every_carried_test_exists_on_develop() -> None:
    missing = [path for path in consumer.CREATION_REVIEW_TESTS if not (ROOT / path).is_file()]
    assert not missing, missing


_READ_FROM_ROOT = re.compile(r'\b(ROOT|REPO_ROOT|SCRIPTS)\s*((?:/\s*"[^"]+"\s*)+)')
FIRST_PARTY_ROOTS = {"scripts", "_project", "benchbox", "tests"}


def _first_party_python(candidate: Path) -> str | None:
    if candidate.suffix == ".py" and candidate.parts[0] in FIRST_PARTY_ROOTS and (ROOT / candidate).is_file():
        return candidate.as_posix()
    return None


def _imported_modules(path: str) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse((ROOT / path).read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            modules = [node.module or "", *(f"{node.module or ''}.{alias.name}" for alias in node.names)]
        elif isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        else:
            continue
        for module in modules:
            for candidate in (
                Path(module.replace(".", "/") + ".py"),
                (Path(path).parent / module.split(".")[0]).with_suffix(".py"),
            ):
                if resolved := _first_party_python(candidate):
                    found.add(resolved)
    return found


def test_every_file_a_carried_test_reads_from_the_tree_is_carried_too() -> None:
    carried = {
        *consumer.CREATION_REVIEW_PATHS,
        *consumer.CREATION_REVIEW_TESTS,
        *consumer.CREATION_LANDING_PATHS,
        *consumer.CREATION_LANDING_TESTS,
    }
    uncarried = []
    for test in (*consumer.CREATION_REVIEW_TESTS, *consumer.CREATION_LANDING_TESTS):
        if not test.endswith(".py"):
            continue
        text = (ROOT / test).read_text(encoding="utf-8")
        reads = {
            "/".join(re.findall(r'"([^"]+)"', chain))
            if base != "SCRIPTS"
            else "_project/scripts/" + "/".join(re.findall(r'"([^"]+)"', chain))
            for base, chain in _READ_FROM_ROOT.findall(text)
        }
        for path in sorted(reads | _imported_modules(test)):
            is_review_workflow = consumer.CREATION_REVIEW_WORKFLOW.fullmatch(path) is not None
            if (ROOT / path).exists() and path not in carried and path not in READ_AT_TAG and not is_review_workflow:
                uncarried.append((test, path))
    assert not uncarried, (
        f"{uncarried}: carried tests read or import these from the tree, so a line that takes the carried files at "
        "develop's state would pair them with the tag's older copy; add each to CREATION_LANDING_PATHS or "
        "CREATION_LANDING_TESTS in scripts/release_artifact_consumer.py, or to READ_AT_TAG here with the reason"
    )


LANDING_TARGETS = ("pr-open", "pr-preflight", "pr-arm")
LANDING_MAKEFILE = "make/pr-landing.mk"
LEFT_AT_TAG = {
    "scripts/changelog_fragments.py": "pr-open and the line's CI both run the line's copy, so they agree",
    "scripts/check_docs_ownership.py": "a guard of the line's own documents",
    "_project/scripts/check_project_references.py": "a guard of the line's own references",
    "benchbox/utils/host_lock.py": "product code; pr-preflight runs it with run --admit --guard, which the tag already has",
    "benchbox/utils/clock.py": "product code; a creation commit never changes the shipped package",
    "scripts/linux_preflight_pytest.sh": "runs only when a change branches on platform and takes a list file, unchanged since the tag",
}
READ_AT_TAG = {
    "Makefile": "a line keeps its own Makefile; carried tests passed against the v0.5.1 one",
    ".github/PULL_REQUEST_TEMPLATE.md": "read as a tag document",
    "CHANGELOG.md": "read as a tag document",
    "LICENSE": "read as a tag document",
    "README.md": "read as a tag document",
    "_project/release-evidence/README.md": "read as a tag document",
    "benchbox/__init__.py": "product code the line keeps",
    "benchbox/cli/exceptions.py": "product code the line keeps",
    "pyproject.toml": "the line's own project file",
    "make/worktrees.mk": "the line's own worktree targets",
    "scripts/worktree_lifecycle.sh": "the line's own worktree script",
    "make": "the line's own make directory, copied whole; make/pr-landing.mk in it is the carried copy",
    "scripts": "the line's own scripts directory; the carried files in it are the carried copies",
    "_project/scripts": "the line's own project scripts directory; the carried files in it are the carried copies",
    ".github/workflows": "the line's own workflows directory; the reviewer workflows in it are the carried copies",
    ".github/ISSUE_TEMPLATE": "read as a tag document",
    "benchbox/platforms": "product code the line keeps",
    "benchbox/utils/host_lock.py": "product code the line keeps; the carried tests pass against the tag's copy",
    "benchbox/core/platform_manifest.py": "product code the line keeps; the carried tests pass against the tag's copy",
    "scripts/check_make_targets.py": "reads the line's own Makefile targets; the carried tests pass against the tag's copy",
    "tests/utilities/batch_evidence.py": "a test helper the line keeps",
    "tests/utilities/paths.py": "a test helper the line keeps",
    "tests/utilities/posix_shell.py": "a test helper the line keeps",
    "tests/unit/workflows/workflow_expressions.py": "a test helper the line keeps",
}
TAG_TARGETS = {
    "changelog-check": "the line's own changelog gate",
    "docs-reference-inventory-check": "the line's own document guard",
}
_SCRIPT_REFERENCE = re.compile(r"(?<![\w.-])((?:_project/)?scripts/[\w/.-]+\.(?:py|sh))")
_SIBLING_REFERENCE = re.compile(r'\$\(dirname "\$0"\)/([\w/.-]+\.(?:py|sh))')
_PYTHON_MODULE = re.compile(r"\bpython3?\s+-m\s+([\w.]+)")
_SUB_MAKE = re.compile(r"\$\(MAKE\)\s+(?:-\S+\s+)*([\w-]+)")


def _recipes(path: str) -> dict[str, str]:
    recipes: dict[str, list[str]] = {}
    current = None
    for line in (ROOT / path).read_text(encoding="utf-8").splitlines():
        if line.startswith("\t") and current is not None:
            recipes[current].append(line)
            continue
        declared = re.fullmatch(r"([\w-]+):", line)
        current = declared.group(1) if declared else None
        if current is not None:
            recipes[current] = []
    return {target: "\n".join(lines) for target, lines in recipes.items()}


def _text_references(text: str, directory: Path) -> set[str]:
    found = {path for path in _SCRIPT_REFERENCE.findall(text) if (ROOT / path).is_file()}
    found |= {
        (directory / name).as_posix()
        for name in _SIBLING_REFERENCE.findall(text)
        if (ROOT / directory / name).is_file()
    }
    for module in _PYTHON_MODULE.findall(text):
        if resolved := _first_party_python(Path(module.replace(".", "/") + ".py")):
            found.add(resolved)
    return found


def _path_chains(path: str) -> set[str]:
    found = set()
    directory = Path(path).parent
    for node in ast.walk(ast.parse((ROOT / path).read_text(encoding="utf-8"))):
        if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)):
            continue
        parts: list[str] = []
        operand: ast.expr = node
        while isinstance(operand, ast.BinOp) and isinstance(operand.op, ast.Div):
            if isinstance(operand.right, ast.Constant) and isinstance(operand.right.value, str):
                parts.append(operand.right.value)
            operand = operand.left
        candidate = "/".join(reversed(parts))
        if candidate.endswith((".py", ".sh")):
            for resolved in (Path(candidate), directory / candidate):
                if (ROOT / resolved).is_file() and resolved.parts[0] in FIRST_PARTY_ROOTS:
                    found.add(resolved.as_posix())
    return found


def _local_dependencies(path: str) -> set[str]:
    if path.endswith(".sh"):
        return _text_references((ROOT / path).read_text(encoding="utf-8"), Path(path).parent)
    return _imported_modules(path) | _path_chains(path)


def _landing_closure() -> set[str]:
    recipes = _recipes(LANDING_MAKEFILE)
    seen: set[str] = set()
    targets: set[str] = set()
    pending = {LANDING_MAKEFILE, *(path for path in consumer.CREATION_LANDING_PATHS if path.endswith((".py", ".sh")))}
    pending_targets = set(LANDING_TARGETS)
    while pending or pending_targets:
        while pending_targets:
            target = pending_targets.pop()
            if target in targets or target not in recipes:
                continue
            targets.add(target)
            pending |= _text_references(recipes[target], Path("."))
            pending_targets |= set(_SUB_MAKE.findall(recipes[target]))
        if pending:
            path = pending.pop()
            if path not in seen:
                seen.add(path)
                if path not in LEFT_AT_TAG and path != LANDING_MAKEFILE:
                    pending |= _local_dependencies(path)
    return seen


def _landing_sub_targets() -> set[str]:
    recipes = _recipes(LANDING_MAKEFILE)
    reached: set[str] = set()
    pending = set(LANDING_TARGETS)
    while pending:
        target = pending.pop()
        if target in reached or target not in recipes:
            continue
        reached.add(target)
        pending |= set(_SUB_MAKE.findall(recipes[target]))
    return reached


def test_every_sub_make_target_the_landing_recipes_run_is_carried_or_left_at_the_tag() -> None:
    recipes = _recipes(LANDING_MAKEFILE)
    reached = _landing_sub_targets()
    called = {name for target in reached for name in _SUB_MAKE.findall(recipes[target])}
    outside = {name for name in called if name not in recipes}
    assert outside == set(TAG_TARGETS), sorted(outside ^ set(TAG_TARGETS))
    defined = {
        name
        for makefile in (ROOT / "Makefile", *(ROOT / "make").glob("*.mk"))
        if makefile.name != "pr-landing.mk"
        for name in re.findall(r"^([\w-]+):", makefile.read_text(encoding="utf-8"), re.M)
    }
    assert set(TAG_TARGETS) <= defined


def test_the_landing_tooling_covers_everything_pr_open_preflight_and_arm_run() -> None:
    carried = {*consumer.CREATION_REVIEW_PATHS, *consumer.CREATION_LANDING_PATHS}
    closure = _landing_closure()
    missing = closure - carried - set(LEFT_AT_TAG)
    assert not missing, (
        f"{sorted(missing)} are run, sourced, loaded or imported by pr-open, pr-preflight or pr-arm, so a line that "
        "keeps the tag's copy can pair them with develop's make/pr-landing.mk; add each to CREATION_LANDING_PATHS in "
        "scripts/release_artifact_consumer.py, or to LEFT_AT_TAG here with the reason"
    )
    assert set(LEFT_AT_TAG) <= closure
    assert not set(LEFT_AT_TAG) & carried


def test_the_landing_declarations_exist_do_not_overlap_and_each_test_names_a_landing_file() -> None:
    paths, tests = consumer.CREATION_LANDING_PATHS, consumer.CREATION_LANDING_TESTS
    review = {*consumer.CREATION_REVIEW_PATHS, *consumer.CREATION_REVIEW_TESTS}
    assert len({*paths, *tests}) == len(paths) + len(tests)
    assert not ({*paths, *tests} & review)
    assert not set(tests) & set(NOT_CARRIED)
    assert [path for path in (*paths, *tests) if not (ROOT / path).is_file()] == []
    names = {Path(path).name for path in paths}
    stems = {Path(path).stem for path in paths if path.endswith(".py")}
    for test in tests:
        text = (ROOT / test).read_text(encoding="utf-8")
        assert any(name in text for name in names) or any(re.search(rf"\b{stem}\b", text) for stem in stems), test
