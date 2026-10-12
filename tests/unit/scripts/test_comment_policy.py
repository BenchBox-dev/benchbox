from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import check_comment_policy
import pytest
import run_comment_policy as policy_runner
import yaml
from check_comment_policy import (
    allowed,
    check_ratchet,
    expired_policy_findings,
    introduced,
    load_policy,
    main,
    scan_sources,
    source_paths,
)
from comment_payloads import stdin_language
from comment_syntax import Finding, javascript_requests, language, python_findings, scan, source_language, sql_comments
from run_comment_policy import TRUSTED_FILES, parser_environment, resolve_base

pytestmark = [pytest.mark.unit, pytest.mark.medium]

ROOT = Path(__file__).resolve().parents[3]
EMPTY_POLICY = {"version": 1, "external": [], "completed": [], "exceptions": []}


def policy(**changes: object) -> dict:
    return {**deepcopy(EMPTY_POLICY), **changes}


@pytest.mark.parametrize(
    "source,kind,symbol",
    [
        ('"module prose"\n', "docstring", ""),
        ('class C:\n    "class prose"\n', "docstring", "C"),
        ('async def f():\n    "function prose"\n', "docstring", "f"),
        ('def f():\n    x = 1\n    "inert prose"\n', "docstring", "f"),
        ('def f():\n    "a" "b"\n', "docstring", "f"),
        ('f.__doc__ = "prose"\n', "runtime-docstring", ""),
        ('__doc__ = "prose"\n', "runtime-docstring", ""),
        ('f.__doc__: str = "prose"\n', "runtime-docstring", ""),
        ('setattr(f, "__doc__", "prose")\n', "runtime-docstring", ""),
        ('f.__dict__["__doc__"] = "prose"\n', "runtime-docstring", ""),
        ("def f():\n    return 1 # explanation\n", "comment", "f"),
    ],
)
def test_python_prohibited_forms(source: str, kind: str, symbol: str) -> None:
    findings = python_findings("a.py", source)
    assert [(f.kind, f.symbol) for f in findings] == [(kind, symbol)]


def test_python_strings_help_and_reads_are_data() -> None:
    assert not python_findings("a.py", 'help = "# useful help"\nx = f.__doc__\n')


def test_runtime_assignment_payload_is_part_of_identity() -> None:
    before = python_findings("a.py", 'f.__doc__ = "before"')
    after = python_findings("a.py", 'f.__doc__ = "after"')
    assert introduced(after, before, []) == after


@pytest.mark.parametrize("source", ["def broken(", '"unterminated', "if True:\n"])
def test_python_syntax_failure_is_visible(source: str) -> None:
    assert scan("a.py", source, "python")[0].kind == "coverage-error"


@pytest.mark.parametrize(
    "source,expected",
    [
        ("SELECT '-- payload', \"-- name\", `#name`, [--name], $$--data$$; -- comment", ["-- comment"]),
        ("SELECT $tag$/*payload*/$tag$; /* outer /* inner */ end */", ["/* outer /* inner */ end */"]),
        ("SELECT 1 # comment", ["# comment"]),
        ("SELECT 'it''s -- data';", []),
    ],
)
def test_sql_literals_and_nested_comments(source: str, expected: list[str]) -> None:
    assert [text for _, text in sql_comments(source, "tsql")] == expected


@pytest.mark.parametrize("source", ["SELECT 'unterminated", "SELECT $$unterminated", "/* unterminated"])
def test_sql_unterminated_constructs_fail(source: str) -> None:
    assert scan("a.sql", source, "sql")[0].kind == "coverage-error"


@pytest.mark.parametrize(
    "lang,source,text",
    [
        ("bash", "cat <<'EOF'\n# payload\nEOF\necho x#data\n# explanation\n", "# explanation"),
        ("yaml", "value: |\n  # payload\n# explanation\n", "# explanation"),
        ("toml", 'value = "# payload"\n# explanation\n', "# explanation"),
        ("css", 'a { content: "/*payload*/"; } /* explanation */', "/* explanation */"),
        ("html", '<p title="<!--payload-->">value</p><!-- explanation -->', "<!-- explanation -->"),
        ("make", "name := value\n# explanation\n", "# explanation"),
        ("docker", "FROM python:3.11\n# explanation\n", "# explanation"),
    ],
)
def test_lexers_preserve_data_and_find_comments(lang: str, source: str, text: str) -> None:
    findings = scan("a", source, lang)
    assert [(f.kind, f.text) for f in findings] == [("comment", text)]


def test_native_request_and_payload_response_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    source = '<script>const x = "// data"; // explanation\n</script>'
    requests = javascript_requests("a.html", source, "html")
    assert len(requests) == 1

    def native_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        assert command[0] == "node"
        assert json.loads(str(kwargs["input"])) == requests
        rows = [
            {"line": 1, "kind": "comment", "text": "// explanation", "symbol": "f"},
            {"line": 1, "kind": "payload", "text": "# executable prose", "symbol": "f", "language": "python"},
        ]
        return subprocess.CompletedProcess(command, 0, json.dumps(dict.fromkeys(requests, rows)))

    monkeypatch.setattr("check_comment_policy.subprocess.run", native_run)
    findings = scan_sources(ROOT, {"a.html": source.encode()}, policy())
    assert [(f.kind, f.text, f.symbol) for f in findings] == [
        ("comment", "// explanation", "script:0:f"),
        ("comment", "# executable prose", "script:0:f:payload:"),
    ]


def test_yaml_run_and_notebook_cells() -> None:
    findings = scan("ci.yml", "run: |\n  echo ok\n  # explanation\n", "yaml")
    assert [(f.kind, f.text) for f in findings] == [("comment", "# explanation")]
    source = json.dumps({"cells": [{"id": "stable", "cell_type": "code", "source": ["# explanation\n"]}]})
    assert scan("a.ipynb", source, "notebook")[0].symbol == "cell:stable:"


def test_executable_examples_and_unknown_languages() -> None:
    assert scan("docs/a.md", "```python\n# explanation\n```\n", "examples")[0].line == 2
    assert scan("docs/a.md", "```unknown-runtime\ncode\n```\n", "examples")[0].kind == "coverage-error"
    assert not scan("docs/a.md", "```text\n# prose\n```\n", "examples")


def exception(text: str = "# noqa: F401", **changes: str) -> dict:
    return {
        "path": "a.py",
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


@pytest.mark.parametrize("text", ["# noqa: F401 - explanation", "# noqa", "# type: ignore", "# why this is needed"])
def test_directive_grammar_rejects_narrative_and_blanket_suppressions(text: str) -> None:
    with pytest.raises(ValueError):
        load_policy(json.dumps(policy(exceptions=[exception(text)])).encode())


def test_valid_directive_needs_exact_registry_identity() -> None:
    registered = load_policy(json.dumps(policy(exceptions=[exception()])).encode())
    finding = Finding("a.py", 20, "comment", "# noqa: F401")
    assert allowed(finding, registered, "")
    assert not allowed(finding, policy(), "")
    assert not allowed(Finding("other.py", 20, "comment", finding.text), registered, "")
    assert not allowed(Finding("a.py", 20, "comment", finding.text, "different"), registered, "")


def test_expired_directive_is_reported_and_does_not_allow_source() -> None:
    registered = load_policy(json.dumps(policy(exceptions=[exception(expires="2000-01-01")])).encode())
    finding = Finding("a.py", 1, "comment", "# noqa: F401")
    policy_findings = expired_policy_findings(registered)
    assert json.loads(json.dumps(registered)) == registered
    assert [finding.kind for finding in policy_findings] == ["policy-error"]
    assert check_comment_policy.exit_status("strict", registered, policy_findings) == 1
    assert check_comment_policy.exit_status("report", registered, policy_findings) == 0
    assert check_comment_policy.exit_status("transition", policy(enforcement="advisory"), policy_findings) == 0
    assert (
        check_comment_policy.exit_status(
            "transition", policy(enforcement="advisory"), policy_findings, policy_owner_failure=True
        )
        == 1
    )
    assert introduced(policy_findings, policy_findings, []) == []
    assert not allowed(finding, registered, "# noqa: F401\n")


def test_malformed_exception_still_fails_configuration_validation() -> None:
    with pytest.raises(ValueError):
        load_policy(json.dumps(policy(exceptions=[exception(kind="explanation")])).encode())


@pytest.mark.parametrize(
    "line,text,accepted",
    [
        (1, "#!/usr/bin/env python3", True),
        (2, "#!/usr/bin/env python3", False),
        (1, "#!/usr/bin/env python3 # explanation", False),
        (1, "# coding: utf-8", False),
        (1, "# coding: latin-1", True),
        (3, "# coding: latin-1", False),
    ],
)
def test_structural_directive_positions(line: int, text: str, accepted: bool) -> None:
    assert allowed(Finding("a.py", line, "comment", text), policy(), "\n") is accepted


def test_delta_compares_exact_multisets_and_completed_scopes() -> None:
    old = Finding("a.py", 1, "comment", "# old", "f")
    moved = Finding("a.py", 100, "comment", "# old", "f")
    replacement = Finding("a.py", 1, "comment", "# new", "f")
    assert not introduced([moved], [old], [])
    assert introduced([old, old], [old], []) == [old]
    assert introduced([replacement], [old], []) == [replacement]
    assert introduced([moved], [old], ["a.py"]) == [moved]
    assert introduced([Finding("a.py", 1, "comment", "# old", "g")], [old], [])


def test_policy_cannot_weaken_completed_or_external_scopes() -> None:
    with pytest.raises(ValueError):
        check_ratchet(policy(), policy(completed=["a.py"]))
    entry = {"path": "benchbox/", "owner": "fake", "provenance": "a.md"}
    with pytest.raises(ValueError):
        check_ratchet(policy(external=[entry]), policy())


def test_ratchet_allows_same_path_wheel_version_bump_without_expanding_exclusions() -> None:
    base_entry = {
        "path": "_project/scripts/vendor/todo_db-0.8.1-py3-none-any.whl",
        "owner": "todo-db",
        "provenance": "_project/scripts/pyproject.toml",
    }
    bumped_entry = {
        "path": "_project/scripts/vendor/todo_db-0.9.1-py3-none-any.whl",
        "owner": "todo-db",
        "provenance": "_project/scripts/pyproject.toml",
    }
    check_ratchet(policy(external=[bumped_entry]), policy(external=[base_entry]))
    with pytest.raises(ValueError, match="cannot add or expand external exclusions"):
        check_ratchet(policy(external=[base_entry, bumped_entry]), policy(external=[base_entry]))
    other_entry = {
        "path": "_project/scripts/vendor/other_pkg-1.0.0-py3-none-any.whl",
        "owner": "todo-db",
        "provenance": "_project/scripts/pyproject.toml",
    }
    with pytest.raises(ValueError, match="cannot add or expand external exclusions"):
        check_ratchet(policy(external=[other_entry]), policy(external=[base_entry]))
    diff_dir_entry = {
        "path": "_project/vendor/todo_db-0.9.1-py3-none-any.whl",
        "owner": "todo-db",
        "provenance": "_project/scripts/pyproject.toml",
    }
    with pytest.raises(ValueError, match="cannot add or expand external exclusions"):
        check_ratchet(policy(external=[diff_dir_entry]), policy(external=[base_entry]))
    diff_prov_entry = {
        "path": "_project/scripts/vendor/todo_db-0.9.1-py3-none-any.whl",
        "owner": "todo-db",
        "provenance": "_project/scripts/other_pyproject.toml",
    }
    with pytest.raises(ValueError, match="cannot add or expand external exclusions"):
        check_ratchet(policy(external=[diff_prov_entry]), policy(external=[base_entry]))


def git_repo(tmp_path: Path, source: str) -> str:
    (tmp_path / "quality").mkdir()
    (tmp_path / "quality/comment-policy.json").write_text(json.dumps(policy()), encoding="utf-8")
    (tmp_path / "a.py").write_text(source, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "a.py", "quality/comment-policy.json"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    return subprocess.check_output(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True).strip()


def test_transition_worktree_and_staged_content(tmp_path: Path) -> None:
    base = git_repo(tmp_path, "# old\nx = 1\n")
    args = ["--root", str(tmp_path), "--mode", "transition", "--base", base]
    assert main(args) == 0
    (tmp_path / "a.py").write_text("# new\nx = 1\n", encoding="utf-8")
    assert main(args) == 1
    assert main([*args, "--staged"]) == 0
    subprocess.run(["git", "-C", str(tmp_path), "add", "a.py"], check=True)
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert main([*args, "--staged"]) == 1


def test_expired_policy_fails_owner_transition_but_not_inherited_advisory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    git_repo(tmp_path, "# noqa: F401\n")
    advisory_policy = policy(enforcement="advisory")
    (tmp_path / "quality/comment-policy.json").write_text(json.dumps(advisory_policy), encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "quality/comment-policy.json"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=F",
            "-c",
            "user.email=f@e.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "advisory",
        ],
        check=True,
    )
    advisory_base = subprocess.check_output(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True).strip()
    expired = policy(enforcement="advisory", exceptions=[exception(consumer="a.py", expires="2000-01-01")])
    (tmp_path / "quality/comment-policy.json").write_text(json.dumps(expired), encoding="utf-8")
    args = ["--root", str(tmp_path), "--mode", "transition", "--base", advisory_base]
    assert main(args) == 1
    assert "quality/comment-policy.json:1: CP policy-error" in capsys.readouterr().out
    subprocess.run(["git", "-C", str(tmp_path), "add", "quality/comment-policy.json"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=F",
            "-c",
            "user.email=f@e.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "expired",
        ],
        check=True,
    )
    inherited_base = subprocess.check_output(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True).strip()
    (tmp_path / "a.py").write_text("# noqa: F401\nx = 1\n", encoding="utf-8")
    assert main(["--root", str(tmp_path), "--mode", "transition", "--base", inherited_base]) == 0
    assert "configuration or parser failure" not in capsys.readouterr().err


def test_enforcement_value_is_validated_and_defaults_to_blocking() -> None:
    assert load_policy(json.dumps(policy()).encode()).get("enforcement", "blocking") == "blocking"
    assert load_policy(json.dumps(policy(enforcement="advisory")).encode())["enforcement"] == "advisory"
    with pytest.raises(ValueError, match="advisory or blocking"):
        load_policy(json.dumps(policy(enforcement="warn")).encode())
    assert load_policy((ROOT / "quality/comment-policy.json").read_bytes())["enforcement"] in {"advisory", "blocking"}
    for malformed in ([], {}, 1, None):
        with pytest.raises(ValueError, match="advisory or blocking"):
            load_policy(json.dumps(policy(enforcement=malformed)).encode())


def test_enforcement_can_be_tightened_but_never_relaxed() -> None:
    check_ratchet(policy(enforcement="blocking"), policy(enforcement="advisory"))
    check_ratchet(policy(enforcement="advisory"), policy(enforcement="advisory"))
    check_ratchet(policy(enforcement="blocking"), policy())
    with pytest.raises(ValueError, match="relaxed"):
        check_ratchet(policy(enforcement="advisory"), policy(enforcement="blocking"))
    with pytest.raises(ValueError, match="relaxed"):
        check_ratchet(policy(enforcement="advisory"), policy())


def test_advisory_enforcement_reports_findings_without_failing_the_comparison(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = git_repo(tmp_path, "x = 1\n")
    args = ["--root", str(tmp_path), "--mode", "transition", "--base", base]
    (tmp_path / "a.py").write_text("# new\nx = 1\n", encoding="utf-8")
    assert main(args) == 1
    (tmp_path / "quality/comment-policy.json").write_text(json.dumps(policy(enforcement="advisory")), encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "quality/comment-policy.json"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=F",
            "-c",
            "user.email=f@e.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "advisory",
        ],
        check=True,
    )
    advisory_base = subprocess.check_output(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True).strip()
    capsys.readouterr()
    advisory = ["--root", str(tmp_path), "--mode", "transition", "--base", advisory_base]
    assert main(advisory) == 0
    out = capsys.readouterr().out
    assert "a.py:1: CP comment" in out
    assert "enforcement is advisory" in out
    assert main(["--root", str(tmp_path), "--mode", "strict"]) == 1
    assert main(["--root", str(tmp_path), "--mode", "transition", "--base", "f" * 40]) == 2


def test_advisory_annotations_escape_path_and_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    printed: list[str] = []
    monkeypatch.setattr("builtins.print", lambda *args, **_: printed.append(" ".join(map(str, args))))
    finding = Finding("dir,x::y%z.py", 7, "comment", "# 100%\r::set-output name=a::b")
    assert check_comment_policy.exit_status("transition", policy(enforcement="advisory"), [finding]) == 0
    annotation = next(line for line in printed if line.startswith("::warning"))
    assert annotation.startswith("::warning file=dir%2Cx%3A%3Ay%25z.py,line=7::")
    assert "\r" not in annotation and "set-output" not in annotation


def commit_policy(tmp_path: Path, enforcement: str) -> str:
    (tmp_path / "quality/comment-policy.json").write_text(json.dumps(policy(enforcement=enforcement)), encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A", "quality/comment-policy.json"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=F",
            "-c",
            "user.email=f@e.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            enforcement,
            "--allow-empty",
        ],
        check=True,
    )
    return subprocess.check_output(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True).strip()


def test_advisory_reports_unanalyzable_input_but_still_returns_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    git_repo(tmp_path, "x = 1\n")
    base = commit_policy(tmp_path, "advisory")
    (tmp_path / "a.py").write_text("def broken(\n", encoding="utf-8")
    capsys.readouterr()
    assert main(["--root", str(tmp_path), "--mode", "transition", "--base", base]) == 0
    out = capsys.readouterr().out
    assert "CP coverage-error" in out
    assert "(1 are inputs the checker could not analyze)" in out


def test_flipping_to_blocking_does_not_block_itself_but_the_next_change_is(tmp_path: Path) -> None:
    git_repo(tmp_path, "x = 1\n")
    advisory_base = commit_policy(tmp_path, "advisory")
    (tmp_path / "quality/comment-policy.json").write_text(json.dumps(policy(enforcement="blocking")), encoding="utf-8")
    (tmp_path / "a.py").write_text("# new\nx = 1\n", encoding="utf-8")
    assert main(["--root", str(tmp_path), "--mode", "transition", "--base", advisory_base]) == 0
    subprocess.run(["git", "-C", str(tmp_path), "add", "a.py", "quality/comment-policy.json"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=F",
            "-c",
            "user.email=f@e.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "flip",
        ],
        check=True,
    )
    blocking_base = subprocess.check_output(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True).strip()
    (tmp_path / "a.py").write_text("# new\n# another\nx = 1\n", encoding="utf-8")
    assert main(["--root", str(tmp_path), "--mode", "transition", "--base", blocking_base]) == 1
    assert main(["--root", str(tmp_path), "--mode", "report"]) == 0


def test_hostile_paths_cannot_start_a_workflow_command_in_the_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    git_repo(tmp_path, "x = 1\n")
    base = commit_policy(tmp_path, "advisory")
    hostile = "::add-mask::secret.py"
    (tmp_path / hostile).write_text("# new\n", encoding="utf-8")
    (tmp_path / "##[stop-commands]hidden.py").write_text("# ##[add-mask]comment-policy\n", encoding="utf-8")
    newline_name = "one.py\n::stop-commands::token\ntwo.py"
    try:
        (tmp_path / newline_name).write_text("# new\n", encoding="utf-8")
    except OSError:
        newline_name = ""
    capsys.readouterr()
    assert main(["--root", str(tmp_path), "--mode", "transition", "--base", base]) == 0
    output = capsys.readouterr().out
    lines = output.splitlines()
    assert "##[" not in output
    assert not [line for line in lines if line.lstrip().startswith("::") and not line.startswith("::warning ")]
    assert not any(line.lstrip().startswith(("::add-mask", "::stop-commands")) for line in lines)
    if newline_name:
        assert not any(line.strip() == "::stop-commands::token" for line in lines)


def test_legacy_command_markers_are_neutralized_in_text_and_annotations() -> None:
    for value in ("# ##[add-mask]comment-policy", "scripts/##[stop-commands]hidden.py"):
        assert "##[" not in check_comment_policy.plain_text(value)
        assert "##[" not in policy_runner.plain_text(value)
        assert "##[" not in check_comment_policy.annotation_text(value)
        assert "##[" not in check_comment_policy.annotation_text(value, property_value=True)
    assert policy_runner.plain_text("::stop-commands::x").startswith("\\::")
    assert policy_runner.plain_text("line one\n::add-mask::x") == "line one\\x0a::add-mask::x"


@pytest.mark.parametrize("fails", [False, True])
def test_native_test_output_runs_between_stop_and_resume_tokens(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], fails: bool, tmp_path: Path
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    seen: list[str] = []

    def native_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        before = capsys.readouterr().out.splitlines()
        assert len(before) == 1 and before[0].startswith("::stop-commands::")
        seen.append(before[0])
        assert kwargs["stderr"] is subprocess.STDOUT
        print("child output")
        if fails:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(policy_runner.subprocess, "run", native_run)
    if fails:
        with pytest.raises(subprocess.CalledProcessError):
            policy_runner.run_native_tests(tmp_path, tmp_path, {})
    else:
        policy_runner.run_native_tests(tmp_path, tmp_path, {})
    after = capsys.readouterr().out.splitlines()
    token = seen[0].removeprefix("::stop-commands::")
    assert len(token) == 32
    assert after == ["child output", f"::{token}::"]


def test_comment_policy_job_sets_up_every_tool_before_the_candidate_checkout() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["comment-policy"]["steps"]
    uses = [str(step.get("uses", "")).split("@")[0] for step in steps]
    checkout = uses.index("actions/checkout")
    for tool in ("actions/setup-python", "astral-sh/setup-uv", "actions/setup-node"):
        assert uses.index(tool) < checkout, f"{tool} must run before the candidate checkout"
    assert checkout == max(index for index, name in enumerate(uses) if name) and steps[checkout + 1]["name"].startswith(
        "Enforce"
    )


def test_comment_policy_job_does_not_let_setup_uv_scan_the_candidate_checkout() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["comment-policy"]["steps"]
    uv = next(step for step in steps if str(step.get("uses", "")).startswith("astral-sh/setup-uv@"))
    assert uv["with"] == {
        "enable-cache": False,
        "working-directory": "${{ runner.temp }}",
        "ignore-empty-workdir": True,
    }


def test_native_test_output_is_not_wrapped_outside_github_actions(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(
        policy_runner.subprocess, "run", lambda command, **kwargs: subprocess.CompletedProcess(command, 0)
    )
    policy_runner.run_native_tests(tmp_path, tmp_path, {})
    assert capsys.readouterr().out == ""


def test_candidate_cannot_relax_blocking_enforcement_in_a_comparison(tmp_path: Path) -> None:
    (tmp_path / "quality").mkdir()
    (tmp_path / "quality/comment-policy.json").write_text(json.dumps(policy(enforcement="blocking")), encoding="utf-8")
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "a.py", "quality/comment-policy.json"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=F",
            "-c",
            "user.email=f@e.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "blocking",
        ],
        check=True,
    )
    base = subprocess.check_output(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True).strip()
    (tmp_path / "quality/comment-policy.json").write_text(json.dumps(policy(enforcement="advisory")), encoding="utf-8")
    (tmp_path / "a.py").write_text("# new\nx = 1\n", encoding="utf-8")
    assert main(["--root", str(tmp_path), "--mode", "transition", "--base", base]) == 2


def test_new_exception_cannot_self_authorize_source(tmp_path: Path) -> None:
    base = git_repo(tmp_path, "x = 1\n")
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / "quality/comment-policy.json").write_text(
        json.dumps(policy(exceptions=[exception()])), encoding="utf-8"
    )
    (tmp_path / "a.py").write_text("x = 1 # noqa: F401\n", encoding="utf-8")
    assert main(["--root", str(tmp_path), "--mode", "transition", "--base", base]) == 1


def test_missing_base_and_unpinned_bootstrap_fail(tmp_path: Path) -> None:
    base = git_repo(tmp_path, "x = 1\n")
    assert main(["--root", str(tmp_path), "--mode", "transition"]) == 2
    assert main(["--root", str(tmp_path), "--mode", "transition", "--base", "f" * 40]) == 2
    assert main(["--root", str(tmp_path), "--mode", "transition", "--base", base, "--bootstrap"]) == 2


def test_bootstrap_base_is_refused_when_a_policy_registry_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pin = plain_repo(tmp_path, "x = 1\n")
    (tmp_path / "quality").mkdir()
    (tmp_path / "quality/comment-policy.json").write_text("{}", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "quality/comment-policy.json"], check=True)
    with_registry = commit_change(tmp_path, "x = 2\n")
    monkeypatch.setattr("check_comment_policy.BOOTSTRAP_BASE", pin)
    monkeypatch.setattr("run_comment_policy.BOOTSTRAP_BASE", pin)
    assert not check_comment_policy.bootstrap_base_allowed(tmp_path, with_registry)
    assert not policy_runner.bootstrap_base_allowed(tmp_path, with_registry)


def git_run(root: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def merge_commit_repo(tmp_path: Path) -> tuple[str, str, str]:
    event_base = plain_repo(tmp_path, "x = 1\n")
    git_run(tmp_path, "checkout", "-q", "-b", "pr")
    (tmp_path / "pr.py").write_text("y = 1\n", encoding="utf-8")
    git_run(tmp_path, "add", "pr.py")
    git_run(tmp_path, "commit", "-qm", "pr")
    pr_head = git_run(tmp_path, "rev-parse", "HEAD")
    git_run(tmp_path, "checkout", "-q", "-B", "target", event_base)
    (tmp_path / "target.py").write_text("z = 1\n", encoding="utf-8")
    git_run(tmp_path, "add", "target.py")
    git_run(tmp_path, "commit", "-qm", "target moved on")
    target_tip = git_run(tmp_path, "rev-parse", "HEAD")
    git_run(tmp_path, "checkout", "-q", "--detach", target_tip)
    git_run(tmp_path, "merge", "--no-ff", "-qm", "merge ref", pr_head)
    return event_base, target_tip, pr_head


def test_ci_comparison_base_is_the_merge_target_not_the_stale_event_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    event_base, target_tip, pr_head = merge_commit_repo(tmp_path)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "merge_group")
    assert policy_runner.comparison_base(tmp_path, event_base) == event_base
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    assert policy_runner.comparison_base(tmp_path, event_base) == target_tip
    with pytest.raises(ValueError, match="not an ancestor"):
        policy_runner.comparison_base(tmp_path, pr_head)
    git_run(tmp_path, "checkout", "-q", "--detach", target_tip)
    assert policy_runner.comparison_base(tmp_path, event_base) == event_base


def test_local_runs_keep_the_requested_base_even_on_a_merge_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    event_base, _target_tip, _pr_head = merge_commit_repo(tmp_path)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    assert policy_runner.comparison_base(tmp_path, event_base) == event_base


def test_checker_command_compares_against_the_given_base(tmp_path: Path) -> None:
    command = policy_runner.checker_command(Path("python"), tmp_path, tmp_path, "c" * 40, bootstrap=True, staged=False)
    assert command[command.index("--base") + 1] == "c" * 40
    assert "--bootstrap" in command
    assert "--staged" not in command


def test_local_default_base_is_the_branch_point_when_develop_moved_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    branch_point = git_repo(tmp_path, "x = 1\n")
    subprocess.run(["git", "-C", str(tmp_path), "branch", "-q", "develop-tip"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "checkout", "-q", "develop-tip"], check=True)
    develop_tip = commit_change(tmp_path, "x = 2\n")
    subprocess.run(["git", "-C", str(tmp_path), "update-ref", "refs/remotes/origin/develop", develop_tip], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "checkout", "-q", "-B", "feature", branch_point], check=True)
    feature_head = commit_change(tmp_path, "x = 3\n")
    assert resolve_base(tmp_path, None) == branch_point
    assert resolve_base(tmp_path, feature_head) == feature_head
    with pytest.raises(subprocess.CalledProcessError):
        resolve_base(tmp_path, develop_tip)


def commit_change(tmp_path: Path, source: str) -> str:
    (tmp_path / "a.py").write_text(source, encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "a.py"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "next",
        ],
        check=True,
    )
    return subprocess.check_output(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True).strip()


def plain_repo(tmp_path: Path, source: str) -> str:
    (tmp_path / "a.py").write_text(source, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return commit_change(tmp_path, source)


def test_bootstrap_base_allows_only_descendants_of_the_initial_rollout_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pin = plain_repo(tmp_path, "x = 1\n")
    descendant = commit_change(tmp_path, "x = 2\n")
    monkeypatch.setattr("check_comment_policy.BOOTSTRAP_BASE", pin)
    monkeypatch.setattr("run_comment_policy.BOOTSTRAP_BASE", pin)
    assert check_comment_policy.bootstrap_base_allowed(tmp_path, pin)
    assert check_comment_policy.bootstrap_base_allowed(tmp_path, descendant)
    assert policy_runner.bootstrap_base_allowed(tmp_path, descendant)
    assert not check_comment_policy.bootstrap_base_allowed(tmp_path, "f" * 40)
    assert not policy_runner.bootstrap_base_allowed(tmp_path, "f" * 40)
    monkeypatch.setattr("check_comment_policy.BOOTSTRAP_BASE", descendant)
    assert not check_comment_policy.bootstrap_base_allowed(tmp_path, pin)


def test_bootstrap_base_is_refused_once_the_trusted_launcher_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pin = plain_repo(tmp_path, "x = 1\n")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/run_comment_policy.py").write_text("", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "scripts/run_comment_policy.py"], check=True)
    with_launcher = commit_change(tmp_path, "x = 2\n")
    monkeypatch.setattr("check_comment_policy.BOOTSTRAP_BASE", pin)
    monkeypatch.setattr("run_comment_policy.BOOTSTRAP_BASE", pin)
    assert check_comment_policy.bootstrap_base_allowed(tmp_path, pin)
    assert not check_comment_policy.bootstrap_base_allowed(tmp_path, with_launcher)
    assert not policy_runner.bootstrap_base_allowed(tmp_path, with_launcher)


@pytest.mark.parametrize(
    "event_name,event",
    [
        ("pull_request", {"pull_request": {"base": {"sha": "a" * 40}}}),
        ("merge_group", {"merge_group": {"base_sha": "a" * 40}}),
    ],
)
def test_ci_event_rejects_base_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, event_name: str, event: dict
) -> None:
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps(event), encoding="utf-8")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_EVENT_NAME", event_name)
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_file))
    with pytest.raises(ValueError, match="platform event"):
        resolve_base(tmp_path, "b" * 40)


def test_ci_policy_is_always_required_and_has_local_equivalent() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    job = workflow["jobs"]["comment-policy"]
    assert "if" not in job
    step = next(step for step in job["steps"] if step.get("name") == "Enforce comment and docstring policy")
    assert 'git show "${BASE_REF}:scripts/run_comment_policy.py"' in step["run"]
    assert 'python -I "$RUNNER_TEMP/comment-policy-runner.py" --native-tests' in step["run"]
    assert ': "${BASE_REF:?' in step["run"]
    listing = step["run"].split('installed="$(git ls-tree -r --name-only "$BASE_REF" --', 1)[1].split(')"', 1)[0]
    assert set(listing.replace("\\", " ").split()) == {*TRUSTED_FILES, "quality/comment-policy.json"}
    assert step["run"].index('installed="$(') < step["run"].index("git cat-file -e")
    assert 'elif [ -z "$installed" ] && git merge-base --is-ancestor' in step["run"]
    assert "git merge-base --is-ancestor ed5c263c513ba65499f4918d3a7de607f280c65b" in step["run"]
    assert "pull_request.base.sha" in step["env"]["BASE_REF"]
    assert "merge_group" not in step["env"]["BASE_REF"]
    tooling = workflow["jobs"]["tooling"]
    assert "comment-policy" in tooling["needs"]
    assert any("--always comment-policy" in step.get("run", "") for step in tooling["steps"])
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert 'failed="$$failed comment-policy"' in makefile


@pytest.mark.parametrize(
    "source,expected",
    [
        ("SELECT ARRAY[1 /* explanation */];", ["/* explanation */"]),
        ("SELECT payload #>> '{a}' FROM t;", []),
        ("SELECT * FROM #temporary_table;", []),
    ],
)
def test_sql_array_and_operator_contexts(source: str, expected: list[str]) -> None:
    assert [text for _, text in sql_comments(source)] == expected


@pytest.mark.parametrize("source", ['sql = "VALUES (1) -- explanation"', 'sql = f"SELECT {column} -- explanation"'])
def test_sql_literals_and_fstrings_in_python(source: str) -> None:
    assert [f.text for f in python_findings("a.py", source)] == ["-- explanation"]


def test_changed_unsupported_source_cannot_cancel_coverage_failure() -> None:
    finding = Finding("a.tpl", 1, "coverage-error", "unsupported")
    assert introduced([finding], [finding], []) == [finding]


@pytest.mark.parametrize(
    "path",
    [".gitignore", ".gitattributes", ".dockerignore", ".importlinter", "skill-sync.conf", "scripts/a.zsh", "scripts/a"],
)
def test_extensionless_and_config_source_are_included(path: str) -> None:
    assert scan_sources(ROOT, {path: b"# explanation\n"}, policy())


def test_executable_python_heredoc() -> None:
    source = "python3 - <<'PY'\n# explanation\nPY\n"
    findings = scan("ci.sh", source, "bash")
    assert [(f.kind, f.text) for f in findings] == [("comment", "# explanation")]


def test_jsonc_and_json_script_are_data() -> None:
    assert not scan_sources(ROOT, {"a.jsonc": b'{"enabled":true}'}, policy())
    assert not scan_sources(ROOT, {"a.html": b'<script type="application/json">{"enabled":true}</script>'}, policy())


def test_removing_exception_revokes_permission(tmp_path: Path) -> None:
    base = git_repo(tmp_path, "x = 1 # noqa: F401\n")
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    registry = tmp_path / "quality/comment-policy.json"
    registry.write_text(json.dumps(policy(exceptions=[exception()])), encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "quality/comment-policy.json", "pyproject.toml"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "fixture permission",
        ],
        check=True,
    )
    base = subprocess.check_output(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True).strip()
    registry.write_text(json.dumps(policy()), encoding="utf-8")
    assert main(["--root", str(tmp_path), "--mode", "transition", "--base", base]) == 1


@pytest.mark.parametrize("source", ['__doc__: str = "prose"', '__doc__ += "prose"', 'f"inert {value}"'])
def test_other_python_prose_forms(source: str) -> None:
    assert python_findings("a.py", source)


def test_python_sql_context_avoids_docstrings_and_fstring_fragments() -> None:
    assert [f.kind for f in python_findings("a.py", '"Show plan evolution -- details"')] == ["docstring"]
    assert not python_findings("a.py", "sql = f\"SELECT * FROM t WHERE x = '{value}'\"")
    assert [f.text for f in python_findings("a.py", 'conn.execute("PRAGMA foreign_keys=ON; -- explanation")')] == [
        "-- explanation"
    ]


@pytest.mark.parametrize("keyword", ["query", "sql", "statement_sql"])
def test_python_sql_context_routes_designated_keyword_arguments(keyword: str) -> None:
    source = f'conn.execute({keyword}="SELECT 1 -- explanation")\n'
    assert [f.text for f in python_findings("a.py", source)] == ["-- explanation"]


def test_python_sql_context_rejects_unrelated_keyword_arguments() -> None:
    assert not python_findings("a.py", 'conn.execute(timeout="SELECT 1 -- explanation")\n')
    assert not python_findings("a.py", 'logger.info(message="not SQL -- explanation")\n')


@pytest.mark.parametrize(
    "source", ["SELECT [1 /* explanation */, 2];", "SELECT 1 FROM t WHERE x = 1 AND [--flag] = 1;"]
)
def test_ambiguous_sql_brackets_require_dialect(source: str) -> None:
    assert scan("a.sql", source, "sql")[0].kind == "coverage-error"
    assert [text for _, text in sql_comments(source, "duckdb")] or not sql_comments(source, "tsql")


@pytest.mark.parametrize(
    "path",
    [
        "results-explorer/a.mts",
        "docker/a.java",
        "benchbox/a.properties",
        ".github/CODEOWNERS",
        "scripts/a.zsh",
        "scripts/new-format.xyz",
    ],
)
def test_closed_inventory_discovers_maintained_sources(tmp_path: Path, path: str) -> None:
    git_repo(tmp_path, "x=1")
    target = tmp_path / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# explanation", encoding="utf-8")
    assert path in source_paths(tmp_path, False)
    subprocess.run(["git", "-C", str(tmp_path), "add", path], check=True)
    assert path in source_paths(tmp_path, True)


def test_zsh_shebang_is_minimum_directive() -> None:
    assert [f.kind for f in scan("a.zsh", "#!/bin/zsh\n# explanation\n", "bash")] == ["comment", "comment"]


@pytest.mark.parametrize(
    "source", ["printf python <<'EOF'\necho ok\nEOF\n", "python script.py <<'EOF'\n# input data\nEOF\n"]
)
def test_heredoc_arguments_and_script_input_are_data(source: str) -> None:
    assert not scan("a.sh", source, "bash")


@pytest.mark.parametrize(
    "header",
    ["uv run --project . python -", "uv run --with pkg python -", "env -u FOO python -"],
)
def test_heredoc_wrapper_option_operands_reach_stdin_consumer(header: str) -> None:
    assert [f.text for f in scan("a.sh", f"{header} <<'PY'\n# explanation\nPY\n", "bash")] == ["# explanation"]


@pytest.mark.parametrize(
    "header",
    [
        "FOO=bar python -",
        "env FOO=bar python -",
        'env -S "python -"',
        'env --split-string="python -"',
    ],
)
def test_heredoc_env_assignments_and_static_split_string_reach_stdin_consumer(header: str) -> None:
    assert [f.text for f in scan("a.sh", f"{header} <<'PY'\n# explanation\nPY\n", "bash")] == ["# explanation"]


@pytest.mark.parametrize(
    "source",
    [
        "env -S \"$COMMAND\" <<'PY'\n# explanation\nPY\n",
        "env -S \"python script.py -\" <<'PY'\n# input data\nPY\n",
    ],
)
def test_heredoc_dynamic_or_wrong_stdin_interpreter_fails_closed(source: str) -> None:
    findings = scan("a.sh", source, "bash")
    if "script.py" in source:
        assert not findings
    else:
        assert findings[0].kind == "coverage-error"


def test_env_split_string_malformed_operand_fails_closed() -> None:
    with pytest.raises(ValueError, match="malformed env split-string operand"):
        stdin_language(["env", "-S", "'python -"])


@pytest.mark.parametrize("operand", ["python - # ignored", r"python - \\c", "python - ${COMMAND}"])
def test_env_split_string_special_gnu_syntax_fails_closed(operand: str) -> None:
    with pytest.raises(ValueError, match="unsupported env split-string syntax"):
        stdin_language(["env", "-S", operand])


@pytest.mark.parametrize("operand", ["python - # ignored", r"python - \\c", "python - ${COMMAND}"])
def test_env_split_string_special_syntax_is_reported_by_source_scan(operand: str) -> None:
    findings = scan("a.sh", f"env -S '{operand}' <<'PY'\n# explanation\nPY\n", "bash")
    assert findings[0].kind == "coverage-error"


def test_heredoc_module_wrapper_requires_explicit_consumer_support() -> None:
    findings = scan("a.sh", "uv run --module python - <<'PY'\n# explanation\nPY\n", "bash")
    assert findings[0].kind == "coverage-error"


def test_heredoc_unknown_wrapper_option_fails_closed() -> None:
    findings = scan("a.sh", "uv run --unknown value python - <<'PY'\n# explanation\nPY\n", "bash")
    assert findings[0].kind == "coverage-error"


@pytest.mark.parametrize("command", ["python -", "uv run python -"])
def test_conditional_heredoc_retains_executable_payload(command: str) -> None:
    source = f"if {command} <<'PY'\n# explanation\npass\nPY\nthen\n  echo ok\nfi\n"
    findings = scan("a.sh", source, "bash")
    assert [(finding.line, finding.text) for finding in findings] == [(2, "# explanation")]


def test_conditional_heredoc_unknown_consumer_fails_visibly() -> None:
    source = "if \"$tool\" - <<'PY'\n# explanation\nPY\nthen\n  echo ok\nfi\n"
    assert [finding.kind for finding in scan("a.sh", source, "bash")] == ["coverage-error"]


def test_multiple_heredocs_and_continued_header() -> None:
    source = "python - \\\n <<'A' <<'B'\n# unused input\nA\n# explanation\nB\n"
    assert [f.text for f in scan("a.sh", source, "bash")] == ["# explanation"]


def test_piped_heredoc_is_executable() -> None:
    source = "cat <<'JS' | node\n// explanation\nJS\n"
    assert javascript_requests("a.sh", source, "bash")


@pytest.mark.parametrize(
    "source",
    [
        "write_sql: 'SELECT 1 -- explanation'",
        "cleanup_sql: 'DELETE FROM t -- explanation'",
        "platform_overrides:\n  duckdb: 'SELECT 1 -- explanation'",
    ],
)
def test_repository_sql_carriers_are_routed(source: str) -> None:
    assert [f.text for f in scan("a.yaml", source, "yaml")] == ["-- explanation"]


def test_github_script_routes_native_request() -> None:
    source = "steps:\n- uses: actions/github-script@sha\n  with:\n    script: |\n      // explanation\n"
    assert list(javascript_requests("ci.yaml", source, "yaml").values()) == ["// explanation\n"]


def test_myst_metadata_and_nested_examples() -> None:
    source = "```{tags}\npython\n```\n```{toctree}\nindex\n```\n````{note}\n```python\n# explanation\n```\n````\n"
    assert [(f.line, f.text) for f in scan("docs/a.md", source, "examples")] == [(9, "# explanation")]
    source = "  ```python\n  # explanation\n  ```\n"
    assert [(f.line, f.text) for f in scan("docs/a.md", source, "examples")] == [(2, "# explanation")]


def test_rst_nested_code_is_dedented() -> None:
    source = "   .. code-block:: python\n      :linenos:\n\n      # explanation\n      x=1\n"
    assert [(f.line, f.text) for f in scan("docs/a.rst", source, "examples")] == [(4, "# explanation")]


def test_exception_budget_and_completed_external_overlap() -> None:
    registered = load_policy(json.dumps(policy(exceptions=[exception()])).encode())
    assert len(scan_sources(ROOT, {"a.py": b"x=1 # noqa: F401\ny=2 # noqa: F401\n"}, registered)) == 1
    with pytest.raises(ValueError, match="overlap"):
        load_policy(
            json.dumps(
                policy(
                    completed=["vendor/"],
                    external=[{"path": "vendor/a.py", "owner": "upstream", "provenance": "README.md"}],
                )
            ).encode()
        )


def test_fixture_exception_binds_payload_kind_text_and_count() -> None:
    entry = exception("-- explanation", kind="fixture", payload="SELECT 1 -- explanation", finding_kind="comment")
    registered = load_policy(json.dumps(policy(exceptions=[entry])).encode())
    finding = Finding("a.py", 1, "comment", "-- explanation", payload="SELECT 1 -- explanation")
    assert allowed(finding, registered, "")
    assert not allowed(
        Finding("a.py", 1, "comment", "-- explanation", payload="SELECT 2 -- explanation"), registered, ""
    )


def test_report_handles_provenance_excluded_vendor_binary(tmp_path: Path) -> None:
    git_repo(tmp_path, "# legacy\n")
    path = "_project/scripts/vendor/package.whl"
    binary = tmp_path / path
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"PK\x03\x04\xff")
    (tmp_path / "quality/comment-policy.json").write_text(
        json.dumps(policy(external=[{"path": path, "owner": "upstream", "provenance": "a.py"}])), encoding="utf-8"
    )
    assert path in source_paths(tmp_path, False)
    assert main(["--root", str(tmp_path), "--mode", "report"]) == 0


def test_parser_environment_installs_only_trusted_material(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("comment-policy-package.json", "comment-policy-package-lock.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    commands = []
    monkeypatch.setenv("PYTHONPATH", "candidate")
    monkeypatch.setenv("NODE_OPTIONS", "--require=candidate")
    monkeypatch.setenv("COMMENT_POLICY_TYPESCRIPT", "candidate")

    def install(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        commands.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("run_comment_policy.subprocess.run", install)
    python, env = parser_environment(tmp_path)
    assert str(python).startswith(str(tmp_path))
    assert "PYTHONPATH" not in env and "NODE_OPTIONS" not in env
    assert env["COMMENT_POLICY_TYPESCRIPT"] == str(tmp_path / "node_modules/typescript")
    assert "--require-hashes" in commands[1][0]
    assert "--ignore-scripts" in commands[2][0]
    assert str(tmp_path / "comment-policy-requirements.txt") in commands[1][0]
    assert commands[2][1]["cwd"] == tmp_path


def test_comment_policy_trust_roots_require_soundness_review() -> None:
    routes = (ROOT / ".github/soundness-paths.txt").read_text().splitlines()
    for path in (*TRUSTED_FILES, "scripts/run_comment_policy.py", "quality/comment-policy.json"):
        assert "file\t" + path in routes


@pytest.mark.parametrize("prose,native_failure", [(True, False), (True, True), (False, False), (False, True)])
def test_candidate_native_execution_cannot_replace_checker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prose: bool, native_failure: bool
) -> None:
    git_repo(tmp_path, "x = 1\n")
    for name in TRUSTED_FILES:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("", encoding="utf-8")
    (tmp_path / "scripts/comment_policy_entry.py").write_bytes((ROOT / "scripts/comment_policy_entry.py").read_bytes())
    (tmp_path / "scripts/check_comment_policy.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "root = Path(sys.argv[sys.argv.index('--root') + 1])\n"
        "raise SystemExit(int('# explanation' in (root / 'scripts/bad.py').read_text()))\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(tmp_path), "add", "scripts", "quality"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "Checker fixture",
        ],
        check=True,
    )
    base = subprocess.check_output(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True).strip()
    (tmp_path / "scripts/bad.py").write_text("# explanation\n" if prose else "x = 1\n", encoding="utf-8")
    order = []
    real_run = subprocess.run

    def execute(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        if command[0] == "node":
            order.append("native")
            directory = kwargs["cwd"]
            assert isinstance(directory, Path)
            (directory / "check_comment_policy.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
            if native_failure:
                raise subprocess.CalledProcessError(1, command)
            return subprocess.CompletedProcess(command, 0)
        if command[0] == sys.executable and any(str(value).endswith("comment_policy_entry.py") for value in command):
            order.append("checker")
        return real_run(command, **kwargs)

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    monkeypatch.setattr(policy_runner, "parser_environment", lambda trusted: (Path(sys.executable), dict(os.environ)))
    monkeypatch.setattr(policy_runner.subprocess, "run", execute)
    assert policy_runner.main(["--base", base, "--native-tests"]) == (1 if prose else 2 if native_failure else 0)
    assert order == (["checker"] if prose else ["checker", "native"])
    assert (tmp_path / "scripts/bad.py").read_text() == ("# explanation\n" if prose else "x = 1\n")


def test_new_files_do_not_inherit_vendor_directory_exclusion() -> None:
    registered = policy(external=[{"path": "vendor/", "owner": "upstream", "provenance": "README.md"}])
    registered["external_members"] = {"vendor/old.py"}
    findings = scan_sources(ROOT, {"vendor/old.py": b"# upstream\n", "vendor/new.py": b"# explanation\n"}, registered)
    assert [(f.path, f.text) for f in findings] == [("vendor/new.py", "# explanation")]


def test_invalid_source_encoding_is_inventory_debt_in_report(tmp_path: Path) -> None:
    git_repo(tmp_path, "x=1")
    (tmp_path / "a.py").write_bytes(b"\xff")
    assert main(["--root", str(tmp_path), "--mode", "report"]) == 0
    assert main(["--root", str(tmp_path), "--mode", "strict"]) == 1


@pytest.mark.parametrize(
    "source",
    [
        'exec("# explanation")',
        'eval("1 # explanation")',
        'compile("# explanation", "generated", "exec")',
        'code = "# explanation"\nexec(code)',
        'code = "# " + "explanation"\nexec(code)',
        'import builtins as bi\nbi.exec("# explanation")',
        'from builtins import exec as run\nrun("# explanation")',
        'exec(compile("# explanation", "generated", "exec"))',
        'import subprocess\nsubprocess.run(["python3", "-c", "# explanation"])',
        'import subprocess, sys\nsubprocess.run([sys.executable, "-c", "# explanation"])',
        "import subprocess\nsubprocess.run(\"python3 -c '# explanation'\", shell=True)",
        'import subprocess\nsubprocess.run(args=["python3", "-c", "# explanation"])',
        'import subprocess\nsubprocess.run(args="echo ok # explanation", shell=True)',
    ],
)
def test_python_executable_strings_reach_scanner(source: str) -> None:
    assert [f.text for f in python_findings("a.py", source)] == ["# explanation"]


@pytest.mark.parametrize(
    "source",
    [
        'import subprocess, sys\nsubprocess.run([sys.executable, "-m", "pytest", "-c", config])',
        'import subprocess\nsubprocess.run(["python3", "probe.py", "-c", config])',
        'import subprocess\nsubprocess.run(["python3", "--", "probe.py", "-c", config])',
        'import subprocess\nsubprocess.run(["pytest", "-c", config])',
    ],
)
def test_application_options_are_not_executable_source(source: str) -> None:
    assert not python_findings("a.py", source)


@pytest.mark.parametrize(
    "source",
    [
        'import subprocess\nsubprocess.run(["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", "echo ok # explanation"])',
        'import subprocess\nsubprocess.run(["bash", "-e", "-c", "echo ok # explanation"])',
        'import subprocess\nsubprocess.run(["bash", "+o", "pipefail", "-c", "echo ok # explanation"])',
        'import subprocess\nsubprocess.run(["python3", "-W", "ignore", "-X", "dev", "-c", "# explanation"])',
        'import subprocess\nsubprocess.run(["python3", "-I", "-B", "-c", "# explanation"])',
    ],
)
def test_interpreter_options_preserve_inline_source_scanning(source: str) -> None:
    assert [finding.text for finding in python_findings("a.py", source)] == ["# explanation"]


@pytest.mark.parametrize(
    "source",
    [
        'import subprocess\nsubprocess.run([program, "-c", source])',
        'import subprocess\nsubprocess.run(["python3", option, "-c", source])',
        'import subprocess\nsubprocess.run(["python3", "--unknown", "-c", source])',
        'import subprocess\nsubprocess.run(["python3", "-W", option, "-c", source])',
    ],
)
def test_unknown_inline_process_source_still_fails_visibly(source: str) -> None:
    findings = python_findings("a.py", source)
    assert [finding.kind for finding in findings] == ["payload-error"]


@pytest.mark.parametrize(
    "source",
    [
        'import subprocess\nsubprocess.run(["/bin/bash", "-e", "-c", "echo ok # explanation"])',
        'import subprocess\nsubprocess.run(["/usr/bin/env", "-u", "IGNORED", "TOKEN=value", "python3", "-c", "# explanation"])',
        'import subprocess\nsubprocess.run(["env", "-S", "python3 -c", "# explanation"])',
        'import subprocess\nsubprocess.run(["/usr/bin/uv", "run", "--with", "pkg", "--", "/usr/bin/python3", "-c", "# explanation"])',
        'import subprocess\nsubprocess.run(["env", "uv", "run", "--", "python3", "-c", "# explanation"])',
    ],
)
def test_wrapped_and_absolute_inline_interpreters_are_scanned(source: str) -> None:
    assert [finding.text for finding in python_findings("a.py", source)] == ["# explanation"]


@pytest.mark.parametrize(
    "source",
    [
        'import subprocess\nsubprocess.run(["uv", "tree", "python3", "-c", "# input data"])',
        'import subprocess\nsubprocess.run(["uv", "run", "pytest", "-c", "pytest.ini"])',
    ],
)
def test_non_interpreter_wrapper_commands_are_not_inline_source(source: str) -> None:
    assert not python_findings("a.py", source)


@pytest.mark.parametrize(
    "source",
    [
        'import subprocess\nsubprocess.run(["env", "--unknown", "python3", "-c", "# explanation"])',
        'import subprocess\nsubprocess.run(["uv", "run", "--unknown", "python3", "-c", "# explanation"])',
        'import subprocess\nsubprocess.run(["env", option, "python3", "-c", "# explanation"])',
        'import subprocess\nsubprocess.run(["uv", "run", "python3", "-c", source])',
        'import subprocess\nsubprocess.run(["env", "-S", "python3 -c $CODE"])',
        'import subprocess\nsubprocess.run(["uv", "run", "--module", "python3", "-c", "# input data"])',
        'import subprocess\nsubprocess.run(["env", "uv", "run", "--module", "python3", "-c", "# input data"])',
    ],
)
def test_unknown_wrapper_execution_fails_visibly(source: str) -> None:
    assert [finding.kind for finding in python_findings("a.py", source)] == ["payload-error"]


def test_absolute_node_inline_source_reports_existing_adapter_gap() -> None:
    source = 'import subprocess\nsubprocess.run(["/usr/bin/node", "-e", "// explanation"])'
    findings = python_findings("a.py", source)
    assert [(finding.kind, finding.payload) for finding in findings] == [("coverage-error", "// explanation")]


@pytest.mark.parametrize(
    "source", ["exec(source)", 'code = code + "text"\nexec(code)', 'code = "before"\ncode = "after"\nexec(code)']
)
def test_python_unresolved_execution_fails_visibly(source: str) -> None:
    assert python_findings("a.py", source)[0].kind == "payload-error"


@pytest.mark.parametrize(
    "source",
    [
        'def exec(value):\n    return value\nexec("# data")',
        'def f(exec):\n    return exec("# data")',
        'text = "# data"',
    ],
)
def test_local_execution_names_and_ordinary_strings_are_data(source: str) -> None:
    assert not python_findings("a.py", source)


@pytest.mark.parametrize(
    "source",
    [
        'python3 -c "# explanation"',
        "eval 'echo ok # explanation'",
        "bash -lc 'echo ok # explanation'",
        "VALUE=$(python3 -c '# explanation' || true\n)",
    ],
)
def test_shell_executable_arguments_are_routed(source: str) -> None:
    assert [f.text for f in scan("a.sh", source, "bash")] == ["# explanation"]


@pytest.mark.parametrize(
    "source",
    [
        "bash -e -o pipefail -c 'echo ok # explanation'",
        "bash +o pipefail -c 'echo ok # explanation'",
        "python3 -W ignore -X dev -c '# explanation'",
        "env -u UNUSED TOKEN=value /usr/bin/python3 -c '# explanation'",
        "uv run --with pkg -- /usr/bin/python3 -c '# explanation'",
    ],
)
def test_shell_uses_shared_interpreter_option_semantics(source: str) -> None:
    assert [finding.text for finding in scan("a.sh", source, "bash")] == ["# explanation"]


@pytest.mark.parametrize(
    "source",
    [
        "python3 -m pytest -c '# input data'",
        "python3 probe.py -c '# input data'",
        "uv tree python3 -c '# input data'",
        "pytest python3 -c '# input data'",
    ],
)
def test_shell_application_arguments_are_not_executable_source(source: str) -> None:
    assert not scan("a.sh", source, "bash")


@pytest.mark.parametrize(
    "source",
    [
        "python3 --unknown -c '# explanation'",
        'bash "$OPTIONS" -c "echo ok # explanation"',
        "env --unknown python3 -c '# explanation'",
        "uv run --module python3 -c '# input data'",
        'env -S "python3 -c" "# explanation"',
    ],
)
def test_shell_unresolved_interpreter_options_fail_visibly(source: str) -> None:
    assert [finding.kind for finding in scan("a.sh", source, "bash")] == ["coverage-error"]


@pytest.mark.parametrize("source", ["# explanation", "echo ok # explanation", "echo ok # explanation\n"])
def test_shell_comments_at_end_of_input(source: str) -> None:
    findings = scan("a.sh", source, "bash")
    assert [(finding.line, finding.text) for finding in findings] == [(1, "# explanation")]
    assert not scan("a.sh", 'printf "%s" "# data"', "bash")


def test_shell_dynamic_execution_requires_adapter() -> None:
    assert scan("a.sh", 'python -c "$code"', "bash")[0].kind == "coverage-error"
    assert not scan("a.sh", 'printf "%s" "eval # input data"', "bash")


@pytest.mark.parametrize("module_name", ["check_deps", "scan_imports"])
def test_markdown_parser_dependency_is_backed_by_import_sites(module_name: str, tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location(
        module_name, ROOT / "_project/scripts/dependency_audit" / f"{module_name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    collect = getattr(module, "_collect_python_imports", None) or module.collect_python_imports
    uses = getattr(module, "_package_uses", None) or module.package_uses
    script = tmp_path / "consumer.py"
    script.write_text("from markdown_it import MarkdownIt\n", encoding="utf-8")
    assert uses("markdown-it-py", collect(tmp_path, ["consumer.py"])) == ["consumer.py:1"]
    script.write_text('data = "from markdown_it import MarkdownIt"\n', encoding="utf-8")
    assert not uses("markdown-it-py", collect(tmp_path, ["consumer.py"]))
    assert not uses("markdown-it-py", {})


def test_opaque_fixture_permission_changes_with_contributing_consumer_code() -> None:
    source = "code = input()\nexec(code)"
    finding = python_findings("a.py", source)[0]
    registered = policy(
        exceptions=[exception(finding.text, kind="fixture", payload=finding.payload, finding_kind="payload-error")]
    )
    assert allowed(finding, registered, source)
    changed = python_findings("a.py", source + '\ncode_input = "# new explanation"')[0]
    assert changed.text == finding.text
    assert changed.payload != finding.payload
    assert not allowed(changed, registered, source)


def test_native_payload_batches_include_nested_shell_javascript(monkeypatch: pytest.MonkeyPatch) -> None:
    source = 'import {execSync} from "node:child_process"; execSync("node -e \'// explanation\'");'
    batches = []

    def native_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        requests = json.loads(str(kwargs["input"]))
        batches.append(requests)
        row = (
            {"kind": "payload", "language": "bash", "line": 1, "symbol": "", "text": "node -e '// explanation'"}
            if len(batches) == 1
            else {"kind": "comment", "line": 1, "text": "// explanation", "symbol": ""}
        )
        return subprocess.CompletedProcess(command, 0, json.dumps({key: [row] for key in requests}))

    monkeypatch.setattr("check_comment_policy.subprocess.run", native_run)
    findings = scan_sources(ROOT, {"a.ts": source.encode()}, policy())
    assert [(f.kind, f.text) for f in findings] == [("comment", "// explanation")]
    assert len(batches) == 2
    assert list(batches[1].values()) == ["// explanation"]


@pytest.mark.parametrize("node_type", [ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef])
def test_consumer_digest_ignores_absent_empty_type_parameters(node_type: type[ast.AST]) -> None:
    from comment_syntax import python_consumer_digest

    node: Any = node_type()
    vars(node)["_fields"] = tuple(name for name in node_type._fields if name != "type_params")
    tree = ast.Module(body=[node], type_ignores=[])
    before = python_consumer_digest(tree)
    vars(node)["_fields"] = (*node._fields, "type_params")
    node.type_params = []
    assert python_consumer_digest(tree) == before
    node.type_params = [ast.Name(id="T", ctx=ast.Load())]
    assert python_consumer_digest(tree) != before


def test_consumer_digest_has_stable_python_version_encoding() -> None:
    from comment_syntax import python_consumer_digest

    tree = ast.parse("def consume(value):\n    return value + 1\n")
    assert python_consumer_digest(tree) == "sha256:c6aaea25c78f8f6fdbf83d97c11fe1be21e859156b678d34cfbcc79f8c261c69"


@pytest.mark.parametrize("prefix", [["python3"], ["env", "python3"], ["uv", "run", "python3"]])
def test_sys_executable_inline_source_is_not_literal_program_name(prefix: list[str]) -> None:
    prefix_expression = ", ".join(repr(word) for word in prefix)
    source = f'import subprocess, sys\nsubprocess.run([{prefix_expression}, "-c", sys.executable])'
    assert [finding.kind for finding in python_findings("a.py", source)] == ["payload-error"]


def test_sys_executable_in_wrapped_command_position_is_scanned() -> None:
    source = 'import subprocess, sys\nsubprocess.run(["uv", "run", sys.executable, "-c", "# explanation"])'
    assert [finding.text for finding in python_findings("a.py", source)] == ["# explanation"]


@pytest.mark.parametrize("prefix", [["uv", "run"], ["env", "--"]])
def test_wrapped_non_interpreter_dynamic_arguments_are_data(prefix: list[str]) -> None:
    prefix_expression = ", ".join(repr(word) for word in prefix)
    source = f'import subprocess\nsubprocess.run([{prefix_expression}, "pytest", "-k", test_filter])'
    assert not python_findings("a.py", source)


@pytest.mark.parametrize(
    "source",
    [
        'import subprocess\nsubprocess.run(["uv", command, "python3", "-c", source])',
        'import subprocess\nsubprocess.run(["uv", "run", "--with", package, "python3", "-c", source])',
    ],
)
def test_dynamic_wrapper_execution_prefix_fails_visibly(source: str) -> None:
    assert [finding.kind for finding in python_findings("a.py", source)] == ["payload-error"]


OWNERSHIP_LEGACY_EXTERNAL = [
    {"path": "benchbox/_binaries/vendor/tools/", "owner": "upstream", "provenance": "a.py"},
    {"path": "catalog/tools/", "owner": "catalog", "provenance": "a.py"},
]


def ownership_policy(exclusions: set[str] | None) -> dict:
    return policy(external=deepcopy(OWNERSHIP_LEGACY_EXTERNAL), ownership_excluded_members=exclusions)


def ownership_repo(
    tmp_path: Path, partial_notice: bool = False, outside_override: str | None = None
) -> tuple[str, dict]:
    git_repo(tmp_path, "value = 1\n")
    directory = tmp_path / "benchbox/_binaries/vendor/tools"
    directory.mkdir(parents=True)
    (directory / "generator").write_bytes(b"\xff\x00upstream binary")
    (directory / "owned.py").write_text("# maintained explanation\n")
    (tmp_path / "quality/comment-policy.json").write_text(json.dumps(policy(external=OWNERSHIP_LEGACY_EXTERNAL)))
    catalog = tmp_path / "catalog/tools"
    catalog.mkdir(parents=True)
    (catalog / "upstream.py").write_text("# catalog provenance\n")
    notice = b"prefix\nProtected notice\nsuffix\n" if partial_notice else b"Required upstream notice\n"
    (directory / "PATCHES.md").write_bytes(notice)
    ledger = {
        name: []
        for name in (
            "maintained_roots",
            "ownership_rules",
            "external_entries",
            "format_classes",
            "payloads",
            "derived_rules",
            "consumer_edges",
            "directives",
            "notices",
            "obligations",
            "review_dispositions",
        )
    }
    ledger["version"] = 1
    ledger["maintained_roots"] = [{"id": "repository", "selector": {"prefix": ""}, "kind": "source"}]
    ledger["ownership_rules"] = [
        {
            "id": "owned",
            "owner": "comment-cleanup-owned",
            "state": "ready",
            "priority": 10,
            "blocking_disposition": "Maintained source.",
            "selectors": [{"path": "benchbox/_binaries/vendor/tools/owned.py"}],
        }
    ]
    ledger["external_entries"] = [
        {
            "selector": {"prefix": "benchbox/_binaries/vendor/"},
            "owner": "comment-cleanup-vendor",
            "provenance": "Upstream fixture",
            "governing_requirement": "Approved upstream ownership.",
            "blocking_disposition": "Excluded upstream source.",
        }
    ]
    ledger["ownership_rules"].append(
        {
            "id": "external-ownership-mirrors",
            "owner": "comment-cleanup-external-ownership",
            "state": "blocked",
            "priority": 10,
            "blocking_disposition": "Frozen catalog audit role.",
            "selectors": [{"prefix": "catalog/tools/"}],
        }
    )
    if outside_override == "owned":
        ledger["ownership_rules"].append(
            {
                "id": "catalog-owned",
                "owner": "comment-cleanup-owned",
                "state": "ready",
                "priority": 20,
                "blocking_disposition": "Maintained first-party source.",
                "selectors": [{"path": "catalog/tools/upstream.py"}],
            }
        )
    elif outside_override == "derived":
        package = tmp_path / "benchbox/core/example.py"
        package.parent.mkdir(parents=True)
        package.write_text("value = 1\n")
        (catalog / "upstream.py").write_text("from benchbox.core import example\n# maintained explanation\n")
        ledger["ownership_rules"].append(
            {
                "id": "package-owned",
                "owner": "comment-cleanup-owned",
                "state": "ready",
                "priority": 20,
                "blocking_disposition": "Maintained package.",
                "selectors": [{"path": "benchbox/core/example.py"}],
            }
        )
        ledger["derived_rules"] = [
            {
                "id": "catalog-derived",
                "method": "python-imports",
                "state": "ready",
                "priority": 30,
                "blocking_disposition": "Owned by imported package.",
                "selectors": [{"path": "catalog/tools/upstream.py"}],
            }
        ]
    digest = hashlib.sha256(notice).hexdigest()
    start = len(b"prefix\n") if partial_notice else 0
    end = start + len(b"Protected notice\n") if partial_notice else len(notice)
    ledger["notices"] = [
        {
            "path": "benchbox/_binaries/vendor/tools/PATCHES.md",
            "blob_sha256": digest,
            "byte_start": start,
            "byte_end": end,
            "retained_sha256": hashlib.sha256(notice[start:end]).hexdigest(),
            "governing_requirement": "Retain exact notice.",
            "source_identity": "fixture",
            "owner": "comment-cleanup-vendor",
            "blocking_disposition": "Preserve bytes.",
        }
    ]
    if outside_override == "notice":
        raw = (catalog / "upstream.py").read_bytes()
        outside_notice = deepcopy(ledger["notices"][0])
        outside_notice.update(
            {
                "path": "catalog/tools/upstream.py",
                "blob_sha256": hashlib.sha256(raw).hexdigest(),
                "byte_start": 0,
                "byte_end": len(raw),
                "retained_sha256": hashlib.sha256(raw).hexdigest(),
                "owner": "comment-cleanup-external-ownership",
            }
        )
        ledger["notices"].append(outside_notice)
    (tmp_path / "quality/comment-cleanup-scope.json").write_text(json.dumps(ledger))
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "add",
            "benchbox/",
            "catalog/tools",
            "quality/comment-policy.json",
            "quality/comment-cleanup-scope.json",
        ],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "ownership fixture",
        ],
        check=True,
    )
    base = subprocess.check_output(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True).strip()
    return base, ledger


def test_immutable_ownership_excludes_approved_binary(tmp_path: Path) -> None:
    base, _ = ownership_repo(tmp_path)
    exclusions = check_comment_policy.ownership_exclusions(tmp_path, base, False)
    path = "benchbox/_binaries/vendor/tools/generator"
    assert path in exclusions
    registered = ownership_policy(exclusions)
    assert scan_sources(ROOT, {path: b"\xff\x00upstream binary"}, registered) == []
    assert [f.kind for f in scan_sources(ROOT, {path: b"\xff\x00upstream binary"}, policy())] == ["coverage-error"]


def test_immutable_ownership_scans_new_vendor_member(tmp_path: Path) -> None:
    base, _ = ownership_repo(tmp_path)
    path = "benchbox/_binaries/vendor/tools/new.py"
    (tmp_path / path).write_text("# explanation\n")
    registered = ownership_policy(check_comment_policy.ownership_exclusions(tmp_path, base, False))
    assert [(f.path, f.text) for f in scan_sources(ROOT, {path: b"# explanation\n"}, registered)] == [
        (path, "# explanation")
    ]


def test_candidate_ownership_expansion_is_ineffective(tmp_path: Path) -> None:
    base, ledger = ownership_repo(tmp_path)
    candidate = deepcopy(ledger)
    candidate["external_entries"][0]["selector"] = {"prefix": "benchbox/"}
    candidate["ownership_rules"] = []
    (tmp_path / "quality/comment-cleanup-scope.json").write_text(json.dumps(candidate))
    exclusions = check_comment_policy.ownership_exclusions(tmp_path, base, False)
    path = "benchbox/core/new.py"
    assert path not in exclusions
    assert "benchbox/_binaries/vendor/tools/owned.py" not in exclusions
    assert [f.text for f in scan_sources(ROOT, {path: b"# explanation\n"}, ownership_policy(exclusions))] == [
        "# explanation"
    ]


@pytest.mark.parametrize("changed", [b"Modified upstream notice\n", b"Required upstream notice\nadded text\n"])
def test_immutable_ownership_rejects_changed_notice(tmp_path: Path, changed: bytes) -> None:
    base, _ = ownership_repo(tmp_path)
    (tmp_path / "benchbox/_binaries/vendor/tools/PATCHES.md").write_bytes(changed)
    with pytest.raises(ValueError, match="protected external notice"):
        check_comment_policy.ownership_exclusions(tmp_path, base, False)


def test_immutable_ownership_respects_explicit_owned_override(tmp_path: Path) -> None:
    base, _ = ownership_repo(tmp_path)
    exclusions = check_comment_policy.ownership_exclusions(tmp_path, base, False)
    path = "benchbox/_binaries/vendor/tools/owned.py"
    assert path not in exclusions
    assert scan_sources(ROOT, {path: b"# maintained explanation\n"}, policy(external=OWNERSHIP_LEGACY_EXTERNAL)) == []
    assert [
        f.text for f in scan_sources(ROOT, {path: b"# maintained explanation\n"}, ownership_policy(exclusions))
    ] == ["# maintained explanation"]


def test_transition_cannot_select_different_ownership_base(tmp_path: Path) -> None:
    base, _ = ownership_repo(tmp_path)
    assert main(["--root", str(tmp_path), "--mode", "transition", "--base", base, "--ownership-base", "a" * 40]) == 2


@pytest.mark.parametrize("mode", ["strict", "report"])
def test_ownership_mode_scans_new_legacy_prefix_member(tmp_path: Path, mode: str) -> None:
    base, _ = ownership_repo(tmp_path)
    path = "benchbox/_binaries/vendor/tools/new.py"
    (tmp_path / path).write_text("# new explanation\n")
    output = tmp_path / "findings.json"
    status = main(["--root", str(tmp_path), "--mode", mode, "--ownership-base", base, "--json-out", str(output)])
    assert status == (1 if mode == "strict" else 0)
    findings = json.loads(output.read_text())["findings"]
    assert any(f["path"] == path and f["text"] == "# new explanation" for f in findings)


def test_ownership_freezes_legacy_members_outside_ledger_domains(tmp_path: Path) -> None:
    base, _ = ownership_repo(tmp_path)
    exclusions = check_comment_policy.ownership_exclusions(tmp_path, base, False)
    old = "catalog/tools/upstream.py"
    new = "catalog/tools/new.py"
    assert old in exclusions
    assert new not in exclusions
    findings = scan_sources(
        ROOT, {old: b"# catalog provenance\n", new: b"# new explanation\n"}, ownership_policy(exclusions)
    )
    assert [(f.path, f.text) for f in findings] == [(new, "# new explanation")]


@pytest.mark.parametrize("mode", ["strict", "report"])
def test_ownership_mode_ignores_candidate_external_expansion(tmp_path: Path, mode: str) -> None:
    base, _ = ownership_repo(tmp_path)
    path = "a.py"
    (tmp_path / path).write_text("# first-party explanation\n")
    candidate = policy(external=[*OWNERSHIP_LEGACY_EXTERNAL, {"path": path, "owner": "fake", "provenance": path}])
    (tmp_path / "quality/comment-policy.json").write_text(json.dumps(candidate))
    output = tmp_path / "findings.json"
    status = main(["--root", str(tmp_path), "--mode", mode, "--ownership-base", base, "--json-out", str(output)])
    assert status == (1 if mode == "strict" else 0)
    findings = json.loads(output.read_text())["findings"]
    assert any(f["path"] == path and f["text"] == "# first-party explanation" for f in findings)


def test_ownership_staged_changed_notice_rejects_clean_worktree(tmp_path: Path) -> None:
    base, _ = ownership_repo(tmp_path)
    path = "benchbox/_binaries/vendor/tools/PATCHES.md"
    (tmp_path / path).write_bytes(b"Modified upstream notice\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", path], check=True)
    (tmp_path / path).write_bytes(b"Required upstream notice\n")
    with pytest.raises(ValueError, match="protected external notice"):
        check_comment_policy.ownership_exclusions(tmp_path, base, True)
    assert path in check_comment_policy.ownership_exclusions(tmp_path, base, False)


def test_ownership_staged_clean_notice_ignores_changed_worktree(tmp_path: Path) -> None:
    base, _ = ownership_repo(tmp_path)
    path = "benchbox/_binaries/vendor/tools/PATCHES.md"
    (tmp_path / path).write_bytes(b"Modified upstream notice\n")
    assert path in check_comment_policy.ownership_exclusions(tmp_path, base, True)
    with pytest.raises(ValueError, match="protected external notice"):
        check_comment_policy.ownership_exclusions(tmp_path, base, False)


def test_ownership_partial_notice_allows_outside_edit_without_file_waiver(tmp_path: Path) -> None:
    base, _ = ownership_repo(tmp_path, partial_notice=True)
    path = "benchbox/_binaries/vendor/tools/PATCHES.md"
    raw = b"prefix\nProtected notice\nchanged suffix\n\n```python\n# outside explanation\n```\n"
    (tmp_path / path).write_bytes(raw)
    exclusions = check_comment_policy.ownership_exclusions(tmp_path, base, False)
    assert path not in exclusions
    assert [f.text for f in scan_sources(ROOT, {path: raw}, ownership_policy(exclusions))] == ["# outside explanation"]


def test_ownership_partial_notice_rejects_inside_edit(tmp_path: Path) -> None:
    base, _ = ownership_repo(tmp_path, partial_notice=True)
    (tmp_path / "benchbox/_binaries/vendor/tools/PATCHES.md").write_bytes(b"prefix\nModified notice!\nsuffix\n")
    with pytest.raises(ValueError, match="protected external notice"):
        check_comment_policy.ownership_exclusions(tmp_path, base, False)


def test_ownership_absent_ledger_preserves_legacy_behavior(tmp_path: Path) -> None:
    base = git_repo(tmp_path, "value = 1\n")
    exclusions = check_comment_policy.ownership_exclusions(tmp_path, base, False)
    assert exclusions is None
    assert (
        scan_sources(ROOT, {"benchbox/_binaries/vendor/tools/new.py": b"# explanation\n"}, ownership_policy(exclusions))
        == []
    )


@pytest.mark.parametrize("override", ["owned", "notice", "derived"])
def test_legacy_outside_domain_obeys_precise_owned_notice_and_derived_priority(tmp_path: Path, override: str) -> None:
    base, _ = ownership_repo(tmp_path, outside_override=override)
    exclusions = check_comment_policy.ownership_exclusions(tmp_path, base, False)
    path = "catalog/tools/upstream.py"
    assert path not in exclusions
    raw = (tmp_path / path).read_bytes()
    assert scan_sources(ROOT, {path: raw}, policy(external=OWNERSHIP_LEGACY_EXTERNAL)) == []
    assert [f.kind for f in scan_sources(ROOT, {path: raw}, ownership_policy(exclusions))] == ["comment"]


@pytest.mark.parametrize(
    "source, expected",
    [
        (
            'import subprocess, sys\nfrom pathlib import Path\nroot = Path(__file__).resolve().parents[2]\nsubprocess.run([sys.executable, str(root / "scripts" / "validate.py"), *values])',
            [],
        ),
        (
            'import subprocess\nfrom pathlib import Path\nproject = Path(__file__).resolve().parent / "scripts"\nsubprocess.run(["uv", "run", "--project", str(project), "--locked", "--", "tool", value])',
            [],
        ),
        (
            'import subprocess\nfrom pathlib import Path\nproject = Path(__file__).resolve().parent / "scripts"\nsubprocess.run(["uv", "run", "--project", str(project), "python3", "-c", "# retained"])',
            ["# retained"],
        ),
    ],
)
def test_path_backed_command_operands_preserve_argument_roles(source: str, expected: list[str]) -> None:
    assert [finding.text for finding in python_findings("a.py", source)] == expected


@pytest.mark.parametrize(
    "source",
    [
        "import subprocess, sys\nsubprocess.run([sys.executable, script, *values])",
        'import subprocess\nfrom pathlib import Path\nproject = unknown / "scripts"\nsubprocess.run(["uv", "run", "--project", str(project), "tool"])',
        'import subprocess\nfrom pathlib import Path\nstr = converter\nproject = Path(__file__) / "scripts"\nsubprocess.run(["uv", "run", "--project", str(project), "tool"])',
        'import subprocess\nfrom pathlib import Path\nPath = constructor\nproject = Path(__file__) / "scripts"\nsubprocess.run(["uv", "run", "--project", str(project), "tool"])',
        'import subprocess\nfrom pathlib import Path\nproject = Path(__file__) / "scripts"\nsubprocess.run(["uv", "run", "--project", str(project), *command])',
        'import subprocess\nfrom pathlib import Path\nsource = str(Path(__file__) / "source.py")\nsubprocess.run(["python3", "-c", source])',
        'import subprocess\nfrom pathlib import Path\nsource = str(Path(__file__) / "source.py")\nsubprocess.run(["env", "-S", "python3 -c", source])',
        'import subprocess\nfrom pathlib import Path\nproject = Path(__file__) / "scripts"\nsubprocess.run(["uv", "run", "--project", str(project), "python3", "-c", code])',
        'import subprocess\nsubprocess.run(["uv", "run", "--with", package, "python3", "-c", code])',
        'import subprocess\nfrom textwrap import dedent\nsource = dedent(f"print(\\"{value}\\")")\nsubprocess.run(["python3", "-c", source])',
        'import subprocess\nfrom pathlib import Path\nsubprocess.run(["python3", str(Path("") / "-c"), code])',
        'import subprocess\nfrom pathlib import Path\nsubprocess.run(["uv", "run", "--project", str(Path("") / "-c"), "python3", "-c", code])',
        'import subprocess\nfrom pathlib import Path\nsubprocess.run(["uv", "run", str(Path("") / "-c"), code])',
        'import subprocess\nfrom pathlib import Path\nsubprocess.run(["env", "-S", "python3", str(Path("") / "-c"), code])',
        'import subprocess\nfrom pathlib import Path\nsubprocess.run(["python3", str(Path("") / "./-c"), code])',
        'import subprocess\nfrom pathlib import Path\nsubprocess.run(["python3", str(Path("") / ".\\\\-c"), code])',
        'import subprocess\nfrom pathlib import Path\nsubprocess.run(["python3", str(Path("-c") / "foo"), code])',
        'import subprocess\nfrom pathlib import Path\nsubprocess.run(["python3", str(Path("") / "-c/foo"), code])',
        'import subprocess\nfrom pathlib import Path\nsubprocess.run(["python3", str(Path("") / "-c\\\\foo"), code])',
        'import subprocess\nfrom pathlib import Path\nsubprocess.run(["python3", str(Path(root) / "script.py"), code])',
        'import subprocess\nfrom pathlib import Path\nsubprocess.run(["python3", str(Path("\\\\").parent / "-cprint(1)" / "x# hidden")])',
    ],
)
def test_unproven_command_paths_and_dynamic_source_fail_visibly(source: str) -> None:
    assert [finding.kind for finding in python_findings("a.py", source)] == ["payload-error"]


@pytest.mark.parametrize(
    "imports, executable",
    [
        ("import sys\nimport sys", "sys.executable"),
        ("import sys as runtime\nimport sys as runtime", "runtime.executable"),
        ("from sys import executable\nfrom sys import executable", "executable"),
    ],
)
def test_identical_import_actors_keep_inline_source_visible(imports: str, executable: str) -> None:
    source = f"import subprocess\n{imports}\nsubprocess.run([{executable}, '-c', '# explanation'])"
    findings = python_findings("a.py", source)
    assert [(finding.kind, finding.text) for finding in findings] == [("comment", "# explanation")]


@pytest.mark.parametrize(
    "binding",
    [
        "sys = opaque",
        "sys = sys",
        "from alternate import sys",
        "import alternate as sys",
    ],
)
def test_assignment_or_differing_import_actor_stays_ambiguous(binding: str) -> None:
    source = (
        f"import subprocess\nimport sys\nimport sys\n{binding}\nsubprocess.run([sys.executable, '-c', '# explanation'])"
    )
    findings = python_findings("a.py", source)
    assert [(finding.kind, finding.text) for finding in findings] == [
        ("payload-error", "unresolved executable source: '# explanation'")
    ]


def test_local_parameter_shadow_stays_ambiguous_with_duplicate_outer_imports() -> None:
    source = "import subprocess\nimport sys\nimport sys\ndef run(sys):\n    subprocess.run([sys.executable, '-c', '# explanation'])\n"
    findings = python_findings("a.py", source)
    assert [(finding.kind, finding.text) for finding in findings] == [
        ("payload-error", "unresolved executable source: '# explanation'")
    ]


@pytest.mark.parametrize(
    "tag, payload, comment",
    [
        ("c", "int value = 1; // explanation", "// explanation"),
        ("powershell", "$value = 1 # explanation", "# explanation"),
    ],
)
def test_document_fences_use_existing_c_and_powershell_adapters(tag: str, payload: str, comment: str) -> None:
    findings = scan("docs/example.md", f"```{tag}\n{payload}\n```\n", "examples")
    assert [(finding.kind, finding.text, finding.line) for finding in findings] == [("comment", comment, 2)]


def test_yaml_multiple_documents_scan_every_executable_scalar() -> None:
    source = "query: SELECT 1 -- first\n---\nquery: SELECT 2 -- second\n"
    findings = scan("config.yaml", source, "yaml")
    assert [(finding.kind, finding.text, finding.line) for finding in findings] == [
        ("comment", "-- first", 1),
        ("comment", "-- second", 3),
    ]
    assert [finding.symbol for finding in findings] == ["document:0.query:", "document:1.query:"]


@pytest.mark.parametrize("tail", ["query: [invalid", "query: *missing", "cycle: &cycle\n  child: *cycle"])
def test_yaml_later_document_errors_fail_closed(tail: str) -> None:
    findings = scan("config.yaml", f"query: SELECT 1 -- first\n---\n{tail}\n", "yaml")
    assert [finding.kind for finding in findings] == ["coverage-error"]


def test_json_does_not_accept_yaml_multiple_documents() -> None:
    findings = scan("config.json", '{"query": "SELECT 1 -- first"}\n---\n{"query": "SELECT 2 -- second"}\n', "json")
    assert [finding.kind for finding in findings] == ["coverage-error"]


@pytest.fixture
def dependency_artifact_inputs(tmp_path: Path, monkeypatch):
    import io
    import zipfile

    import check_comment_cleanup_scope as scope

    wheel_path = "_project/scripts/vendor/todo_db-0.8.1-py3-none-any.whl"
    project_path = "_project/scripts/pyproject.toml"
    lock_path = "_project/scripts/uv.lock"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("todo_db-0.8.1.dist-info/METADATA", "Name: todo-db\nVersion: 0.8.1\n")
        archive.writestr("todo_db/__init__.py", "value = 1\n")
    wheel = buffer.getvalue()
    project = b'[project]\ndependencies = ["todo-db[mcp]"]\n[tool.uv.sources]\ntodo-db = {path = "vendor/todo_db-0.8.1-py3-none-any.whl"}\n'
    digest = hashlib.sha256(wheel).hexdigest()
    lock = (
        '[[package]]\nname = "todo-db"\nversion = "0.8.1"\n'
        'source = {path = "vendor/todo_db-0.8.1-py3-none-any.whl"}\n'
        'wheels = [{filename = "todo_db-0.8.1-py3-none-any.whl", hash = "sha256:' + digest + '"}]\n'
    ).encode()
    frozen = {wheel_path: wheel, project_path: project, lock_path: lock}
    index = deepcopy(frozen)
    for path, raw in frozen.items():
        destination = tmp_path / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
    monkeypatch.setattr(scope, "base_blob", lambda root, base, path: frozen[path])
    monkeypatch.setattr(scope, "git", lambda root, *args: index[args[-1].removeprefix(":")])
    records = [{"path": wheel_path, "owner": "comment-cleanup-project-tooling", "rule": "tooling", "state": "blocked"}]
    rules = [{"id": "tooling", "selectors": [{"prefix": "_project/scripts/"}]}]
    external = [{"path": wheel_path, "owner": "todo-db", "provenance": project_path}]
    return scope, wheel_path, project_path, lock_path, frozen, index, records, rules, external


def test_trusted_locked_dependency_overrides_only_generic_prefix(tmp_path: Path, dependency_artifact_inputs) -> None:
    scope, path, _, _, _, _, records, rules, external = dependency_artifact_inputs
    assert scope.immutable_dependency_artifacts(tmp_path, "base", records, rules, external) == {path}
    assert records[0]["state"] == "excluded"


@pytest.mark.parametrize("guard", ["exact-owned", "derived", "notice", "candidate-only", "legacy-prefix", "hash"])
def test_dependency_classification_preserves_ownership_guards(
    tmp_path: Path, dependency_artifact_inputs, guard: str
) -> None:
    scope, path, _, lock_path, frozen, _, records, rules, external = dependency_artifact_inputs
    if guard == "exact-owned":
        rules[0]["selectors"] = [{"path": path}]
    elif guard in {"derived", "notice"}:
        records[0]["rule"] = guard
    elif guard == "candidate-only":
        external.clear()
    elif guard == "legacy-prefix":
        external[0]["path"] = "_project/scripts/vendor/"
    elif guard == "hash":
        frozen[lock_path] = frozen[lock_path].replace(hashlib.sha256(frozen[path]).hexdigest().encode(), b"0" * 64)
    assert scope.immutable_dependency_artifacts(tmp_path, "base", records, rules, external) == set()
    assert records[0]["state"] == "blocked"


@pytest.mark.parametrize("staged", [False, True])
@pytest.mark.parametrize("changed", ["artifact", "dependency", "lock"])
def test_dependency_replacements_fail_closed(
    tmp_path: Path, dependency_artifact_inputs, staged: bool, changed: str
) -> None:
    scope, path, project_path, lock_path, frozen, index, records, rules, external = dependency_artifact_inputs
    changed_path = {"artifact": path, "dependency": project_path, "lock": lock_path}[changed]
    raw = frozen[changed_path]
    if changed == "artifact":
        raw += b"replacement"
    elif changed == "dependency":
        raw = raw.replace(b'"todo-db[mcp]"', b'"other-package"')
    else:
        raw = raw.replace(hashlib.sha256(frozen[path]).hexdigest().encode(), b"0" * 64)
    if staged:
        index[changed_path] = raw
    else:
        (tmp_path / changed_path).write_bytes(raw)
    with pytest.raises(scope.PolicyError, match="immutable dependency (artifact|binding) changed"):
        scope.immutable_dependency_artifacts(tmp_path, "base", records, rules, external, staged)


@pytest.mark.parametrize("staged", [False, True])
def test_dependency_protection_reads_selected_tree(tmp_path: Path, dependency_artifact_inputs, staged: bool) -> None:
    scope, path, _, _, frozen, index, records, rules, external = dependency_artifact_inputs
    if staged:
        (tmp_path / path).write_bytes(frozen[path] + b"unstaged replacement")
    else:
        index[path] += b"staged replacement"
    assert scope.immutable_dependency_artifacts(tmp_path, "base", records, rules, external, staged) == {path}


@pytest.mark.parametrize("changed", [False, True])
def test_preclassified_external_dependency_still_checks_immutable_bytes(
    tmp_path: Path, dependency_artifact_inputs, changed: bool
) -> None:
    scope, path, _, _, frozen, _, records, rules, external = dependency_artifact_inputs
    records[0].update(rule="external", state="excluded")
    if changed:
        (tmp_path / path).write_bytes(frozen[path] + b"replacement")
        with pytest.raises(scope.PolicyError, match="immutable dependency artifact changed"):
            scope.immutable_dependency_artifacts(tmp_path, "base", records, rules, external)
    else:
        assert scope.immutable_dependency_artifacts(tmp_path, "base", records, rules, external) == {path}


@pytest.mark.parametrize(
    "name,source,language,expected",
    [
        (
            "dbt",
            "{{ config(materialized='incremental') }}\nSELECT * FROM {{ ref('orders') }} -- explanation",
            "sql+jinja",
            "comment",
        ),
        ("literal", "{{ '-- hidden prose' }}", "sql+jinja", "comment"),
        ("binding", '{% set sql="SELECT 1 -- hidden" %}{{ sql }}', "sql+jinja", "comment"),
        ("quoted", "SELECT '{{ '-- data' }}'", "sql+jinja", None),
        ("malformed", "SELECT {{ unclosed\n-- hidden", "sql+jinja", "coverage-error"),
        ("unknown", "{{ unknown() }}", "sql+jinja", "coverage-error"),
        ("hook", "{{ config(pre_hook='SELECT 1 -- hidden') }}SELECT 1", "sql+jinja", "coverage-error"),
        ("filter", "{{ '-- hidden' | trim }}", "sql+jinja", "coverage-error"),
        ("forward", '{{ sql }}{% set sql="SELECT 1" %}', "sql+jinja", "coverage-error"),
        ("branchbinding", '{% if x %}{% set sql="SELECT 1" %}{% endif %}{{ sql }}', "sql+jinja", "coverage-error"),
        (
            "branches",
            "SELECT '{% if is_incremental() %}a'{% else %}b' -- user's hidden{% endif %}",
            "sql+jinja",
            "comment",
        ),
        ("htmlinvalid", "<html>{{ broken</html>", "html+jinja", "coverage-error"),
        ("groovy", "sh '''\n# hidden shell prose\necho ok\n'''", "groovy", "comment"),
        ("groovydynamic", "sh command", "groovy", "coverage-error"),
        ("groovygstring", 'sh "echo $SECRET"', "groovy", "coverage-error"),
        ("groovyescape", "sh 'echo \\nvalue'", "groovy", "coverage-error"),
        ("groovycompound", "sh 'echo ok' + command", "groovy", "coverage-error"),
        ("groovyexecute", "'echo ok'.execute()", "groovy", "coverage-error"),
        ("htmloutput", "<script>{{ payload }}</script>", "html+jinja", "coverage-error"),
        ("htmlliteral", '<script>{{ "// hidden" }}</script>', "html+jinja", "coverage-error"),
        ("htmlemittedtags", '{{ "<script>// hidden</script>" }}', "html+jinja", "coverage-error"),
        ("htmlbranch", "{% if x %}<script>// hidden</script>{% endif %}", "html+jinja", "coverage-error"),
        ("htmlstatic", "<div>{# template prose #}</div>", "html+jinja", "comment"),
        ("quotedcallee", 'this."sh"("echo ok # hidden")', "groovy", "coverage-error"),
        ("unclosedcall", "sh('echo ok'", "groovy", "coverage-error"),
        ("closedcall", "sh('echo ok # hidden')", "groovy", "comment"),
        ("namedclosed", "sh(script: 'echo ok # hidden')", "groovy", "comment"),
        ("uniquekeysource", '{{ config(unique_key="id -- hidden") }} SELECT 1', "sql+jinja", "coverage-error"),
        (
            "uniquekeyexpression",
            '{{ config(unique_key="concat(user_id,session_number)") }} SELECT 1',
            "sql+jinja",
            "coverage-error",
        ),
        (
            "materializedunknown",
            '{{ config(materialized="custom -- hidden") }} SELECT 1',
            "sql+jinja",
            "coverage-error",
        ),
        (
            "schemachangeunknown",
            '{{ config(on_schema_change="custom -- hidden") }} SELECT 1',
            "sql+jinja",
            "coverage-error",
        ),
    ],
)
def test_template_and_groovy_carriers_fail_closed(name, source, language, expected):
    findings = scan("case." + language, source, language)
    if expected is None:
        assert findings == [], name
    else:
        assert expected in {finding.kind for finding in findings}, name


def test_groovy_shell_payload_location():
    findings = scan("Jenkinsfile", "sh '''\n# shell prose\necho ok\n'''", "groovy")
    assert [(finding.line, finding.text) for finding in findings if finding.kind == "comment"] == [(2, "# shell prose")]


def test_owned_template_scripts_are_scanned_beside_rendered_output():
    source = "{% block body %}{{ body }}<script>// owned\nconsole.log(1)</script>{% endblock %}"
    requests = javascript_requests("page.html", source, "html+jinja")
    assert list(requests.values()) == ["// owned\nconsole.log(1)"]
    results = {key: [{"kind": "comment", "line": 1, "text": "// owned"}] for key in requests}
    findings = scan("page.html", source, "html+jinja", results)
    assert not any(finding.kind == "coverage-error" for finding in findings)
    assert any(finding.kind == "comment" and finding.text == "// owned" for finding in findings)


@pytest.mark.parametrize(
    "source",
    [
        "<script>{{ payload }}</script>",
        '<script>{{ "// hidden" }}</script>',
        '{{ "<script>// hidden</script>" }}',
        "<script>{% if x %}// hidden{% endif %}</script>",
    ],
)
def test_template_output_is_not_treated_as_literal_javascript(source):
    assert javascript_requests("page.html", source, "html+jinja") == {}
    assert any(finding.kind == "coverage-error" for finding in scan("page.html", source, "html+jinja"))


def test_owned_template_css_comments_are_found_inside_template_logic():
    source = "{% if x %}<style>/* owned CSS */a{color:red}</style>{% endif %}"
    findings = scan("page.html", source, "html+jinja")
    assert not any(finding.kind == "coverage-error" for finding in findings)
    assert any(finding.kind == "comment" and "owned CSS" in finding.text for finding in findings)


ASTRO_SOURCE = """---
// frontmatter explanation
const title = "x";
---
<!-- template explanation -->
<div>{/* expression explanation */}{title}</div>
<style>
  /* style explanation */
  div { color: red; }
</style>
<script>
  // script explanation
  console.log("<!-- data -->");
</script>
"""


def test_astro_sources_are_a_registered_maintained_language() -> None:
    from comment_syntax import language

    assert language("website/src/pages/index.astro") == "astro"
    assert language("website/src/data.unregistered") == "unsupported"
    assert "website/" in json.loads((ROOT / "quality/comment-policy.json").read_text(encoding="utf-8"))["completed"]


def test_astro_template_comments_are_found_without_flagging_embedded_blocks() -> None:
    source = "<div>{/* expression */}</div>\n<!-- html -->\n<style>a::after { content: '<!-- data -->'; }</style>\n"
    findings = scan("a.astro", source, "astro", {k: [] for k in javascript_requests("a.astro", source, "astro")})
    assert [(f.line, f.kind, f.text) for f in findings if f.kind == "comment" and f.text.startswith("<!--")] == [
        (2, "comment", "<!-- html -->")
    ]


ASTRO_STRING_MARKER_SOURCES = [
    '<a title="<!-- data -->">x</a>\n',
    "<a title='<!-- data -->'>x</a>\n",
    '<p>{"<!-- data -->"}</p>\n',
    "<p>{`<!-- ${x} -->`}</p>\n",
    "---\nconst s = '<!-- data -->';\n---\n<p>x</p>\n",
]


def test_astro_html_comment_markers_inside_strings_are_data() -> None:
    for source in ASTRO_STRING_MARKER_SOURCES:
        assert not [
            f
            for f in scan("a.astro", source, "astro", {k: [] for k in javascript_requests("a.astro", source, "astro")})
            if f.text.startswith("<!--")
        ]


@pytest.mark.parametrize(
    "source,expressions",
    [
        ("<p>{x /* c */}</p>\n", ["x /* c */"]),
        ("<ul>{items.map((i) => /* c */ i)}</ul>\n", ["items.map((i) => /* c */ i)"]),
        ("<p>{\n// c\nx}</p>\n", ["\n// c\nx"]),
        ('<a href={u /* c */} class="b">x</a>\n', ["u /* c */"]),
        ("<p>{cond && <b>{y /* c */}</b>}</p>\n", ["cond && <b>{y /* c */}</b>"]),
        ("<p>{cond && <b>don't {y}</b> /* c */}</p>\n", ["cond && <b>don't {y}</b> /* c */"]),
        ("<p>{`a ${b /* c */} d`}</p>\n", ["`a ${b /* c */} d`"]),
    ],
)
def test_astro_template_expressions_are_scanned_as_typescript(source: str, expressions: list[str]) -> None:
    requests = javascript_requests("a.astro", source, "astro")
    assert list(requests.values()) == [f"[\n{text}\n];" for text in expressions]


@pytest.mark.parametrize(
    "source",
    [
        "<script>{# note #}// hidden</script>",
        "<scr{# note #}ipt>// hidden</script>",
    ],
)
def test_template_comments_cannot_hide_assembled_executable_regions(source):
    findings = scan("page.html", source, "html+jinja")
    assert any(finding.kind == "coverage-error" for finding in findings)


def test_sphinx_path_selects_template_adapter_without_waiving_dynamic_output():
    source = "<script>{{ payload }}</script>"
    lang = source_language("docs/_templates/page.html", source)
    assert lang == "html+jinja"
    assert any(f.kind == "coverage-error" for f in scan("docs/_templates/page.html", source, lang))


def test_sphinx_path_preserves_literal_style_comments():
    source = "<style>/* owned */a{color:red}</style>"
    lang = source_language("docs/_templates/page.html", source)
    assert any(f.kind == "comment" and "owned" in f.text for f in scan("docs/_templates/page.html", source, lang))


def test_sphinx_alias_leaves_other_html_paths_unchanged():
    assert source_language("landing/index.html", "<p>hi</p>") == "html"


def notebook_cell(source: str, metadata: dict | None = None) -> str:
    return json.dumps(
        {
            "metadata": metadata
            if metadata is not None
            else {"language_info": {"name": "python", "pygments_lexer": "ipython3"}},
            "cells": [{"id": "stable", "cell_type": "code", "source": source.splitlines(keepends=True)}],
        }
    )


def test_ipython_literal_shell_and_python_offsets() -> None:
    assert not scan("a.ipynb", notebook_cell("!pip install -q benchbox duckdb\n"), "notebook")
    findings = scan(
        "a.ipynb", notebook_cell("!echo ok # shell comment\n%matplotlib inline\n# Python comment\n"), "notebook"
    )
    assert [(f.line, f.text, f.symbol) for f in findings if f.kind == "comment"] == [
        (3, "# Python comment", "cell:stable:"),
        (1, "# shell comment", "cell:stable:shell:"),
    ]
    assert any(f.kind == "coverage-error" for f in findings)
    findings = scan("a.ipynb", notebook_cell('!python -c "# nested"\n'), "notebook")
    assert [(f.kind, f.text) for f in findings if f.kind == "comment"] == [("comment", "# nested")]
    assert any(f.kind == "coverage-error" for f in findings)


@pytest.mark.parametrize(
    "source",
    [
        "%run evil.py\n",
        "!cmd /c rem hidden prose\n",
        "!powershell -Command Write-Output\n",
        "%pip uninstall package\n",
        "%%bash\n# hidden\n",
        "value = !echo ok\n",
        "!!echo ok\n",
        "!echo {dangerous()}\n",
        "!echo $name\n",
        "!echo a" + chr(92) + "\nb\n",
        "if True:\n    !echo ok\n",
        "x = (\n!echo ok\n)\n",
        "x = " + chr(92) + "\n!echo ok\n",
        "%matplotlib inline # comment\n",
        '!python -c "$CODE"\n',
    ],
)
def test_unknown_ipython_constructs_remain_coverage_errors(source: str) -> None:
    findings = scan("a.ipynb", notebook_cell(source), "notebook")
    assert any(f.kind == "coverage-error" for f in findings)


def test_notebook_multiline_strings_are_not_magics() -> None:
    source = 'x = """\n!echo literal\n%matplotlib inline\n"""\n# retained\n'
    findings = scan("a.ipynb", notebook_cell(source), "notebook")
    assert [(f.line, f.text) for f in findings] == [(5, "# retained")]


@pytest.mark.parametrize(
    "metadata",
    [
        {"language_info": {"name": "python"}},
        {"language_info": {"name": "python", "codemirror_mode": "python"}},
        {"language_info": []},
    ],
)
def test_notebook_requires_explicit_ipython_metadata(metadata: dict) -> None:
    findings = scan("a.ipynb", notebook_cell("!echo ok\n", metadata), "notebook")
    assert any(f.kind == "coverage-error" for f in findings)


def test_notebook_unknown_cell_does_not_hide_other_cells() -> None:
    source = json.loads(notebook_cell("%run evil.py\n"))
    source["cells"].extend(
        [
            {"id": "python", "cell_type": "code", "source": ["# retained Python\n"]},
            {"id": "shell", "cell_type": "code", "source": ["!echo ok # retained shell\n"]},
        ]
    )
    findings = scan("a.ipynb", json.dumps(source), "notebook")
    assert any(f.kind == "coverage-error" and f.symbol == "cell:stable:" for f in findings)
    assert {f.text for f in findings if f.kind == "comment"} == {"# retained Python", "# retained shell"}


def test_notebook_static_pip_arguments_and_offsets() -> None:
    source = "%pip install benchbox[databricks] matplotlib seaborn pandas --quiet\n# retained\n"
    findings = scan("a.ipynb", notebook_cell(source), "notebook")
    assert [(f.line, f.kind, f.text) for f in findings] == [(2, "comment", "# retained")]


@pytest.mark.parametrize(
    "source",
    [
        "%pip uninstall package\n",
        "%pip install $PACKAGE\n",
        "%pip install {package}\n",
        "%pip install package; python -c pass\n",
        "%pip install package # hidden\n",
        '%pip install "package"\n',
        "%pip install --quiet\n",
        "%pip install -r requirements.txt\n",
        "%pip install --config-settings x=y package\n",
        "%pip install git+https://example.test/package\n",
        "%%pip install package\n",
        "x = (\n%pip install package\n)\n",
        "x = \\\n%pip install package\n",
        "x = %pip install package\n",
        "%pip install demo-1.0-py3-none-any.whl\n",
        "%pip install demo.tar.gz\n",
        "%pip install demo.zip\n",
        "%pip install demo.tar.bz2\n",
        "%pip install demo.tar.xz\n",
        "%pip install demo.tar.lz\n",
        "%pip install demo.tar.lzma\n",
        "%pip install demo.WHL[extra]\n",
        "%pip install demo.ta[r]\n",
        "%pip install .\n",
        "%pip install ../project\n",
        "%pip install -r requirements.txt\n",
        "%pip install https://example.test/demo.whl\n",
        "!pip install demo-1.0-py3-none-any.whl\n",
        "!pip install demo.tar.gz\n",
        "!pip install demo.zip\n",
        "!pip install demo.tar.bz2\n",
        "!pip install demo.tar.xz\n",
        "!pip install demo.tar.lz\n",
        "!pip install demo.tar.lzma\n",
        "!pip install demo.WHL[extra]\n",
        "!pip install demo.ta[r]\n",
        "!pip install .\n",
        "!pip install ../project\n",
        "!pip install -r requirements.txt\n",
        "!pip install https://example.test/demo.whl\n",
        "!pip3 install demo-1.0-py3-none-any.whl\n",
        "!pip3 install demo.tar.gz\n",
        "!pip3 install demo.zip\n",
        "!pip3 install demo.tar.bz2\n",
        "!pip3 install demo.tar.xz\n",
        "!pip3 install demo.tar.lz\n",
        "!pip3 install demo.tar.lzma\n",
        "!pip3 install demo.WHL[extra]\n",
        "!pip3 install demo.ta[r]\n",
        "!pip3 install .\n",
        "!pip3 install ../project\n",
        "!pip3 install -r requirements.txt\n",
        "!pip3 install https://example.test/demo.whl\n",
    ],
)
def test_notebook_pip_source_and_file_options_remain_unknown(source: str) -> None:
    findings = scan("a.ipynb", notebook_cell(source), "notebook")
    assert any(f.kind == "coverage-error" for f in findings)


@pytest.mark.parametrize(
    "source",
    [
        '(directory / "page.html").write_text(dynamic)',
        '(directory / "page.html").write_text(data=dynamic)',
        'HTML = "<style>body{}</style>"\nHTML = dynamic\n(directory / "page.html").write_text(HTML)',
        'HTML = "<script>\\n// hidden</script>"\n(directory / "page.html").write_text(HTML)',
        'HTML = "<script>" + "// hidden</script>"\n(directory / "page.html").write_text(HTML)',
        'HTML = "<script>" "// hidden</script>"\n(directory / "page.html").write_text(HTML)',
        '(directory / "page.html").write_text(f"<script>{dynamic}</script>")',
        '(directory / "page.html").write_text()',
    ],
)
def test_python_html_output_unresolved_source_is_visible(source: str) -> None:
    source = 'from pathlib import Path\ndirectory = Path("/tmp")\n' + source
    findings = scan("a.py", source, "python", {})
    assert any(f.kind == "payload-error" and f.text == "unresolved HTML output source" for f in findings)
    assert not javascript_requests("a.py", source, "python")


@pytest.mark.parametrize(
    "source",
    [
        'HTML = "<script>// inert</script>"',
        '(directory / "page.txt").write_text("<script>// data</script>")',
        'help_text = "A <script> tag"',
    ],
)
def test_python_non_html_output_stays_data(source: str) -> None:
    assert not scan("a.py", source, "python", {})
    assert not javascript_requests("a.py", source, "python")


@pytest.mark.parametrize(
    "writer",
    [
        '(directory / "page.html").write_text(HTML)',
        '(directory / "page.HTML").write_text(data=HTML)',
        'from pathlib import Path\nPath("page.htm").write_text(HTML)',
        'from pathlib import Path as P\nP("page.html").write_text(HTML)',
    ],
)
def test_python_html_output_uses_declaration_offsets(writer: str) -> None:
    source = (
        'from pathlib import Path\ndirectory = Path("/tmp")\nHTML = """<script>\n// visible\n</script>"""\n\n' + writer
    )
    requests = javascript_requests("a.py", source, "python")
    assert len(requests) == 1
    rows = {key: [{"line": 2, "kind": "comment", "text": "// visible", "symbol": ""}] for key in requests}
    findings = scan("a.py", source, "python", rows)
    assert [(f.kind, f.line, f.text) for f in findings] == [("comment", 4, "// visible")]
    missing = scan("a.py", source, "python", {})
    assert any(f.kind == "coverage-error" and "TypeScript parser result missing" in f.text for f in missing)


def test_published_404_html_reaches_native_scanner(monkeypatch: pytest.MonkeyPatch) -> None:
    source = (ROOT / "scripts/assemble_public_site.py").read_text(encoding="utf-8")
    comment = "// checker fixture"
    source = source.replace("<script>", "<script>\n" + comment, 1)
    requests = javascript_requests("scripts/assemble_public_site.py", source, "python")
    assert len(requests) == 1
    payload = next(iter(requests.values()))
    rows = {
        key: [
            {
                "line": payload[: payload.index(comment)].count("\n") + 1,
                "kind": "comment",
                "text": comment,
                "symbol": "",
            }
        ]
        for key in requests
    }

    def native_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        assert json.loads(str(kwargs["input"])) == requests
        return subprocess.CompletedProcess(command, 0, json.dumps(rows))

    monkeypatch.setattr("check_comment_policy.subprocess.run", native_run)
    findings = scan_sources(ROOT, {"scripts/assemble_public_site.py": source.encode()}, policy())
    visible = [f for f in findings if f.text == comment]
    assert len(visible) == 1
    assert visible[0].line == source[: source.index(comment)].count("\n") + 1


def test_python_html_output_invalid_python_retains_coverage_error() -> None:
    source = '(directory / "page.html").write_text('
    assert javascript_requests("a.py", source, "python") == {}
    assert scan("a.py", source, "python")[0].kind == "coverage-error"


@pytest.mark.parametrize(
    "call",
    [
        '(directory / "page.html").write_text("<script>const x=1;</script>", data=dynamic)',
        '(directory / "page.html").write_text(data="<script>const x=1;</script>", **options)',
        '(directory / "page.html").write_text("<script>const x=1;</script>", **options)',
        '(directory / "page.html").write_text(*arguments)',
    ],
)
def test_python_html_output_ambiguous_argument_binding_is_visible(call: str) -> None:
    source = 'from pathlib import Path\ndirectory = Path("/tmp")\n' + call
    assert any(f.kind == "payload-error" for f in scan("a.py", source, "python", {}))
    assert not javascript_requests("a.py", source, "python")


def test_non_path_html_named_data_sink_is_not_executed_source() -> None:
    source = 'class DataSink:\n    def __truediv__(self, name): return self\n    def write_text(self, data): return data\nvalue = DataSink()\n(value / "page.html").write_text("<script>// data</script>")\n'
    assert not javascript_requests("a.py", source, "python")
    assert not scan("a.py", source, "python", {})


def test_path_parameter_rebinding_does_not_prove_html_sink() -> None:
    source = 'from pathlib import Path\ndef write(directory: Path):\n    directory = arbitrary_object\n    (directory / "page.html").write_text("<script>// data</script>")\n'
    assert not javascript_requests("a.py", source, "python")
    assert any(f.kind == "payload-error" for f in scan("a.py", source, "python", {}))


def test_path_parameter_html_sink_preserves_comment_line() -> None:
    source = 'from pathlib import Path\ndef write(directory: Path):\n    directory = directory.resolve()\n    (directory / "page.html").write_text("""<style>\n/* visible */\n</style>""")\n'
    assert [(f.line, f.text) for f in scan("a.py", source, "python", {})] == [(5, "/* visible */")]


def test_unknown_division_html_sink_stays_unresolved() -> None:
    source = '(unknown / "page.html").write_text("<script>// unresolved</script>")'
    assert not javascript_requests("a.py", source, "python")
    assert any(f.kind == "payload-error" for f in scan("a.py", source, "python", {}))


def test_c_include_targets_are_not_comments() -> None:
    findings = scan("a.c", '#include <stdio.h>\n#include "local.h"\nint x; /* note */\n', "c")
    assert [(f.line, f.text) for f in findings] == [(3, "/* note */")]


@pytest.mark.parametrize(
    ("path", "source", "expected"),
    [
        ("tests/a.jsonl", '{"a": 1}\n\n{"b": [2]}\n', []),
        ("tests/a.xml", "<a><!-- note --><b/></a>\n", [(1, "<!-- note -->")]),
        ("docs/CNAME", "example.org\n", []),
    ],
)
def test_data_formats_have_adapters(path: str, source: str, expected: list) -> None:
    findings = scan(path, source, language(path))
    assert [(f.line, f.text) for f in findings] == expected


def test_invalid_json_line_is_a_coverage_error() -> None:
    findings = scan("tests/a.jsonl", '{"a": 1}\n# not json\n', "jsonl")
    assert [f.kind for f in findings] == ["coverage-error"]


@pytest.mark.parametrize("path", ["tests/parity/fixtures/.gitkeep", "docs/operations/key.pem"])
def test_inert_data_files_are_not_sources(path: str) -> None:
    assert language(path) is None


def test_reviewed_process_argv_is_exact() -> None:
    from comment_execution import REVIEWED_PROCESS_ARGV

    path, argv = next(key for key in REVIEWED_PROCESS_ARGV if key[0] == "benchbox/core/tpch/streams.py")
    source = f"import subprocess\ncmd = {argv}\nsubprocess.run(cmd)\n"
    assert not [f for f in scan(path, source, "python", {}) if f.kind == "payload-error"]
    assert [f.kind for f in scan("other.py", source, "python", {})] == ["payload-error"]
    edited = source.replace("'-p'", "'-c'")
    assert [f.kind for f in scan(path, edited, "python", {})] == ["payload-error"]


def test_reviewed_process_argv_entries_match_current_sources() -> None:
    from comment_execution import REVIEWED_PROCESS_ARGV, PythonBindings

    for path, argv in REVIEWED_PROCESS_ARGV:
        tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
        bindings = PythonBindings(tree)
        assert any(isinstance(node, ast.Call) and bindings.reviewed_argv(path, node) for node in ast.walk(tree)), (
            path,
            argv,
        )


def test_absolute_literal_path_executable_is_resolved() -> None:
    source = "import subprocess\nfrom pathlib import Path\nhelper = Path('/usr/libexec/java_home')\nsubprocess.run([str(helper), '-v', '17'])\n"
    assert scan("a.py", source, "python", {}) == []


def test_module_file_path_script_operand_is_resolved() -> None:
    source = "import subprocess, sys\nfrom pathlib import Path\nscript = Path(__file__).parent / 'run.py'\nsubprocess.run([sys.executable, str(script)])\n"
    assert scan("a.py", source, "python", {}) == []
    rebound = "__file__ = '-c'\n" + source
    assert [f.kind for f in scan("a.py", rebound, "python", {})] == ["payload-error"]


def test_tailwind_apply_does_not_hide_css_comments() -> None:
    source = ".btn {\n  @apply inline-flex px-4;\n}\n/* note */\n"
    assert [(f.line, f.kind) for f in scan("a.css", source, "css")] == [(4, "comment")]


@pytest.mark.parametrize(
    ("literal", "kinds"),
    [("_binaries/dsdgen", []), ("-c", ["payload-error"])],
)
def test_literal_path_executable_must_not_be_an_option(literal: str, kinds: list) -> None:
    source = f"import subprocess\nfrom pathlib import Path\nexe = Path({literal!r})\nsubprocess.run([str(exe), '-c', 'print(1)'])\n"
    assert [f.kind for f in scan("a.py", source, "python", {})] == kinds


@pytest.mark.parametrize(
    ("source", "kinds"),
    [
        ("{% for p in posts %}<li>{{ p.title }}</li>{% endfor %}\n", []),
        ("{% if x %}<!-- note -->{% endif %}\n", ["comment"]),
        ("<script>var a = '{{ name }}';</script>\n", ["coverage-error"]),
        ("{{ '<!-- x -->' }}\n", ["coverage-error"]),
        ('<script data-v="{{ v }}">var a = 1;</script>\n', []),
    ],
)
def test_jinja_html_template_logic_is_bounded(source: str, kinds: list) -> None:
    assert [f.kind for f in scan("docs/_templates/a.html", source, "html+jinja", {})] == kinds


@pytest.mark.parametrize(
    ("source", "kinds"),
    [
        ('read -r -a parts <<< "$(git rev-list --parents -n 1 HEAD)"\n', []),
        ('if ! jq -e . <<<"$payload" >/dev/null; then\n  exit 1\nfi\n', []),
        ('value=$(jq -r .a <<< "$json")\n', []),
        ('python3 <<< "$code"\n', ["coverage-error"]),
        ('hammerdbcli <<< "$script"\n', ["coverage-error"]),
        ('xargs -n1 <<< "$items"\n', ["coverage-error"]),
    ],
)
def test_here_string_consumers_are_classified(source: str, kinds: list) -> None:
    assert [f.kind for f in scan("a.sh", source, "bash")] == kinds


def test_github_expressions_in_run_scripts_are_data() -> None:
    source = (
        "jobs:\n  a:\n    steps:\n      - run: |\n"
        '          uv run -- python -c "\n'
        "          name = '${{ matrix.comparison }}'\n"
        "          # hidden\n"
        "          print(name)\n"
        '          "\n'
    )
    findings = scan(".github/workflows/a.yml", source, "yaml", {})
    assert [f.kind for f in findings] == ["comment"]
    assert findings[0].text == "# hidden"


def test_github_expressions_outside_workflows_stay_dynamic() -> None:
    source = "steps:\n  - run: |\n      uv run -- python -c \"print('${{ x }}')\"\n"
    assert [f.kind for f in scan("docs/a.yml", source, "yaml", {})] == ["coverage-error"]


@pytest.mark.parametrize(
    ("source", "kinds"),
    [
        ('uv run --with "$wheel" -- python -c "import sys  # note"\n', ["comment"]),
        ('env A="$x" python -c "print(1)  # note"\n', ["comment"]),
        ('uv run --with "$wheel" python -c "print(1)"\n', ["coverage-error"]),
        ('uv run -- python -c "$code"\n', ["coverage-error"]),
    ],
)
def test_wrapper_dynamic_words_before_the_command(source: str, kinds: list) -> None:
    assert sorted(f.kind for f in scan("a.sh", source, "bash")) == kinds


def test_shell_fallback_scans_interpreter_chunks_when_full_parse_fails() -> None:
    source = 'X=$(git show a:b 2>/dev/null \\\n  || echo "{}")\npython3 -c "import sys  # note"\n'
    findings = scan("a.sh", source, "bash")
    assert [(f.kind, f.line, f.text) for f in findings] == [("comment", 3, "# note")]


def test_shell_fallback_keeps_unparsable_interpreter_chunks_visible() -> None:
    source = 'X=$(git show a:b 2>/dev/null \\\n  || echo "{}")\npython3 -c "$(cat <<EOF\nprint(1)\nEOF\n)"\n'
    assert any(f.kind == "coverage-error" for f in scan("a.sh", source, "bash"))


@pytest.mark.parametrize(
    ("source", "kinds"),
    [
        ("gh pr create --title t \\\n  --body \"$(cat <<'EOF'\n# Heading is PR text\nEOF\n)\"\n", []),
        ("BODY=$(cat <<EOF | awk '{print}'\ntext\nEOF\n)\n", []),
        ("X=\"$(python3 - <<'PY'\nimport os  # note\nPY\n)\"\n", ["comment"]),
    ],
)
def test_heredoc_inside_command_substitution(source: str, kinds: list) -> None:
    assert [f.kind for f in scan("a.sh", source, "bash")] == kinds


def test_shell_list_operators_inside_substitution_do_not_hide_inline_code() -> None:
    source = 'X=$(a | b \\\n  || true)\nY=$(echo "$L" | python3 -c "import sys  # note" || echo "")\n'
    assert [(f.kind, f.text) for f in scan("a.sh", source, "bash")] == [("comment", "# note")]


@pytest.mark.parametrize(
    ("body", "kinds"),
    [("Escaped \\`code\\` and \\$(not run)\n", []), ("Real `date`\n", ["coverage-error"])],
)
def test_unquoted_heredoc_ignores_escaped_substitutions(body: str, kinds: list) -> None:
    source = "cat <<EOF\n" + body + "EOF\n"
    assert [f.kind for f in scan("a.sh", source, "bash")] == kinds


def test_uv_run_interpreter_with_dynamic_script_arguments() -> None:
    source = 'uv run python -c "import sys  # note" "$RUN_ID"\n'
    assert [(f.kind, f.text) for f in scan("a.sh", source, "bash")] == [("comment", "# note")]


@pytest.mark.parametrize(
    ("source", "kinds"),
    [
        ('uv pip install --python "$venv/bin/python" "$wheel" && python3 -c "import sys  # note"\n', ["comment"]),
        ("cat > out.md << EOF\n# Title\nGenerated on $(date).\nEOF\n", []),
        ("cat > out.md << EOF\nRun $(echo a # hidden)\nEOF\n", ["coverage-error"]),
        ('python3 - << EOF\nprint("$(date)")\nEOF\n', ["coverage-error"]),
    ],
)
def test_inert_uv_subcommands_and_simple_data_heredoc_substitutions(source: str, kinds: list) -> None:
    assert [f.kind for f in scan("a.sh", source, "bash")] == kinds


def test_reviewed_javascript_flows_are_exact_and_current() -> None:
    from comment_syntax import REVIEWED_JAVASCRIPT_FLOWS, javascript_key

    for path, text in REVIEWED_JAVASCRIPT_FLOWS:
        source = (ROOT / path).read_text(encoding="utf-8")
        row = {"kind": "coverage-error", "line": 1, "text": text}
        key = javascript_key(path, source)
        assert scan(path, source, "javascript", {key: [row]}) == []
        other = "results-explorer/src/other.ts"
        assert [f.text for f in scan(other, source, "javascript", {javascript_key(other, source): [row]})] == [text]


def test_reviewed_javascript_flows_still_occur() -> None:
    import json
    import os
    import shutil
    import subprocess

    from comment_syntax import REVIEWED_JAVASCRIPT_FLOWS

    typescript = os.environ.get("COMMENT_POLICY_TYPESCRIPT", str(ROOT / "results-explorer/node_modules/typescript"))
    if shutil.which("node") is None or not Path(typescript).exists():
        pytest.skip("the TypeScript package for scripts/comment_syntax_js.cjs is not installed")
    paths = sorted({path for path, _ in REVIEWED_JAVASCRIPT_FLOWS})
    requests = {path: (ROOT / path).read_text(encoding="utf-8") for path in paths}
    result = subprocess.run(
        ["node", str(ROOT / "scripts/comment_syntax_js.cjs")],
        input=json.dumps(requests),
        capture_output=True,
        text=True,
        check=True,
    )
    observed = {(path, row["text"]) for path, rows in json.loads(result.stdout).items() for row in rows}
    assert set(REVIEWED_JAVASCRIPT_FLOWS) <= observed


@pytest.mark.parametrize(
    ("source", "kind"),
    [
        ('import subprocess\nsubprocess.run(["perl", "-e", "# hello"])\n', "payload-error"),
        ('import subprocess\nsubprocess.run(["sudo", "python3", "-c", "# hello"])\n', "comment"),
        ('import subprocess\nsubprocess.run(["-c", "# hi"], executable="python3")\n', "payload-error"),
        ('import os\nos.system("echo hi # there")\n', "comment"),
        ('import os\nos.popen("echo hi # there")\n', "comment"),
        ('import asyncio\nasyncio.create_subprocess_shell("echo hi # there")\n', "comment"),
        ('import subprocess\nsubprocess.run(["echo hi # there"], shell=True)\n', "comment"),
        ('import subprocess\nsubprocess.run(["uv", "tool", "run", "python", "-c", "# hello"])\n', "comment"),
        ('import subprocess\nsubprocess.run(["timeout", "5", "python3", "-c", "x = 1  # n"])\n', "comment"),
        ('import os\nos.execvp("python3", ["python3", "-c", code])\n', "payload-error"),
        ('from pathlib import Path\nPath("o.html").write_bytes(b"<!-- hi -->")\n', "comment"),
        ('open("o.html", "w").write("<!-- hi -->")\n', "comment"),
        ('with open("o.html", "w") as f:\n    f.write("<!-- hi -->")\n', "comment"),
        ('from pathlib import Path\np = Path("/tmp/o")\np.with_suffix(".html").write_text("<!-- hi -->")\n', "comment"),
    ],
)
def test_python_process_and_html_sinks_fail_closed(source: str, kind: str) -> None:
    assert [f.kind for f in scan("a.py", source, "python", {})] == [kind]


@pytest.mark.parametrize(
    "source",
    ['import subprocess\nsubprocess.run(["git", "-c", "user.name=x", "status"])\n', 'open("o.html").read()\n'],
)
def test_data_process_and_html_reads_stay_clean(source: str) -> None:
    assert scan("a.py", source, "python", {}) == []


@pytest.mark.parametrize(
    ("source", "kind"),
    [
        ("bash --command '# hello'\n", "comment"),
        ("uvx python -c '# hello'\n", "comment"),
        ("uv tool run python -c '# hello'\n", "comment"),
        ("command python -c '# hello'\n", "comment"),
        ("sudo bash -c '# hello'\n", "comment"),
        ("nice -n 5 python -c 'x'\n", "coverage-error"),
        ("perl -e 'print 1 # x'\n", "coverage-error"),
        ("perl -ne 'print # x'\n", "coverage-error"),
        ("perl -e\n", "coverage-error"),
        ('perl -e "$CODE"\n', "coverage-error"),
        ("/usr/bin/time -l bash -c '# hello'\n", "comment"),
        ("/usr/bin/time -v bash -c 'x'\n", "coverage-error"),
        ("time bash -c 'x'\n", "coverage-error"),
    ],
)
def test_shell_wrappers_and_unmodeled_interpreters_fail_closed(source: str, kind: str) -> None:
    assert [f.kind for f in scan("a.sh", source, "bash")] == [kind]


@pytest.mark.parametrize(
    "source",
    [
        "/usr/bin/time -l perl -e 'alarm shift; exec @ARGV' 300 uv run -- benchbox run\n",
        "/usr/bin/time -p ruby -e 'puts 1'\n",
    ],
)
def test_unmodeled_inline_source_without_a_comment_marker_is_accepted(source: str) -> None:
    assert scan("a.sh", source, "bash") == []


@pytest.mark.parametrize(
    ("path", "source", "lang"),
    [
        (
            "azure-pipelines.yml",
            "steps:\n  - script: |\n      curl -LsSf https://astral.sh/uv/install.sh | sh\n      benchbox run --platform duckdb\n",
            "yaml",
        ),
        ("audit.json", '{"script": "_project/audits/replay.py"}\n', "json"),
    ],
)
def test_shell_script_keys_without_comments_are_clean(path: str, source: str, lang: str) -> None:
    assert scan(path, source, lang, {}) == []


@pytest.mark.parametrize(
    ("path", "source", "lang", "kind"),
    [
        ("docker-compose.yml", "services:\n  a:\n    command: bash -c '# hello'\n", "yaml", "comment"),
        ("docker-compose.yml", 'services:\n  a:\n    entrypoint: ["bash", "-c", "# hello"]\n', "yaml", "comment"),
        ("ci.yml", "jobs:\n  a:\n    script: doStuff(); // explain\n", "yaml", "coverage-error"),
        ("ci.yml", "jobs:\n  a:\n    script: run // explain\n", "yaml", "coverage-error"),
        ("ci.yml", "jobs:\n  a:\n    script: |\n      make test # explain\n", "yaml", "comment"),
        (".gitlab-ci.yml", "test:\n  script:\n    - make lint\n    - make test # explain\n", "yaml", "comment"),
        ("package.json", '{"scripts": {"x": "echo hi # there"}}\n', "json", "comment"),
    ],
)
def test_structured_command_keys_are_scanned(path: str, source: str, lang: str, kind: str) -> None:
    assert [f.kind for f in scan(path, source, lang, {})] == [kind]


def test_reviewed_joinorder_copy_where_clause_is_an_integer_id_list() -> None:
    tree = ast.parse((ROOT / "_project/scripts/build_joinorder_data.py").read_text(encoding="utf-8"))
    assignments = {
        node.targets[0].id: node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
    }
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "copy_table_to_csv"
    ]
    assert calls
    for call in calls:
        for keyword in call.keywords:
            if keyword.arg == "sql_where":
                assert isinstance(keyword.value, ast.Name)
                value = assignments[keyword.value.id]
                assert isinstance(value, ast.JoinedStr)
                text = ast.unparse(value)
                assert "id IN (" in text and "str(value) for value in table_ids" in text


@pytest.mark.parametrize(
    "source",
    ["<p>{x}</p>\n", '<a href="http://example.com">x</a>\n', '<p>{"http://example.com"}</p>\n'],
)
def test_astro_expressions_without_comment_tokens_are_still_parsed(source: str) -> None:
    requests = javascript_requests("a.astro", source, "astro")
    assert len(requests) == source.count("{")


def test_astro_expression_comment_lines_map_to_source_lines() -> None:
    source = "<div>\n  <p>\n    {\n      // c\n      x\n    }\n  </p>\n</div>\n"
    requests = javascript_requests("a.astro", source, "astro")
    rows = {key: [{"kind": "comment", "line": 3, "text": "// c", "symbol": ""}] for key in requests}
    findings = scan("a.astro", source, "astro", rows)
    assert [(f.line, f.text) for f in findings] == [(4, "// c")]


@pytest.mark.parametrize(
    "source,expressions",
    [
        ('<p>{/"/.test(s) /* c */}</p>\n', ['/"/.test(s) /* c */']),
        ("<p>{/{/.test(s) /* c */}</p>\n", ["/{/.test(s) /* c */"]),
        ('<p>{s.replace(/https?:\\/\\//, "") /* c */}</p>\n', ['s.replace(/https?:\\/\\//, "") /* c */']),
        ("<p>{/a\\/*b/.test(s) /* c */}</p>\n", ["/a\\/*b/.test(s) /* c */"]),
        ("<p>{/[/}]/.test(s) /* c */}</p>\n", ["/[/}]/.test(s) /* c */"]),
        ("<p>{a / b /* c */}</p>\n", ["a / b /* c */"]),
        ("<p>{x/2 /* c */}</p>\n", ["x/2 /* c */"]),
        ("<p>{(a) / (b) // c\n}</p>\n", ["(a) / (b) // c\n"]),
    ],
)
def test_astro_regex_literals_and_division_in_expressions(source: str, expressions: list[str]) -> None:
    requests = javascript_requests("a.astro", source, "astro")
    assert list(requests.values()) == [f"[\n{text}\n];" for text in expressions]
    rows = {key: [{"kind": "comment", "line": 2, "text": "/* c */", "symbol": ""}] for key in requests}
    findings = scan("a.astro", source, "astro", rows)
    assert [f.kind for f in findings] == ["comment"]


@pytest.mark.parametrize("source", ['<p>{/"/.test(s)}</p>\n', "<p>{/{/.test(s)}</p>\n", "<p>{a / b}</p>\n"])
def test_astro_regex_literal_without_comment_tokens_is_not_a_coverage_error(source: str) -> None:
    assert scan("a.astro", source, "astro", {k: [] for k in javascript_requests("a.astro", source, "astro")}) == []


def test_astro_frontmatter_regex_literal_with_backtick_does_not_open_a_template() -> None:
    source = "---\nconst r = /`/;\nconst d = a / b;\n// c\n---\n<p>x</p>\n"
    requests = javascript_requests("a.astro", source, "astro")
    assert list(requests.values()) == ["const r = /`/;\nconst d = a / b;\n// c\n"]


def test_astro_html_comment_inside_jsx_in_an_expression_is_found() -> None:
    source = "<ul>{xs.map((x) => <li><!-- in jsx --></li>)}</ul>\n<p>{'<!-- data -->'}</p>\n"
    findings = scan("a.astro", source, "astro", {k: [] for k in javascript_requests("a.astro", source, "astro")})
    assert [(f.line, f.text) for f in findings] == [(1, "<!-- in jsx -->")]


def test_astro_frontmatter_ends_at_the_first_top_level_fence() -> None:
    source = "---\nconst text = `\n---\nstill code\n`;\nconst other = '---';\n---\n<p>{text}</p>\n"
    requests = javascript_requests("a.astro", source, "astro")
    assert list(requests.values()) == [
        "const text = `\n---\nstill code\n`;\nconst other = '---';\n",
        "[\ntext\n];",
    ]
    assert not scan("a.astro", source, "astro", {k: [] for k in requests})


def test_astro_frontmatter_fence_after_comment_and_interpolation() -> None:
    source = "---\n// ---\nconst a = `${'}'}`;\n/*\n---\n*/\n---\n<p>x</p>\n"
    requests = javascript_requests("a.astro", source, "astro")
    assert list(requests.values()) == ["// ---\nconst a = `${'}'}`;\n/*\n---\n*/\n"]


def test_astro_unterminated_frontmatter_is_a_coverage_error() -> None:
    findings = scan("a.astro", "---\nconst a = 1;\n<p>x</p>\n", "astro", {})
    assert [f.kind for f in findings] == ["coverage-error"]


def test_astro_frontmatter_script_and_style_are_scanned_as_their_own_languages() -> None:
    requests = javascript_requests("a.astro", ASTRO_SOURCE, "astro")
    assert len(requests) == 4
    comments = ["// frontmatter explanation", "// script explanation", "/* expression explanation */", None]
    lines = [1, 1, 2, 2]
    rows = {
        key: [{"kind": "comment", "line": line, "text": text, "symbol": ""}] if text else []
        for key, text, line in zip(requests, comments, lines, strict=True)
    }
    assert list(requests.values()) == [
        '// frontmatter explanation\nconst title = "x";\n',
        '\n  // script explanation\n  console.log("<!-- data -->");\n',
        "[\n/* expression explanation */\n];",
        "[\ntitle\n];",
    ]
    findings = scan("a.astro", ASTRO_SOURCE, "astro", rows)
    assert sorted((f.line, f.text) for f in findings) == [
        (2, "// frontmatter explanation"),
        (5, "<!-- template explanation -->"),
        (6, "/* expression explanation */"),
        (8, "/* style explanation */"),
        (11, "// script explanation"),
    ]


def test_astro_without_frontmatter_has_no_javascript_requests() -> None:
    assert javascript_requests("a.astro", "<p>text</p>\n", "astro") == {}


def test_myst_list_table_content_is_scanned_and_postlist_is_display_only() -> None:
    table = "````{list-table}\n:header-rows: 1\n\n* - Name\n  - Example\n* - a\n  - ```python\n    x = 1  # note\n    ```\n````\n"
    assert [f.text for f in scan("docs/a.md", table, "examples", {})] == ["# note"]
    assert scan("docs/blog/index.md", "```{postlist}\n:list-style: none\n```\n", "examples", {}) == []


def test_myst_eval_rst_content_is_scanned_like_an_rst_page() -> None:
    source = "```{eval-rst}\n.. code-block:: python\n\n   x = 1  # note\n```\n"
    assert [f.text for f in scan("docs/a.md", source, "examples", {})] == ["# note"]
    assert scan("docs/blog/index.md", "```{eval-rst}\n* `Archive <archive.html>`_\n```\n", "examples", {}) == []


@pytest.mark.parametrize(
    ("path", "source", "lang"),
    [
        ("a.py", 'import subprocess\nsubprocess.run(["mytool", "-c", "# hello"])\n', "python"),
        ("a.py", 'import subprocess\nsubprocess.run("echo hi # there", shell=flag)\n', "python"),
        ("a.py", 'import subprocess\nsubprocess.run(["echo hi # there"], shell=flag)\n', "python"),
        ("a.py", 'import subprocess\nsubprocess.run(["psql", "--command=SELECT 1 -- hi"])\n', "python"),
        ("a.py", 'import subprocess\nsubprocess.run(["node", "--eval=// hi"])\n', "python"),
        ("a.sh", "bash --command='# hello'\n", "bash"),
        ("a.sh", "node -e'// hi'\n", "bash"),
        ("a.sh", "lua -e '-- hi'\n", "bash"),
        ("a.sh", "deno eval '// hi'\n", "bash"),
        ("a.sh", "bun -e '// hi'\n", "bash"),
        ("a.sh", "php -r '# hi'\n", "bash"),
        ("a.sh", "php -r '?> <!-- hi -->'\n", "bash"),
        ("a.sh", "Rscript -e '# hi'\n", "bash"),
        ("a.sh", "powershell -c '# hi'\n", "bash"),
        ("a.py", 'from pathlib import Path\n(Path("d").joinpath("o.html")).write_text("<!-- hi -->")\n', "python"),
        ("ci.yml", 'job:\n  script: "call();// hi"\n', "yaml"),
    ],
)
def test_unmodeled_executable_forms_fail_closed(path: str, source: str, lang: str) -> None:
    assert [f.kind for f in scan(path, source, lang, {})] in (["payload-error"], ["coverage-error"])


@pytest.mark.parametrize(
    ("path", "source", "lang", "text"),
    [
        ("a.py", 'import subprocess\nsubprocess.run(["sqlite3", "a.db", "SELECT 1 -- hi"])\n', "python", "-- hi"),
        ("a.py", 'import subprocess\nsubprocess.run(["duckdb", "a.db", "SELECT 1 -- hi"])\n', "python", "-- hi"),
        ("x.yml", "a:\n  entry: bash -c '# hello'\n", "yaml", "# hello"),
        (
            "a.py",
            'import subprocess\nsubprocess.run(["docker", "exec", "c", "bash", "-c", "# hi"])\n',
            "python",
            "# hi",
        ),
        ("a.sh", "docker exec c bash -c '# hi'\n", "bash", "# hi"),
        ("a.sh", "sqlite3 a.db 'SELECT 1 -- hi'\n", "bash", "-- hi"),
        ("a.sh", "psql -c 'SELECT 1 -- hi'\n", "bash", "-- hi"),
        ("a.sh", "bash -ce '# hi'\n", "bash", "# hi"),
    ],
)
def test_sql_clients_entry_keys_and_nested_commands_are_scanned(path: str, source: str, lang: str, text: str) -> None:
    assert [f.text for f in scan(path, source, lang, {})] == [text]


@pytest.mark.parametrize(
    ("path", "source", "lang"),
    [
        ("a.py", 'import subprocess\nsubprocess.run(["git", "-c", "user.name=x", "commit"])\n', "python"),
        ("a.py", 'import subprocess\nsubprocess.run(["docker", "run", "-e", "A=1", "img"])\n', "python"),
        ("a.py", 'import subprocess\nsubprocess.run(["ls", "-l"], shell=False)\n', "python"),
        ("a.sh", "perl script.pl --verbose\n", "bash"),
        ("a.sh", "sqlite3 a.db 'SELECT 1'\n", "bash"),
    ],
)
def test_data_flags_and_comment_free_sources_stay_clean(path: str, source: str, lang: str) -> None:
    assert scan(path, source, lang, {}) == []


def test_every_astro_expression_is_sent_to_the_typescript_scanner() -> None:
    source = '<div>{query("SELECT 1 -- hi")}</div>\n'
    assert list(javascript_requests("a.astro", source, "astro").values()) == ['[\nquery("SELECT 1 -- hi")\n];']


@pytest.mark.parametrize(
    ("source", "kinds"),
    [
        ('LEVELS=($(printf "%s\\n" 1 2 | awk -v max="$MAX" \'$1 <= max\' | sort -nu))\n', []),
        ('if ! awk "BEGIN { exit !($s >= 1) }"; then\n  exit 1\nfi\n', ["coverage-error"]),
        ("if ! awk -v s=\"$s\" 'BEGIN { exit !(s >= 1) }'; then\n  exit 1\nfi\n", []),
        ("LEVELS=($(printf x | awk '{print} # note'))\n", ["coverage-error"]),
        ("LEVELS=($(psql -c 'SELECT 1 -- note'))\n", ["coverage-error"]),
    ],
)
def test_unparseable_shell_chunks_fail_closed_only_when_they_can_hold_a_comment(source: str, kinds: list) -> None:
    assert [f.kind for f in scan("a.sh", source, "bash")] == kinds


def test_heredoc_inside_command_substitution_is_skipped_as_data() -> None:
    source = "BODY=$(cat <<EOF | awk '{print}'\nIt's data\nEOF\n)\necho \"$BODY\"\n"
    assert scan("a.sh", source, "bash") == []


@pytest.mark.parametrize(
    ("path", "source", "lang", "kinds"),
    [
        ("a.sh", "ruby -I lib -e '# hi'\n", "bash", ["coverage-error"]),
        ("a.sh", "ruby -r set -e '# hi'\n", "bash", ["coverage-error"]),
        ("a.sh", "osascript -l JavaScript -e '// hi'\n", "bash", ["coverage-error"]),
        ("a.sh", "php -B '# hi'\n", "bash", ["coverage-error"]),
        ("a.sh", "php -R '# hi'\n", "bash", ["coverage-error"]),
        ("a.sh", "node -p'// hi'\n", "bash", ["coverage-error"]),
        ("a.sh", "find . -exec sh -c '# hi' +\n", "bash", ["comment"]),
        (
            "a.py",
            'import subprocess\nsubprocess.run(["find", ".", "-exec", "sh", "-c", "# hi", ";"])\n',
            "python",
            ["comment"],
        ),
        (
            "a.py",
            'import subprocess\nsubprocess.run(["find", ".", "-exec", "rm", "{}", ";"])\n',
            "python",
            ["payload-error"],
        ),
        ("a.sh", 'perl script.pl "$x" --verbose\n', "bash", []),
        ("a.sh", "ruby -I lib -e 'puts 1'\n", "bash", []),
        ("a.py", 'import subprocess\nsubprocess.run(["find", ".", "-name", "*.py"])\n', "python", []),
    ],
)
def test_interpreter_options_before_inline_source_and_find_exec(path: str, source: str, lang: str, kinds: list) -> None:
    assert [f.kind for f in scan(path, source, lang, {})] == kinds


@pytest.mark.parametrize("source", ["node -p '// hi'\n", "node --print '// hi'\n"])
def test_shell_node_print_source_is_sent_to_the_typescript_scanner(source: str) -> None:
    assert list(javascript_requests("a.sh", source, "bash").values()) == ["// hi"]


@pytest.mark.parametrize(
    ("path", "source", "lang", "kinds"),
    [
        ("a.sh", "find sh -exec perl -e '# hi' \\;\n", "bash", ["coverage-error"]),
        ("a.sh", "find . -name sh -exec perl -e '# hi' \\;\n", "bash", ["coverage-error"]),
        ("a.sh", "find psql -exec perl -e '# hi' \\;\n", "bash", ["coverage-error"]),
        ("a.sh", "find . -exec $DYN -c '# hi' \\;\n", "bash", ["coverage-error"]),
        (
            "a.py",
            'import subprocess\nsubprocess.run(["find", "sh", "-exec", "perl", "-e", "# hi", ";"])\n',
            "python",
            ["payload-error"],
        ),
        ("a.sh", "deno eval --ext=ts 'console.log(1) // hi'\n", "bash", ["coverage-error"]),
        ("a.sh", "find . -name sh -print\n", "bash", []),
        ("a.sh", "deno eval --ext=ts 'console.log(1)'\n", "bash", []),
    ],
)
def test_find_exec_and_deno_eval_options_are_read_in_order(path: str, source: str, lang: str, kinds: list) -> None:
    assert [f.kind for f in scan(path, source, lang, {})] == kinds


@pytest.mark.parametrize("error", [AssertionError, AttributeError, IndexError, TypeError])
def test_bashlex_internal_errors_count_as_parse_failures(monkeypatch: pytest.MonkeyPatch, error: type) -> None:
    import comment_payloads

    def crash(_source: str) -> None:
        raise error("bashlex internal failure")

    monkeypatch.setattr(comment_payloads.bashlex, "parse", crash)
    assert [f.kind for f in scan("a.sh", "bash -c '# hi'\n", "bash")] == ["coverage-error"]
    assert scan("a.sh", "awk '{print}' input.txt\n", "bash") == []


def test_unparseable_chunk_with_perl_pod_or_ruby_block_comment_fails_closed() -> None:
    perl = "LEVELS=($(perl -e '1;\n=pod\nnote\n=cut\n'))\n"
    ruby = "LEVELS=($(ruby -e 'x = 1\n=begin\nnote\n=end\n'))\n"
    assert [f.kind for f in scan("a.sh", perl, "bash")] == ["coverage-error"]
    assert [f.kind for f in scan("a.sh", ruby, "bash")] == ["coverage-error"]


def test_mdx_pages_are_scanned_like_markdown() -> None:
    from comment_syntax import language

    assert language("website/src/content/docs/page.mdx") == "examples"
    source = "import X from './x.astro';\n\n```python\nx = 1  # note\n```\n"
    assert [f.text for f in scan("website/src/content/docs/page.mdx", source, "examples", {})] == ["# note"]
