from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT = REPO_ROOT / "scripts" / "publication" / "reconciler.py"

SPEC = importlib.util.spec_from_file_location("reconciler", SCRIPT)
assert SPEC and SPEC.loader
reconciler = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reconciler)

SHA40 = "x" * 40
SHA64 = "x" * 64


def _write_events(tmp_path: Path, events: list[dict]) -> Path:
    p = tmp_path / "events.jsonl"
    with p.open("w", encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e) + "\n")
    return p


def _run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=cwd or REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_rejects_when_ledger_head_sha_is_none() -> None:
    result = _run_cli("--events-file", "/tmp/nonexistent-events.jsonl", "--limit", "5")
    assert result.returncode != 0
    assert "ledger-head-sha" in result.stderr or "ledger-head-sha" in result.stdout


def test_rejects_empty_ledger_head_sha() -> None:
    events_file = _write_events(Path("/tmp"), [])
    result = _run_cli(
        "--events-file",
        str(events_file),
        "--ledger-head-sha",
        "",
        "--limit",
        "5",
    )
    assert result.returncode != 0
    assert "not be empty" in result.stdout


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _init_corpus_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "corpus-git"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "results-data" / "bundles").mkdir(parents=True)
    return repo


def _commit_bundles(repo: Path, names: list[str], message: str) -> str:
    bundles = repo / "results-data" / "bundles"
    desired = set(names)
    for existing in list(bundles.glob("*.json")):
        if existing.name not in desired:
            rel = existing.relative_to(repo).as_posix()
            existing.unlink()
            subprocess.run(
                ["git", "rm", "--quiet", "--ignore-unmatch", rel],
                cwd=repo,
                check=False,
                capture_output=True,
                text=True,
            )
    for name in names:
        path = bundles / name
        path.write_text(f'{{"id": "{name}"}}\n', encoding="utf-8")
        _git(repo, "add", path.relative_to(repo).as_posix())
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    if status.stdout.strip():
        _git(repo, "commit", "-m", message)
    else:
        _git(repo, "commit", "--allow-empty", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def test_candidate_with_more_paths_than_ledger_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _init_corpus_repo(tmp_path)
    base_sha = _commit_bundles(repo, ["a.json", "b.json"], "base")
    ledger_sha = base_sha
    _git(repo, "checkout", "-b", "add-c", base_sha)
    _commit_bundles(repo, ["a.json", "b.json", "c.json"], "add-c")
    _git(repo, "checkout", "main")
    _git(repo, "merge", "--no-ff", "-m", "merge add-c", "add-c")
    merge_sha = _git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(reconciler, "REPO_ROOT", repo)

    ledger_paths = reconciler.get_corpus_paths(ledger_sha)
    candidate_paths = reconciler.get_corpus_paths(merge_sha)
    assert ledger_paths <= candidate_paths
    assert "results-data/bundles/c.json" in candidate_paths

    accepted, gen, reason = reconciler.reconcile_event(
        {"merge_sha": merge_sha, "base_sha": base_sha, "ts": "t"},
        ledger_sha,
        0,
    )
    assert accepted is True
    assert gen == 1
    assert reason == "accepted"


def test_candidate_with_fewer_paths_than_ledger_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _init_corpus_repo(tmp_path)
    base_sha = _commit_bundles(repo, ["a.json", "b.json"], "base")
    ledger_sha = _commit_bundles(repo, ["a.json", "b.json", "c.json"], "ledger")
    _git(repo, "checkout", "-b", "fewer", base_sha)
    fewer_tip = _commit_bundles(repo, ["a.json", "b.json"], "fewer-paths")
    _git(repo, "checkout", "-b", "merge-fewer", fewer_tip)
    _git(repo, "merge", "--no-ff", "-m", "merge ledger ref", "-s", "ours", ledger_sha)
    merge_sha = _git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(reconciler, "REPO_ROOT", repo)

    ledger_paths = reconciler.get_corpus_paths(ledger_sha)
    candidate_paths = reconciler.get_corpus_paths(merge_sha)
    assert ledger_paths - candidate_paths, "candidate must lose at least one ledger path"
    assert not (ledger_paths <= candidate_paths)

    accepted, gen, reason = reconciler.reconcile_event(
        {"merge_sha": merge_sha, "base_sha": base_sha, "ts": "t"},
        ledger_sha,
        0,
    )
    assert accepted is False
    assert gen == 0
    assert "coalescing rejected" in reason


def test_candidate_with_same_paths_as_ledger_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _init_corpus_repo(tmp_path)
    base_sha = _commit_bundles(repo, ["a.json", "b.json"], "base")
    ledger_sha = base_sha
    _git(repo, "checkout", "-b", "side", base_sha)
    _commit_bundles(repo, ["a.json", "b.json"], "side-empty-change")
    _git(repo, "checkout", "main")
    _git(repo, "merge", "--no-ff", "-m", "merge side same paths", "-s", "ours", "side")
    merge_sha = _git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(reconciler, "REPO_ROOT", repo)

    ledger_paths = reconciler.get_corpus_paths(ledger_sha)
    candidate_paths = reconciler.get_corpus_paths(merge_sha)
    assert ledger_paths <= candidate_paths
    assert ledger_paths - candidate_paths == set()

    accepted, gen, reason = reconciler.reconcile_event(
        {"merge_sha": merge_sha, "base_sha": base_sha, "ts": "t"},
        ledger_sha,
        0,
    )
    assert accepted is True
    assert gen == 1
    assert reason == "accepted"


def test_verify_merge_commit_rejects_non_existent() -> None:
    is_merge, count, err = reconciler.verify_merge_commit("0" * 40)
    assert is_merge is False
    assert count == 0
    assert "git cat-file failed" in err


def test_verify_merge_commit_rejects_single_parent() -> None:
    is_merge, count, err = reconciler.verify_merge_commit("0" * 40)
    assert is_merge is False


def test_verify_base_ancestor_rejects_non_ancestor() -> None:
    is_ancestor, err = reconciler.verify_base_ancestor("0" * 40, "1" * 40)
    assert is_ancestor is False
    assert "not an ancestor" in err


def test_read_event_file_handles_empty(tmp_path: Path) -> None:
    p = tmp_path / "empty.jsonl"
    p.touch()
    assert reconciler.read_event_file(p, 100) == []


def test_read_event_file_respects_limit(tmp_path: Path) -> None:
    events = [{"merge_sha": SHA40, "base_sha": SHA40, "ts": "t"} for _ in range(10)]
    p = _write_events(tmp_path, events)
    got = reconciler.read_event_file(p, 3)
    assert len(got) == 3


def test_read_event_file_parses_jsonl(tmp_path: Path) -> None:
    events = [{"merge_sha": SHA40, "base_sha": SHA40, "ts": "t"}]
    p = _write_events(tmp_path, events)
    got = reconciler.read_event_file(p, 100)
    assert got == events


def test_reconcile_decision_compares_against_ledger_head_not_parent() -> None:
    ledger = {"results-data/bundles/a.json", "results-data/bundles/b.json"}
    candidate_more = ledger | {"results-data/bundles/c.json"}
    candidate_less = ledger - {"results-data/bundles/b.json"}
    candidate_same = set(ledger)

    assert ledger <= candidate_more
    assert not (ledger <= candidate_less)
    assert ledger <= candidate_same


def test_duplicate_events_are_idempotent() -> None:
    event = {"merge_sha": SHA40, "base_sha": SHA40, "ts": "t"}
    decisions = []
    for _ in range(2):
        decisions.append(reconciler.reconcile_event(event, "ledger", 0))
    assert decisions[0][0] == decisions[1][0]


def test_stale_events_rejected() -> None:
    event = {"merge_sha": "0" * 40, "base_sha": "0" * 40, "ts": "stale"}
    accepted, gen, reason = reconciler.reconcile_event(event, "ledger", 0)
    assert accepted is False
    assert gen == 0


def test_generation_advances_monotonically() -> None:
    gen = 7
    rejected_accepted, gen_after_reject, _ = reconciler.reconcile_event(
        {"merge_sha": "0" * 40, "base_sha": "0" * 40, "ts": "t"}, "ledger", gen
    )
    assert rejected_accepted is False
    assert gen_after_reject == gen

    new_gen = gen + 1
    assert new_gen == gen + 1


def test_missed_events_are_replayable_from_log() -> None:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    events_file = _write_events(
        Path("/tmp"),
        [{"merge_sha": "0" * 40, "base_sha": "0" * 40, "ts": "missed-1"}],
    )
    result = _run_cli(
        "--events-file",
        str(events_file),
        "--ledger-head-sha",
        head,
        "--limit",
        "100",
    )
    assert result.returncode != 0
    assert "rejected" in result.stdout


def test_cli_exits_nonzero_when_event_rejected(tmp_path: Path) -> None:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    events_file = _write_events(
        tmp_path,
        [{"merge_sha": "0" * 40, "base_sha": "0" * 40, "ts": "reject-me"}],
    )
    result = _run_cli(
        "--events-file",
        str(events_file),
        "--ledger-head-sha",
        head,
        "--limit",
        "10",
    )
    assert result.returncode == 1
    lines = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    assert any(line.get("accepted") is False for line in lines)
    summary = lines[-1]
    assert summary.get("rejected") == 1
    assert summary.get("accepted") == 0


def test_reconcile_cap_respects_limit(tmp_path: Path) -> None:
    events = [{"merge_sha": SHA40, "base_sha": SHA40, "ts": f"t-{i}"} for i in range(20)]
    event_file = _write_events(tmp_path, events)
    got = reconciler.read_event_file(event_file, 100)
    assert len(got) == 20
    limited = reconciler.read_event_file(event_file, 5)
    assert len(limited) == 5


def test_cli_rejects_missing_events_file(tmp_path: Path) -> None:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    result = _run_cli(
        "--events-file",
        str(tmp_path / "missing.jsonl"),
        "--ledger-head-sha",
        head,
    )
    assert result.returncode == 0
    assert "no_events" in result.stdout


def test_cli_missing_ledger_head_sha_is_fatal(tmp_path: Path) -> None:
    events_file = _write_events(tmp_path, [{"merge_sha": SHA40, "base_sha": SHA40, "ts": "t"}])
    result = _run_cli(
        "--events-file",
        str(events_file),
        "--ledger-head-sha",
        "",
        "--limit",
        "5",
    )
    assert result.returncode != 0
    assert "not be empty" in result.stdout
