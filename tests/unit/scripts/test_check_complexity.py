from __future__ import annotations

import importlib.util
import subprocess
import sys
import tomllib
from datetime import date
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "_project" / "scripts" / "check_complexity.py"
PYPROJECT = REPO_ROOT / "pyproject.toml"
MAKEFILE = REPO_ROOT / "Makefile"
PR_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_complexity_under_test", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_config(tmp_path: Path, exclusion: str = "") -> Path:
    config = tmp_path / "pyproject.toml"
    exclusions_value = "" if exclusion else "exclusions = []\n"
    config.write_text(
        """
[tool.benchbox.complexity]
max_complexity = 20
warn_complexity = 12
max_exception_days = 90
""".strip()
        + "\n"
        + exclusions_value
        + exclusion,
        encoding="utf-8",
    )
    return config


def _args(module, config: Path, *extra: str):
    return module.parse_args(["--pyproject", str(config), *extra])


def _scan(module, *violations):
    return module.RuffScan(violations=tuple(violations))


def test_isolated_measurement_ignores_project_c901_per_file_ignore(tmp_path: Path, monkeypatch) -> None:
    module = _load_module()
    (tmp_path / "pyproject.toml").write_text(
        """
[tool.ruff.lint.per-file-ignores]
"sample.py" = ["C901"]
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "sample.py").write_text(
        """
def still_measured(value: int) -> int:
    if value > 2:
        return 2
    if value > 1:
        return 1
    return 0
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    scan = module._run_ruff("sample.py")

    assert scan.error is None
    assert [(item.func, item.score) for item in scan.violations] == [("still_measured", 3)]


def test_scanner_rejects_unparseable_diagnostic(monkeypatch) -> None:
    module = _load_module()
    result = subprocess.CompletedProcess(
        args=[],
        returncode=1,
        stdout="sample.py:1:1: C901 changed output\nFound 1 error.\n",
        stderr="",
    )
    monkeypatch.setattr(module.subprocess, "run", lambda *_args, **_kwargs: result)

    scan = module._run_ruff("benchbox")

    assert scan.violations == ()
    assert "unparseable stdout" in scan.error


def test_scanner_rejects_summary_count_mismatch(monkeypatch) -> None:
    module = _load_module()
    result = subprocess.CompletedProcess(
        args=[],
        returncode=1,
        stdout="sample.py:1:1: C901 `example` is too complex (3 > 1)\nFound 2 errors.\n",
        stderr="",
    )
    monkeypatch.setattr(module.subprocess, "run", lambda *_args, **_kwargs: result)

    scan = module._run_ruff("benchbox")

    assert scan.violations == ()
    assert "summary/count mismatch: summary=2, parsed=1" in scan.error


def test_scanner_preserves_valid_return_zero_behavior(monkeypatch) -> None:
    module = _load_module()
    result = subprocess.CompletedProcess(args=[], returncode=0, stdout="All checks passed!\n", stderr="")
    monkeypatch.setattr(module.subprocess, "run", lambda *_args, **_kwargs: result)

    scan = module._run_ruff("benchbox")

    assert scan.error is None
    assert scan.violations == ()


def test_advisory_scores_remain_visible_and_hard_scores_fail(tmp_path: Path, monkeypatch, capsys) -> None:
    module = _load_module()
    config = _write_config(tmp_path)
    monkeypatch.setattr(
        module,
        "_run_ruff",
        lambda _root: _scan(
            module,
            module.Violation("benchbox/example.py", 10, "advisory", 12),
            module.Violation("benchbox/example.py", 20, "hard", 21),
        ),
    )

    assert module.run(_args(module, config), today=date(2026, 8, 8)) == 1
    output = capsys.readouterr().out
    assert "Advisory (12 <= CC <= 20): 1" in output
    assert "Hard failures (CC > 20): 1" in output


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        (None, "authoritative policy file is missing"),
        ("[tool.other]\nvalue = 1\n", "missing table [tool.benchbox.complexity]"),
        ("[tool.benchbox]\ncomplexity = 'not-a-table'\n", "must define [tool.benchbox.complexity] as a table"),
        ("[tool.benchbox.complexity\n", "cannot read authoritative policy file"),
    ],
)
def test_missing_or_malformed_policy_fails_before_measurement(
    tmp_path: Path,
    monkeypatch,
    capsys,
    contents: str | None,
    message: str,
) -> None:
    module = _load_module()
    config = tmp_path / "pyproject.toml"
    if contents is not None:
        config.write_text(contents, encoding="utf-8")
    monkeypatch.setattr(module, "_run_ruff", lambda _root: pytest.fail("invalid policy must fail before Ruff"))

    assert module.run(_args(module, config, "--max-complexity", "99"), today=date(2026, 8, 8)) == 1
    assert message in capsys.readouterr().out


def test_missing_exception_policy_key_fails_closed(tmp_path: Path, monkeypatch, capsys) -> None:
    module = _load_module()
    config = tmp_path / "pyproject.toml"
    config.write_text(
        """
[tool.benchbox.complexity]
max_complexity = 20
warn_complexity = 12
max_exception_days = 90
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "_run_ruff", lambda _root: pytest.fail("invalid policy must fail before Ruff"))

    assert module.run(_args(module, config), today=date(2026, 8, 8)) == 1
    assert "complexity.exclusions is required" in capsys.readouterr().out


def test_missing_source_root_fails_before_scanner(tmp_path: Path, monkeypatch, capsys) -> None:
    module = _load_module()
    config = _write_config(tmp_path)
    monkeypatch.setattr(module, "_run_ruff", lambda _root: pytest.fail("missing source root must fail before Ruff"))

    args = _args(module, config, "--source-root", str(tmp_path / "absent"))
    assert module.run(args, today=date(2026, 8, 8)) == 1
    assert "source root is not a directory" in capsys.readouterr().out


def test_empty_authoritative_measurement_fails(tmp_path: Path, monkeypatch, capsys) -> None:
    module = _load_module()
    config = _write_config(tmp_path)
    source_root = tmp_path / "empty"
    source_root.mkdir()
    monkeypatch.setattr(module, "_run_ruff", lambda _root: _scan(module))

    args = _args(module, config, "--source-root", str(source_root))
    assert module.run(args, today=date(2026, 8, 8)) == 1
    assert "no CC > 1 functions measured" in capsys.readouterr().out


def test_valid_exception_pins_a_real_hard_score_without_hiding_advisory(tmp_path: Path, monkeypatch, capsys) -> None:
    module = _load_module()
    config = _write_config(
        tmp_path,
        """
[[tool.benchbox.complexity.exclusions]]
target = "benchbox/example.py:hard"
line = 20
score = 21
owner = "quality-governance"
rationale = "Temporary branch fan-out while the upstream parser is redesigned."
expires = "2026-09-01"
""",
    )
    monkeypatch.setattr(
        module,
        "_run_ruff",
        lambda _root: _scan(
            module,
            module.Violation("benchbox/example.py", 10, "advisory", 14),
            module.Violation("benchbox/example.py", 20, "hard", 21),
        ),
    )

    assert module.run(_args(module, config), today=date(2026, 8, 8)) == 0
    output = capsys.readouterr().out
    assert "Advisory (12 <= CC <= 20): 1" in output
    assert "Current hard exceptions: 1" in output
    assert "EXEMPT" in output


@pytest.mark.parametrize(
    ("expiry", "expected_code", "message"),
    [
        ("2026-11-06", 0, "Current hard exceptions: 1"),
        ("2026-11-07", 1, "maximum exception lifetime is 90 days"),
    ],
)
def test_exception_expiry_has_a_bounded_90_day_window(
    tmp_path: Path,
    monkeypatch,
    capsys,
    expiry: str,
    expected_code: int,
    message: str,
) -> None:
    module = _load_module()
    config = _write_config(
        tmp_path,
        f"""
[[tool.benchbox.complexity.exclusions]]
target = "benchbox/example.py:hard"
line = 20
score = 21
owner = "quality-governance"
rationale = "Boundary check for the bounded temporary-exception review window."
expires = "{expiry}"
""",
    )
    monkeypatch.setattr(
        module,
        "_run_ruff",
        lambda _root: _scan(module, module.Violation("benchbox/example.py", 20, "hard", 21)),
    )

    assert module.run(_args(module, config), today=date(2026, 8, 8)) == expected_code
    assert message in capsys.readouterr().out


@pytest.mark.parametrize(
    ("metadata", "message"),
    [
        (
            """
[[tool.benchbox.complexity.exclusions]]
target = "benchbox/example.py:hard"
line = 20
score = 21
owner = ""
rationale = "Owner is intentionally missing for the negative control."
expires = "2026-09-01"
""",
            "owner must be non-empty",
        ),
        (
            """
[[tool.benchbox.complexity.exclusions]]
target = "benchbox/example.py:hard"
line = 20
score = 21
owner = "quality-governance"
rationale = "Expired entries must stop suppressing a hard score."
expires = "2026-08-08"
""",
            "expired on 2026-08-08",
        ),
    ],
)
def test_unowned_or_expired_metadata_fails_even_in_report_mode(
    tmp_path: Path, monkeypatch, capsys, metadata: str, message: str
) -> None:
    module = _load_module()
    config = _write_config(tmp_path, metadata)
    monkeypatch.setattr(module, "_run_ruff", lambda _root: pytest.fail("invalid metadata must fail before Ruff"))

    assert module.run(_args(module, config, "--no-fail"), today=date(2026, 8, 8)) == 1
    assert message in capsys.readouterr().out


@pytest.mark.parametrize(
    ("line", "score", "message"),
    [
        (19, 21, "stale complexity exclusion"),
        (20, 22, "pinned score 22, measured 21"),
    ],
)
def test_stale_or_drifted_pin_fails_even_in_report_mode(
    tmp_path: Path, monkeypatch, capsys, line: int, score: int, message: str
) -> None:
    module = _load_module()
    config = _write_config(
        tmp_path,
        f"""
[[tool.benchbox.complexity.exclusions]]
target = "benchbox/example.py:hard"
line = {line}
score = {score}
owner = "quality-governance"
rationale = "Synthetic stale-pin negative control."
expires = "2026-09-01"
""",
    )
    monkeypatch.setattr(
        module,
        "_run_ruff",
        lambda _root: _scan(module, module.Violation("benchbox/example.py", 20, "hard", 21)),
    )

    assert module.run(_args(module, config, "--no-fail"), today=date(2026, 8, 8)) == 1
    assert message in capsys.readouterr().out


def test_exception_is_stale_when_score_no_longer_exceeds_custom_ceiling(tmp_path: Path, monkeypatch, capsys) -> None:
    module = _load_module()
    config = _write_config(
        tmp_path,
        """
[[tool.benchbox.complexity.exclusions]]
target = "benchbox/example.py:improved"
line = 20
score = 20
owner = "quality-governance"
rationale = "Synthetic resolved-exception negative control."
expires = "2026-09-01"
""",
    )
    monkeypatch.setattr(
        module,
        "_run_ruff",
        lambda _root: _scan(module, module.Violation("benchbox/example.py", 20, "improved", 20)),
    )

    assert module.run(_args(module, config), today=date(2026, 8, 8)) == 1
    assert "no longer exceeds hard ceiling 20" in capsys.readouterr().out


def test_repo_policy_pins_scope_commands_and_required_ci_together() -> None:
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    ruff = config["tool"]["ruff"]
    complexity = config["tool"]["benchbox"]["complexity"]
    makefile = MAKEFILE.read_text(encoding="utf-8")
    workflow = PR_WORKFLOW.read_text(encoding="utf-8")

    assert "_project" not in ruff["exclude"]
    assert "ruff==0.16.7" in config["dependency-groups"]["dev"]
    assert complexity["max_complexity"] == 18
    assert complexity["warn_complexity"] == 12
    assert complexity["max_exception_days"] == 90
    assert complexity["exclusions"] == []

    assert "uv run -- python _project/scripts/check_complexity.py" in makefile
    assert "\tuv run -- python scripts/check_complexity.py" not in makefile
    assert "uv run ty check --error all _project/scripts/check_complexity.py" in makefile
    assert "$(MAKE) complexity-check" in makefile
    assert "$(MAKE) quality-governance-typecheck" in makefile

    assert "id: guard-quality-governance-typecheck" in workflow
    assert "run: make quality-governance-typecheck" in workflow
    assert "id: guard-complexity-policy" in workflow
    assert "run: make complexity-check" in workflow
    assert "id: guard-duplicate-delta" in workflow
    assert "run: make duplicate-check-delta" in workflow
