from __future__ import annotations

import argparse
import ast
import functools
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NamedTuple

CLI_DESCRIPTION = (
    "Select release-canary or local-medium tests that changed paths can affect.\n"
    "\n"
    "The daily release canary runs the full non-fast suite (about 644 tests in\n"
    "72 files); running all of it in the merge queue would roughly double\n"
    "merge-queue runner-minutes. This selector computes each canary test file's\n"
    "dependencies from the tree under test and selects the tests whose\n"
    "dependencies include a changed path.\n"
    "\n"
    "Dependency rules (each closes a known gap in naive import scanning):\n"
    "\n"
    "- static imports, including every parent package ``__init__.py`` (Python\n"
    "  executes them on import), expanded transitively: an imported module's\n"
    "  own imports are edges too, so re-exported names (``from pkg import X``\n"
    "  where ``X`` lives in another module) and helper modules are covered;\n"
    '- ``importlib.import_module("<literal>")`` and ``__import__("<literal>")``;\n'
    '- repo paths built from constants: ``REPO_ROOT / "a" / "b"``,\n'
    '  ``Path(__file__).with_name("x")``, and path string literals in path\n'
    "  positions. A directory path depends on every file under it;\n"
    "- imports inside a string constant that parses as Python (tests that run\n"
    "  repo code via ``sys.executable -c <script>``);\n"
    "- ``tests/conftest.py`` and every module in its ``pytest_plugins`` list,\n"
    "  with their transitive dependencies, for every test;\n"
    "- the per-directory conftest chain pytest loads for each test file\n"
    "  (``tests/a/conftest.py`` for ``tests/a/b/test_x.py``), with their\n"
    "  ``pytest_plugins`` modules;\n"
    "- a changed non-Python file under ``benchbox/`` selects every canary test\n"
    "  that imports a module in the same directory;\n"
    "- changed or added canary test files always select themselves.\n"
    "\n"
    "Fail-safe rules (when unsure, select -- the selector may over-select but\n"
    "must never under-select silently):\n"
    "\n"
    "- per test: a canary test containing a dynamic edge the selector cannot\n"
    "  resolve (an import name or path built at runtime), in its own code or its\n"
    "  conftest chain, is always selected. Unresolved imports in its test-specific\n"
    "  library closure also select it;\n"
    "- whole suite: ``pyproject.toml``, ``uv.lock``, pytest configuration,\n"
    "  ``tests/conftest.py``, ``release-canary.yml``, or the selector itself\n"
    "  changed;\n"
    "- unmapped paths: a changed path that no dependency map references and\n"
    "  that is not on the reviewed ``CANT_AFFECT_CANARY`` list runs the whole\n"
    "  suite.\n"
    "\n"
    "Stdlib-only and read-only: the selector never imports ``benchbox`` and\n"
    "never executes test code. The marker expression and collection parsing\n"
    "are imported from ``scripts/release_canary_sharding.py``, not copied.\n"
    "\n"
    "Output is JSON with the selected node IDs, the reason each was selected\n"
    "(which changed path, through which edge), whether a whole-suite fallback\n"
    "fired and why, the dynamic edges observed in library code\n"
    "(``dynamic_library_sites``, for the shadow watch), and\n"
    "the canary collection it was computed against.\n"
    "\n"
    "Known limitation: registry-style dynamic loading inside the shared fixture\n"
    "closure (``import_module(name)`` with a runtime name) is unbounded, so it is\n"
    "reported rather than propagated: propagating it would always-select\n"
    "every test. A changed file no test references\n"
    "still runs the whole suite through the unmapped-path backstop; the\n"
    "residual shape (a mapped file affecting a test only through dynamic\n"
    "loading) is pinned by known-regression replay tests.\n"
)

try:
    from release_canary_sharding import MARKER_EXPRESSION, parse_collection_output
except ImportError:
    from scripts.release_canary_sharding import MARKER_EXPRESSION, parse_collection_output


SELECTOR_REPO_PATH = "scripts/canary_impact.py"
CANARY_WORKFLOW_PATH = ".github/workflows/release-canary.yml"
CONFTEST_REPO_PATH = "tests/conftest.py"

WHOLE_SUITE_PATHS = frozenset(
    {
        "pyproject.toml",
        "uv.lock",
        "pytest.ini",
        "pytest-ci.ini",
        "tox.ini",
        "tests/conftest.py",
        CANARY_WORKFLOW_PATH,
        SELECTOR_REPO_PATH,
    }
)

CANT_AFFECT_CANARY = frozenset(
    {
        "CHANGELOG.md",
        "LICENSE",
        "DISCLAIMER.md",
        "COPYRIGHT.md",
        ".codespell-ignore.txt",
        "_project/audits/",
        "_project/config/fast_test_lane_policy.json",
    }
)

MEDIUM_PRODUCT_ROOTS = (
    "benchbox/",
    "scripts/",
    "_project/scripts/",
    "_project/config/",
    "_project/compat/",
    "_project/evals/agent-instructions/",
    "tools/",
    "results-data/",
    "examples/",
    "landing/",
    "docker/",
    "_binaries/",
    "_sources/",
    "publication/",
    "quality/",
)
MEDIUM_DOC_CODE_SUFFIXES = (".py", ".js", ".css", ".html", ".jinja", ".j2")
MEDIUM_WHOLE_SUITE_PATHS = frozenset(
    {"pyproject.toml", "uv.lock", "pytest.ini", "pytest-ci.ini", "tox.ini", CONFTEST_REPO_PATH, SELECTOR_REPO_PATH}
)


def medium_relevant_paths(changed_paths: list[str]) -> list[str]:
    return [
        path
        for path in changed_paths
        if path.startswith(MEDIUM_PRODUCT_ROOTS)
        or path in MEDIUM_WHOLE_SUITE_PATHS
        or (path.startswith("docs/") and path.endswith(MEDIUM_DOC_CODE_SUFFIXES))
    ]


def _is_cant_affect(path: str, cant_affect: frozenset[str] = CANT_AFFECT_CANARY) -> bool:
    if path in cant_affect:
        return True
    return any(entry.endswith("/") and path.startswith(entry) for entry in cant_affect)


BARE_MODULE_SEARCH_DIRS = ("", "scripts")

TESTLOCAL_ROOTS = frozenset({"tmp_path", "tmpdir", "tmp_path_factory"})

PATHLIKE_KEYWORDS = frozenset(
    {
        "path",
        "filename",
        "filepath",
        "fname",
        "dir",
        "directory",
        "root",
        "src",
        "dst",
        "target",
        "location",
    }
)

PATH_CONSTRUCTOR_FUNCS = frozenset(
    {
        "open",
        "Path",
        "glob",
        "rglob",
        "join",
        "abspath",
        "realpath",
        "normpath",
        "dirname",
        "exists",
        "isfile",
        "isdir",
        "listdir",
        "read_text",
        "write_text",
        "mkdir",
    }
)


_PRUNE_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "build",
        "dist",
    }
)


@functools.lru_cache(maxsize=32)
def _repo_module_index(root_str: str) -> tuple[frozenset[str], frozenset[str]]:
    stems: set[str] = set()
    packages: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root_str):
        dirnames[:] = [d for d in dirnames if d not in _PRUNE_DIRS and not d.startswith(".")]
        for filename in filenames:
            if filename.endswith(".py"):
                stems.add(filename[:-3])
                if filename == "__init__.py":
                    packages.add(os.path.basename(dirpath))
    return frozenset(stems), frozenset(packages)


def _could_be_repo(dotted: str, root: Path) -> bool:
    parts = [part for part in dotted.split(".") if part]
    if not parts or not all(part.isidentifier() for part in parts):
        return True
    try:
        if root.joinpath(*parts).is_dir():
            return True
    except (OSError, ValueError):
        return True
    stems, packages = _repo_module_index(str(root))
    leaf = parts[-1]
    return leaf in stems or leaf in packages


class FileDeps(NamedTuple):
    deps: frozenset[str]
    dynamic: bool
    dynamic_kinds: tuple[str, ...]


def normalize_rel(path: str) -> str:
    normalized = path.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _stdlib_names() -> frozenset[str]:
    return frozenset(sys.stdlib_module_names)


def _exists(path: Path) -> bool:
    try:
        return path.exists()
    except (OSError, ValueError):
        return False


def _is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except (OSError, ValueError):
        return False


def _looks_external(top_level: str, root: Path) -> bool:
    if top_level in _stdlib_names():
        return True
    for candidate in (
        root / f"{top_level}.py",
        root / top_level / "__init__.py",
        root / "scripts" / f"{top_level}.py",
    ):
        if candidate.is_file():
            return False
    try:
        import importlib.util

        return importlib.util.find_spec(top_level) is not None
    except (ImportError, AttributeError, ValueError):
        return False


def _module_file(dotted: str, root: Path) -> Path | None:
    parts = dotted.split(".")
    module_path = root.joinpath(*parts).with_suffix(".py")
    if module_path.is_file():
        return module_path
    package_init = root.joinpath(*parts, "__init__.py")
    if package_init.is_file():
        return package_init
    namespace_member = root.joinpath(*parts).with_suffix(".py")
    if namespace_member.is_file():
        return namespace_member
    return None


def _parent_inits(module_file: Path, root: Path) -> list[Path]:
    inits: list[Path] = []
    try:
        relative_parent = module_file.parent.relative_to(root)
    except ValueError:
        return inits
    current = root
    for part in relative_parent.parts:
        current = current / part
        candidate = current / "__init__.py"
        if candidate.is_file():
            inits.append(candidate)
    return inits


def _rel(path: Path, root: Path) -> str | None:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return None


def _resolve_absolute(dotted: str, root: Path) -> tuple[set[str], bool]:
    top_level = dotted.split(".")[0]
    module_file = _module_file(dotted, root)
    if module_file is not None:
        deps = {_rel(module_file, root)}
        deps.update(_rel(init, root) for init in _parent_inits(module_file, root))
        return {dep for dep in deps if dep is not None}, False
    if root.joinpath(*dotted.split(".")).is_dir():
        return set(), False
    if _looks_external(top_level, root):
        return set(), False
    return set(), True


def _resolve_bare(name: str, root: Path) -> tuple[set[str], bool]:
    for search_dir in BARE_MODULE_SEARCH_DIRS:
        base = root if not search_dir else root / search_dir
        for candidate in (base / f"{name}.py", base / name / "__init__.py"):
            if candidate.is_file():
                rel = _rel(candidate, root)
                deps = {rel} if rel else set()
                if not search_dir:
                    deps.update(
                        dep for dep in (_rel(init, root) for init in _parent_inits(candidate, root)) if dep is not None
                    )
                return deps, False
        if (base / name).is_dir():
            return set(), False
    if _looks_external(name, root):
        return set(), False
    return set(), True


def _resolve_import(name: str, importer: Path, root: Path) -> tuple[set[str], bool]:
    if name.startswith("."):
        level = len(name) - len(name.lstrip("."))
        remainder = name.lstrip(".")
        try:
            package_dir = importer.parent.relative_to(root)
        except ValueError:
            return set(), True
        for _ in range(level - 1):
            package_dir = package_dir.parent
        dotted = ".".join([*package_dir.parts, remainder] if remainder else list(package_dir.parts))
        if not dotted:
            return set(), True
        return _resolve_absolute(dotted, root)
    if "." in name:
        return _resolve_absolute(name, root)
    return _resolve_bare(name, root)


_RESOLVED = "resolved"
_TESTLOCAL = "testlocal"
_EXTERNAL = "external"
_UNKNOWN = "unknown"


def _is_environ_expr(node: ast.AST) -> bool:
    if isinstance(node, ast.Call):
        func_name = _call_name(node.func)
        if func_name == "getenv":
            return True
        return _is_environ_expr(node.func)
    if isinstance(node, ast.Attribute):
        if node.attr == "environ" and isinstance(node.value, ast.Name) and node.value.id == "os":
            return True
        return _is_environ_expr(node.value)
    if isinstance(node, ast.Subscript):
        return _is_environ_expr(node.value)
    return False


class _PathResolution(NamedTuple):
    outcome: str
    path: Path | None = None


class _FileAnalyzer(ast.NodeVisitor):
    def __init__(self, path: Path, root: Path) -> None:
        self.path = path
        self.root = root
        self.deps: set[str] = set()
        self.dynamic_kinds: list[str] = []
        self.module_env: dict[str, _PathResolution] = {}
        self.function_env: dict[str, _PathResolution] | None = None
        self.function_assigned: set[str] = set()
        self.class_env_stack: list[dict[str, _PathResolution]] = []

    def _note_dynamic(self, kind: str) -> None:
        if kind not in self.dynamic_kinds:
            self.dynamic_kinds.append(kind)

    def _add_dep_path(self, path: Path) -> None:
        rel = _rel(path, self.root)
        if rel is not None:
            self.deps.add(rel)
            if path.is_dir():
                pass

    def _add_repo_path(self, path: Path) -> None:
        self._add_dep_path(path)

    def _is_bound(self, name: str) -> bool:
        if self.function_env is not None:
            if name in self.function_env:
                return True
            if name in self.function_assigned:
                return False
        return name in self.module_env

    def _lookup(self, name: str) -> _PathResolution | None:
        if self.function_env is not None and name in self.function_env:
            return self.function_env[name]
        if self.function_env is not None and name in self.function_assigned:
            return None
        if self.function_env is None and self.class_env_stack and name in self.class_env_stack[-1]:
            return self.class_env_stack[-1][name]
        return self.module_env.get(name)

    def _bind(self, name: str, resolution: _PathResolution) -> None:
        if self.function_env is not None:
            self.function_env[name] = resolution
            self.function_assigned.add(name)
        elif self.class_env_stack:
            self.class_env_stack[-1][name] = resolution
        else:
            self.module_env[name] = resolution

    def _resolve_expr(self, node: ast.AST) -> _PathResolution:  # noqa: C901
        if _is_environ_expr(node):
            return _PathResolution(_EXTERNAL)
        if isinstance(node, ast.Name):
            if node.id == "__file__":
                return _PathResolution(_RESOLVED, self.path)
            if node.id in TESTLOCAL_ROOTS:
                return _PathResolution(_TESTLOCAL)
            bound = self._lookup(node.id)
            if bound is not None:
                return bound
            return _PathResolution(_UNKNOWN)
        if isinstance(node, ast.Constant):
            value = node.value
            if isinstance(value, str) and len(value) > 4096:
                return _PathResolution(_UNKNOWN)
            if isinstance(value, str) and _is_dir(self.root / value):
                candidate = self.root / value
                return _PathResolution(_RESOLVED, candidate)
            if isinstance(value, str) and _looks_like_path(value):
                if value.startswith("/") or (len(value) > 2 and value[1] == ":" and value[2] in "/\\"):
                    return _PathResolution(_EXTERNAL)
                if any(char in value for char in "*?["):
                    static_prefix = value
                    for char in "*?[":
                        static_prefix = static_prefix.split(char, 1)[0]
                    parent = (self.root / static_prefix).parent
                    if _is_dir(parent) and parent != self.root:
                        return _PathResolution(_RESOLVED, parent)
                    return _PathResolution(_UNKNOWN)
                candidate = self.root / value
                if _exists(candidate):
                    return _PathResolution(_RESOLVED, candidate)
                return _PathResolution(_UNKNOWN)
            return _PathResolution(_UNKNOWN)
        if isinstance(node, ast.Attribute):
            if node.attr == "parent":
                inner = self._resolve_expr(node.value)
                if inner.outcome == _RESOLVED and inner.path is not None:
                    return _PathResolution(_RESOLVED, inner.path.parent)
                return inner
            if (
                isinstance(node.value, ast.Name)
                and node.value.id in {"self", "cls"}
                and self.class_env_stack
                and node.attr in self.class_env_stack[-1]
            ):
                return self.class_env_stack[-1][node.attr]
            return _PathResolution(_UNKNOWN)
        if isinstance(node, ast.Subscript):
            if (
                isinstance(node.value, ast.Attribute)
                and node.value.attr == "parents"
                and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, int)
            ):
                inner = self._resolve_expr(node.value.value)
                if inner.outcome == _RESOLVED and inner.path is not None:
                    current = inner.path
                    for _ in range(node.slice.value + 1):
                        current = current.parent
                    return _PathResolution(_RESOLVED, current)
            base = self._resolve_expr(node.value)
            if base.outcome in (_TESTLOCAL, _EXTERNAL):
                return base
            if base.outcome == _RESOLVED and base.path is not None and base.path.is_dir():
                return base
            return _PathResolution(_UNKNOWN)
        if isinstance(node, ast.Call):
            return self._resolve_call_expr(node)
        if isinstance(node, ast.BinOp):
            left = self._resolve_expr(node.left)
            right = self._resolve_expr(node.right)
            for side in (left, right):
                if side.outcome == _TESTLOCAL:
                    return side
            for side in (left, right):
                if side.outcome == _EXTERNAL:
                    return side
            if not isinstance(node.op, ast.Div) or left.outcome != _RESOLVED or left.path is None:
                return _PathResolution(_UNKNOWN)
            right_node = node.right
            if isinstance(right_node, ast.Constant) and isinstance(right_node.value, str):
                return _PathResolution(_RESOLVED, left.path / right_node.value)
            return _PathResolution(_UNKNOWN)
        if isinstance(node, ast.JoinedStr):
            if len(node.values) == 1 and isinstance(node.values[0], ast.FormattedValue):
                return self._resolve_expr(node.values[0].value)
            parts: list[str] = []
            for value in node.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    parts.append(value.value)
                elif isinstance(value, ast.FormattedValue):
                    inner = self._resolve_expr(value.value)
                    if inner.outcome == _RESOLVED and inner.path is not None:
                        parts.append(str(inner.path))
                    else:
                        return _PathResolution(_UNKNOWN)
                else:
                    return _PathResolution(_UNKNOWN)
            candidate = self.root / "".join(parts)
            if _exists(candidate):
                return _PathResolution(_RESOLVED, candidate)
            return _PathResolution(_UNKNOWN)
        return _PathResolution(_UNKNOWN)

    def _resolve_call_expr(self, node: ast.Call) -> _PathResolution:  # noqa: C901
        func_name = _call_name(node.func)
        if func_name in {"mkdtemp", "mkstemp", "TemporaryDirectory", "NamedTemporaryFile"}:
            return _PathResolution(_TESTLOCAL)
        if func_name in {"resolve", "absolute", "realpath", "abspath", "normpath"} and node.args:
            return self._resolve_expr(node.args[0])
        if func_name == "dirname" and node.args:
            inner = self._resolve_expr(node.args[0])
            if inner.outcome == _RESOLVED and inner.path is not None:
                parent = inner.path.parent
                return _PathResolution(_RESOLVED, parent)
            return inner
        if func_name in {"join", "joinpath"}:
            current: Path | None = None
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if current is None:
                        candidate = self.root / arg.value
                        current = candidate
                    else:
                        current = current / arg.value
                    continue
                inner = self._resolve_expr(arg)
                if inner.outcome != _RESOLVED or inner.path is None:
                    return inner if inner.outcome != _RESOLVED else _PathResolution(_UNKNOWN)
                if current is None:
                    current = inner.path
                else:
                    current = inner.path if inner.path.is_absolute() else current / inner.path.name
            if current is not None:
                return _PathResolution(_RESOLVED, current)
            return _PathResolution(_UNKNOWN)
        if func_name in {"Path", "path"} and node.args:
            inner = self._resolve_expr(node.args[0])
            if inner.outcome == _RESOLVED:
                return inner
            if isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                candidate = self.root / node.args[0].value
                if _exists(candidate):
                    return _PathResolution(_RESOLVED, candidate)
            return inner
        if func_name == "with_name" and node.args:
            base = self._resolve_expr(node.func.value) if isinstance(node.func, ast.Attribute) else None
            if (
                base is not None
                and base.outcome == _RESOLVED
                and base.path is not None
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                return _PathResolution(_RESOLVED, base.path.parent / node.args[0].value)
            return _PathResolution(_UNKNOWN)
        if func_name == "with_suffix" and node.args:
            base = self._resolve_expr(node.func.value) if isinstance(node.func, ast.Attribute) else None
            if base is not None and base.outcome == _RESOLVED and base.path is not None:
                return _PathResolution(_RESOLVED, base.path)
            return _PathResolution(_UNKNOWN)
        if func_name in {"sorted", "list", "tuple", "set", "frozenset", "reversed", "str"}:
            if node.args:
                return self._resolve_expr(node.args[0])
            return _PathResolution(_UNKNOWN)
        if func_name in {"glob", "rglob", "listdir", "iterdir", "walk", "scandir"} or (
            func_name is not None
            and (func_name.startswith(("find_", "glob_")) or func_name.endswith(("_files", "_paths")))
        ):
            candidates: list[ast.AST] = []
            if isinstance(node.func, ast.Attribute):
                candidates.append(node.func.value)
            candidates.extend(node.args)
            candidates.extend(kw.value for kw in node.keywords if kw.value is not None)
            for candidate in candidates:
                inner = self._resolve_expr(candidate)
                if inner.outcome == _RESOLVED and inner.path is not None:
                    return inner
            for candidate in candidates:
                inner = self._resolve_expr(candidate)
                if inner.outcome in (_TESTLOCAL, _EXTERNAL):
                    return inner
            return _PathResolution(_UNKNOWN)
        if isinstance(node.func, ast.Attribute):
            base = self._resolve_expr(node.func.value)
            if base.outcome == _RESOLVED and base.path is not None:
                return base
            if base.outcome in (_TESTLOCAL, _EXTERNAL):
                return base
        return _PathResolution(_UNKNOWN)

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        outer_env = self.function_env
        outer_assigned = self.function_assigned
        self.function_env = {}
        self.function_assigned = {
            arg.arg
            for arg in (
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
                node.args.vararg,
                node.args.kwarg,
            )
            if arg is not None
        }
        self.generic_visit(node)
        self.function_env = outer_env
        self.function_assigned = outer_assigned

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.class_env_stack.append({})
        self.generic_visit(node)
        self.class_env_stack.pop()

    def visit_For(self, node: ast.For | ast.AsyncFor) -> None:
        self._mark_target_assigned(node.target)
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.visit_For(node)

    def visit_With(self, node: ast.With | ast.AsyncWith) -> None:
        for item in node.items:
            if item.optional_vars is not None:
                self._mark_target_assigned(item.optional_vars)
        self.generic_visit(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self.visit_With(node)

    def _mark_target_assigned(self, target: ast.AST) -> None:
        for child in ast.walk(target):
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store) and self.function_env is not None:
                self.function_assigned.add(child.id)

    def visit_Assign(self, node: ast.Assign) -> None:
        self._handle_assignment(node.value, list(node.targets))
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self._handle_assignment(node.value, [node.target])
        self.generic_visit(node)

    def _handle_assignment(self, value: ast.AST, targets: list[ast.AST]) -> None:
        names = [t.id for t in targets if isinstance(t, ast.AST) and isinstance(t, ast.Name)]
        if not names:
            return
        if self.function_env is not None:
            self.function_assigned.update(names)
        value_names = {n.id for n in ast.walk(value) if isinstance(n, ast.Name)}
        resolution = self._resolve_expr(value)
        if any(name in value_names for name in names):
            return
        if resolution.outcome == _RESOLVED and resolution.path is not None:
            self._add_repo_path(resolution.path)
        if resolution.outcome in (_RESOLVED, _TESTLOCAL, _EXTERNAL) or (
            resolution.outcome == _UNKNOWN and _touches_known(value, self)
        ):
            for name in names:
                self._bind(name, resolution)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and "import" in node.value:
            self._scan_string_snippet(node.value)

    def _scan_string_snippet(self, snippet: str) -> None:
        try:
            tree = ast.parse(snippet)
        except (SyntaxError, ValueError):
            return
        for child in ast.walk(tree):
            if isinstance(child, ast.Import):
                self.visit_Import(child)
            elif isinstance(child, ast.ImportFrom):
                if child.level or child.module is None:
                    continue
                else:
                    self.visit_ImportFrom(child)
            elif isinstance(child, ast.Call) and _call_name(child.func) in {
                "import_module",
                "import_",
                "__import__",
                "importorskip",
            }:
                self._handle_dynamic_import(child)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            deps, unknown = _resolve_import(alias.name, self.path, self.root)
            self.deps.update(deps)
            if unknown and _could_be_repo(alias.name, self.root):
                self._note_dynamic(f"unresolvable_import:{alias.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module is None and node.level == 0:
            self._note_dynamic("unresolvable_import:relative_without_module")
            self.generic_visit(node)
            return
        base = ("." * node.level + (node.module or "")).rstrip(".")
        candidates = [base] if node.module else []
        if not node.module:
            try:
                package_dir = self.path.parent.relative_to(self.root)
                package = ".".join(package_dir.parts)
            except ValueError:
                package = ""
            for alias in node.names:
                if alias.name != "*":
                    candidates.append(f"{package}.{alias.name}" if package else alias.name)
        deps: set[str] = set()
        unknown = False
        for candidate in candidates:
            candidate_deps, candidate_unknown = _resolve_import(candidate, self.path, self.root)
            deps.update(candidate_deps)
            if candidate_unknown and _could_be_repo(candidate, self.root):
                unknown = True
        if node.module and not node.level:
            package_dir = self.root.joinpath(*node.module.split("."))
            if _is_dir(package_dir):
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    sub = _module_file(f"{node.module}.{alias.name}", self.root)
                    if sub is not None:
                        rel = _rel(sub, self.root)
                        if rel is not None:
                            deps.add(rel)
                        deps.update(
                            dep
                            for dep in (_rel(init, self.root) for init in _parent_inits(sub, self.root))
                            if dep is not None
                        )
        self.deps.update(deps)
        if unknown:
            self._note_dynamic(f"unresolvable_import:{base or 'relative'}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func_name = _call_name(node.func)
        if func_name in {"import_module", "import_", "__import__", "importorskip"}:
            self._handle_dynamic_import(node)
        elif func_name == "patch":
            self._handle_mock_patch(node)
        elif func_name in {"exec", "eval"}:
            if any(_is_strong_path_signal(arg, self) for arg in (*node.args, *(kw.value for kw in node.keywords))):
                self._note_dynamic(f"dynamic_{func_name}")
        else:
            self._handle_path_call(node, func_name)
        self.generic_visit(node)

    def _handle_dynamic_import(self, node: ast.Call) -> None:
        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            name = node.args[0].value
            package = None
            if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                package = node.args[1].value
            if name.startswith(".") and isinstance(package, str):
                level = len(name) - len(name.lstrip("."))
                full = "." * level + package + "." + name.lstrip(".")
                deps, unknown = _resolve_import(full, self.path, self.root)
            else:
                deps, unknown = _resolve_import(name, self.path, self.root)
            self.deps.update(deps)
            if unknown and _could_be_repo(name, self.root):
                self._note_dynamic(f"unresolvable_import:{name}")
        else:
            self._note_dynamic("dynamic_import")

    def _handle_mock_patch(self, node: ast.Call) -> None:
        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            target = node.args[0].value
            parts = target.split(".")
            if all(part.isidentifier() for part in parts):
                for end in range(len(parts) - 1, 0, -1):
                    candidate = ".".join(parts[:end])
                    module_file = _module_file(candidate, self.root)
                    if module_file is not None:
                        rel = _rel(module_file, self.root)
                        if rel is not None:
                            self.deps.add(rel)
                        self.deps.update(
                            dep
                            for dep in (_rel(init, self.root) for init in _parent_inits(module_file, self.root))
                            if dep is not None
                        )
                        return
                if _looks_external(parts[0], self.root):
                    return
            if _could_be_repo(target, self.root):
                self._note_dynamic(f"unresolvable_import:{target}")

    _SKIP_EVAL_FUNCS = frozenset(
        {
            "parametrize",
            "mark",
            "fixture",
            "raises",
            "warns",
            "skip",
            "skipif",
            "fail",
            "xfail",
            "filterwarnings",
            "deprecated_call",
        }
    )

    _PATH_CONSUMING_CALLS = frozenset(
        {
            "open",
            "copy",
            "copy2",
            "copytree",
            "move",
            "remove",
            "unlink",
            "chdir",
        }
    )

    _STRING_METHODS = frozenset(
        {
            "startswith",
            "endswith",
            "removeprefix",
            "removesuffix",
            "strip",
            "lstrip",
            "rstrip",
            "split",
            "rsplit",
            "splitlines",
            "partition",
            "rpartition",
            "format",
            "format_map",
            "encode",
            "decode",
            "lower",
            "upper",
            "casefold",
            "replace",
            "match",
            "search",
            "fullmatch",
            "find",
            "rfind",
            "index",
            "rindex",
            "sub",
            "subn",
            "compile",
            "escape",
            "findall",
            "finditer",
        }
    )

    _DATA_FUNCS = frozenset({"len", "sum", "min", "max", "any", "all"})

    def _handle_path_call(self, node: ast.Call, func_name: str | None) -> None:
        if func_name in self._SKIP_EVAL_FUNCS or func_name in self._STRING_METHODS or func_name in self._DATA_FUNCS:
            return
        if func_name is not None and func_name.endswith(("Error", "Exception", "Warning", "Exit")):
            return
        if func_name == "join" and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Constant):
            force = False
        else:
            force = func_name in PATH_CONSTRUCTOR_FUNCS or func_name in self._PATH_CONSUMING_CALLS
        for arg in node.args:
            self._handle_call_arg(arg, force_path=force, call_name=func_name)
        for keyword in node.keywords:
            if keyword.arg is None:
                continue
            if force or keyword.arg in PATHLIKE_KEYWORDS:
                self._handle_call_arg(keyword.value, force_path=True, call_name=func_name)

    def _handle_call_arg(self, arg: ast.AST, *, force_path: bool, call_name: str | None) -> None:
        if isinstance(arg, (ast.List, ast.Tuple, ast.Set)):
            for element in arg.elts:
                self._handle_call_arg(element, force_path=force_path, call_name=call_name)
            return
        if isinstance(arg, ast.Dict):
            for value in arg.values:
                self._handle_call_arg(value, force_path=force_path, call_name=call_name)
            return
        if isinstance(arg, ast.Starred):
            self._handle_call_arg(arg.value, force_path=force_path, call_name=call_name)
            return
        if isinstance(arg, ast.Constant) and not (isinstance(arg.value, str) and _looks_like_path(arg.value)):
            return
        if not force_path and not _is_strong_path_signal(arg, self):
            return
        resolution = self._resolve_expr(arg)
        if resolution.outcome == _RESOLVED and resolution.path is not None:
            self._add_repo_path(resolution.path)
        elif resolution.outcome == _UNKNOWN and not _is_unbound_root(arg, self):
            self._note_dynamic(f"dynamic_path:{call_name or 'call'}")


def _call_name(func: ast.AST) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _looks_like_path(value: str) -> bool:
    if not value or value.startswith(("http://", "https://", "mailto:")):
        return False
    if "://" in value:
        return False
    if "{" in value or "}" in value:
        return False
    if " " in value and "/" not in value:
        return False
    if "/" in value or value.startswith("."):
        return True
    suffix = Path(value).suffix
    if not suffix or len(suffix) > 6:
        return False
    stem = value[: -len(suffix)]
    return any(char.isalpha() for char in stem)


def _expr_root_name(node: ast.AST) -> str | None:
    current = node
    while isinstance(current, (ast.Attribute, ast.Subscript, ast.Call, ast.BinOp)):
        if isinstance(current, ast.Call):
            current = current.func
        elif isinstance(current, ast.Attribute):
            current = current.value
        elif isinstance(current, ast.BinOp):
            current = current.left
        else:
            current = current.value
    return current.id if isinstance(current, ast.Name) else None


def _touches_known(node: ast.AST, analyzer: _FileAnalyzer) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            if child.id == "__file__":
                return True
            bound = analyzer._lookup(child.id)
            if bound is not None and bound.outcome == _RESOLVED:
                return True
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            if _exists(analyzer.root / child.value):
                return True
    return False


def _is_unbound_root(node: ast.AST, analyzer: _FileAnalyzer) -> bool:
    for child in ast.walk(node):
        if not isinstance(child, ast.Name):
            continue
        if child.id == "__file__" or child.id in TESTLOCAL_ROOTS:
            return False
        if analyzer._is_bound(child.id):
            return False
    return True


def _is_strong_path_signal(node: ast.AST, analyzer: _FileAnalyzer) -> bool:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        if isinstance(node.left, ast.Constant) and not isinstance(node.left.value, str):
            return False
        return True
    if isinstance(node, ast.JoinedStr):
        values = node.values
        if len(values) == 1 and isinstance(values[0], ast.FormattedValue):
            return _is_strong_path_signal(values[0].value, analyzer)

        def _neighbor_is_pathish(index: int) -> bool:
            neighbor = values[index]
            return isinstance(neighbor, ast.FormattedValue) and _is_strong_path_signal(neighbor.value, analyzer)

        for index, value in enumerate(values):
            if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
                continue
            text = value.value
            if index > 0 and text.startswith("/") and _neighbor_is_pathish(index - 1):
                return True
            if index + 1 < len(values) and text.endswith("/") and _neighbor_is_pathish(index + 1):
                return True
        return False
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str) and _looks_like_path(node.value)
    if isinstance(node, ast.Name):
        if node.id == "__file__":
            return True
        bound = analyzer._lookup(node.id)
        return bound is not None and bound.outcome == _RESOLVED
    if isinstance(node, ast.Call):
        func_name = _call_name(node.func)
        return func_name in PATH_CONSTRUCTOR_FUNCS or func_name == "open"
    if isinstance(node, (ast.Attribute, ast.Subscript)):
        root_name = _expr_root_name(node)
        if root_name == "__file__":
            return True
        if root_name is None:
            return False
        bound = analyzer._lookup(root_name)
        return bound is not None and bound.outcome == _RESOLVED
    return False


def analyze_python_file(path: Path, root: Path) -> FileDeps:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, ValueError):
        return FileDeps(deps=frozenset(), dynamic=True, dynamic_kinds=("unparseable_file",))
    analyzer = _FileAnalyzer(path, root)
    analyzer.visit(tree)
    return FileDeps(
        deps=frozenset(analyzer.deps),
        dynamic=len(analyzer.dynamic_kinds) > 0,
        dynamic_kinds=tuple(analyzer.dynamic_kinds),
    )


def _conftest_chain(test_file: str, root: Path) -> list[str]:
    chain: list[str] = []
    directory = (root / test_file).parent.resolve()
    while True:
        if directory != root and root not in directory.parents:
            break
        candidate = directory / "conftest.py"
        if candidate.is_file():
            rel = _rel(candidate, root)
            if rel is not None and rel != CONFTEST_REPO_PATH:
                chain.append(rel)
        if directory == root:
            break
        directory = directory.parent
    return chain


def _expand_closure(
    seeds: set[str], root: Path, cache: dict[str, FileDeps]
) -> tuple[set[str], dict[str, tuple[str, ...]]]:
    all_deps = set(seeds)
    dynamic_files: dict[str, list[str]] = {}
    stack = sorted(seed for seed in seeds if seed.endswith(".py"))
    visited: set[str] = set()
    while stack:
        rel = stack.pop()
        if rel in visited:
            continue
        visited.add(rel)
        file_deps = cache.get(rel)
        if file_deps is None:
            file_deps = analyze_python_file(root / rel, root)
            cache[rel] = file_deps
        if file_deps.dynamic:
            dynamic_files[rel] = list(file_deps.dynamic_kinds)
        for dep in file_deps.deps:
            if dep not in all_deps:
                all_deps.add(dep)
                if dep.endswith(".py"):
                    stack.append(dep)
    return all_deps, {rel: tuple(kinds) for rel, kinds in dynamic_files.items()}


def _read_pytest_plugins(conftest: Path) -> tuple[list[str], str | None]:
    try:
        tree = ast.parse(conftest.read_text(encoding="utf-8"), filename=str(conftest))
    except (OSError, SyntaxError, ValueError):
        return [], "conftest_unparseable"
    plugins: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value if isinstance(node, ast.Assign) else node.value
            if value is None:
                continue
            if any(isinstance(t, ast.Name) and t.id == "pytest_plugins" for t in targets):
                if isinstance(value, (ast.List, ast.Tuple)) and all(
                    isinstance(e, ast.Constant) and isinstance(e.value, str) for e in value.elts
                ):
                    plugins.extend(e.value for e in value.elts)
                else:
                    return [], "pytest_plugins_not_a_literal_list"
    return plugins, None


def _resolve_plugin_file(plugin: str, conftest: Path, root: Path) -> str | None:
    if plugin.startswith("."):
        level = len(plugin) - len(plugin.lstrip("."))
        remainder = plugin.lstrip(".")
        try:
            package_dir = conftest.parent.relative_to(root)
        except ValueError:
            return None
        for _ in range(level - 1):
            package_dir = package_dir.parent
        dotted = ".".join([*package_dir.parts, remainder] if remainder else list(package_dir.parts))
        if not dotted:
            return None
    else:
        dotted = plugin
    if "." in dotted:
        module_file = _module_file(dotted, root)
    else:
        module_file = None
        for search_dir in BARE_MODULE_SEARCH_DIRS:
            base = root if not search_dir else root / search_dir
            for candidate in (base / f"{dotted}.py", base / dotted / "__init__.py"):
                if candidate.is_file():
                    module_file = candidate
                    break
            if module_file is not None:
                break
    if module_file is None:
        return None
    return _rel(module_file, root)


def _seed_with_parent_inits(seeds: set[str], rel: str, root: Path) -> None:
    seeds.add(rel)
    for init in _parent_inits(root / rel, root):
        init_rel = _rel(init, root)
        if init_rel is not None:
            seeds.add(init_rel)


def _build_shared_deps(root: Path, cache: dict[str, FileDeps]) -> tuple[set[str], str | None, list[str]]:
    conftest = root / CONFTEST_REPO_PATH
    if not conftest.is_file():
        return set(), "conftest_missing", []
    plugins, plugins_error = _read_pytest_plugins(conftest)
    if plugins_error is not None:
        return set(), plugins_error, []
    shared_seeds = {CONFTEST_REPO_PATH}
    for plugin in plugins:
        plugin_file = _resolve_plugin_file(plugin, conftest, root)
        if plugin_file is None:
            return set(), f"plugin_unresolvable:{plugin}", []
        _seed_with_parent_inits(shared_seeds, plugin_file, root)
    shared_deps, dynamic_files = _expand_closure(shared_seeds, root, cache)
    direct_dynamic = sorted(
        f"{kind}:{rel}" for rel, kinds in dynamic_files.items() for kind in kinds if rel in shared_seeds
    )
    if direct_dynamic:
        return set(), f"shared_dynamic:{','.join(direct_dynamic)}", []
    library_sites = sorted(f"{rel}:{kind}" for rel, kinds in dynamic_files.items() for kind in kinds)
    return shared_deps, None, library_sites


def build_dependency_map(root: Path, test_files: list[str]) -> tuple[dict[str, FileDeps], str | None, list[str]]:
    root = root.resolve()
    cache: dict[str, FileDeps] = {}
    shared_deps, fallback, library_sites = _build_shared_deps(root, cache)
    if fallback is not None:
        return {}, fallback, []
    dep_map: dict[str, FileDeps] = {}
    for test_file in test_files:
        direct = cache.get(test_file)
        if direct is None:
            direct = analyze_python_file(root / test_file, root)
            cache[test_file] = direct
        chain_files = _conftest_chain(test_file, root)
        seeds = set(direct.deps)
        dynamic_kinds = list(direct.dynamic_kinds)
        for chain_rel in chain_files:
            chain_deps = cache.get(chain_rel)
            if chain_deps is None:
                chain_deps = analyze_python_file(root / chain_rel, root)
                cache[chain_rel] = chain_deps
            seeds.add(chain_rel)
            seeds.update(chain_deps.deps)
            dynamic_kinds.extend(f"transitive:{kind}:{chain_rel}" for kind in chain_deps.dynamic_kinds)
            chain_plugins, chain_error = _read_pytest_plugins(root / chain_rel)
            if chain_error is not None:
                dynamic_kinds.append(f"chain_conftest_plugins:{chain_rel}:{chain_error}")
                continue
            for plugin in chain_plugins:
                plugin_file = _resolve_plugin_file(plugin, root / chain_rel, root)
                if plugin_file is None:
                    dynamic_kinds.append(f"chain_plugin_unresolvable:{chain_rel}:{plugin}")
                else:
                    _seed_with_parent_inits(seeds, plugin_file, root)
        expanded, closure_dynamic = _expand_closure(seeds, root, cache)
        handled = set(chain_files) | {test_file}
        dynamic_kinds.extend(
            f"transitive:{kind}:{rel}"
            for rel, kinds in closure_dynamic.items()
            for kind in kinds
            if rel not in handled
            and rel not in shared_deps
            and (kind == "dynamic_import" or kind.startswith("unresolvable_import:"))
        )
        library_sites.extend(
            f"{rel}:{kind}" for rel, kinds in closure_dynamic.items() for kind in kinds if rel not in handled
        )
        test_path = root / test_file
        expanded.update(dep for dep in (_rel(init, root) for init in _parent_inits(test_path, root)) if dep is not None)
        dep_map[test_file] = FileDeps(
            deps=frozenset(expanded | shared_deps),
            dynamic=len(dynamic_kinds) > 0,
            dynamic_kinds=tuple(dynamic_kinds),
        )
    return dep_map, None, sorted(set(library_sites))


def collect_canary_node_ids(
    root: Path,
    timeout_seconds: int = 600,
    marker_expression: str = MARKER_EXPRESSION,
    paths: Sequence[str] | None = None,
) -> list[str]:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-n",
            "0",
            "-p",
            "no:cacheprovider",
            "-m",
            marker_expression,
            *(paths or ("tests",)),
        ],
        cwd=str(root),
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    if completed.returncode not in (0, 5):
        raise RuntimeError(f"canary collection failed (exit {completed.returncode}): {completed.stderr[-2000:]}")
    return parse_collection_output(completed.stdout)


def files_from_node_ids(node_ids: list[str]) -> list[str]:
    return sorted({node_id.split("::", 1)[0] for node_id in node_ids})


def _change_covers(changed: str, dep: str) -> bool:
    if changed == dep:
        return True
    if dep.endswith("/"):
        return changed.startswith(dep)
    if changed.startswith(dep + "/"):
        return True
    if dep.startswith(changed + "/"):
        return True
    return False


def _same_directory_non_python(changed: str, test_deps: frozenset[str]) -> bool:
    if not changed.startswith("benchbox/") or changed.endswith(".py"):
        return False
    changed_dir = changed.rpartition("/")[0]
    return any(
        dep.startswith("benchbox/") and dep.endswith(".py") and dep.rpartition("/")[0] == changed_dir
        for dep in test_deps
    )


def compute_selection(  # noqa: C901
    changed_paths: list[str],
    dep_map: dict[str, FileDeps],
    node_ids: list[str],
    *,
    collection_info: dict[str, Any] | None = None,
    fallback_reason: str | None = None,
    marker_expression: str = MARKER_EXPRESSION,
    cant_affect: frozenset[str] = CANT_AFFECT_CANARY,
    whole_suite_paths: frozenset[str] = WHOLE_SUITE_PATHS,
) -> dict[str, Any]:
    changed = sorted({normalize_rel(path) for path in changed_paths if normalize_rel(path)})
    collection_info = collection_info or {}
    file_nodes: dict[str, list[str]] = {}
    for node_id in node_ids:
        file_nodes.setdefault(node_id.split("::", 1)[0], []).append(node_id)

    whole_suite: str | None = fallback_reason
    trigger_paths: list[str] = []
    if whole_suite is None:
        for path in changed:
            if path in whole_suite_paths:
                whole_suite = f"whole_suite_path:{path}"
                trigger_paths.append(path)
    unmapped: list[str] = []
    ignored: list[str] = []
    if whole_suite is None:
        for path in changed:
            if any(_change_covers(path, dep) for deps in dep_map.values() for dep in deps.deps):
                continue
            if any(path == test_file or path.startswith(test_file + "/") for test_file in dep_map):
                continue
            if any(_same_directory_non_python(path, deps.deps) for deps in dep_map.values()):
                continue
            if _is_cant_affect(path, cant_affect):
                ignored.append(path)
                continue
            unmapped.append(path)
        if unmapped:
            whole_suite = f"unmapped_path:{unmapped[0]}"
            trigger_paths.extend(unmapped)

    selected: dict[str, list[dict[str, str]]] = {}
    if whole_suite is not None:
        for node_id in node_ids:
            selected[node_id] = [{"changed_path": trigger, "edge": "whole_suite"} for trigger in trigger_paths] or [
                {"changed_path": "", "edge": "whole_suite"}
            ]
    elif changed:
        effective = [path for path in changed if path not in ignored]
        for test_file, file_deps in dep_map.items():
            nodes = file_nodes.get(test_file, [])
            if not nodes:
                continue
            if test_file in changed:
                for node_id in nodes:
                    selected.setdefault(node_id, []).append({"changed_path": test_file, "edge": "self"})
            for path in effective:
                if path == test_file:
                    continue
                if any(_change_covers(path, dep) for dep in file_deps.deps):
                    for node_id in nodes:
                        selected.setdefault(node_id, []).append({"changed_path": path, "edge": "dependency"})
                elif _same_directory_non_python(path, file_deps.deps):
                    for node_id in nodes:
                        selected.setdefault(node_id, []).append({"changed_path": path, "edge": "same_directory"})
            if file_deps.dynamic:
                for node_id in nodes:
                    for path in effective:
                        selected.setdefault(node_id, []).append(
                            {"changed_path": path, "edge": "unresolvable_dynamic_edge"}
                        )
    return {
        "marker_expression": marker_expression,
        "collection": collection_info,
        "changed_paths": changed,
        "whole_suite": whole_suite is not None,
        "whole_suite_reason": whole_suite,
        "unmapped_changed_paths": unmapped,
        "selected": [
            {
                "node_id": node_id,
                "file": node_id.split("::", 1)[0],
                "reasons": selected[node_id],
            }
            for node_id in sorted(selected)
        ],
        "selected_count": len(selected),
        "total_count": len(node_ids),
    }


def _git_changed_paths(repo_root: Path, base_ref: str) -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), "diff", "--name-only", "--diff-filter=ACDMRT", f"{base_ref}...HEAD"],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"git diff {base_ref}...HEAD failed: {completed.stderr[-1000:]}")
    return [line for line in (normalize_rel(line) for line in completed.stdout.splitlines()) if line]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--changed-path", action="append", default=[], help="changed repo-relative path (repeatable)")
    source.add_argument("--base-ref", help="git ref to diff against HEAD (e.g. origin/develop)")
    source.add_argument("--from-stdin", action="store_true", help="read changed paths (one per line) from stdin")
    source.add_argument("--changed-json-env", help="environment variable containing a JSON array of changed paths")
    parser.add_argument("--collection-file", type=Path, help="newline-delimited node IDs (skip live collection)")
    parser.add_argument("--output", type=Path, help="write the JSON selection (default: stdout)")
    parser.add_argument(
        "--marker-expression", default=MARKER_EXPRESSION, help="pytest marker expression to collect and run"
    )
    parser.add_argument("--cant-affect-list", choices=("canary", "empty"), default="canary")
    parser.add_argument("--product-code-only", action="store_true", help="ignore non-product paths for local preflight")
    parser.add_argument(
        "--run-selected", action="store_true", help="run selected tests with the medium tier's pytest options"
    )
    return parser


def run_selected_tests(
    selection: dict[str, Any], root: Path, marker_expression: str, changed_json_env: str | None
) -> int:
    whole_suite = selection["whole_suite"]
    target = ["tests"] if whole_suite else sorted({entry["file"] for entry in selection["selected"]})
    if not target:
        print("[impact] no medium tests selected", file=sys.stderr)
        return 0
    if whole_suite:
        print("[impact] running the full medium tier", file=sys.stderr, flush=True)
    else:
        print(
            f"[impact] running {len(target)} test files covering {selection['selected_count']} selected cases",
            file=sys.stderr,
            flush=True,
        )
    command = [sys.executable, "-m", "pytest", "-m", marker_expression, "--tb=short", "--timeout=60", "-n", "5"]
    if not whole_suite:
        command.append("--dist=loadfile")
    command.extend(target)
    pytest_env = os.environ.copy()
    if changed_json_env:
        pytest_env.pop(changed_json_env, None)
    return subprocess.run(command, cwd=str(root), env=pytest_env, check=False).returncode


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    root = args.repo_root.resolve()
    try:
        if args.base_ref:
            changed = _git_changed_paths(root, args.base_ref)
        elif args.from_stdin:
            changed = [line for line in (normalize_rel(line) for line in sys.stdin.read().splitlines()) if line]
        elif args.changed_json_env:
            changed_json = os.environ.get(args.changed_json_env)
            if changed_json is None:
                raise ValueError(f"missing changed-path environment variable: {args.changed_json_env}")
            changed_value = json.loads(changed_json)
            if not isinstance(changed_value, list) or not all(isinstance(path, str) for path in changed_value):
                raise ValueError("changed paths must be a JSON array of strings")
            changed = [path for path in (normalize_rel(path) for path in changed_value) if path]
        else:
            changed = list(args.changed_path)
        if args.product_code_only:
            changed = medium_relevant_paths(changed)
        fallback_reason: str | None = None
        node_ids: list[str] = []
        if args.product_code_only and not changed:
            collection_info = {
                "source": "skipped_no_product_code",
                "marker_expression": args.marker_expression,
                "node_count": 0,
            }
        elif args.collection_file is not None:
            node_ids = parse_collection_output(args.collection_file.read_text(encoding="utf-8"))
            collection_info: dict[str, Any] = {"source": str(args.collection_file), "node_count": len(node_ids)}
        else:
            try:
                node_ids = collect_canary_node_ids(root, marker_expression=args.marker_expression)
            except (RuntimeError, subprocess.SubprocessError, OSError) as exc:
                fallback_reason = f"collection_failed:{exc}"
            collection_info = {
                "source": "live_collection",
                "marker_expression": args.marker_expression,
                "node_count": len(node_ids),
            }
        dep_map: dict[str, FileDeps] = {}
        library_sites: list[str] = []
        if fallback_reason is None and not (args.product_code_only and not changed):
            dep_map, map_fallback, library_sites = build_dependency_map(root, files_from_node_ids(node_ids))
            if map_fallback is not None:
                fallback_reason = f"dependency_map_failed:{map_fallback}"
        selection = compute_selection(
            changed,
            dep_map,
            node_ids,
            collection_info=collection_info,
            fallback_reason=fallback_reason,
            marker_expression=args.marker_expression,
            cant_affect=CANT_AFFECT_CANARY if args.cant_affect_list == "canary" else frozenset(),
            whole_suite_paths=MEDIUM_WHOLE_SUITE_PATHS if args.product_code_only else WHOLE_SUITE_PATHS,
        )
        selection["dynamic_library_sites"] = sorted(set(library_sites))
        rendered = json.dumps(selection, indent=2) + "\n"
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        else:
            sys.stdout.write(rendered)
        if args.run_selected:
            return run_selected_tests(selection, root, args.marker_expression, args.changed_json_env)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"canary-impact error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
