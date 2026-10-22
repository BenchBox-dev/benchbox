from __future__ import annotations

import json
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest
from report_dead_comment_exceptions import dead_exceptions, main, registration

pytestmark = [pytest.mark.unit, pytest.mark.medium]

POLICY_PATH = "quality/comment-policy.json"


def entry(path: str, text: str = "# noqa: F401", **changes: str) -> dict:
    return {
        "path": path,
        "symbol": "",
        "text": text,
        "kind": "directive",
        "consumer": "pyproject.toml",
        "necessity": "The import is an intentionally exported compatibility name.",
        "alternative": "Replacing the public export would break callers.",
        "owner": "maintainers",
        "removal": "Remove when the export is retired.",
        "expires": (date.today() + timedelta(days=90)).isoformat(),
        **changes,
    }


def repo(tmp_path: Path, exceptions: list[dict]) -> dict:
    policy = {"version": 1, "external": [], "completed": [], "exceptions": exceptions}
    (tmp_path / "quality").mkdir()
    (tmp_path / POLICY_PATH).write_text(json.dumps(policy), encoding="utf-8")
    (tmp_path / "live.py").write_text("import os  # noqa: F401\n", encoding="utf-8")
    (tmp_path / "changed.py").write_text("import os  # noqa: F401\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "live.py", "changed.py", POLICY_PATH], check=True)
    return policy


def test_reports_only_exceptions_without_a_current_finding(tmp_path: Path) -> None:
    policy = repo(
        tmp_path,
        [
            entry("live.py"),
            entry("changed.py", "# noqa: E501"),
            entry("gone.py"),
        ],
    )
    dead = dead_exceptions(tmp_path, policy)
    assert [(e["path"], reason) for e, reason in dead] == [
        ("changed.py", "no current finding at path and digest"),
        ("gone.py", "path missing"),
    ]


def test_fixture_with_mismatched_digest_is_reported(tmp_path: Path) -> None:
    policy = repo(tmp_path, [])
    stale = entry("live.py", "# noqa: F401", kind="fixture", payload="sha256:" + "0" * 64, finding_kind="comment")
    policy["exceptions"] = [stale]
    assert [e["path"] for e, _ in dead_exceptions(tmp_path, policy)] == ["live.py"]


def test_cli_exits_zero_and_leaves_policy_byte_identical(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo(tmp_path, [entry("live.py"), entry("gone.py")])
    before = (tmp_path / POLICY_PATH).read_bytes()
    assert main(["--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "gone.py\tdirective" in out
    assert "live.py\t" not in out
    assert "dead-exceptions: 1 of 2" in out
    assert (tmp_path / POLICY_PATH).read_bytes() == before


def test_report_is_not_wired_into_ci_or_hooks() -> None:
    root = Path(__file__).resolve().parents[3]
    wired = [
        str(path.relative_to(root))
        for path in [
            *root.glob(".github/**/*.yml"),
            *root.glob(".github/**/*.yaml"),
            root / ".pre-commit-config.yaml",
        ]
        if path.is_file()
        and (
            "report_dead_comment_exceptions" in path.read_text() or "comment-policy-dead-exceptions" in path.read_text()
        )
    ]
    assert wired == []
    makefile = (root / "Makefile").read_text()
    for line in makefile.splitlines():
        if line.startswith("\t") and "comment-policy-dead-exceptions" in line:
            raise AssertionError(line)


def test_expired_exception_with_live_finding_is_reported_as_expired_not_dead(tmp_path: Path) -> None:
    expired = entry("live.py", expires=(date.today() - timedelta(days=1)).isoformat())
    policy = repo(tmp_path, [expired])
    assert [(e["path"], reason) for e, reason in dead_exceptions(tmp_path, policy)] == [("live.py", "expired but live")]


def commit_policy(tmp_path: Path, policy: dict, message: str) -> None:
    (tmp_path / POLICY_PATH).write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", POLICY_PATH], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=F",
            "-c",
            "user.email=f@example.invalid",
            "commit",
            "-qm",
            message,
        ],
        check=True,
    )


def test_registration_names_the_commit_that_added_the_exact_entry(tmp_path: Path) -> None:
    first, second = entry("live.py"), entry("changed.py")
    repo(tmp_path, [])
    commit_policy(tmp_path, {"version": 1, "external": [], "completed": [], "exceptions": [first]}, "add first (#11)")
    commit_policy(
        tmp_path, {"version": 1, "external": [], "completed": [], "exceptions": [first, second]}, "add second (#12)"
    )
    assert registration(tmp_path, first).endswith("PR #11")
    assert registration(tmp_path, second).endswith("PR #12")


def test_registration_handles_quotes_and_flags_ambiguous_identity(tmp_path: Path) -> None:
    quoted = entry("live.py", 'x = "a\\b"  # noqa: F401')
    twin_a, twin_b = entry("changed.py"), entry("changed.py")
    repo(tmp_path, [])
    commit_policy(
        tmp_path,
        {"version": 1, "external": [], "completed": [], "exceptions": [quoted, twin_a, twin_b]},
        "add (#13)",
    )
    assert registration(tmp_path, quoted).endswith("PR #13")
    assert registration(tmp_path, twin_a) == "registered: ambiguous"


def test_registration_matches_non_ascii_entries_as_stored(tmp_path: Path) -> None:
    unicode_entry = entry("live.py", "# \u00a9 BenchBox \u2122 \u6f22")
    repo(tmp_path, [])
    commit_policy(
        tmp_path,
        {"version": 1, "external": [], "completed": [], "exceptions": []},
        "empty (#20)",
    )
    policy = {"version": 1, "external": [], "completed": [], "exceptions": [unicode_entry]}
    (tmp_path / POLICY_PATH).write_text(json.dumps(policy, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", POLICY_PATH], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=F",
            "-c",
            "user.email=f@example.invalid",
            "commit",
            "-qm",
            "add (#21)",
        ],
        check=True,
    )
    assert registration(tmp_path, unicode_entry).endswith("PR #21")
