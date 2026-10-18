from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_PATH = REPO_ROOT / "scripts" / "publication" / "create_ledger_seed.py"
SEED_PATH = REPO_ROOT / "publication" / "ledger-seed.json"
CORPUS_PREFIX = "results-data/bundles/"
IGNORED_SUFFIXES = (".manifest.json", ".applied.json", ".plans.json", ".tuning.json", ".override.json", ".gitkeep")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

ACCEPTED_REF = "origin/published-results"


def _recorded_source() -> str:
    try:
        return str(json.loads(SEED_PATH.read_text(encoding="utf-8")).get("source") or "")
    except (OSError, ValueError):
        return ""


RECORDED_SOURCE = _recorded_source()

_ALLOW_MISSING_REF = os.environ.get("BENCHBOX_ALLOW_MISSING_PUBLISHED_REF") == "1"


def _recorded_source_available() -> bool:
    if not COMMIT_SHA_RE.match(RECORDED_SOURCE):
        return False
    try:
        subprocess.run(
            ["git", "cat-file", "-e", RECORDED_SOURCE],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
        )
        return True
    except subprocess.CalledProcessError:
        try:
            subprocess.run(
                ["git", "fetch", "--no-tags", "origin", "published-results:refs/remotes/origin/published-results"],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "cat-file", "-e", RECORDED_SOURCE],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
            )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False


REF_AVAILABLE = _recorded_source_available()
skip_no_ref = pytest.mark.skipif(
    not REF_AVAILABLE,
    reason="recorded ledger source snapshot not reachable",
)


def _seed() -> dict:
    return json.loads(SEED_PATH.read_text(encoding="utf-8"))


def _primary(path: str) -> bool:
    return path.endswith(".json") and not any(path.endswith(sfx) for sfx in IGNORED_SUFFIXES)


def _bundles_at_ref(ref: str) -> list[str]:
    out = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", ref, "--", CORPUS_PREFIX],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    return sorted(p.strip() for p in out.splitlines() if p.strip().startswith(CORPUS_PREFIX) and _primary(p.strip()))


def _worktree_bundles() -> list[str]:
    base = REPO_ROOT / CORPUS_PREFIX
    return sorted(
        p.relative_to(REPO_ROOT).as_posix()
        for p in base.rglob("*.json")
        if _primary(p.relative_to(REPO_ROOT).as_posix())
    )


def _blob_sha256(ref: str, path: str) -> str:
    proc = subprocess.run(
        ["git", "cat-file", "blob", f"{ref}:{path}"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    return hashlib.sha256(proc.stdout).hexdigest()


def test_ref_dependent_suite_is_not_silently_skipped() -> None:
    assert REF_AVAILABLE or _ALLOW_MISSING_REF, (
        f"recorded source {RECORDED_SOURCE or '<missing>'} is unreachable; set "
        "BENCHBOX_ALLOW_MISSING_PUBLISHED_REF=1 to allow an offline run, otherwise fetch "
        "the accepted ref so the ledger-seed contract is actually tested"
    )


def test_seed_file_exists_and_is_valid_json() -> None:
    assert SEED_PATH.is_file()
    assert isinstance(_seed(), dict)


def test_seed_schema_version() -> None:
    assert _seed()["schema_version"] == 2


def test_seed_has_required_keys() -> None:
    data = _seed()
    for key in (
        "schema_version",
        "generated_at",
        "source",
        "source_ref",
        "source_resolved_at",
        "source_resolved_from",
        "main_source",
        "bidirectional",
        "union",
        "dispositions",
        "digests",
        "published_only",
        "legacy_overlay",
        "count",
    ):
        assert key in data, f"missing key: {key}"


def test_seed_provenance_binds_recorded_snapshot() -> None:
    data = _seed()
    assert COMMIT_SHA_RE.match(data["source"])
    assert data["source_resolved_from"] == data["source_ref"] == ACCEPTED_REF
    assert re.match(r"^\d{4}-\d{2}-\d{2}T", data["source_resolved_at"]), "resolved_at must be a UTC timestamp"


def test_seed_source_is_pinned_commit_sha() -> None:
    data = _seed()
    assert COMMIT_SHA_RE.match(data["source"]), f"source must be a 40-hex commit SHA, got {data['source']!r}"
    assert data["source_ref"] == ACCEPTED_REF


def test_seed_count_matches_union_length() -> None:
    data = _seed()
    assert data["count"] == len(data["union"]) == len(data["dispositions"]) == len(data["digests"])


def test_seed_bidirectional_is_derived() -> None:
    data = _seed()
    assert isinstance(data["bidirectional"], bool)
    expected = not data["published_only"] and not data["legacy_overlay"]
    assert data["bidirectional"] is expected


def test_seed_every_path_has_a_sha256_digest() -> None:
    data = _seed()
    for path in data["union"]:
        digest = data["digests"].get(path)
        assert digest and SHA256_RE.match(digest), f"missing/invalid digest for {path}: {digest!r}"


@skip_no_ref
def test_seed_union_is_the_two_sided_union() -> None:
    accepted = set(_bundles_at_ref(_seed()["source"]))
    main = set(_worktree_bundles())
    data = _seed()
    assert sorted(accepted | main) == sorted(data["union"])
    assert accepted, "accepted set is empty (broken ref?)"


@skip_no_ref
def test_seed_dispositions_match_set_membership() -> None:
    accepted = set(_bundles_at_ref(_seed()["source"]))
    main = set(_worktree_bundles())
    data = _seed()
    assert sorted(accepted - main) == sorted(data["published_only"])
    assert sorted(main - accepted) == sorted(data["legacy_overlay"])
    for path, disp in data["dispositions"].items():
        if path in data["legacy_overlay"]:
            assert disp == "legacy_overlay"
        elif path in data["published_only"]:
            assert disp == "published_only"
        else:
            assert disp == "accepted"


@skip_no_ref
def test_seed_dispositions_cover_all_union_paths() -> None:
    data = _seed()
    assert set(data["union"]) == set(data["dispositions"]) == set(data["digests"])


@skip_no_ref
def test_seed_digests_match_git_blob_bytes() -> None:
    data = _seed()
    accepted = set(_bundles_at_ref(data["source"]))
    sample = list(data["union"])[:: max(1, len(data["union"]) // 15)]
    for path in sample:
        if path in accepted:
            expected = _blob_sha256(data["source"], path)
        else:
            expected = hashlib.sha256((REPO_ROOT / path).read_bytes()).hexdigest()
        assert data["digests"][path] == expected, f"digest mismatch for {path}"


@skip_no_ref
def test_seed_count_is_nonzero() -> None:
    assert _seed()["count"] > 0


def _run_cli(*args: str, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


@skip_no_ref
def test_cli_regenerates_committed_seed_content(tmp_path: Path) -> None:
    out = tmp_path / "seed.json"
    result = _run_cli("--accepted-ref", RECORDED_SOURCE, "--expect-source", RECORDED_SOURCE, "--output", str(out))
    assert result.returncode == 0, result.stderr
    regen = json.loads(out.read_text(encoding="utf-8"))
    committed = _seed()
    assert regen["source"] == committed["source"] == RECORDED_SOURCE
    for key in ("union", "dispositions", "digests", "published_only", "legacy_overlay", "count"):
        assert regen[key] == committed[key], f"regenerated seed drifted at {key}"


@skip_no_ref
def test_cli_expect_source_rejects_moved_ref(tmp_path: Path) -> None:
    out = tmp_path / "seed.json"
    result = _run_cli("--accepted-ref", "HEAD", "--expect-source", RECORDED_SOURCE, "--output", str(out))
    assert result.returncode != 0
    assert "moved" in result.stderr
    assert not out.exists()


@skip_no_ref
def test_cli_classifies_forced_legacy_overlay(tmp_path: Path) -> None:
    out = tmp_path / "seed.json"
    first = _bundles_at_ref(RECORDED_SOURCE)[0]
    result = _run_cli(
        "--accepted-ref",
        RECORDED_SOURCE,
        "--expect-source",
        RECORDED_SOURCE,
        "--output",
        str(out),
        "--legacy-overlay",
        first,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["dispositions"][first] == "legacy_overlay"
    assert data["bidirectional"] is False


def test_cli_rejects_disposition_path_outside_union(tmp_path: Path) -> None:
    out = tmp_path / "seed.json"
    result = _run_cli(
        "--accepted-ref",
        RECORDED_SOURCE if REF_AVAILABLE else "HEAD",
        "--output",
        str(out),
        "--published-only",
        "results-data/bundles/does-not-exist.json",
    )
    assert result.returncode != 0
    assert not out.exists()


def test_cli_empty_corpus_fails(tmp_path: Path) -> None:
    out = tmp_path / "seed.json"
    result = _run_cli("--accepted-ref", "4b825dc642cb6eb9a060e54bf899d150063038c2", "--output", str(out))
    assert result.returncode != 0
    assert not out.exists()


def test_cli_reports_bad_ref_distinctly(tmp_path: Path) -> None:
    out = tmp_path / "seed.json"
    result = _run_cli("--accepted-ref", "totally/not/a/ref", "--output", str(out))
    assert result.returncode != 0
    assert "rev-parse" in result.stderr or "ls-tree" in result.stderr


@skip_no_ref
def test_cli_materialize_dest_writes_union_and_verifies_digests(tmp_path: Path) -> None:
    dest = tmp_path / "corpus_archive"
    result = _run_cli(
        "--accepted-ref",
        RECORDED_SOURCE,
        "--expect-source",
        RECORDED_SOURCE,
        "--ledger-seed",
        str(SEED_PATH),
        "--materialize-dest",
        str(dest),
    )
    assert result.returncode == 0, result.stderr
    seed = _seed()
    written = sorted(p.name for p in dest.rglob("*.json") if _primary(p.name))
    expected = sorted(Path(p).name for p in seed["union"])
    assert written == expected

    bad_seed = tmp_path / "bad-seed.json"
    tampered = dict(seed)
    tampered["digests"] = dict(seed["digests"])
    first = seed["union"][0]
    tampered["digests"][first] = "0" * 64
    bad_seed.write_text(json.dumps(tampered), encoding="utf-8")
    bad_dest = tmp_path / "bad_archive"
    bad = _run_cli(
        "--accepted-ref",
        RECORDED_SOURCE,
        "--expect-source",
        RECORDED_SOURCE,
        "--ledger-seed",
        str(bad_seed),
        "--materialize-dest",
        str(bad_dest),
    )
    assert bad.returncode != 0
    assert "digest mismatch" in bad.stderr


@skip_no_ref
def test_cli_materialize_rejects_seed_without_recorded_source(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy-seed.json"
    seed = _seed()
    stripped = {k: v for k, v in seed.items() if k != "source"}
    legacy.write_text(json.dumps(stripped), encoding="utf-8")
    result = _run_cli(
        "--accepted-ref",
        RECORDED_SOURCE,
        "--expect-source",
        RECORDED_SOURCE,
        "--ledger-seed",
        str(legacy),
        "--materialize-dest",
        str(tmp_path / "legacy_archive"),
    )
    assert result.returncode != 0
    assert "no immutable source SHA" in result.stderr


def _isolated_corpus_repo(path: Path) -> tuple[str, str]:
    path.mkdir(parents=True, exist_ok=True)

    def git(*args: str) -> str:
        result = subprocess.run(["git", *args], cwd=path, check=True, capture_output=True, text=True)
        return result.stdout.strip()

    git("init", "-b", "main")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    git("config", "commit.gpgsign", "false")
    bundles = path / "results-data" / "bundles"
    bundles.mkdir(parents=True)
    (bundles / "a.json").write_text('{"id": "a"}')
    (bundles / "b.json").write_text('{"id": "b"}')
    git("add", ".")
    git("commit", "-m", "accepted snapshot")
    git("branch", "published-results")
    snapshot = git("rev-parse", "HEAD")
    (bundles / "c.json").write_text('{"id": "c"}')
    git("add", ".")
    git("commit", "-m", "main-only addition")
    return str(path), snapshot


def test_isolated_replay_moved_ref_fails_closed_snapshot_reproduces(tmp_path: Path) -> None:
    repo, snapshot = _isolated_corpus_repo(tmp_path / "iso")

    def cli(*args: str) -> subprocess.CompletedProcess[str]:
        return _run_cli("--repo-root", repo, *args, cwd=Path(repo))

    seed_path = tmp_path / "seed.json"
    result = cli("--accepted-ref", snapshot, "--expect-source", snapshot, "--output", str(seed_path))
    assert result.returncode == 0, result.stderr
    seed = json.loads(seed_path.read_text(encoding="utf-8"))
    assert seed["source"] == snapshot
    assert seed["count"] == 3
    assert seed["dispositions"]["results-data/bundles/c.json"] == "legacy_overlay"

    subprocess.run(["git", "checkout", "-q", "published-results"], cwd=repo, check=True)
    (Path(repo) / "results-data" / "bundles" / "d.json").write_text('{"id": "d"}')
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "mirror advance"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-q", "main"], cwd=repo, check=True)

    regen_path = tmp_path / "regen.json"
    result = cli("--accepted-ref", snapshot, "--expect-source", snapshot, "--output", str(regen_path))
    assert result.returncode == 0, result.stderr
    regen = json.loads(regen_path.read_text(encoding="utf-8"))
    assert regen["union"] == seed["union"]
    assert regen["digests"] == seed["digests"]

    moved = cli(
        "--accepted-ref", "published-results", "--expect-source", snapshot, "--output", str(tmp_path / "moved.json")
    )
    assert moved.returncode != 0
    assert "moved" in moved.stderr

    (Path(repo) / "results-data" / "bundles" / "b.json").unlink()
    dest = tmp_path / "archive"
    result = cli(
        "--accepted-ref",
        "published-results",
        "--expect-source",
        snapshot,
        "--ledger-seed",
        str(seed_path),
        "--materialize-dest",
        str(dest),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads((dest / "b.json").read_text(encoding="utf-8")) == {"id": "b"}


def test_isolated_replay_mirror_drift_breaks_bidirectional(tmp_path: Path) -> None:
    repo, snapshot = _isolated_corpus_repo(tmp_path / "iso")

    def cli(*args: str) -> subprocess.CompletedProcess[str]:
        return _run_cli("--repo-root", repo, *args, cwd=Path(repo))

    seed_path = tmp_path / "seed.json"
    result = cli("--accepted-ref", snapshot, "--main-ref", "published-results", "--output", str(seed_path))
    assert result.returncode == 0, result.stderr
    seed = json.loads(seed_path.read_text(encoding="utf-8"))
    assert seed["bidirectional"] is True
    assert seed["legacy_overlay"] == []

    subprocess.run(["git", "checkout", "-q", "main"], cwd=repo, check=True)
    (Path(repo) / "results-data" / "bundles" / "d.json").write_text('{"id": "d"}')
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "mirror drift"], cwd=repo, check=True)

    drift_path = tmp_path / "drift.json"
    result = cli("--accepted-ref", snapshot, "--main-ref", "main", "--output", str(drift_path))
    assert result.returncode == 0, result.stderr
    drift = json.loads(drift_path.read_text(encoding="utf-8"))
    assert drift["bidirectional"] is False
    assert drift["dispositions"]["results-data/bundles/d.json"] == "legacy_overlay"
    assert drift["union"] != seed["union"]

    regen_path = tmp_path / "regen.json"
    result = cli("--accepted-ref", snapshot, "--main-ref", "published-results", "--output", str(regen_path))
    assert result.returncode == 0, result.stderr
    regen = json.loads(regen_path.read_text(encoding="utf-8"))
    assert regen["union"] == seed["union"]
    assert regen["digests"] == seed["digests"]

    main_sha = subprocess.run(
        ["git", "rev-parse", "main"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    (Path(repo) / "results-data" / "bundles" / "d.json").unlink()
    (Path(repo) / "results-data" / "bundles" / "c.json").unlink()
    dest = tmp_path / "archive"
    result = cli(
        "--accepted-ref",
        snapshot,
        "--ledger-seed",
        str(drift_path),
        "--materialize-dest",
        str(dest),
    )
    assert result.returncode == 0, result.stderr
    assert drift["main_source"] == main_sha
    assert json.loads((dest / "d.json").read_text(encoding="utf-8")) == {"id": "d"}


def test_isolated_replay_mirror_only_refuses_without_immutable_main(tmp_path: Path) -> None:
    repo, snapshot = _isolated_corpus_repo(tmp_path / "iso")

    def cli(*args: str) -> subprocess.CompletedProcess[str]:
        return _run_cli("--repo-root", repo, *args, cwd=Path(repo))

    seed_path = tmp_path / "seed.json"
    result = cli("--accepted-ref", snapshot, "--output", str(seed_path))
    assert result.returncode == 0, result.stderr
    seed = json.loads(seed_path.read_text(encoding="utf-8"))
    assert seed["main_source"] == "working-tree"
    assert seed["dispositions"]["results-data/bundles/c.json"] == "legacy_overlay"
    (Path(repo) / "results-data" / "bundles" / "c.json").write_text('{"id": "drifted"}')
    dest = tmp_path / "archive"
    result = cli(
        "--accepted-ref",
        snapshot,
        "--ledger-seed",
        str(seed_path),
        "--materialize-dest",
        str(dest),
    )
    assert result.returncode != 0
    assert "no immutable main SHA" in result.stderr
