from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

ROOT = Path(__file__).resolve().parents[3]
HEAD = "a" * 40


def _make_pr_ready(
    tmp_path: Path, *assignments: str, gh_output: str = "7 https://github.com/BenchBox-dev/BenchBox/pull/7"
) -> tuple[subprocess.CompletedProcess[str], list[str], dict[str, str]]:

    for name, body in (
        ("uv", '#!/bin/sh\nprintf "%s\\n" "$@" > "$RECORD/argv"\nenv | grep "^PR_ARM_" | sort > "$RECORD/env"\n'),
        ("gh", f"#!/bin/sh\necho '{gh_output}'\n"),
    ):
        shim = tmp_path / name
        shim.write_text(body)
        shim.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k not in {"PR", "HEAD", "REPO"} and not k.startswith("PR_ARM_")}
    env.update({"PATH": f"{tmp_path}{os.pathsep}{env['PATH']}", "RECORD": str(tmp_path)})
    result = subprocess.run(
        ["make", "--no-print-directory", "pr-ready", *assignments],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    argv_file, env_file = tmp_path / "argv", tmp_path / "env"
    argv = argv_file.read_text().splitlines() if argv_file.exists() else []
    pairs = [line.split("=", 1) for line in env_file.read_text().splitlines()] if env_file.exists() else []
    return result, argv, dict(pairs)


def test_pr_ready_without_evidence_arms_through_pr_arm(tmp_path: Path) -> None:

    for name, assignments, expected in (
        ("number", ("PR=7",), "7"),
        ("url", ("URL=https://github.com/BenchBox-dev/BenchBox/pull/7",), "7"),
        ("hostile", ("PR=7 --pr 8",), "7 --pr 8"),
    ):
        record = tmp_path / name
        record.mkdir()
        result, argv, env = _make_pr_ready(record, *assignments, f"HEAD={HEAD}")
        assert result.returncode == 0, (name, result.stderr)
        assert argv == ["run", "--", "python", "scripts/pr_arm.py"], name
        assert (env["PR_ARM_PR"], env["PR_ARM_HEAD"], env["PR_ARM_REPO"]) == (expected, HEAD, "BenchBox-dev/BenchBox")


def test_pr_ready_refuses_a_url_that_names_a_pr_in_another_repository(tmp_path: Path) -> None:
    result, argv, env = _make_pr_ready(
        tmp_path,
        "URL=https://github.com/other-org/other-repo/pull/7",
        f"HEAD={HEAD}",
        gh_output="7 https://github.com/other-org/other-repo/pull/7",
    )
    assert result.returncode == 2 and "outside BenchBox-dev/BenchBox" in result.stderr
    assert argv == [] and env == {}


def test_pr_ready_with_evidence_or_batch_keeps_the_readiness_transaction(tmp_path: Path) -> None:
    for name, assignments in (
        ("evidence", ("EVIDENCE=evidence.json",)),
        ("both", ("EVIDENCE=evidence.json", "BATCH=1")),
    ):
        record = tmp_path / name
        record.mkdir()
        result, argv, env = _make_pr_ready(record, "PR=7", f"HEAD={HEAD}", *assignments)
        assert result.returncode == 0, (name, result.stderr)
        assert "scripts/pr_landing.py" in argv and "scripts/pr_arm.py" not in argv, name
        assert env == {}, name

    record = tmp_path / "batch-only"
    record.mkdir()
    result, argv, env = _make_pr_ready(record, "PR=7", f"HEAD={HEAD}", "BATCH=1")
    assert result.returncode == 0, result.stderr
    assert argv == ["run", "--", "python", "scripts/pr_arm.py"]
    assert (env["PR_ARM_PR"], env["PR_ARM_REQUIRE_BATCH"]) == ("7", "1")

    record = tmp_path / "ordinary"
    record.mkdir()
    _, _, env = _make_pr_ready(record, "PR=7", f"HEAD={HEAD}")
    assert env["PR_ARM_REQUIRE_BATCH"] == "0"


def test_pr_ready_still_requires_a_pr_and_a_head(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    missing_pr, _, _ = _make_pr_ready(tmp_path / "a", f"HEAD={HEAD}")
    missing_head, _, _ = _make_pr_ready(tmp_path / "b", "PR=7")
    assert missing_pr.returncode == 2 and "PR or URL is required" in missing_pr.stderr
    assert missing_head.returncode == 2 and "HEAD is required" in missing_head.stderr
