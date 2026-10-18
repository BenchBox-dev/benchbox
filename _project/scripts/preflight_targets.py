#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path, PurePosixPath

SOURCE_ROOTS = ("benchbox/", "scripts/", "_project/scripts/")
TESTS_ROOT = "tests"
PLATFORMS_ROOT = "benchbox/platforms/"
SMOKE_TESTS_ROOT = "tests/integration/platforms/"
CONFTEST_SUBTREE_LIMIT = 200
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
}


def _git_lines(repo: Path, *args: str) -> list[str]:
    proc = subprocess.run(["git", *args], cwd=repo, check=True, text=True, capture_output=True, timeout=120)
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
    return ".".join(PurePosixPath(path).with_suffix("").parts)


def _imported_modules(repo: Path, path: str) -> set[str]:
    try:
        tree = ast.parse((repo / path).read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
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


def _helper_importers(repo: Path, helpers: list[str], test_files: list[str]) -> set[str]:
    if not helpers:
        return set()
    wanted = {_module_name(helper) for helper in helpers}
    return {
        path
        for path in test_files
        if any(
            module == name or module.startswith(f"{name}.")
            for module in _imported_modules(repo, path)
            for name in wanted
        )
    }


def _conftest_subtree(conftest: str, test_files: list[str]) -> list[str]:
    directory = PurePosixPath(conftest).parent
    subtree = [path for path in test_files if directory in PurePosixPath(path).parents]
    return subtree if len(subtree) <= CONFTEST_SUBTREE_LIMIT else []


def _platform_smoke_tests(path: str, test_files: list[str]) -> list[str]:
    if not path.startswith(PLATFORMS_ROOT):
        return []
    platform = PurePosixPath(path[len(PLATFORMS_ROOT) :]).parts[0].removesuffix(".py").split("_")[0]
    return [
        test
        for test in test_files
        if test.startswith(SMOKE_TESTS_ROOT)
        and "_smoke" in PurePosixPath(test).stem
        and PurePosixPath(test).stem.removeprefix("test_").split("_")[0] == platform
    ]


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
    for path in paths:
        if not path.endswith(".py"):
            continue
        pure = PurePosixPath(path)
        if _is_test_file(path):
            if (repo / path).is_file():
                selected.add(path)
        elif pure.parts[:1] == (TESTS_ROOT,):
            if pure.name == "conftest.py":
                selected.update(_conftest_subtree(path, test_files))
            elif pure.name != "__init__.py":
                helpers.append(path)
        elif path.startswith(SOURCE_ROOTS):
            module = pure.stem
            if module != "__init__":
                selected.update(index.get(f"test_{module}.py", ()))
            selected.update(_platform_smoke_tests(path, test_files))
            selected.update(_guard_tests(path, test_files))
        selected.update(EXTRA_TEST_MAP.get(path, ()))
    selected.update(_helper_importers(repo, helpers, test_files))
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
