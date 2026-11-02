from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from _project.scripts.oracle_reviewers import promotion, protocol

from .fake_github import OWNER_TOKEN
from .harness import SLUG, Harness, git

pytestmark = [pytest.mark.integration, pytest.mark.medium]

OLD_COMMIT = "1" * 40
NEW_COMMIT = "2" * 40
WORKFLOW_SHA = "3" * 40
OLD_ART = "4" * 64
NEW_ART = "5" * 64


def _register_tarball(harness: Harness, repo_slug: str, commit: str, files: dict[str, bytes]) -> str:
    harness.github.add_tarball(repo_slug, commit, files)
    return promotion.tree_sha256(files)


def _lock_json(
    *,
    version: str,
    source_commit: str,
    source_sha256: str,
    artifact_sha256: str,
    delta_sha256: str,
    bootstrap: dict[str, Any] | None = None,
) -> str:
    payload: dict[str, Any] = {
        "schema": 1,
        "package": "theseus-reviewer",
        "version": version,
        "repository": "BenchBox-dev/theseus",
        "source_commit": source_commit,
        "source_sha256": source_sha256,
        "delta_sha256": delta_sha256,
        "artifact": {
            "sha256": artifact_sha256,
            "urls": [
                f"https://github.com/BenchBox-dev/theseus/releases/download/v{version}/theseus_reviewer-{version}-py3-none-any.whl",
                f"https://mirror.example/theseus/theseus_reviewer-{version}-py3-none-any.whl",
            ],
        },
        "dependencies": [
            {
                "name": "pyyaml",
                "version": "6.0.3",
                "sha256": "6" * 64,
                "urls": ["https://files.pythonhosted.org/packages/pyyaml-6.0.3.whl"],
            }
        ],
        "workflow_sha": WORKFLOW_SHA,
    }
    if bootstrap is not None:
        payload["bootstrap"] = bootstrap
    return json.dumps(payload, indent=2) + "\n"


def _large_upstream_trees(harness: Harness) -> tuple[dict[str, bytes], dict[str, bytes], str, str, str]:
    old_files = {
        "src/theseus_reviewer/engine.py": "".join(f"LINE_{idx} = {idx}\n" for idx in range(60)).encode("utf-8"),
        "src/theseus_reviewer/gate.py": "".join(f"GATE_{idx} = True\n" for idx in range(40)).encode("utf-8"),
        "README.md": b"old readme\n",
    }
    new_files = {
        "src/theseus_reviewer/engine.py": "".join(f"LINE_{idx} = {idx + 100}\n" for idx in range(90)).encode("utf-8"),
        "src/theseus_reviewer/gate.py": "".join(f"GATE_{idx} = False\n" for idx in range(70)).encode("utf-8"),
        "src/theseus_reviewer/ verifier.py".replace(" ", ""): "".join(
            f"VERIFY_{idx} = {idx}\n" for idx in range(50)
        ).encode("utf-8"),
        "README.md": b"new readme\n",
    }
    old_hash = _register_tarball(harness, "BenchBox-dev/theseus", OLD_COMMIT, old_files)
    new_hash = _register_tarball(harness, "BenchBox-dev/theseus", NEW_COMMIT, new_files)
    delta_hash = promotion.compute_delta_sha256(old_files, new_files)
    return old_files, new_files, old_hash, new_hash, delta_hash


def test_one_line_lock_bump_forces_gate_file_list_mode_requires_every_upstream_read_and_blocks_standin(
    harness: Harness,
) -> None:
    old_files, _, old_hash, new_hash, delta_hash = _large_upstream_trees(harness)

    initial_delta = promotion.compute_delta_sha256({}, old_files)
    harness.write(
        {
            promotion.LOCK_PATH: _lock_json(
                version="0.1.0",
                source_commit=OLD_COMMIT,
                source_sha256=old_hash,
                artifact_sha256=OLD_ART,
                delta_sha256=initial_delta,
            )
        },
        "seed theseus lock",
    )
    updated_lock = _lock_json(
        version="0.2.0",
        source_commit=NEW_COMMIT,
        source_sha256=new_hash,
        artifact_sha256=NEW_ART,
        delta_sha256=delta_hash,
    )
    head = harness.branch("feat/promote-theseus", {promotion.LOCK_PATH: updated_lock}, "bump theseus lock")
    harness.github.open_pull(1, "feat/promote-theseus")

    required_reads = [
        promotion.LOCK_PATH,
        ".oracle-promotion/new/src/theseus_reviewer/engine.py",
        ".oracle-promotion/new/src/theseus_reviewer/gate.py",
        ".oracle-promotion/new/src/theseus_reviewer/verifier.py",
    ]
    omitted = required_reads[:-1]
    harness.script_omp(
        [
            {
                "decision": "SHIP",
                "summary": "Omitted verifier.py read.",
                "files_examined": omitted,
                "reads": omitted,
                "defects": [],
            },
            {
                "decision": "SHIP",
                "summary": "Still omitted verifier.py read.",
                "files_examined": omitted,
                "reads": omitted,
                "defects": [],
            },
        ]
    )
    incomplete_run = harness.review(
        1,
        classification=("trivial", "standard"),
    )
    assert incomplete_run.plan["brief_mode"] == "file-list"
    assert incomplete_run.plan["diff_inline"] is None
    assert set(required_reads) <= set(incomplete_run.plan["evidence_files"])
    classification = json.loads(
        (incomplete_run.temp / "oracle-classification" / "classification.json").read_text(encoding="utf-8")
    )
    assert classification["template"] == "gate"
    assert classification["size_bucket"] == "large"

    attempts = incomplete_run.attempts()
    assert attempts[0]["outcome"] == "absent"
    assert attempts[0]["absence"]["kind"] == "incomplete"
    assert ".oracle-promotion/new/src/theseus_reviewer/verifier.py" in attempts[0]["absence"]["unread"]
    verdict_statuses = [item for item in harness.github.statuses[head] if item["context"] == "oracle-verdict"]
    assert verdict_statuses[-1]["state"] == "pending"
    assert harness.github.statuses[head][-1]["context"] == "oracle-gate"
    assert harness.github.statuses[head][-1]["state"] == "failure"

    harness.gh(
        "standin",
        [
            "api",
            "--method",
            "POST",
            f"repos/{SLUG}/issues/1/comments",
            "-f",
            f"body=Stand-in oracle review: APPROVE {head}",
        ],
        harness.base_env(),
        OWNER_TOKEN,
    )
    gate_with_standin = harness.gate(1, head)
    assert gate_with_standin.code == 3
    assert harness.github.statuses[head][-1]["state"] == "failure"
    assert "theseus promotion refused" in harness.github.statuses[head][-1]["description"]

    git(harness.repo, "switch", "--quiet", "feat/promote-theseus")
    git(harness.repo, "commit", "--quiet", "--amend", "-m", "bump theseus lock (retry)")
    shipped_head = git(harness.repo, "rev-parse", "HEAD")
    git(harness.repo, "switch", "--quiet", "develop")

    harness.script_omp(
        [
            {
                "decision": "SHIP",
                "summary": "All staged upstream files examined.",
                "files_examined": required_reads,
                "reads": required_reads,
                "defects": [],
            }
        ]
    )
    shipped = harness.review(1)
    assert shipped.steps["finalize"].outputs["state"] == "success"
    reviews = harness.github.reviews[1]
    decoded = protocol.decode_marker(reviews[-1]["body"])
    assert decoded is not None
    assert decoded["promotion"] == {
        "v": 1,
        "head_sha": shipped_head,
        "old_source_sha256": old_hash,
        "new_source_sha256": new_hash,
        "old_artifact_sha256": OLD_ART,
        "new_artifact_sha256": NEW_ART,
        "delta_sha256": delta_hash,
        "bootstrap": False,
    }
    gate_after_ship = harness.gate(1, shipped_head)
    assert gate_after_ship.code == 0
    assert harness.github.statuses[shipped_head][-1]["context"] == "oracle-gate"
    assert harness.github.statuses[shipped_head][-1]["state"] == "success"

    harness.write({"docs/note.md": "advance develop\n"}, "advance develop")
    git(harness.repo, "switch", "--quiet", "feat/promote-theseus")
    git(harness.repo, "rebase", "--quiet", "develop")
    rebased_head = git(harness.repo, "rev-parse", "HEAD")
    git(harness.repo, "switch", "--quiet", "develop")
    assert rebased_head != shipped_head
    gate_before_rereview = harness.gate(1, rebased_head)
    assert gate_before_rereview.code == 3

    harness.script_omp(
        [
            {
                "decision": "SHIP",
                "summary": "Fresh review on rebased promotion head.",
                "files_examined": required_reads,
                "reads": required_reads,
                "defects": [],
            }
        ]
    )
    rebased_run = harness.review(1)
    assert rebased_run.plan["decision"] == "review"
    assert rebased_run.plan["protocol"]["kind"] == protocol.FIRST
    assert harness.omp_calls() == 1
    assert harness.gate(1, rebased_head).code == 0


def test_first_promotion_enforces_policy_pinned_bootstrap_baseline_and_removed_upstream_reads(
    harness: Harness,
) -> None:
    import yaml

    baseline_sha = harness.write(
        {
            "_project/scripts/oracle_reviewers/sample.py": "VALUE = 1\n",
            "_project/scripts/oracle_reviewers/obsolete_guard.py": "GUARD = True\n",
        },
        "record benchbox baseline",
    )
    wrong_baseline_sha = harness.write({"docs/other.md": "later\n"}, "advance develop")
    path_map = {"_project/scripts/oracle_reviewers": "src/theseus_reviewer"}
    policy_data = yaml.safe_load((harness.repo / ".github/oracle-reviewers.yml").read_text(encoding="utf-8"))
    policy_data["promotion"] = {
        "package": "theseus-reviewer",
        "repository": "BenchBox-dev/theseus",
        "bootstrap_baseline_commit": baseline_sha,
        "bootstrap_path_map": path_map,
        "bootstrap_reviewers": ["joeharris76"],
    }
    dumped_policy = yaml.safe_dump(policy_data, sort_keys=False)
    harness.policy_file.write_text(dumped_policy, encoding="utf-8")
    harness.write({".github/oracle-reviewers.yml": dumped_policy}, "pin bootstrap")

    baseline_tree = promotion._git_archive_tree(harness.repo, baseline_sha)
    mapped_baseline = promotion.apply_path_map(baseline_tree, path_map)
    baseline_source_hash = promotion.tree_sha256(mapped_baseline)

    new_files = {
        "src/theseus_reviewer/sample.py": b"VALUE = 2\n",
        "src/theseus_reviewer/added.py": b"EXTRA = True\n",
    }
    new_hash = _register_tarball(harness, "BenchBox-dev/theseus", NEW_COMMIT, new_files)
    delta_hash = promotion.compute_delta_sha256(mapped_baseline, new_files)

    forged_lock = _lock_json(
        version="0.1.0",
        source_commit=NEW_COMMIT,
        source_sha256=new_hash,
        artifact_sha256=NEW_ART,
        delta_sha256=delta_hash,
        bootstrap={
            "baseline_repo": SLUG,
            "baseline_commit": wrong_baseline_sha,
            "baseline_source_sha256": baseline_source_hash,
            "path_map": path_map,
            "reviewer": "joeharris76",
            "approved_head": NEW_COMMIT,
        },
    )
    forged_head = harness.branch("feat/forged-bootstrap", {promotion.LOCK_PATH: forged_lock}, "forged baseline")
    harness.github.open_pull(2, "feat/forged-bootstrap")
    forged_run = harness.review(2)
    assert forged_run.plan["decision"] == "hold"
    assert "configured baseline commit" in forged_run.plan["decision_reason"]
    assert harness.gate(2, forged_head).code == 3

    first_lock = _lock_json(
        version="0.1.0",
        source_commit=NEW_COMMIT,
        source_sha256=new_hash,
        artifact_sha256=NEW_ART,
        delta_sha256=delta_hash,
        bootstrap={
            "baseline_repo": SLUG,
            "baseline_commit": baseline_sha,
            "baseline_source_sha256": baseline_source_hash,
            "path_map": path_map,
            "reviewer": "joeharris76",
            "approved_head": NEW_COMMIT,
        },
    )
    head = harness.branch("feat/first-theseus-cutover", {promotion.LOCK_PATH: first_lock}, "add first lock")
    harness.github.open_pull(3, "feat/first-theseus-cutover")

    reads_without_removed = [
        promotion.LOCK_PATH,
        ".oracle-promotion/new/src/theseus_reviewer/sample.py",
        ".oracle-promotion/new/src/theseus_reviewer/added.py",
    ]
    full_reads = [
        *reads_without_removed,
        ".oracle-promotion/old/src/theseus_reviewer/obsolete_guard.py",
    ]
    harness.script_omp(
        [
            {
                "decision": "SHIP",
                "summary": "Omitted removed obsolete_guard.py.",
                "files_examined": reads_without_removed,
                "reads": reads_without_removed,
                "defects": [],
            },
            {
                "decision": "SHIP",
                "summary": "Read all added, modified, and removed upstream files.",
                "files_examined": full_reads,
                "reads": full_reads,
                "defects": [],
            },
        ]
    )
    first_run = harness.review(3)
    attempts = first_run.attempts()
    assert attempts[0]["outcome"] == "absent"
    assert ".oracle-promotion/old/src/theseus_reviewer/obsolete_guard.py" in attempts[0]["absence"]["unread"]
    assert attempts[1]["outcome"] == "verdict"
    assert first_run.steps["finalize"].outputs["state"] == "success"
    assert harness.gate(3, head).code == 0
