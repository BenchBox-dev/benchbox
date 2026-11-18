from __future__ import annotations

import subprocess
import types
from pathlib import Path

import pytest
import theseus_cli

pytestmark = [pytest.mark.unit, pytest.mark.fast]

SHA = "c" * 40
LINE = "release/0.5.x"


@pytest.mark.parametrize(
    ("ref", "fetched"),
    [
        ("origin/develop", "develop"),
        (f"origin/{LINE}", LINE),
        ("origin/release/0.5", None),
        ("origin/feature/x", None),
        ("develop", None),
        ("HEAD", None),
    ],
)
def test_only_the_tracked_integration_refs_are_fetched_before_they_are_resolved(
    monkeypatch: pytest.MonkeyPatch, ref: str, fetched: str | None
) -> None:
    calls: list[tuple[str, ...]] = []

    def git(_root: Path, *args: str) -> bytes:
        calls.append(args)
        return f"{SHA}\n".encode()

    monkeypatch.setattr(theseus_cli, "_git", git)

    assert theseus_cli.resolve_commit(Path("."), ref) == SHA
    expected = [("fetch", "origin", fetched, "--quiet")] if fetched else []
    assert [call for call in calls if call[0] == "fetch"] == expected


def _gate_args(*extra: str):
    return theseus_cli._parser().parse_args(
        ["--trusted-ref", f"origin/{LINE}", "gate", *extra, "--repo", "o/r", "--pr", "7", "--head-sha", "a" * 40]
    )


def _stub_gate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict:
    seen: dict = {}
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "review.yml").write_text("policy\n", encoding="utf-8")
    for args in (
        ["init", "-q"],
        ["add", "."],
        [
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.com",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-q",
            "-m",
            "x",
        ],
    ):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    seen["sha"] = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, encoding="utf-8"
    ).stdout.strip()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(theseus_cli, "resolve_commit", lambda _root, ref: seen.setdefault("ref", ref) and seen["sha"])

    def run(_root: Path, commit: str, command: list[str], env=None) -> types.SimpleNamespace:
        seen.update(commit=commit, command=command, env=env)
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(theseus_cli, "run", run)
    return seen


def test_the_gate_of_a_line_pull_request_names_the_line_as_the_trusted_base(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _stub_gate(monkeypatch, tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "token")

    assert theseus_cli.gate(_gate_args("--base", LINE)) == 0

    assert seen["ref"] == f"origin/{LINE}"
    assert seen["commit"] == seen["sha"]
    assert seen["env"]["REVIEW_TRUSTED_BASE"] == LINE
    assert seen["env"]["GITHUB_TOKEN"] == "token"
    assert "--no-post" in seen["command"]


def test_the_gate_of_a_develop_pull_request_leaves_the_environment_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _stub_gate(monkeypatch, tmp_path)

    assert theseus_cli.gate(_gate_args()) == 0

    assert seen["env"] is None
