from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("comment_cleanup_scope", ROOT / "scripts/check_comment_cleanup_scope.py")
assert SPEC and SPEC.loader
scope = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = scope
SPEC.loader.exec_module(scope)


@pytest.fixture
def policy() -> dict:
    return {
        "version": 1,
        "maintained_roots": [{"id": "source", "selector": {"prefix": "src/"}, "kind": "source"}],
        "ownership_rules": [
            {
                "id": "source-owner",
                "owner": "comment-cleanup-core-bootstrap",
                "state": "blocked",
                "blocking_disposition": "Await source-reader inventory.",
                "priority": 0,
                "selectors": [{"prefix": "src/"}],
            }
        ],
        "external_entries": [],
        "format_classes": [],
        "payloads": [],
        "derived_rules": [],
        "consumer_edges": [],
        "directives": [],
        "notices": [],
        "obligations": [],
        "review_dispositions": [
            {
                "id": f"R{number}",
                "disposition": "accepted",
                "original_verdict": "accepted",
                "requirements": ["requirement"],
                "rationale": "Fixture rationale.",
                "acceptance_refs": ["Fixture acceptance reference."],
                "owner": "comment-cleanup-scope-policy",
            }
            for number in range(1, 19)
        ],
    }


def test_owned_paths_reports_unclassified_as_blocked(policy: dict) -> None:
    roots = scope.validate_roots(policy)
    resolved, findings = scope.owned_paths(["src/a.py"], roots, [])
    assert [finding.code for finding in findings] == ["SCOPE001"]
    assert resolved == [
        {
            "path": "src/a.py",
            "kind": "source",
            "owner": None,
            "state": "blocked",
            "blocking_disposition": "Ownership is not frozen for this maintained path.",
            "rule": None,
        }
    ]


def test_high_priority_shared_override_selects_one_owner(policy: dict) -> None:
    override = {
        "id": "shared-test",
        "owner": "comment-cleanup-cross-module-unit-tests",
        "state": "blocked",
        "blocking_disposition": "Serialize shared test changes.",
        "priority": 1,
        "selectors": [{"path": "src/shared.py"}],
    }
    rules = scope.validate_rules({**policy, "ownership_rules": [*policy["ownership_rules"], override]})
    resolved, findings = scope.owned_paths(["src/shared.py"], scope.validate_roots(policy), rules)
    assert not findings
    assert resolved[0]["owner"] == "comment-cleanup-cross-module-unit-tests"
    assert resolved[0]["rule"] == "shared-test"


def test_equal_priority_rules_are_an_ownership_collision(policy: dict) -> None:
    conflicting = deepcopy(policy["ownership_rules"][0])
    conflicting["id"] = "other-owner"
    conflicting["owner"] = "comment-cleanup-core-catalogs"
    rules = scope.validate_rules({**policy, "ownership_rules": [*policy["ownership_rules"], conflicting]})
    resolved, findings = scope.owned_paths(["src/a.py"], scope.validate_roots(policy), rules)
    assert [finding.code for finding in findings] == ["SCOPE002"]
    assert resolved[0]["state"] == "blocked"
    assert resolved[0]["rule"] == ["other-owner", "source-owner"]


def test_more_specific_root_wins_over_repository_fallback(policy: dict) -> None:
    policy["maintained_roots"].append({"id": "repository", "selector": {"prefix": ""}, "kind": "unclassified"})
    root = scope.selected_root("src/a.py", scope.validate_roots(policy))
    assert root["id"] == "source"


def test_equally_specific_roots_are_rejected(policy: dict) -> None:
    policy["maintained_roots"].append({"id": "other-source", "selector": {"prefix": "src/"}, "kind": "other"})
    with pytest.raises(scope.PolicyError, match="maintained-root collision"):
        scope.selected_root("src/a.py", scope.validate_roots(policy))


def test_policy_requires_all_review_dispositions(policy: dict) -> None:
    policy["review_dispositions"].pop()
    with pytest.raises(scope.PolicyError, match="review dispositions"):
        scope.validate_dispositions(policy)


def test_evidence_requires_immutable_base_paths(policy: dict) -> None:
    policy["notices"] = [
        {
            "path": "missing.py",
            "blob_sha256": "a" * 64,
            "byte_start": 0,
            "byte_end": 1,
            "retained_sha256": "a" * 64,
            "governing_requirement": "required notice",
            "source_identity": "upstream source",
            "owner": "comment-cleanup-scope-policy",
            "blocking_disposition": "Await provenance proof.",
        }
    ]
    with pytest.raises(scope.PolicyError, match="not tracked"):
        scope.validate_evidence(policy, ROOT, "0" * 40, {"src/a.py"})


def test_consumer_edge_rejects_unknown_endpoint(policy: dict) -> None:
    policy["consumer_edges"] = [
        {
            "producer": "src/a.py",
            "consumer": "unknown",
            "owner": "comment-cleanup-core-bootstrap",
            "state": "blocked",
            "blocking_disposition": "Await consumer mapping.",
            "proof": "source proof",
        }
    ]
    with pytest.raises(scope.PolicyError, match="endpoint"):
        scope.validate_payloads_and_edges(policy, {"src/a.py"})


def test_duplicate_consumer_edge_is_rejected(policy: dict) -> None:
    edge = {
        "producer": "src/a.py",
        "consumer": "src/consumer.py",
        "owner": "comment-cleanup-core-bootstrap",
        "state": "blocked",
        "blocking_disposition": "Await consumer migration.",
        "proof": "source proof",
    }
    policy["consumer_edges"] = [edge, deepcopy(edge)]
    with pytest.raises(scope.PolicyError, match="duplicate consumer edge"):
        scope.validate_payloads_and_edges(policy, {"src/a.py", "src/consumer.py"})


def test_cyclic_consumer_edges_are_rejected(policy: dict) -> None:
    policy["consumer_edges"] = [
        {
            "producer": "src/a.py",
            "consumer": "src/b.py",
            "owner": "comment-cleanup-core-bootstrap",
            "state": "blocked",
            "blocking_disposition": "Await consumer migration.",
            "proof": "source proof",
        },
        {
            "producer": "src/b.py",
            "consumer": "src/a.py",
            "owner": "comment-cleanup-core-bootstrap",
            "state": "blocked",
            "blocking_disposition": "Await consumer migration.",
            "proof": "source proof",
        },
    ]
    with pytest.raises(scope.PolicyError, match="cyclic consumer edge"):
        scope.validate_payloads_and_edges(policy, {"src/a.py", "src/b.py"})


def test_blocked_consumer_prevents_ready_producer(policy: dict) -> None:
    policy["ownership_rules"][0]["state"] = "ready"
    policy["consumer_edges"] = [
        {
            "producer": "src/a.py",
            "consumer": "src/consumer.py",
            "owner": "comment-cleanup-core-bootstrap",
            "state": "blocked",
            "blocking_disposition": "Await consumer migration.",
            "proof": "source proof",
        }
    ]
    resolved, _ = scope.owned_paths(
        ["src/a.py", "src/consumer.py"],
        scope.validate_roots(policy),
        scope.validate_rules(policy),
    )
    assert [finding.code for finding in scope.dependency_findings(policy, resolved)] == ["SCOPE003"]


def test_frozen_task_ids_rejects_unknown_owner(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".git/info/exclude").write_text(".todo-batch/\n", encoding="utf-8")
    (tmp_path / ".todo-batch").mkdir()
    (tmp_path / ".todo-batch/tasks.txt").write_text("TODO: comment-cleanup-known\n", encoding="utf-8")
    task_ids = scope.frozen_task_ids(tmp_path, ".todo-batch/tasks.txt")
    with pytest.raises(scope.PolicyError, match="frozen task set"):
        scope.check_owner("comment-cleanup-unknown", "owner", task_ids)


def test_notice_digest_must_match_immutable_base_bytes(tmp_path: Path, policy: dict) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True)
    (tmp_path / "src").mkdir()
    notice = b"required notice\n"
    (tmp_path / "src/a.py").write_bytes(notice)
    policy["notices"] = [
        {
            "path": "src/a.py",
            "blob_sha256": hashlib.sha256(notice).hexdigest(),
            "byte_start": 0,
            "byte_end": len(notice),
            "retained_sha256": hashlib.sha256(notice).hexdigest(),
            "governing_requirement": "fixture requirement",
            "source_identity": "fixture source",
            "owner": "comment-cleanup-core-bootstrap",
            "blocking_disposition": "Retain required notice.",
        }
    ]
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "fixture"], check=True)
    base = subprocess.check_output(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True).strip()
    scope.validate_evidence(policy, tmp_path, base, {"src/a.py"})
    policy["notices"][0]["retained_sha256"] = "0" * 64
    with pytest.raises(scope.PolicyError, match="retained-byte digest"):
        scope.validate_evidence(policy, tmp_path, base, {"src/a.py"})


def test_obligation_requires_exactly_one_destination_reference(tmp_path: Path, policy: dict) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src/a.py").write_text("# TODO: follow up\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "fixture"], check=True)
    base = subprocess.check_output(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True).strip()
    entry = {
        "path": "src/a.py",
        "token": "TODO",
        "destination": "follow-up",
        "tracker_reference": "",
        "approved_destination": "",
        "owner": "comment-cleanup-core-bootstrap",
        "blocking_disposition": "Await approved follow-up.",
    }
    policy["obligations"] = [entry]
    with pytest.raises(scope.PolicyError, match="exactly one"):
        scope.validate_evidence(policy, tmp_path, base, {"src/a.py"})
    entry["tracker_reference"] = "comment-cleanup-follow-up"
    scope.validate_evidence(policy, tmp_path, base, {"src/a.py"})
    entry["approved_destination"] = "approved destination"
    with pytest.raises(scope.PolicyError, match="exactly one"):
        scope.validate_evidence(policy, tmp_path, base, {"src/a.py"})


@pytest.mark.parametrize(
    "legacy_policy",
    [
        "absent",
        "candidate-only",
        "malformed",
        "missing-external",
        "missing-required-schema",
        "bad-external-entry",
        "invalid-version",
        "valid",
        "candidate-only-ownership",
    ],
)
def test_validator_writes_only_to_ignored_output(
    tmp_path: Path, policy: dict, legacy_policy: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src/a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "quality").mkdir()
    (tmp_path / "quality/comment-cleanup-scope.json").write_text(json.dumps(policy), encoding="utf-8")
    (tmp_path / ".git/info/exclude").write_text(".todo-batch/\n", encoding="utf-8")
    (tmp_path / ".todo-batch").mkdir()
    owners = {rule["owner"] for rule in policy["ownership_rules"]}
    owners.update(item["owner"] for item in policy["review_dispositions"])
    (tmp_path / ".todo-batch/tasks.txt").write_text(
        "".join(f"TODO: {owner}\n" for owner in sorted(owners)), encoding="utf-8"
    )
    invalid_policies = {
        "malformed",
        "missing-external",
        "missing-required-schema",
        "bad-external-entry",
        "invalid-version",
    }
    if legacy_policy not in {"absent", "candidate-only"}:
        legacy = {"version": 1, "external": [], "completed": [], "exceptions": []}
        if legacy_policy == "candidate-only-ownership":
            legacy["external"] = [{"path": "src/", "owner": "upstream", "provenance": "fixture source archive"}]
        if legacy_policy == "missing-required-schema":
            legacy = {"external": []}
        elif legacy_policy == "bad-external-entry":
            legacy["external"] = [{"path": "src/", "owner": "upstream"}]
        elif legacy_policy == "invalid-version":
            legacy["version"] = 0
        raw = (
            "{" if legacy_policy == "malformed" else "{}" if legacy_policy == "missing-external" else json.dumps(legacy)
        )
        (tmp_path / "quality/comment-policy.json").write_text(raw)
    if legacy_policy == "candidate-only-ownership":
        subprocess.run(["git", "-C", str(tmp_path), "add", "src/a.py", "quality/comment-policy.json"], check=True)

        def reject_dependency_admission(*args: object, **kwargs: object) -> None:
            pytest.fail("candidate-only ownership must not grant legacy dependency exemptions")

        monkeypatch.setattr(scope, "immutable_dependency_artifacts", reject_dependency_admission)
    else:
        subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "fixture"], check=True)
    base = subprocess.check_output(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True).strip()
    if legacy_policy == "candidate-only-ownership":
        base_paths = set(scope.tracked_paths(tmp_path, base))
        assert "quality/comment-policy.json" in base_paths
        assert "quality/comment-cleanup-scope.json" not in base_paths
        assert scope.immutable_external_ownership(tmp_path, base, legacy["external"]) is None
    if legacy_policy == "candidate-only":
        (tmp_path / "quality/comment-policy.json").write_text("{")
    assert scope.main(
        [
            "--root",
            str(tmp_path),
            "--base",
            base,
            "--task-set",
            ".todo-batch/tasks.txt",
            "--output",
            ".todo-batch/manifest.json",
        ]
    ) == (2 if legacy_policy in invalid_policies else 0)
    assert (tmp_path / ".todo-batch/manifest.json").is_file() == (legacy_policy not in invalid_policies)
    if legacy_policy == "candidate-only-ownership":
        report = json.loads((tmp_path / ".todo-batch/manifest.json").read_text())
        assert all(record["state"] != "excluded" for record in report["paths"])
        source = next(record for record in report["paths"] if record["path"] == "src/a.py")
        assert source["owner"] == "comment-cleanup-core-bootstrap"
        assert source["state"] == "blocked"

    assert (
        scope.main(
            [
                "--root",
                str(tmp_path),
                "--base",
                base,
                "--task-set",
                ".todo-batch/tasks.txt",
                "--output",
                "manifest.json",
            ]
        )
        == 2
    )


def test_verified_source_slices_have_distinct_blocked_owners() -> None:
    repository_policy = scope.load_policy(ROOT / "quality/comment-cleanup-scope.json")
    roots = scope.validate_roots(repository_policy)
    rules = scope.validate_rules(repository_policy)
    paths = [
        "benchbox/core/tpch/queries.py",
        "benchbox/core/tpcds/dataframe_queries/queries.py",
        "benchbox/core/tpcds/generator/runner.py",
        "benchbox/core/tpcdi/generator/data.py",
        "benchbox/core/tpchavoc/dataframe_queries/queries.py",
        "benchbox/core/ssb/queries.py",
        "benchbox/core/clickbench/queries.py",
        "benchbox/core/read_primitives/dataframe_queries.py",
    ]
    resolved, findings = scope.owned_paths(paths, roots, rules)
    assert not findings
    assert all(record["state"] == "blocked" for record in resolved)
    assert {record["owner"] for record in resolved} == {
        "comment-cleanup-tpch",
        "comment-cleanup-tpcds-dataframe",
        "comment-cleanup-tpcds-generation",
        "comment-cleanup-tpcdi-generation",
        "comment-cleanup-tpchavoc-dataframe",
        "comment-cleanup-ssb",
        "comment-cleanup-clickbench",
        "comment-cleanup-read-primitives",
    }


def test_verified_source_slice_paths_remain_blocked_when_unmapped() -> None:
    repository_policy = scope.load_policy(ROOT / "quality/comment-cleanup-scope.json")
    roots = scope.validate_roots(repository_policy)
    rules = scope.validate_rules(repository_policy)
    resolved, findings = scope.owned_paths(["benchbox/core/tpchavoc_variants/new.py"], roots, rules)
    assert [finding.code for finding in findings] == ["SCOPE001"]
    assert resolved[0]["owner"] is None
    assert resolved[0]["state"] == "blocked"


def test_verified_source_slice_rule_ids_are_unique() -> None:
    policy = scope.load_policy(ROOT / "quality/comment-cleanup-scope.json")
    rules = scope.validate_rules(policy)
    ids = [rule["id"] for rule in rules if rule["id"].endswith("-source-slice")]
    assert len(ids) >= 20
    assert len(ids) == len(set(ids))


def test_review_dispositions_preserve_original_verdict_and_rationale() -> None:
    policy = scope.load_policy(ROOT / "quality/comment-cleanup-scope.json")
    dispositions = policy["review_dispositions"]
    assert len(dispositions) == 18
    assert {item["id"] for item in dispositions} == {f"R{number}" for number in range(1, 19)}
    for item in dispositions:
        assert item["original_verdict"] == item["disposition"]
        assert item["rationale"]
        assert item["acceptance_refs"]


def test_every_tracked_eula_and_notice_file_has_a_notice_entry() -> None:
    policy = scope.load_policy(ROOT / "quality/comment-cleanup-scope.json")
    listed = {entry["path"] for entry in policy["notices"]}
    tracked = scope.git(ROOT, "ls-files", "-z", "--", "_binaries", "_sources").decode().split("\0")
    notice_files = {
        path for path in tracked if path and Path(path).name in {"EULA.txt", "NOTICE.txt"} and (ROOT / path).exists()
    }
    assert notice_files
    assert notice_files <= listed


def test_committed_payloads_and_edges_validate_at_immutable_head() -> None:
    policy = scope.load_policy(ROOT / "quality/comment-cleanup-scope.json")
    head = scope.git(ROOT, "rev-parse", "HEAD").decode().strip()
    scope.validate_payloads_and_edges(policy, set(scope.tracked_paths(ROOT, head)))


def test_committed_consumer_edges_name_tracked_paths_and_the_docstring_readers() -> None:
    policy = scope.load_policy(ROOT / "quality/comment-cleanup-scope.json")
    tracked = set(scope.git(ROOT, "ls-files", "-z").decode().split("\0"))
    edges = policy["consumer_edges"]
    assert edges
    for edge in edges:
        assert edge["producer"] in tracked and edge["consumer"] in tracked
    pairs = {(edge["producer"], edge["consumer"]) for edge in edges}
    assert ("benchbox/mcp/tools/visualization.py", "tests/unit/mcp/test_surface_defect_regressions.py") in pairs
    assert ("benchbox/core/tpch/dataframe_queries.py", "benchbox/core/query_catalog.py") in pairs
    assert not [consumer for _, consumer in pairs if consumer.endswith(".rst")]


def test_duration_policy_has_exactly_one_exact_path_rule_owned_by_shared_infrastructure() -> None:
    policy = scope.load_policy(ROOT / "quality/comment-cleanup-scope.json")
    rules = [rule for rule in policy["ownership_rules"] if {"path": "tests/duration_policy.py"} in rule["selectors"]]
    assert [rule["owner"] for rule in rules] == ["comment-cleanup-shared-infrastructure"]


def test_duration_policy_disposition_names_every_importer() -> None:
    policy = scope.load_policy(ROOT / "quality/comment-cleanup-scope.json")
    rule = next(rule for rule in policy["ownership_rules"] if rule["id"] == "root-test-tier-policy")
    importers = {
        line.split(":", 1)[0]
        for line in scope.git(
            ROOT, "grep", "-n", "-E", r"(from|import) +tests(\.| +import +)duration_policy", "--", "*.py"
        )
        .decode()
        .splitlines()
    } - {"tests/unit/scripts/test_comment_cleanup_scope.py"}
    assert importers
    missing = {path for path in importers if path not in rule["blocking_disposition"]}
    assert not missing, f"the disposition omits importers: {sorted(missing)}"


def test_the_unit_test_umbrella_owns_nothing_and_its_three_children_split_the_tree() -> None:
    policy = scope.load_policy(ROOT / "quality/comment-cleanup-scope.json")
    umbrella = "comment-cleanup-cross-module-unit-tests"
    assert not [rule["id"] for rule in policy["ownership_rules"] if rule["owner"] == umbrella]
    assert not [edge["consumer"] for edge in policy["consumer_edges"] if edge["owner"] == umbrella]
    rules = {rule["id"]: rule for rule in policy["ownership_rules"]}
    core, platforms, rest = (
        rules[name] for name in ("unit-tests-core", "unit-tests-platforms", "unit-tests-cli-scripts-rest")
    )
    assert {rule["owner"] for rule in (core, platforms, rest)} == {
        "comment-cleanup-unit-tests-core",
        "comment-cleanup-unit-tests-platforms",
        "comment-cleanup-unit-tests-cli-scripts-rest",
    }
    assert core["priority"] > platforms["priority"] > rest["priority"]
    assert {"prefix": "tests/unit/"} in rest["selectors"] and {"prefix": "tests/unit/platforms/"} in platforms[
        "selectors"
    ]


def test_the_checkers_own_files_have_named_owners() -> None:
    policy = scope.load_policy(ROOT / "quality/comment-cleanup-scope.json")
    rules = {rule["id"]: rule for rule in policy["ownership_rules"]}
    tooling = rules["comment-policy-tooling"]
    assert tooling["owner"] == "comment-cleanup-checker" and tooling["priority"] > 20
    assert {"path": "scripts/check_comment_policy.py"} in tooling["selectors"]
    assert {"path": "scripts/run_comment_policy.py"} in tooling["selectors"]
    registry = rules["comment-policy-registry"]
    assert registry["owner"] == "comment-cleanup-exception-register"
    assert registry["selectors"] == [{"path": "quality/comment-policy.json"}]
    assert not [edge for edge in policy["consumer_edges"] if edge["consumer"].startswith("docs/")]


def _derived_rule(priority: int = 20) -> dict:
    return {
        "id": "test-import-owner",
        "method": "python-imports",
        "state": "blocked",
        "blocking_disposition": "Derived from imports.",
        "priority": priority,
        "selectors": [{"prefix": "tests/"}],
    }


def _resolved(path: str, owner: str | None) -> dict:
    return {"path": path, "kind": "x", "owner": owner, "state": "blocked", "blocking_disposition": "b", "rule": "r"}


def _git_repo_with(tmp_path: Path, files: dict[str, str]) -> tuple[Path, str]:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for name, content in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "--", *files], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "x"],
        check=True,
    )
    base = subprocess.check_output(["git", "-C", str(tmp_path), "rev-parse", "HEAD"]).decode().strip()
    return tmp_path, base


def test_derived_rule_assigns_single_import_owner_and_falls_back_otherwise(tmp_path: Path) -> None:
    root, base = _git_repo_with(
        tmp_path,
        {
            "benchbox/__init__.py": "",
            "benchbox/a.py": "",
            "benchbox/b.py": "",
            "tests/test_a.py": "from benchbox.a import thing\n",
            "tests/test_ab.py": "import benchbox.a\nimport benchbox.b\n",
            "tests/test_facade.py": "from benchbox import thing\n",
            "tests/test_none.py": "import os\n",
            "tests/broken.py": "def (:\n",
        },
    )
    resolved = [
        _resolved("benchbox/a.py", "comment-cleanup-a"),
        _resolved("benchbox/b.py", "comment-cleanup-b"),
        _resolved("benchbox/__init__.py", "comment-cleanup-boot"),
        *(
            _resolved(f"tests/{n}.py", "comment-cleanup-fallback")
            for n in ("test_a", "test_ab", "test_facade", "test_none", "broken")
        ),
    ]
    scope.apply_derived_rules(resolved, [_derived_rule()], root, base, {})
    owners = {record["path"]: record["owner"] for record in resolved}
    assert owners["tests/test_a.py"] == "comment-cleanup-a"
    assert owners["tests/test_ab.py"] == "comment-cleanup-fallback"
    assert owners["tests/test_facade.py"] == "comment-cleanup-fallback"
    assert owners["tests/test_none.py"] == "comment-cleanup-fallback"
    assert owners["tests/broken.py"] == "comment-cleanup-fallback"


def test_derived_rule_does_not_override_a_higher_priority_rule(tmp_path: Path) -> None:
    root, base = _git_repo_with(tmp_path, {"benchbox/a.py": "", "tests/test_a.py": "import benchbox.a\n"})
    resolved = [_resolved("benchbox/a.py", "comment-cleanup-a"), _resolved("tests/test_a.py", "comment-cleanup-exact")]
    scope.apply_derived_rules(resolved, [_derived_rule()], root, base, {"tests/test_a.py": 30})
    assert resolved[1]["owner"] == "comment-cleanup-exact"


def test_derived_rule_rejects_unknown_method(policy: dict) -> None:
    policy["derived_rules"] = [{**_derived_rule(), "method": "guess-from-names"}]
    with pytest.raises(scope.PolicyError):
        scope.validate_derived_rules(policy)


def test_derived_rule_skips_ownership_collisions(tmp_path: Path) -> None:
    root, base = _git_repo_with(tmp_path, {"benchbox/a.py": "", "tests/t.py": "import benchbox.a\n"})
    collision = {**_resolved("tests/t.py", None), "rule": ["rule-a", "rule-b"], "competing_owners": ["a", "b"]}
    resolved = [_resolved("benchbox/a.py", "comment-cleanup-a"), collision]
    scope.apply_derived_rules(resolved, [_derived_rule()], root, base, {})
    assert resolved[1]["owner"] is None
    assert resolved[1]["rule"] == ["rule-a", "rule-b"]


def test_derived_rule_counts_module_literals_and_rejects_unowned_modules(tmp_path: Path) -> None:
    root, base = _git_repo_with(
        tmp_path,
        {
            "benchbox/a.py": "",
            "benchbox/b.py": "",
            "benchbox/loose.py": "",
            "tests/test_patch.py": 'import benchbox.a\npatch("benchbox.b.Thing")\n',
            "tests/test_dynamic.py": 'importlib.import_module("benchbox.a")\n',
            "tests/test_unowned.py": "import benchbox.a\nimport benchbox.loose\n",
        },
    )
    resolved = [
        _resolved("benchbox/a.py", "comment-cleanup-a"),
        _resolved("benchbox/b.py", "comment-cleanup-b"),
        _resolved("benchbox/loose.py", None),
        *(
            _resolved(f"tests/{n}.py", "comment-cleanup-fallback")
            for n in ("test_patch", "test_dynamic", "test_unowned")
        ),
    ]
    scope.apply_derived_rules(resolved, [_derived_rule()], root, base, {})
    owners = {record["path"]: record["owner"] for record in resolved}
    assert owners["tests/test_patch.py"] == "comment-cleanup-fallback"
    assert owners["tests/test_dynamic.py"] == "comment-cleanup-a"
    assert owners["tests/test_unowned.py"] == "comment-cleanup-fallback"


def test_notice_owner_claims_an_unowned_path_and_reports_conflicts() -> None:
    notices = [
        {"path": "LICENSE", "owner": "comment-cleanup-scope-policy", "blocking_disposition": "Retain exact bytes."},
        {"path": "NOTICE", "owner": "comment-cleanup-scope-policy", "blocking_disposition": "Retain exact bytes."},
    ]
    resolved = [_resolved("LICENSE", None), _resolved("NOTICE", "comment-cleanup-other")]
    resolved[0]["rule"] = None
    findings = scope.apply_notice_owners(resolved, notices)
    assert resolved[0]["owner"] == "comment-cleanup-scope-policy"
    assert resolved[0]["rule"] == "notice"
    assert resolved[1]["owner"] == "comment-cleanup-other"
    assert [finding.code for finding in findings] == ["SCOPE005"]


def test_doc_carriers_classify_docstring_readers_and_writers() -> None:
    source = b"""
import inspect

def f():
    parser = P(description=__doc__)
    text = inspect.getsource(f)
    a = getattr(f, "__doc__", "")
    b = f.__doc__
    f.__doc__ = "x"
    setattr(f, "__doc__", "y")
    globals()["g"].__doc__ = "z"
"""
    found = {(role, form) for _, role, form, _target in scope.doc_carriers(source)}
    assert found == {
        ("reader", "module-docstring-read"),
        ("reader", "getsource"),
        ("reader", "getattr"),
        ("reader", "attribute-read"),
        ("writer", "attribute-assignment"),
        ("writer", "setattr"),
    }
    assert scope.doc_carriers(b"def (:\n") == []


def test_unregistered_docstring_writer_is_a_finding(policy: dict) -> None:
    carriers = [
        {"path": "src/a.py", "line": 3, "role": "writer", "form": "attribute-assignment", "owner": "o"},
        {"path": "src/b.py", "line": 4, "role": "reader", "form": "getsource", "owner": "o"},
    ]
    findings = scope.carrier_findings(carriers, policy)
    assert [(finding.code, finding.subject) for finding in findings] == [("SCOPE006", "src/a.py")]
    policy["payloads"] = [
        {
            "id": "a",
            "path": "src/a.py",
            "carrier": "x",
            "owner": "comment-cleanup-x",
            "state": "blocked",
            "blocking_disposition": "d",
        }
    ]
    assert scope.carrier_findings(carriers, policy) == []


def test_exact_rule_must_outrank_the_derived_rule() -> None:
    rule = {"id": "exact", "priority": 20, "selectors": [{"path": "tests/unit/test_x.py"}]}
    derived = [{"id": "d", "priority": 20, "selectors": [{"prefix": "tests/unit/"}]}]
    with pytest.raises(scope.PolicyError):
        scope.validate_rule_priorities([rule], derived)
    scope.validate_rule_priorities([{**rule, "priority": 30}], derived)
    scope.validate_rule_priorities([{**rule, "selectors": [{"prefix": "tests/unit/"}], "priority": 5}], derived)


def _directive(**overrides: object) -> dict:
    entry = {
        "path": "src/a.py",
        "token": "# noqa: F401",
        "count": 1,
        "consumer": "Ruff",
        "necessity": "Imported for side effects.",
        "alternative": "Call an explicit function.",
        "owner": "comment-cleanup-core-bootstrap",
        "removal_trigger": "Explicit function exists.",
    }
    entry.update(overrides)
    return entry


def test_directive_token_and_count_must_match_the_base_file(tmp_path: Path, policy: dict) -> None:
    root, base = _git_repo_with(tmp_path, {"src/a.py": "import x  # noqa: F401\nimport y  # noqa: F401\n"})
    policy["directives"] = [_directive(count=2)]
    scope.validate_evidence(policy, root, base, {"src/a.py"})
    policy["directives"] = [_directive(count=1)]
    with pytest.raises(scope.PolicyError, match="counts 1"):
        scope.validate_evidence(policy, root, base, {"src/a.py"})
    policy["directives"] = [_directive(token="# noqa: N815", count=1)]
    with pytest.raises(scope.PolicyError, match="not present"):
        scope.validate_evidence(policy, root, base, {"src/a.py"})
    policy["directives"] = [_directive(count=0)]
    with pytest.raises(scope.PolicyError, match="integer"):
        scope.validate_evidence(policy, root, base, {"src/a.py"})


def test_obligation_token_must_be_present_at_the_base(tmp_path: Path, policy: dict) -> None:
    root, base = _git_repo_with(tmp_path, {"src/a.py": "# TODO: follow up\n"})
    entry = {
        "path": "src/a.py",
        "token": "TODO: elsewhere",
        "destination": "follow-up",
        "tracker_reference": "existing-item",
        "approved_destination": "",
        "owner": "comment-cleanup-core-bootstrap",
        "blocking_disposition": "Await follow-up.",
    }
    policy["obligations"] = [entry]
    with pytest.raises(scope.PolicyError, match="not present"):
        scope.validate_evidence(policy, root, base, {"src/a.py"})
    entry["token"] = "TODO: follow up"
    scope.validate_evidence(policy, root, base, {"src/a.py"})


def test_directive_and_obligation_owners_must_match_the_path_owner(policy: dict) -> None:
    policy["directives"] = [_directive(owner="comment-cleanup-other")]
    policy["obligations"] = [
        {
            "path": "src/a.py",
            "token": "TODO",
            "destination": "d",
            "tracker_reference": "t",
            "approved_destination": "",
            "owner": "comment-cleanup-core-bootstrap",
            "blocking_disposition": "b",
        }
    ]
    resolved = [_resolved("src/a.py", "comment-cleanup-core-bootstrap")]
    findings = scope.evidence_owner_findings(policy, resolved)
    assert [(finding.code, finding.subject) for finding in findings] == [("SCOPE007", "src/a.py")]
    assert scope.evidence_owner_findings(policy, [_resolved("src/a.py", None)]) == []


def test_doc_carriers_record_the_writer_target_and_implicit_decorator_readers() -> None:
    source = b"""
@click.command()
def with_doc():
    "Implicit help."

@click.command(help="Explicit.")
def explicit():
    "Docstring."

@mcp.tool()
async def tool_with_doc():
    "Implicit description."

@click.group()
def no_doc():
    pass

impl.__doc__ = "x"
globals()["g"].__doc__ = "y"
setattr(other, "__doc__", "z")
text = inspect.cleandoc(value)
"""
    found = scope.doc_carriers(source)
    assert [(role, form, target) for _, role, form, target in found if role == "writer"] == [
        ("writer", "attribute-assignment", "impl"),
        ("writer", "attribute-assignment", "globals()"),
        ("writer", "setattr", "other"),
    ]
    decorators = [line for line, _role, form, _target in found if form == "decorator-docstring"]
    assert len(decorators) == 2
    assert any(form == "cleandoc" for _line, _role, form, _target in found)


def test_docstring_writer_must_be_named_by_a_payload(policy: dict) -> None:
    carriers = [
        {"path": "src/a.py", "line": 3, "role": "writer", "form": "attribute-assignment", "target": "impl"},
        {"path": "src/a.py", "line": 9, "role": "writer", "form": "attribute-assignment", "target": "other"},
        {"path": "src/a.py", "line": 12, "role": "writer", "form": "attribute-assignment", "target": None},
    ]
    policy["payloads"] = [
        {
            "id": "a",
            "path": "src/a.py",
            "carrier": "_impl.__doc__ and impl.__doc__",
            "owner": "comment-cleanup-x",
            "state": "blocked",
            "blocking_disposition": "d",
        }
    ]
    findings = scope.carrier_findings(carriers, policy)
    assert [(finding.code, "other" in finding.detail) for finding in findings] == [("SCOPE006", True)]
    policy["payloads"][0]["carrier"] = "_other.__doc__"
    assert len(scope.carrier_findings(carriers, policy)) == 2


def test_python_comment_markers_count_comments_only_and_unregistered_counts_subtract(policy: dict) -> None:
    source = b"""x = "# noqa: E501 TODO in a string"
y = 1  # noqa: E501
z = 2  # type: ignore[attr-defined]  TODO later
# FIXME remove
"""
    assert scope.python_comment_markers(source) == (2, 1)
    assert scope.python_comment_markers(b"def (:\n") == (0, 0)
    markers = [{"path": "a.py", "directives": 5, "todos": 3}]
    policy["directives"] = [{"path": "a.py", "count": 2}, {"path": "ci.yml", "count": 4}]
    policy["obligations"] = [{"path": "a.py"}, {"path": "nightly.yml"}]
    assert scope.unregistered_markers(markers, policy) == (3, 2)


@pytest.mark.parametrize(
    "comment,counted",
    [
        ("# TODO: link the issue", True),
        ("# TODO(name): link the issue", True),
        ("# FIXME remove", True),
        ("#TODO later", True),
        ("x = 1  # noqa: E501  TODO: later", True),
        ("# see the renderer-consolidation TODO", False),
        ("# TODO) only for a divergence", False),
        ("# Confirmed (TODO w5): only the cells", False),
        ("# see TODO/main/planning/item.yaml", False),
        ("# the TODO's w4 stays pure", False),
        ("# Per the tuning-keys TODO: do not add new aliases", False),
    ],
)
def test_todo_counter_counts_marker_comments_and_not_prose_mentions(comment: str, counted: bool) -> None:
    assert scope.python_comment_markers(f"{comment}\n".encode())[1] == int(counted)


def test_facade_imports_are_ambiguous_but_submodule_imports_resolve() -> None:
    owners = {
        "benchbox/__init__.py": "comment-cleanup-boot",
        "benchbox/a.py": "comment-cleanup-a",
        "benchbox/b/__init__.py": "comment-cleanup-b",
    }
    assert scope.import_owner(b"from benchbox.a import thing\n", owners) == "comment-cleanup-a"
    assert scope.import_owner(b"from benchbox.a import thing\nfrom benchbox import Exported\n", owners) is None
    assert scope.import_owner(b"import benchbox\nfrom benchbox.a import thing\n", owners) is None
    assert scope.import_owner(b"from benchbox import a\n", owners) == "comment-cleanup-a"


def test_malformed_policy_exits_with_a_configuration_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root, base = _git_repo_with(tmp_path, {"src/a.py": "x = 1\n"})
    (root / "quality").mkdir()
    (root / "quality/comment-cleanup-scope.json").write_text("{}", encoding="utf-8")
    (root / ".gitignore").write_text(".todo-batch/\n", encoding="utf-8")
    (root / ".todo-batch").mkdir()
    (root / ".todo-batch/tasks.txt").write_text("comment-cleanup-x\n", encoding="utf-8")

    def broken(path: Path) -> dict:
        raise KeyError("ownership_rules")

    monkeypatch.setattr(scope, "load_policy", broken)
    code = scope.main(["--root", str(root), "--base", base, "--task-set", ".todo-batch/tasks.txt"])
    assert code == 2
    assert "malformed policy" in capsys.readouterr().err


def _external(**overrides: object) -> dict:
    entry = {
        "selector": {"prefix": "vendor/"},
        "owner": "comment-cleanup-external-ownership",
        "provenance": "Vendor kit.",
        "governing_requirement": "Owner decision.",
        "blocking_disposition": "Excluded.",
    }
    entry.update(overrides)
    return entry


def test_external_entry_excludes_unowned_paths_only(policy: dict) -> None:
    policy["external_entries"] = [_external()]
    entries = scope.validate_external_entries(policy, {"vendor/a.c", "src/a.py"})
    resolved = [
        _resolved("vendor/a.c", None),
        _resolved("vendor/b.c", "comment-cleanup-owned"),
        _resolved("src/a.py", None),
    ]
    for record in resolved:
        record["rule"] = None if record["owner"] is None else "r"
    scope.apply_external_entries(resolved, entries)
    assert (resolved[0]["owner"], resolved[0]["state"]) == ("comment-cleanup-external-ownership", "excluded")
    assert resolved[0]["blocking_disposition"] == "Excluded."
    assert resolved[1]["owner"] == "comment-cleanup-owned"
    assert resolved[2]["owner"] is None


def test_external_entry_must_match_a_tracked_path(policy: dict) -> None:
    policy["external_entries"] = [_external(selector={"prefix": "missing/"})]
    with pytest.raises(scope.PolicyError, match="matches no tracked path"):
        scope.validate_external_entries(policy, {"src/a.py"})
    policy["external_entries"] = [_external(selector={"glob": "*"})]
    with pytest.raises(scope.PolicyError, match="invalid selector"):
        scope.validate_external_entries(policy, {"src/a.py"})


def _format_class(**overrides: object) -> dict:
    entry = {
        "id": "c",
        "verifier": "strict-json",
        "extensions": [".json"],
        "selectors": [{"prefix": "a/"}],
        "owner": "comment-cleanup-final-enforcement",
        "blocking_disposition": "d",
    }
    entry.update(overrides)
    return entry


def test_blocked_format_class_needs_a_blocking_verifier_and_the_reverse(policy: dict) -> None:
    policy["format_classes"] = [_format_class(state="blocked")]
    with pytest.raises(scope.PolicyError, match="blocking verifier"):
        scope.validate_format_classes(policy)
    policy["format_classes"] = [_format_class(verifier="any-content")]
    with pytest.raises(scope.PolicyError, match="blocking verifier"):
        scope.validate_format_classes(policy)
    policy["format_classes"] = [_format_class(verifier="any-content", state="blocked")]
    assert scope.validate_format_classes(policy)
    policy["format_classes"] = [_format_class(state="done")]
    with pytest.raises(scope.PolicyError, match="unknown state"):
        scope.validate_format_classes(policy)


def test_blocked_classes_claim_only_what_the_clean_classes_left_unowned(tmp_path: Path, policy: dict) -> None:
    root, base = _git_repo_with(
        tmp_path,
        {
            "a/prose.md": "plain prose\n",
            "a/sample.md": "text\n\n```sh\nls\n```\n",
            "a/ref.md": "[todo]: target\n",
            "a/.keep": "",
            "a/tool.py": "x = 1\n",
        },
    )
    policy["format_classes"] = [
        _format_class(id="prose", verifier="markdown-prose", extensions=[".md"]),
        _format_class(id="empty", verifier="empty-file", extensions=[".keep"]),
        _format_class(
            id="review",
            verifier="markdown-needs-review",
            extensions=[".md"],
            owner="comment-cleanup-documentation-samples",
            state="blocked",
        ),
        _format_class(
            id="helpers",
            verifier="any-content",
            extensions=[".py"],
            owner="comment-cleanup-project-tooling",
            state="blocked",
        ),
    ]
    resolved = [_resolved(path, None) for path in ("a/prose.md", "a/sample.md", "a/ref.md", "a/.keep", "a/tool.py")]
    for record in resolved:
        record["rule"] = None
    scope.apply_format_classes(resolved, scope.validate_format_classes(policy), root, base)
    states = {record["path"]: (record["owner"], record["state"]) for record in resolved}
    assert states["a/prose.md"] == ("comment-cleanup-final-enforcement", "comment-free")
    assert states["a/.keep"] == ("comment-cleanup-final-enforcement", "comment-free")
    assert states["a/sample.md"] == ("comment-cleanup-documentation-samples", "blocked")
    assert states["a/ref.md"] == ("comment-cleanup-documentation-samples", "blocked")
    assert states["a/tool.py"] == ("comment-cleanup-project-tooling", "blocked")


def test_empty_file_verifier_accepts_only_zero_bytes() -> None:
    assert scope.verify_empty_file("a/.gitkeep", b"")
    assert not scope.verify_empty_file("a/.gitkeep", b"\n")


@pytest.mark.parametrize(
    "path,blob,expected",
    [
        ("a.json", b'{"a": 1}', True),
        ("a.json", b'{"a": 1} // note', False),
        ("a.json", b"{\n  // note\n}", False),
        ("a.jsonl", b'{"a": 1}\n\n{"b": 2}\n', True),
        ("a.jsonl", b'{"a": 1}\nnot json\n', False),
    ],
)
def test_strict_json_verifier(path: str, blob: bytes, expected: bool) -> None:
    assert scope.verify_strict_json(path, blob) is expected


def test_png_and_markdown_verifiers() -> None:
    assert scope.verify_png_signature("a.png", b"\x89PNG\r\n\x1a\nrest")
    assert not scope.verify_png_signature("a.png", b"GIF89a")
    assert scope.verify_markdown_prose("a.md", b"# Title\n\nPlain prose.\n")
    assert not scope.verify_markdown_prose("a.md", b"text\n\n```python\n# note\n```\n")
    assert not scope.verify_markdown_prose("a.md", b"text <!-- hidden --> text\n")
    assert scope.verify_markdown_prose("a.md", b"---\ntitle: x\n---\nbody\n")
    assert not scope.verify_markdown_prose("a.md", b"---\n# comment\ntitle: x\n---\nbody\n")
    assert not scope.verify_markdown_prose("a.md", b"\xff\xfe")


def test_format_class_marks_only_verified_unowned_files(tmp_path: Path, policy: dict) -> None:
    root, base = _git_repo_with(
        tmp_path,
        {
            "data/ok.json": '{"a": 1}',
            "data/bad.json": '{"a": 1} // c',
            "data/code.py": "x = 1\n",
            "other/ok.json": "{}",
        },
    )
    policy["format_classes"] = [
        {
            "id": "json-data",
            "verifier": "strict-json",
            "extensions": [".json"],
            "selectors": [{"prefix": "data/"}],
            "owner": "comment-cleanup-final-enforcement",
            "blocking_disposition": "Parses as strict JSON.",
        }
    ]
    classes = scope.validate_format_classes(policy)
    resolved = [_resolved(path, None) for path in ("data/ok.json", "data/bad.json", "data/code.py", "other/ok.json")]
    for record in resolved:
        record["rule"] = None
    scope.apply_format_classes(resolved, classes, root, base)
    states = {record["path"]: (record["owner"], record["state"]) for record in resolved}
    assert states["data/ok.json"] == ("comment-cleanup-final-enforcement", "comment-free")
    assert states["data/bad.json"][0] is None
    assert states["data/code.py"][0] is None
    assert states["other/ok.json"][0] is None


def test_format_class_rejects_unknown_verifier_and_bad_extension(policy: dict) -> None:
    entry = {
        "id": "x",
        "verifier": "guess",
        "extensions": [".json"],
        "selectors": [{"prefix": "a/"}],
        "owner": "comment-cleanup-final-enforcement",
        "blocking_disposition": "d",
    }
    policy["format_classes"] = [entry]
    with pytest.raises(scope.PolicyError, match="unknown verifier"):
        scope.validate_format_classes(policy)
    entry.update(verifier="strict-json", extensions=["json"])
    with pytest.raises(scope.PolicyError, match="start with a dot"):
        scope.validate_format_classes(policy)


@pytest.mark.parametrize(
    "path,blob,expected",
    [
        ("a.json", b'{"_comment": ["note"], "a": 1}', False),
        ("a.json", b'{"nested": {"$comment": "note"}}', False),
        ("a.json", b'{"//": "note"}', False),
        ("a.json", b'{"__comment": "note"}', False),
        ("a.json", b'{"items": [{"comment": "note"}]}', False),
        ("a.json", b'{"a": 1, "a": 2}', False),
        ("a.json", b'{"a": NaN}', False),
        ("a.json", b'\xef\xbb\xbf{"a": 1}', False),
        ("a.json", b"", False),
        ("a.jsonl", b"\n\n", False),
    ],
)
def test_strict_json_verifier_rejects_comment_conventions_and_ambiguity(path: str, blob: bytes, expected: bool) -> None:
    assert scope.verify_strict_json(path, blob) is expected


@pytest.mark.parametrize(
    "blob",
    [
        b"text\n\n    indented code\n",
        b"text\n\n\tindented code\n",
        b"---\ntitle: x # note\n---\nbody\n",
        b"---\ntitle: x\n",
        b"+++\ntitle = 'x'\n+++\nbody\n",
        b"[//]: # (hidden)\n",
        b"text {/* hidden */}\n",
        b"% myst comment\n",
        b"> ```\n> code\n> ```\n",
        b"- item\n  ```\n  code\n  ```\n",
        b"1. ```sh\n",
        b"~~~\ncode\n~~~\n",
        b"[//]: <> (hidden)\n",
        b"[comment]: # (hidden)\n",
        b"[//]:# (hidden)\n",
        b"{% comment %}hidden{% endcomment %}\n",
        b"{# hidden #}\n",
        b"<pre>code</pre>\n",
        b"<SCRIPT>x</SCRIPT>\n",
        b"  % myst comment\n",
        b"[todo]: ../planning/item.yaml\n",
        b'[a]: <https://example.com> "hidden title"\n',
        b"- [a]: target\n",
        b"> [a]: target\n",
    ],
)
def test_markdown_prose_verifier_rejects_every_comment_or_code_form(blob: bytes) -> None:
    assert not scope.verify_markdown_prose("a.md", blob)


def test_markdown_prose_verifier_keeps_visible_links_and_footnotes() -> None:
    blob = b"See [the guide](guide.md) and the note[^1].\n\nRef: see [a]: b in running text.\n\n[^1]: A visible note.\n"
    assert scope.verify_markdown_prose("a.md", blob)


def test_sql_verifier_requires_the_absence_of_comment_markers() -> None:
    assert scope.verify_sql_without_comment_markers("a.sql", b"SELECT 1 FROM t WHERE a = 'x';\n")
    for blob in (b"SELECT 1; -- note\n", b"/* note */ SELECT 1;", b"SELECT 1; # note", b"\xff"):
        assert not scope.verify_sql_without_comment_markers("a.sql", blob)


def test_external_and_format_classes_leave_collisions_and_notices_alone(tmp_path: Path, policy: dict) -> None:
    root, base = _git_repo_with(tmp_path, {"vendor/a.json": "{}", "vendor/b.json": "{}"})
    policy["external_entries"] = [_external()]
    policy["format_classes"] = [
        {
            "id": "json-data",
            "verifier": "strict-json",
            "extensions": [".json"],
            "selectors": [{"prefix": "vendor/"}],
            "owner": "comment-cleanup-final-enforcement",
            "blocking_disposition": "Strict JSON.",
        }
    ]
    notice = {"path": "vendor/a.json", "owner": "comment-cleanup-scope-policy", "blocking_disposition": "Retain."}
    resolved = [_resolved("vendor/a.json", None), _resolved("vendor/b.json", None)]
    resolved[0]["rule"] = None
    resolved[1].update(rule=["rule-a", "rule-b"], competing_owners=["a", "b"])
    scope.apply_notice_owners(resolved, [notice])
    scope.apply_external_entries(resolved, scope.validate_external_entries(policy, {"vendor/a.json", "vendor/b.json"}))
    scope.apply_format_classes(resolved, scope.validate_format_classes(policy), root, base)
    assert (resolved[0]["owner"], resolved[0]["state"]) == ("comment-cleanup-scope-policy", "blocked")
    assert resolved[1]["owner"] is None and resolved[1]["rule"] == ["rule-a", "rule-b"]


def test_overlapping_external_prefixes_are_rejected(policy: dict) -> None:
    policy["external_entries"] = [_external(), _external(selector={"prefix": "vendor/sub/"})]
    with pytest.raises(scope.PolicyError, match="overlaps"):
        scope.validate_external_entries(policy, {"vendor/sub/a.c"})


def test_native_checker_reuses_the_scope_schema_validator() -> None:
    import check_comment_cleanup_scope as shared
    import check_comment_policy as native

    assert native.load_policy is shared.load_comment_policy
    assert native.validate_path is shared.validate_comment_policy_path
    assert native.matches is shared.comment_policy_matches
    assert native.DIRECTIVES is shared.COMMENT_POLICY_DIRECTIVES
    assert native.ENFORCEMENT_MODES is shared.COMMENT_POLICY_ENFORCEMENT_MODES


def _drift_notice(path: str, payload: bytes) -> dict:
    digest = hashlib.sha256(payload).hexdigest()
    return {
        "path": path,
        "blob_sha256": digest,
        "byte_start": 0,
        "byte_end": len(payload),
        "retained_sha256": digest,
        "governing_requirement": "Fixture pin.",
        "source_identity": "Fixture source.",
        "owner": "comment-cleanup-external-ownership",
        "blocking_disposition": "Fixture disposition.",
    }


def _drift_repo(tmp_path: Path, policy: dict, payload: bytes) -> tuple[Path, str]:
    root = tmp_path / "drift"
    root.mkdir()
    setup = (
        ["init"],
        ["config", "user.email", "drift@example.com"],
        ["config", "user.name", "Drift"],
    )
    for args in setup:
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    (root / "PATCHES.md").write_bytes(payload)
    (root / "quality").mkdir()
    (root / "quality" / "comment-cleanup-scope.json").write_text(json.dumps(policy), encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "base"], check=True, capture_output=True)
    base = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    return root, base


def test_pinned_notice_drift_accepts_an_unmodified_tree(tmp_path: Path, policy: dict) -> None:
    payload = b"pinned\n"
    policy["notices"] = [_drift_notice("PATCHES.md", payload)]
    root, base = _drift_repo(tmp_path, policy, payload)
    assert scope.pinned_notice_drift_findings(root, base) == []


def test_pinned_notice_drift_rejects_a_modification_without_a_pin_update(tmp_path: Path, policy: dict) -> None:
    payload = b"pinned\n"
    policy["notices"] = [_drift_notice("PATCHES.md", payload)]
    root, base = _drift_repo(tmp_path, policy, payload)
    (root / "PATCHES.md").write_bytes(payload + b"more\n")
    findings = scope.pinned_notice_drift_findings(root, base)
    assert [finding.code for finding in findings] == ["SCOPE008"]
    assert findings[0].subject == "PATCHES.md"
    assert "quality/comment-cleanup-scope.json" in findings[0].detail


def test_pinned_notice_drift_accepts_a_modification_with_a_pin_update(tmp_path: Path, policy: dict) -> None:
    payload = b"pinned\n"
    policy["notices"] = [_drift_notice("PATCHES.md", payload)]
    root, base = _drift_repo(tmp_path, policy, payload)
    updated = payload + b"more\n"
    (root / "PATCHES.md").write_bytes(updated)
    policy["notices"] = [_drift_notice("PATCHES.md", updated)]
    (root / "quality" / "comment-cleanup-scope.json").write_text(json.dumps(policy), encoding="utf-8")
    assert scope.pinned_notice_drift_findings(root, base) == []


def test_pinned_notice_drift_rejects_a_deleted_file(tmp_path: Path, policy: dict) -> None:
    payload = b"pinned\n"
    policy["notices"] = [_drift_notice("PATCHES.md", payload)]
    root, base = _drift_repo(tmp_path, policy, payload)
    (root / "PATCHES.md").unlink()
    findings = scope.pinned_notice_drift_findings(root, base)
    assert [finding.code for finding in findings] == ["SCOPE008"]
    assert findings[0].subject == "PATCHES.md"


def test_check_drift_main_reports_zero_for_a_clean_tree_and_one_for_drift(
    tmp_path: Path, policy: dict, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = b"pinned\n"
    policy["notices"] = [_drift_notice("PATCHES.md", payload)]
    root, base = _drift_repo(tmp_path, policy, payload)
    assert scope.main(["--check-drift", "--root", str(root), "--base", base]) == 0
    (root / "PATCHES.md").write_bytes(payload + b"more\n")
    assert scope.main(["--check-drift", "--root", str(root), "--base", base]) == 1
    captured = capsys.readouterr()
    assert "SCOPE008 PATCHES.md" in captured.err
