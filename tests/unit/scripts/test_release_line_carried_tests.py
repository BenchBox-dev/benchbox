from __future__ import annotations

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
    "tests/unit/scripts/test_local_review.py": "tests the local review driver",
    "tests/unit/scripts/test_pr_arm.py": "tests the arming script",
    "tests/unit/scripts/test_pr_landing.py": "tests the landing script",
    "tests/unit/scripts/test_preflight_targets.py": "tests the preflight selector",
    "tests/unit/scripts/test_release_flow.py": "tests the release flow script",
    "tests/unit/scripts/test_release_maintenance_admission.py": "tests the creation and admission scripts",
    "tests/unit/scripts/test_soundness_merge_digest.py": "tests the merge digest script",
    "tests/unit/scripts/test_soundness_merge_digest_dispatch.py": "tests the merge digest dispatch",
    "tests/unit/scripts/test_theseus_prefetch.py": "tests the preflight prefetch on a scratch lock",
    "tests/unit/test_cloud_builder_scripts.py": "tests the cloud builder scripts",
    "tests/unit/workflows/test_batch_loop_e2e_workflow.py": "tests the batch-loop workflow",
    "tests/unit/workflows/test_ci_changelog_gate.py": "tests the changelog gate workflow",
    "tests/unit/workflows/test_omp_pin_bump_workflow.py": "tests the pin-bump workflow",
    "tests/unit/workflows/test_omp_pin_watch_workflow.py": "tests the pin-watch workflow",
}


def _carried_names() -> set[str]:
    names = {Path(path).name for path in consumer.CREATION_REVIEW_PATHS}
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
    unclassified = _referencing_tests() - set(consumer.CREATION_REVIEW_TESTS) - set(NOT_CARRIED)
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


def test_every_file_a_carried_test_reads_from_the_tree_is_carried_too() -> None:
    carried = set(consumer.CREATION_REVIEW_PATHS) | set(consumer.CREATION_REVIEW_TESTS)
    uncarried = []
    for test in consumer.CREATION_REVIEW_TESTS:
        if not test.endswith(".py"):
            continue
        text = (ROOT / test).read_text(encoding="utf-8")
        for base, chain in _READ_FROM_ROOT.findall(text):
            path = "/".join(re.findall(r'"([^"]+)"', chain))
            if base == "SCRIPTS":
                path = f"_project/scripts/{path}"
            is_review_workflow = consumer.CREATION_REVIEW_WORKFLOW.fullmatch(path) is not None
            if (ROOT / path).is_file() and path not in carried and not is_review_workflow:
                uncarried.append((test, path))
    assert not uncarried, (
        f"{uncarried}: carried tests read these files, so a line that takes the carried files at "
        "develop's state would pair them with the tag's older copy; add each to CREATION_REVIEW_PATHS "
        "or CREATION_REVIEW_TESTS in scripts/release_artifact_consumer.py"
    )
