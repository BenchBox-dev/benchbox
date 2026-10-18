from __future__ import annotations

import ast
import hashlib
import inspect
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.medium

REPO_ROOT = Path(__file__).resolve().parents[2]
ALLOWED_INTERNAL_CLI_FILES = {
    "benchbox/cli/__init__.py",
    "benchbox/cli/app.py",
    "benchbox/cli/logo.py",
    "benchbox/cli/benchmark_hooks.py",
    "benchbox/cli/platform_hooks.py",
    "benchbox/cli/benchmarks.py",
    "benchbox/cli/commands/aggregate.py",
    "benchbox/cli/commands/__init__.py",
    "benchbox/cli/commands/calculate_qphh.py",
    "benchbox/cli/commands/checks.py",
    "benchbox/cli/commands/compare_dataframes.py",
    "benchbox/cli/commands/compare_plans.py",
    "benchbox/cli/commands/compare.py",
    "benchbox/cli/commands/show_plan.py",
    "benchbox/cli/commands/convert.py",
    "benchbox/cli/commands/df_tuning.py",
    "benchbox/cli/commands/export.py",
    "benchbox/cli/commands/publish.py",
    "benchbox/cli/commands/report.py",
    "benchbox/cli/commands/run.py",
    "benchbox/cli/commands/run_official.py",
    "benchbox/cli/commands/submit.py",
    "benchbox/cli/commands/tuning.py",
    "benchbox/cli/commands/tuning_group.py",
    "benchbox/cli/commands/visualize.py",
    "benchbox/cli/execution_pipeline.py",
    "benchbox/cli/tuning.py",
    "benchbox/cli/commands/datagen.py",
    "benchbox/cli/commands/metrics.py",
    "benchbox/cli/composite_params.py",
    "benchbox/cli/config.py",
    "benchbox/cli/database.py",
    "benchbox/cli/display.py",
    "benchbox/cli/dryrun.py",
    "benchbox/cli/tuning_resolver.py",
    "benchbox/cli/exceptions.py",
    "benchbox/cli/help.py",
    "benchbox/cli/main.py",
    "benchbox/cli/onboarding.py",
    "benchbox/cli/orchestrator.py",
    "benchbox/cli/platform_readiness.py",
    "benchbox/cli/platform_defaults.py",
    "benchbox/cli/platform.py",
    "benchbox/cli/run_resolution.py",
    "benchbox/cli/run_platform_resolution.py",
    "benchbox/cli/preferences.py",
    "benchbox/cli/commands/benchmarks.py",
    "benchbox/cli/commands/config.py",
    "benchbox/cli/commands/download_answers.py",
    "benchbox/cli/commands/plan_history.py",
    "benchbox/cli/commands/profile.py",
    "benchbox/cli/commands/results.py",
    "benchbox/cli/commands/shell.py",
    "benchbox/cli/tuning_runtime.py",
    "benchbox/cli/cloud_storage.py",
    "benchbox/cli/commands/auth.py",
    "benchbox/cli/common_types.py",
    "benchbox/cli/output.py",
    "benchbox/cli/platform_checks.py",
    "benchbox/cli/presentation/__init__.py",
    "benchbox/cli/presentation/system.py",
    "benchbox/cli/progress.py",
    "benchbox/cli/shared.py",
    "benchbox/cli/submit_auth.py",
    "benchbox/cli/submit_service.py",
    "benchbox/cli/system.py",
    "benchbox/cli/verbose_logging.py",
}
ALLOWED_HIDDEN_COMPAT_CLI_FILES = {
    "benchbox/cli/commands/setup.py",
    "benchbox/cli/commands/calculate_qphh.py",
    "benchbox/cli/commands/compare_dataframes.py",
    "benchbox/cli/commands/compare_plans.py",
    "benchbox/cli/commands/df_tuning.py",
    "benchbox/cli/commands/run_official.py",
    "benchbox/cli/commands/tuning.py",
    "benchbox/cli/commands/tuning_group.py",
    "benchbox/cli/commands/run.py",
    "benchbox/cli/commands/submit.py",
    "benchbox/cli/commands/export.py",
    "benchbox/cli/platform.py",
    "benchbox/cli/commands/plan_history.py",
}
ALLOWED_INTERNAL_CLI_FILES = ALLOWED_INTERNAL_CLI_FILES | ALLOWED_HIDDEN_COMPAT_CLI_FILES
ACCEPTED_CLI_SURFACE_DIGESTS = {
    "benchbox/cli/commands/compare.py": "67a3440457f153324d50a64ce1161879aa5e542d028a51f6ad63e1ef57ada898",
    "benchbox/cli/commands/metrics.py": "170f47a581bd13aa1e57a5ddbbb7743630f3600524c3508eb17dccea7f0fe096",
}
FORBIDDEN_CLI_SURFACE_DECORATORS = {"argument", "command", "group", "option"}
FORBIDDEN_CLI_SURFACE_FUNCTIONS = {
    "benchbox/cli/commands/convert.py": "convert",
    "benchbox/cli/commands/run.py": "run",
    "benchbox/cli/commands/submit.py": "submit",
    "benchbox/cli/commands/visualize.py": "visualize",
}


def test_uat_did_not_modify_benchbox_cli_surface():
    base = _verified_base_ref()
    changed = set(_git("diff", "--name-only", base, "--", "benchbox/cli/").stdout.splitlines())
    unexpected = changed - ALLOWED_INTERNAL_CLI_FILES

    assert not unexpected, f"Unexpected benchbox CLI file changes: {sorted(unexpected)}"

    forbidden = []
    for path in sorted(ALLOWED_INTERNAL_CLI_FILES):
        if path in ALLOWED_HIDDEN_COMPAT_CLI_FILES:
            continue
        if path in ACCEPTED_CLI_SURFACE_DIGESTS:
            if _cli_surface_digest(_source_at_worktree(path), path) != ACCEPTED_CLI_SURFACE_DIGESTS[path]:
                forbidden.append(path)
        elif _cli_surface_changed(
            path,
            base_source=_source_at_ref(base, path),
            current_source=_source_at_worktree(path),
        ):
            forbidden.append(path)
    assert not forbidden, f"Unexpected CLI surface changes: {forbidden}"


def test_cli_surface_guard_detects_click_argument_changes():
    base_source = """import click

@click.command("submit")
@click.argument("result_file", required=False)
def submit(
    ctx,
):
    pass
"""
    current_source = """import click

@click.command("submit")
@click.argument("extra_result_file")
@click.argument("result_file", required=False)
def submit(
    ctx,
):
    pass
"""
    assert _cli_surface_changed(
        "benchbox/cli/commands/submit.py",
        base_source=base_source,
        current_source=current_source,
    )


def test_cli_surface_guard_detects_signature_continuation_changes():
    base_source = """import click

@click.command("submit")
def submit(
    ctx,
):
    pass
"""
    current_source = """import click

@click.command("submit")
def submit(
    ctx,
    result_file,
):
    pass
"""
    assert _cli_surface_changed(
        "benchbox/cli/commands/submit.py",
        base_source=base_source,
        current_source=current_source,
    )


def test_cli_surface_guard_allows_moving_help_from_docstring_to_decorator():
    base_source = """import click

@click.command("visualize")
@click.option("--theme")
def visualize(theme):
    \"\"\"Render charts.

    Examples:
        benchbox visualize
    \"\"\"
    return theme
"""
    current_source = """import click

@click.command(
    "visualize",
    help=("Render charts.\\n\\nExamples:\\n    benchbox visualize"),
)
@click.option("--theme")
def visualize(theme):

    return theme
"""
    assert not _cli_surface_changed(
        "benchbox/cli/commands/visualize.py",
        base_source=base_source,
        current_source=current_source,
    )


def test_cli_surface_guard_detects_help_text_changes():
    base_source = """import click

@click.command("visualize")
def visualize():
    \"\"\"Render charts.\"\"\"
"""
    current_source = """import click

@click.command("visualize", help="Render all charts.")
def visualize():
    pass
"""
    assert _cli_surface_changed(
        "benchbox/cli/commands/visualize.py",
        base_source=base_source,
        current_source=current_source,
    )


def test_cli_surface_guard_allows_submit_body_changes():
    base_source = """import click

@click.command("submit")
def submit(
    ctx,
):
    return "old"
"""
    current_source = """import click

@click.command("submit")
def submit(
    ctx,
):
    return "new"
"""
    assert not _cli_surface_changed(
        "benchbox/cli/commands/submit.py",
        base_source=base_source,
        current_source=current_source,
    )


def test_cli_surface_guard_allows_package_init_lazy_body_changes():
    base_source = """def __getattr__(name):
    if name == "main":
        return entrypoint
"""
    current_source = """def __getattr__(name):
    return import_module(f"benchbox.cli.{name}")
"""

    assert "benchbox/cli/__init__.py" in ALLOWED_INTERNAL_CLI_FILES
    assert not _cli_surface_changed(
        "benchbox/cli/__init__.py",
        base_source=base_source,
        current_source=current_source,
    )


def test_cli_surface_guard_skips_when_base_ref_is_missing(monkeypatch: pytest.MonkeyPatch):
    missing_base = "refs/heads/__benchbox_missing_base__"
    monkeypatch.setenv("BENCHBOX_BASE_REF", missing_base)

    with pytest.raises(pytest.skip.Exception, match=f"base ref {missing_base!r} is not available"):
        _verified_base_ref()


def test_git_output_decoding_is_independent_of_the_host_locale(monkeypatch: pytest.MonkeyPatch):
    utf8_output = "benchbox/cli/commands/λ.py\n".encode()

    def fake_run(args, **kwargs):
        encoding = kwargs.get("encoding", "cp1252")
        return subprocess.CompletedProcess(args, 0, utf8_output.decode(encoding), "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = _git("diff", "--name-only")

    assert result.stdout == "benchbox/cli/commands/λ.py\n"


def test_cli_surface_guard_uses_branch_fork_when_target_advances(monkeypatch: pytest.MonkeyPatch):
    fork = "a" * 40
    monkeypatch.setenv("BENCHBOX_BASE_REF", "origin/develop")

    def fake_git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        if args == ("rev-parse", "--is-inside-work-tree"):
            output = "true\n"
        elif args == ("rev-parse", "--verify", "origin/develop^{commit}"):
            output = "b" * 40 + "\n"
        elif args == ("merge-base", "origin/develop", "HEAD"):
            output = fork + "\n"
        elif args == ("diff", "--name-only", fork, "--", "benchbox/cli/"):
            output = ""
        elif args == ("diff", "--name-only", "origin/develop", "--", "benchbox/cli/"):
            output = "benchbox/cli/logo.py\n"
        else:
            raise AssertionError(f"Unexpected git call: {args}")
        return subprocess.CompletedProcess(args, 0, output, "")

    monkeypatch.setitem(globals(), "_git", fake_git)
    monkeypatch.setitem(globals(), "ALLOWED_INTERNAL_CLI_FILES", set())

    test_uat_did_not_modify_benchbox_cli_surface()


def test_cli_surface_guard_rejects_unrelated_base_history(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BENCHBOX_BASE_REF", "origin/develop")

    def fake_git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        if args == ("rev-parse", "--is-inside-work-tree"):
            return subprocess.CompletedProcess(args, 0, "true\n", "")
        if args == ("rev-parse", "--verify", "origin/develop^{commit}"):
            return subprocess.CompletedProcess(args, 0, "b" * 40 + "\n", "")
        if args == ("merge-base", "origin/develop", "HEAD"):
            return subprocess.CompletedProcess(args, 1, "", "no common ancestor")
        raise AssertionError(f"Unexpected git call: {args}")

    monkeypatch.setitem(globals(), "_git", fake_git)

    with pytest.raises(AssertionError, match="cannot find a common ancestor"):
        _verified_base_ref()


def _verified_base_ref() -> str:
    inside = _git("rev-parse", "--is-inside-work-tree", check=False)
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        pytest.skip("CLI surface drift guard requires a git worktree")

    base = os.environ.get("BENCHBOX_BASE_REF", "origin/develop")
    verified = _git("rev-parse", "--verify", f"{base}^{{commit}}", check=False)
    if verified.returncode != 0:
        pytest.skip(f"CLI surface drift guard base ref {base!r} is not available")
    fork = _git("merge-base", base, "HEAD", check=False)
    if fork.returncode != 0 or not fork.stdout.strip():
        raise AssertionError(f"CLI surface drift guard cannot find a common ancestor for {base!r} and HEAD")
    return fork.stdout.strip()


def _source_at_ref(ref: str, path: str) -> str:
    source = _git("show", f"{ref}:{path}", check=False)
    if source.returncode != 0:
        return ""
    return source.stdout


def _source_at_worktree(path: str) -> str:
    try:
        return (REPO_ROOT / path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _cli_surface_changed(
    path: str,
    *,
    base_source: str,
    current_source: str,
) -> bool:
    return _cli_surface_snapshot(base_source, path) != _cli_surface_snapshot(current_source, path)


def _cli_surface_digest(source: str, path: str) -> str:
    snapshot = _cli_surface_snapshot(source, path, option_help=True)
    return hashlib.sha256("\n".join(snapshot).encode("utf-8")).hexdigest()


def _cli_surface_snapshot(source: str, path: str, *, option_help: bool = False) -> tuple[str, ...]:
    if not source:
        return ()

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise AssertionError(f"Could not parse {path} while checking CLI surface drift: {exc}") from exc

    surface = []
    function_name = FORBIDDEN_CLI_SURFACE_FUNCTIONS.get(path)
    functions = sorted(
        (node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)),
        key=lambda node: node.lineno,
    )
    for node in functions:
        for decorator in node.decorator_list:
            name = _click_surface_decorator_name(decorator)
            if name in FORBIDDEN_CLI_SURFACE_DECORATORS:
                keeps_help = option_help and name in {"argument", "option"}
                surface.append(ast.unparse(decorator) if keeps_help else _decorator_without_help(decorator))
                if name in {"command", "group"}:
                    surface.append(f"help={_effective_help(decorator, node)!r}")
        if function_name is not None and node.name == function_name:
            returns = ast.unparse(node.returns) if node.returns else ""
            surface.append(f"def {node.name}({ast.unparse(node.args)}) -> {returns}")
    return tuple(surface)


def _decorator_without_help(decorator: ast.expr) -> str:
    if not isinstance(decorator, ast.Call):
        return ast.unparse(decorator)
    call = ast.Call(
        func=decorator.func,
        args=decorator.args,
        keywords=[keyword for keyword in decorator.keywords if keyword.arg != "help"],
    )
    return ast.unparse(call)


def _effective_help(decorator: ast.expr, node: ast.FunctionDef) -> str | None:
    if isinstance(decorator, ast.Call):
        for keyword in decorator.keywords:
            if keyword.arg == "help":
                try:
                    value = ast.literal_eval(keyword.value)
                except ValueError:
                    return ast.unparse(keyword.value)
                return inspect.cleandoc(value) if isinstance(value, str) else repr(value)
    docstring = ast.get_docstring(node, clean=True)
    return docstring


def _click_surface_decorator_name(decorator: ast.expr) -> str | None:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "click":
        return target.attr
    return None


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed with {result.returncode}: {result.stderr.strip() or result.stdout.strip()}"
        )
    return result


def test_accepted_cli_surface_digest_changes_with_any_option_or_command_help_edit():
    source = """import click

@click.command("compare")
@click.option("--fail-on-regression")
def compare(fail_on_regression):
    pass
"""
    path = "benchbox/cli/commands/compare.py"
    edited_option = source.replace('"--fail-on-regression"', '"--fail-on-regression", type=str')
    extra_option = source.replace(
        '@click.option("--fail-on-regression")', '@click.option("--other")\n@click.option("--fail-on-regression")'
    )

    assert _cli_surface_digest(source, path) == _cli_surface_digest(source + "\nX = 1\n", path)
    assert _cli_surface_digest(source, path) != _cli_surface_digest(edited_option, path)
    assert _cli_surface_digest(source, path) != _cli_surface_digest(extra_option, path)
    assert _cli_surface_digest(source, path) != _cli_surface_digest(
        source.replace('@click.option("--fail-on-regression")', '@click.option("--fail-on-regression", help="x")'), path
    )
    assert _cli_surface_digest(source, path) != _cli_surface_digest(
        source.replace('@click.command("compare")', '@click.command("compare", help="changed")'), path
    )
