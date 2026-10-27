#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

SOURCE_ROOTS = ("benchbox/", "scripts/", "_project/scripts/")
TESTS_ROOT = "tests"
PLATFORMS_ROOT = "benchbox/platforms/"
SMOKE_TESTS_ROOT = "tests/integration/platforms/"
CONFTEST_SUBTREE_LIMIT = 200
PATCH_CALLEE = re.compile(r"(^|\.)patch(\.|$)")
ORACLE_SCENARIO_TESTS = (
    "tests/integration/oracle/test_followup_scenarios.py",
    "tests/integration/oracle/test_local_review_scenario.py",
    "tests/integration/oracle/test_pr_arm_scenario.py",
    "tests/integration/oracle/test_review_scenarios.py",
    "tests/integration/oracle/test_sweep_scenarios.py",
)
SOURCE_GUARD_TESTS: dict[str, tuple[str, ...]] = {
    "benchbox/": ("tests/system/test_module_size_thresholds.py",),
    "benchbox/cli/": ("tests/uat/test_no_cli_surface_drift.py",),
    "benchbox/core/tuning/": (
        "tests/unit/core/tuning/test_capability_registry_parity.py",
        "tests/unit/core/tuning/test_capability_source_drift.py",
        "tests/unit/core/tuning/test_tpc_tuning_template_parity.py",
    ),
    "benchbox/platforms/": ("tests/uat/test_connection_registry_drift.py",),
    "benchbox/platforms/base/": ("tests/integration/platforms/test_local_platforms_smoke.py",),
    "_project/scripts/oracle_reviewers/": ORACLE_SCENARIO_TESTS,
    "_project/scripts/oracle_review_check.py": ORACLE_SCENARIO_TESTS,
    "_project/scripts/oracle_gate.py": ORACLE_SCENARIO_TESTS,
    "_project/scripts/soundness_merge_digest.py": ORACLE_SCENARIO_TESTS,
    "scripts/pr_arm.py": ORACLE_SCENARIO_TESTS,
    "tests/integration/oracle/": ORACLE_SCENARIO_TESTS,
    ".github/workflows/oracle-": ORACLE_SCENARIO_TESTS,
    ".github/oracle-": ORACLE_SCENARIO_TESTS,
}


def _git_lines(repo: Path, *args: str) -> list[str]:
    proc = subprocess.run(
        ["git", *args], cwd=repo, check=True, text=True, encoding="utf-8", capture_output=True, timeout=120
    )
    return [line for line in proc.stdout.splitlines() if line]


def changed_paths(repo: Path, base_ref: str) -> list[str]:
    base = _git_lines(repo, "merge-base", base_ref, "HEAD")[0]
    names = set(_git_lines(repo, "diff", "--name-only", "--no-renames", f"{base}...HEAD"))
    names.update(_git_lines(repo, "diff", "--name-only", "--no-renames", "HEAD"))
    names.update(_git_lines(repo, "ls-files", "--others", "--exclude-standard"))
    return sorted(names)


def changed_python(repo: Path, paths: list[str]) -> list[str]:
    return [path for path in paths if path.endswith(".py") and (repo / path).is_file()]


def _is_test_file(path: str) -> bool:
    pure = PurePosixPath(path)
    return pure.parts[:1] == (TESTS_ROOT,) and pure.name.startswith("test_") and pure.suffix == ".py"


def _test_files(repo: Path) -> list[str]:
    return [path.relative_to(repo).as_posix() for path in sorted((repo / TESTS_ROOT).rglob("test_*.py"))]


def _test_index(test_files: list[str]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for path in test_files:
        index.setdefault(PurePosixPath(path).name, []).append(path)
    return index


def _module_name(path: str) -> str:
    parts = PurePosixPath(path).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _parse(repo: Path, path: str, cache: dict[str, ast.AST | None]) -> ast.AST | None:
    if path not in cache:
        try:
            cache[path] = ast.parse((repo / path).read_text(encoding="utf-8"))
        except (OSError, SyntaxError, ValueError):
            cache[path] = None
    return cache[path]


def _imported_modules(repo: Path, path: str, cache: dict[str, ast.AST | None] | None = None) -> set[str]:
    tree = _parse(repo, path, {} if cache is None else cache)
    if tree is None:
        return set()
    package = PurePosixPath(path).parent.parts
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = package[: len(package) - node.level + 1] if node.level else ()
            base = ".".join([*prefix, *(node.module.split(".") if node.module else [])])
            modules.add(base)
            modules.update(f"{base}.{alias.name}" for alias in node.names)
    return modules


def _patch_targets(repo: Path, path: str, cache: dict[str, ast.AST | None]) -> set[str]:
    tree = _parse(repo, path, cache)
    if tree is None:
        return set()
    targets: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and node.args):
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            continue
        callee = ast.unparse(node.func)
        if PATCH_CALLEE.search(callee) or callee.endswith("setattr"):
            targets.add(first.value)
    return targets


def _resolve_module(repo: Path, dotted: str) -> str | None:
    parts = dotted.split(".")
    for end in range(len(parts), 0, -1):
        base = "/".join(parts[:end])
        for candidate in (f"{base}.py", f"{base}/__init__.py"):
            if (repo / candidate).is_file():
                return candidate
    return None


def _helper_importers(repo: Path, helpers: list[str], test_files: list[str]) -> set[str]:
    if not helpers:
        return set()
    cache: dict[str, ast.AST | None] = {}
    wanted = {_module_name(helper) for helper in helpers}
    return {
        path
        for path in test_files
        if any(
            module == name or module.startswith(f"{name}.")
            for module in _imported_modules(repo, path, cache)
            for name in wanted
        )
    }


def _source_dependents(repo: Path, sources: list[str], test_files: list[str]) -> set[str]:
    if not sources:
        return set()
    cache: dict[str, ast.AST | None] = {}
    wanted = {_module_name(source) for source in sources}
    hop: dict[str, bool] = {}

    def reaches(module_file: str) -> bool:
        if module_file not in hop:
            hop[module_file] = _module_name(module_file) in wanted or bool(
                _imported_modules(repo, module_file, cache) & wanted
            )
        return hop[module_file]

    selected: set[str] = set()
    for test in test_files:
        if _imported_modules(repo, test, cache) & wanted:
            selected.add(test)
            continue
        for target in _patch_targets(repo, test, cache):
            module_file = _resolve_module(repo, target)
            if module_file is not None and reaches(module_file):
                selected.add(test)
                break
    return selected


def _package_subtree(path: str, test_files: list[str]) -> list[str] | None:
    directory = PurePosixPath(path).parent
    subtree = [test for test in test_files if directory in PurePosixPath(test).parents]
    return subtree if len(subtree) <= CONFTEST_SUBTREE_LIMIT else None


def _smoke_platform(test: str) -> str | None:
    stem = PurePosixPath(test).stem
    if not test.startswith(SMOKE_TESTS_ROOT) or not stem.endswith("_smoke"):
        return None
    return stem.removeprefix("test_").removesuffix("_smoke")


def _platform_smoke_tests(path: str, test_files: list[str]) -> list[str]:
    if not path.startswith(PLATFORMS_ROOT):
        return []
    platform = PurePosixPath(path[len(PLATFORMS_ROOT) :]).parts[0].removesuffix(".py")
    selected = []
    for test in test_files:
        smoke = _smoke_platform(test)
        if smoke and (platform == smoke or platform.startswith(f"{smoke}_") or smoke.startswith(f"{platform}_")):
            selected.append(test)
    return selected


def _guard_tests(path: str, test_files: list[str]) -> list[str]:
    known = set(test_files)
    return [
        test
        for prefix, tests in SOURCE_GUARD_TESTS.items()
        if path.startswith(prefix)
        for test in tests
        if test in known
    ]


EXTRA_TEST_MAP: dict[str, list[str]] = {
    "tests/integration/platforms/common.py": ["tests/integration/test_ducklake_integration.py"],
}


def map_tests(repo: Path, paths: list[str]) -> list[str]:
    test_files = _test_files(repo)
    index = _test_index(test_files)
    selected: set[str] = set()
    helpers: list[str] = []
    sources: list[str] = []
    for path in paths:
        if not path.endswith(".py"):
            selected.update(_guard_tests(path, test_files))
            continue
        pure = PurePosixPath(path)
        if _is_test_file(path):
            if (repo / path).is_file():
                selected.add(path)
        elif pure.parts[:1] == (TESTS_ROOT,):
            if pure.name in {"conftest.py", "__init__.py"}:
                subtree = _package_subtree(path, test_files)
                if subtree is not None:
                    selected.update(subtree)
                    if pure.name == "__init__.py":
                        helpers.append(path)
            else:
                helpers.append(path)
            selected.update(_guard_tests(path, test_files))
        elif path.startswith(SOURCE_ROOTS):
            sources.append(path)
            module = pure.stem
            if module != "__init__":
                selected.update(index.get(f"test_{module}.py", ()))
            selected.update(_platform_smoke_tests(path, test_files))
            selected.update(_guard_tests(path, test_files))
        selected.update(EXTRA_TEST_MAP.get(path, ()))
    selected.update(_helper_importers(repo, helpers, test_files))
    selected.update(_source_dependents(repo, sources, test_files))
    return sorted(selected)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=("python", "tests"))
    parser.add_argument("--base-ref", default="origin/develop")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        paths = changed_paths(args.repo_root, args.base_ref)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, IndexError) as exc:
        print(f"preflight_targets: cannot compute changed paths against {args.base_ref}: {exc}", file=sys.stderr)
        return 2
    selected = changed_python(args.repo_root, paths) if args.kind == "python" else map_tests(args.repo_root, paths)
    for path in selected:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
