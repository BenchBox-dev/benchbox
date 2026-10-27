#!/usr/bin/env python3

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

CLI_DESCRIPTION = (
    "Check for Windows anti-patterns in benchbox/, tests/, scripts/ and _project/.\n"
    "\n"
    "Uses AST analysis to find patterns that compile and run on Linux/macOS\n"
    "but silently misbehave or fail on Windows:\n"
    "\n"
    "  1. os.access(path, os.X_OK) - always returns True on Windows; use\n"
    '     .suffix == ".exe" or os.name checks instead.\n'
    "  2. Unguarded Unix-only signals (SIGTERM, SIGKILL, SIGHUP, SIGUSR1/2,\n"
    "     SIGCHLD, SIGPIPE) - these don't exist on Windows; guard with\n"
    "     `if hasattr(signal, ...)`.\n"
    "  3. pathlib-style read_text() calls without an explicit encoding - these use\n"
    "     the locale encoding and can fail on non-UTF-8 Windows installations.\n"
    "     Builtin open() without an encoding is covered by ruff PLW1514.\n"
    "  4. The literal path /dev/null - it does not exist on Windows; use\n"
    "     os.devnull or a temporary file. Diff headers and platform guards are exempt.\n"
    "  5. Module-level imports of POSIX-only modules (fcntl, termios, pwd, grp,\n"
    "     resource) outside a sys.platform/os.name guard or a try/except\n"
    "     ImportError - the import itself fails on Windows.\n"
    "  6. subprocess calls passing an env= dict or local dict variable without SYSTEMROOT\n"
    "     that is not built from os.environ - Windows children cannot initialise\n"
    "     Winsock or the CRT without it (WinError 10106).\n"
    "     Only unmodified literal bindings are resolved; mutations and computed mappings are not proved safe.\n"
    "  7. subprocess calls running git with text=True or universal_newlines=True\n"
    "     and no encoding= - git emits UTF-8 but the pipe is decoded with the\n"
    "     locale code page.\n"
    "     POSIX-only reviewer process/build helpers and sql-translation-ml are excluded.\n"
    "\n"
    "Existing violations are recorded per file and code in\n"
    "quality/windows-antipatterns-baseline.json. A file fails when it holds more\n"
    "violations of a code than its baseline entry, and when it holds fewer, so the\n"
    "baseline can only shrink. After fixing violations, lower the baseline with\n"
    "--write-baseline.\n"
    "\n"
    "Usage:\n"
    "    uv run -- python scripts/check_windows_antipatterns.py\n"
    "    uv run -- python scripts/check_windows_antipatterns.py benchbox/core/query.py\n"
    "    uv run -- python scripts/check_windows_antipatterns.py --path benchbox/\n"
    "    uv run -- python scripts/check_windows_antipatterns.py --write-baseline\n"
)

_UNIX_SIGNALS = {"SIGTERM", "SIGKILL", "SIGHUP", "SIGUSR1", "SIGUSR2", "SIGCHLD", "SIGPIPE"}

_DEV_NULL = "/".join(("", "dev", "null"))

_POSIX_ONLY_MODULES = {"fcntl", "termios", "pwd", "grp", "resource"}

_SUBPROCESS_CALLS = {"run", "Popen", "check_output", "check_call", "call"}

_IMPORT_ERROR_NAMES = {"ImportError", "ModuleNotFoundError", "Exception", "BaseException"}

_DEFAULT_ROOTS = ["benchbox", "tests", "scripts", "_project"]

_DEFAULT_EXCLUDES = {
    "_project/sql-translation-ml",
    "_project/scripts/oracle_reviewers/omp_pin.py",
    "_project/scripts/oracle_reviewers/processes.py",
    "_project/scripts/oracle_reviewers/runner.py",
}

_DEFAULT_BASELINE = "quality/windows-antipatterns-baseline.json"

Baseline = dict[str, dict[str, int]]


@dataclass(frozen=True)
class Violation:
    file: Path
    line: int
    col: int
    code: str
    message: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line}:{self.col}: {self.code} {self.message}"


class WindowsAntipatternsVisitor(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.violations: list[Violation] = []
        self.subprocess_modules: set[str] = {"subprocess"}
        self.subprocess_funcs: set[str] = set()
        self.scope_stack: list[dict[str, ast.expr]] = [{}]

    def visit_Module(self, node: ast.Module) -> None:
        for parent in ast.walk(node):
            for child in ast.iter_child_nodes(parent):
                child.parent = parent
        for child in ast.walk(node):
            if isinstance(child, ast.Import):
                for alias in child.names:
                    if alias.name == "subprocess":
                        self.subprocess_modules.add(alias.asname or alias.name)
            elif isinstance(child, ast.ImportFrom) and child.module == "subprocess" and child.level == 0:
                for alias in child.names:
                    if alias.name in _SUBPROCESS_CALLS:
                        self.subprocess_funcs.add(alias.asname or alias.name)
        self._check_posix_module_imports(node.body)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.scope_stack.append({})
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.scope_stack.append({})
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if isinstance(target, ast.Name):
                self.scope_stack[-1][target.id] = node.value
            elif isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
                self.scope_stack[-1].pop(target.value.id, None)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name) and node.value is not None:
            self.scope_stack[-1][node.target.id] = node.value
        self.generic_visit(node)

    def _check_posix_module_imports(self, body: list[ast.stmt]) -> None:
        for stmt in body:
            if isinstance(stmt, ast.Import):
                for alias in stmt.names:
                    self._flag_posix_import(stmt, alias.name)
            elif isinstance(stmt, ast.ImportFrom) and stmt.level == 0 and stmt.module:
                self._flag_posix_import(stmt, stmt.module)
            elif isinstance(stmt, ast.If):
                if _is_posix_check(stmt.test) or (isinstance(stmt.test, ast.Name) and stmt.test.id == "TYPE_CHECKING"):
                    self._check_posix_module_imports(stmt.orelse)
                elif _is_windows_check(stmt.test):
                    self._check_posix_module_imports(stmt.body)
                else:
                    self._check_posix_module_imports(stmt.body)
                    self._check_posix_module_imports(stmt.orelse)
            elif isinstance(stmt, ast.Try):
                if not any(_handler_catches_import_error(h) for h in stmt.handlers):
                    self._check_posix_module_imports(stmt.body)
                for h in stmt.handlers:
                    self._check_posix_module_imports(h.body)
                self._check_posix_module_imports(stmt.orelse)
                self._check_posix_module_imports(stmt.finalbody)
            elif isinstance(stmt, ast.With):
                self._check_posix_module_imports(stmt.body)

    def _flag_posix_import(self, node: ast.stmt, module: str) -> None:
        root = module.split(".")[0]
        if root in _POSIX_ONLY_MODULES:
            self._add(
                node,
                "WA005",
                f"module-level import of POSIX-only '{root}' fails on Windows - import it inside the function "
                "that needs it, or guard with `if sys.platform != 'win32'` or try/except ImportError",
            )

    def _add(self, node: ast.AST, code: str, message: str) -> None:
        self.violations.append(
            Violation(
                file=self.path,
                line=node.lineno,
                col=node.col_offset,
                code=code,
                message=message,
            )
        )

    def visit_Call(self, node: ast.Call) -> None:
        self._check_os_access(node)
        self._check_getattr_signal(node)
        self._check_read_text_encoding(node)
        self._check_subprocess_call(node)
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            val = node.value
            if val in {"--- /dev/null", "+++ /dev/null"} or val.startswith(("--- /dev/null", "+++ /dev/null")):
                return
            if val == _DEV_NULL:
                if _is_comparison_operand(node) or _is_in_posix_branch(node):
                    return
                self._add(
                    node,
                    "WA004",
                    "'/dev/null' does not exist on Windows - use os.devnull or a temporary file",
                )

    def _is_subprocess_call(self, node: ast.Call) -> bool:
        func = node.func
        if isinstance(func, ast.Attribute):
            return (
                func.attr in _SUBPROCESS_CALLS
                and isinstance(func.value, ast.Name)
                and func.value.id in self.subprocess_modules
            )
        return isinstance(func, ast.Name) and func.id in self.subprocess_funcs

    def _check_subprocess_call(self, node: ast.Call) -> None:
        if not self._is_subprocess_call(node):
            return
        keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg is not None}
        env = keywords.get("env")
        if isinstance(env, ast.Name):
            env = self.scope_stack[-1].get(env.id)
        if isinstance(env, ast.Dict) and not _env_dict_is_safe(env) and not _is_in_posix_branch(node):
            self._add(
                node,
                "WA006",
                "subprocess env= dict literal lacks SYSTEMROOT and is not built from os.environ - Windows children "
                "fail with WinError 10106; use {**os.environ, ...} or add SYSTEMROOT",
            )
        if (
            any(_is_true_constant(keywords.get(name)) for name in ("text", "universal_newlines"))
            and "encoding" not in keywords
            and not any(kw.arg is None for kw in node.keywords)
            and _runs_git(node)
        ):
            self._add(
                node,
                "WA007",
                "git subprocess with text=True decodes output with the locale encoding - "
                "pass encoding='utf-8' (and errors=) instead",
            )

    def _check_read_text_encoding(self, node: ast.Call) -> None:
        encoding: ast.expr | None = None
        if not (isinstance(node.func, ast.Attribute) and node.func.attr == "read_text"):
            return
        if node.args and not isinstance(node.args[0], ast.Starred):
            encoding = node.args[0]
        for keyword in node.keywords:
            if keyword.arg == "encoding":
                encoding = keyword.value
                break
        if encoding is not None and not (isinstance(encoding, ast.Constant) and encoding.value is None):
            return
        self._add(
            node,
            "WA003",
            "read_text() without an explicit encoding uses the locale default - pass encoding='utf-8' "
            "or the file format's required encoding",
        )

    def _check_os_access(self, node: ast.Call) -> None:
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "access"):
            return
        if not (isinstance(func.value, ast.Name) and func.value.id == "os"):
            return
        all_args = list(node.args) + [kw.value for kw in node.keywords]
        for arg in all_args:
            if (
                isinstance(arg, ast.Attribute)
                and arg.attr == "X_OK"
                and isinstance(arg.value, ast.Name)
                and arg.value.id == "os"
            ):
                self._add(
                    node,
                    "WA001",
                    "os.access(path, os.X_OK) is always True on Windows - "
                    "use `path.suffix == '.exe'` check or guard with `if os.name != 'nt'`",
                )
                return

    def _check_getattr_signal(self, node: ast.Call) -> None:
        if not (isinstance(node.func, ast.Name) and node.func.id == "getattr"):
            return
        if len(node.args) != 2:
            return
        if not (isinstance(node.args[0], ast.Name) and node.args[0].id == "signal"):
            return
        if not (isinstance(node.args[1], ast.Constant) and node.args[1].value in _UNIX_SIGNALS):
            return
        sig_name = node.args[1].value
        self._add(
            node,
            "WA002",
            f"getattr(signal, '{sig_name}') raises AttributeError on Windows - "
            f"use getattr(signal, '{sig_name}', None) or guard with `if hasattr(signal, '{sig_name}')`",
        )

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self._check_unix_signal(node)
        self.generic_visit(node)

    def _check_unix_signal(self, node: ast.Attribute) -> None:
        if node.attr not in _UNIX_SIGNALS:
            return
        if not (isinstance(node.value, ast.Name) and node.value.id == "signal"):
            return
        self._add(
            node,
            "WA002",
            f"signal.{node.attr} does not exist on Windows - "
            f"guard with `if hasattr(signal, '{node.attr}')` or `if os.name != 'nt'`",
        )


def _is_true_constant(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _runs_git(node: ast.Call) -> bool:
    if not node.args:
        return False
    cmd = node.args[0]
    if isinstance(cmd, (ast.List, ast.Tuple)) and cmd.elts:
        cmd = cmd.elts[0]
    return isinstance(cmd, ast.Constant) and cmd.value in {"git", "git.exe"}


def _is_comparison_operand(node: ast.AST) -> bool:
    parent = getattr(node, "parent", None)
    if isinstance(parent, (ast.Tuple, ast.List, ast.Set)):
        parent = getattr(parent, "parent", None)
    return isinstance(parent, ast.Compare)


def _is_in_posix_branch(node: ast.AST) -> bool:
    curr = getattr(node, "parent", None)
    prev = node
    while curr is not None:
        if isinstance(curr, ast.If):
            if _is_posix_check(curr.test):
                if prev in curr.body or any(prev is b for b in curr.body):
                    return True
            elif _is_windows_check(curr.test):
                if prev in curr.orelse or any(prev is b for b in curr.orelse):
                    return True
        elif isinstance(curr, ast.IfExp):
            if _is_posix_check(curr.test) and prev is curr.body:
                return True
            if _is_windows_check(curr.test) and prev is curr.orelse:
                return True
        elif isinstance(curr, ast.BoolOp) and isinstance(curr.op, ast.And):
            if any(_is_posix_check(v) for v in curr.values):
                return True
        prev = curr
        curr = getattr(curr, "parent", None)
    return False


def _is_sys_platform(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "platform"
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
    )


def _is_os_name(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "name"
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    )


def _is_platform_system(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "system"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "platform"
    )


def _is_posix_check(node: ast.expr) -> bool:
    if isinstance(node, ast.Compare):
        if len(node.ops) == 1 and len(node.comparators) == 1:
            op = node.ops[0]
            val = node.comparators[0]
            left = node.left
            if isinstance(op, ast.NotEq):
                if _is_sys_platform(left) and isinstance(val, ast.Constant) and val.value == "win32":
                    return True
                if _is_os_name(left) and isinstance(val, ast.Constant) and val.value == "nt":
                    return True
                if _is_platform_system(left) and isinstance(val, ast.Constant) and val.value == "Windows":
                    return True
            elif isinstance(op, ast.Eq):
                if _is_os_name(left) and isinstance(val, ast.Constant) and val.value == "posix":
                    return True
                if _is_platform_system(left) and isinstance(val, ast.Constant) and val.value in {"Linux", "Darwin"}:
                    return True
    return False


def _is_windows_check(node: ast.expr) -> bool:
    if isinstance(node, ast.Compare):
        if len(node.ops) == 1 and len(node.comparators) == 1:
            op = node.ops[0]
            val = node.comparators[0]
            left = node.left
            if isinstance(op, ast.Eq):
                if _is_sys_platform(left) and isinstance(val, ast.Constant) and val.value == "win32":
                    return True
                if _is_os_name(left) and isinstance(val, ast.Constant) and val.value == "nt":
                    return True
                if _is_platform_system(left) and isinstance(val, ast.Constant) and val.value == "Windows":
                    return True
            elif isinstance(op, ast.NotEq):
                if _is_os_name(left) and isinstance(val, ast.Constant) and val.value == "posix":
                    return True
    return False


def _mentions_environ(node: ast.AST) -> bool:
    return any(isinstance(n, ast.Attribute) and n.attr == "environ" for n in ast.walk(node))


def _env_dict_is_safe(env: ast.Dict) -> bool:
    for key, value in zip(env.keys, env.values, strict=True):
        if key is None:
            if _mentions_environ(value) or isinstance(value, (ast.Name, ast.Attribute, ast.Call, ast.Subscript)):
                return True
        elif isinstance(key, ast.Constant) and isinstance(key.value, str) and key.value.upper() == "SYSTEMROOT":
            return True
    return False


def _handler_catches_import_error(h: ast.ExceptHandler) -> bool:
    if h.type is None:
        return True
    types = h.type.elts if isinstance(h.type, ast.Tuple) else [h.type]
    return any(isinstance(t, ast.Name) and t.id in _IMPORT_ERROR_NAMES for t in types)


def _is_platform_check(node: ast.expr) -> bool:
    if not isinstance(node, ast.Compare):
        return False
    if not (len(node.ops) == 1 and len(node.comparators) == 1):
        return False
    op = node.ops[0]
    val = node.comparators[0]
    if not isinstance(op, (ast.Eq, ast.NotEq)):
        return False
    left = node.left

    if (
        isinstance(left, ast.Attribute)
        and left.attr == "name"
        and isinstance(left.value, ast.Name)
        and left.value.id == "os"
        and isinstance(val, ast.Constant)
        and val.value == "nt"
    ):
        return True

    if (
        isinstance(left, ast.Attribute)
        and left.attr == "platform"
        and isinstance(left.value, ast.Name)
        and left.value.id == "sys"
        and isinstance(val, ast.Constant)
        and val.value == "win32"
    ):
        return True

    if (
        isinstance(left, ast.Call)
        and isinstance(left.func, ast.Attribute)
        and left.func.attr == "system"
        and isinstance(left.func.value, ast.Name)
        and left.func.value.id == "platform"
        and isinstance(val, ast.Constant)
        and val.value == "Windows"
    ):
        return True

    return False


def _collect_os_access_lines(node: ast.AST, guarded_lines: set[int]) -> None:
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr == "access"
            and isinstance(func.value, ast.Name)
            and func.value.id == "os"
        ):
            continue
        for arg in list(child.args) + [kw.value for kw in child.keywords]:
            if (
                isinstance(arg, ast.Attribute)
                and arg.attr == "X_OK"
                and isinstance(arg.value, ast.Name)
                and arg.value.id == "os"
            ):
                guarded_lines.add(child.lineno)


def _collect_os_name_guarded_lines(tree: ast.AST) -> set[int]:
    guarded_lines: set[int] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.BoolOp):
            if any(_is_platform_check(v) or _is_posix_check(v) for v in node.values):
                for v in node.values:
                    _collect_os_access_lines(v, guarded_lines)

        if isinstance(node, ast.If):
            if _is_platform_check(node.test) or _is_posix_check(node.test):
                for stmt in node.body:
                    for child in ast.walk(stmt):
                        _collect_os_access_lines(child, guarded_lines)
            elif _is_windows_check(node.test):
                for stmt in node.orelse:
                    for child in ast.walk(stmt):
                        _collect_os_access_lines(child, guarded_lines)

    return guarded_lines


def _extract_hasattr_signal_from_node(n: ast.AST) -> str | None:
    if (
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "hasattr"
        and len(n.args) >= 2
        and isinstance(n.args[1], ast.Constant)
        and n.args[1].value in _UNIX_SIGNALS
    ):
        return n.args[1].value
    return None


def _hasattr_signal_in_call(node: ast.Call) -> str | None:
    return _extract_hasattr_signal_from_node(node)


def _getattr_signal_in_call(node: ast.Call) -> str | None:
    if (
        isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 3
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "signal"
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value in _UNIX_SIGNALS
    ):
        return node.args[1].value
    return None


def _guard_signals_from_if_test(test: ast.AST) -> set[str]:
    signals: set[str] = set()
    direct = _extract_hasattr_signal_from_node(test)
    if direct:
        signals.add(direct)
    elif isinstance(test, ast.BoolOp):
        for value in test.values:
            sig = _extract_hasattr_signal_from_node(value)
            if sig:
                signals.add(sig)
    return signals


def _handler_catches_attr_error(h: ast.ExceptHandler) -> bool:
    if h.type is None:
        return True
    if isinstance(h.type, ast.Name) and h.type.id == "AttributeError":
        return True
    if isinstance(h.type, ast.Tuple):
        return any(isinstance(t, ast.Name) and t.id == "AttributeError" for t in h.type.elts)
    return False


def _iter_signal_attrs(stmts: list[ast.stmt]):
    for stmt in stmts:
        for child in ast.walk(stmt):
            if (
                isinstance(child, ast.Attribute)
                and child.attr in _UNIX_SIGNALS
                and isinstance(child.value, ast.Name)
                and child.value.id == "signal"
            ):
                yield child


def _collect_guarded_signal_lines(tree: ast.AST) -> set[tuple[int, str]]:
    guarded: set[tuple[int, str]] = set()

    class _HasattrFinder(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            sig = _hasattr_signal_in_call(node) or _getattr_signal_in_call(node)
            if sig:
                guarded.add((node.lineno, sig))
            self.generic_visit(node)

        def visit_If(self, node: ast.If) -> None:
            guard_signals = _guard_signals_from_if_test(node.test)
            if guard_signals:
                for sig_name in guard_signals:
                    for child in ast.walk(node):
                        if hasattr(child, "lineno"):
                            guarded.add((child.lineno, sig_name))
            self.generic_visit(node)

        def visit_Try(self, node: ast.Try) -> None:
            if any(_handler_catches_attr_error(h) for h in node.handlers):
                for child in _iter_signal_attrs(node.body):
                    guarded.add((child.lineno, child.attr))
            self.generic_visit(node)

    _HasattrFinder().visit(tree)
    return guarded


def _has_noqa(lines: list[str], v: Violation) -> bool:
    if v.line > len(lines):
        return False
    line = lines[v.line - 1]
    noqa_match = re.search(r"#\s*noqa:\s*([\w,\s]+)", line)
    if noqa_match:
        codes = {c.strip() for c in noqa_match.group(1).split(",")}
        return v.code in codes
    return False


def check_file(path: Path) -> list[Violation]:
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    visitor = WindowsAntipatternsVisitor(path)
    visitor.visit(tree)

    if not visitor.violations:
        return []

    source_lines = source.splitlines()
    after_noqa = [v for v in visitor.violations if not _has_noqa(source_lines, v)]

    if not after_noqa:
        return []

    os_name_guarded = _collect_os_name_guarded_lines(tree)

    signal_guarded = _collect_guarded_signal_lines(tree)

    filtered: list[Violation] = []
    for v in after_noqa:
        if v.code == "WA001" and v.line in os_name_guarded:
            continue
        if v.code == "WA002":
            sig_name = next((s for s in _UNIX_SIGNALS if s in v.message), "")
            if sig_name and (v.line, sig_name) in signal_guarded:
                continue
        filtered.append(v)

    return filtered


def _is_excluded(path: Path, repo_root: Path) -> bool:
    key = _file_key(path, repo_root)
    return any(key == ex or key.startswith(f"{ex}/") for ex in _DEFAULT_EXCLUDES)


def check_tree(roots: list[Path], repo_root: Path | None = None) -> tuple[list[Violation], int]:
    violations: list[Violation] = []
    file_count = 0
    repo_root = repo_root or Path(__file__).resolve().parent.parent
    for root in roots:
        for py_file in sorted(root.rglob("*.py")):
            if _is_excluded(py_file, repo_root):
                continue
            violations.extend(check_file(py_file))
            file_count += 1
    return violations, file_count


@dataclass(frozen=True)
class RatchetResult:
    regressions: list[Violation]
    stale: list[str]

    @property
    def clean(self) -> bool:
        return not self.regressions and not self.stale


def _file_key(path: Path, repo_root: Path) -> str:
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return path.as_posix()


def load_baseline(path: Path) -> Baseline:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {file: {code: int(count) for code, count in codes.items()} for file, codes in raw["files"].items()}


def count_violations(violations: list[Violation], repo_root: Path) -> Baseline:
    counts: Baseline = {}
    for v in violations:
        codes = counts.setdefault(_file_key(v.file, repo_root), {})
        codes[v.code] = codes.get(v.code, 0) + 1
    return counts


def apply_baseline(
    violations: list[Violation],
    baseline: Baseline,
    repo_root: Path,
    roots: list[Path],
    explicit_files: list[Path] | None = None,
) -> RatchetResult:
    current = count_violations(violations, repo_root)
    regressions = [
        v
        for v in violations
        if current[_file_key(v.file, repo_root)][v.code] > baseline.get(_file_key(v.file, repo_root), {}).get(v.code, 0)
    ]
    stale: list[str] = []
    if explicit_files is not None:
        explicit_keys = {_file_key(f, repo_root) for f in explicit_files}
        for key in explicit_keys:
            if key in baseline:
                for code, allowed in baseline[key].items():
                    found = current.get(key, {}).get(code, 0)
                    if found < allowed:
                        stale.append(f"{key}: {code} baseline allows {allowed}, found {found}")
    else:
        for key, codes in baseline.items():
            if not (repo_root / key).is_file() or not any((repo_root / key).is_relative_to(root) for root in roots):
                continue
            for code, allowed in codes.items():
                found = current.get(key, {}).get(code, 0)
                if found < allowed:
                    stale.append(f"{key}: {code} baseline allows {allowed}, found {found}")
    return RatchetResult(regressions=regressions, stale=sorted(stale))


def write_baseline(path: Path, violations: list[Violation], repo_root: Path, roots: list[Path]) -> int:
    existing = load_baseline(path)
    current = count_violations(violations, repo_root)
    grown = sorted(
        f"{key}: {code} baseline allows {existing.get(key, {}).get(code, 0)}, found {count}"
        for key, codes in current.items()
        for code, count in codes.items()
        if path.exists() and count > existing.get(key, {}).get(code, 0)
    )
    if grown:
        print("error: refusing to raise the baseline; fix or suppress these violations instead:", file=sys.stderr)
        for line in grown:
            print(f"  {line}", file=sys.stderr)
        return 1
    merged = {
        key: codes for key, codes in existing.items() if not any((repo_root / key).is_relative_to(r) for r in roots)
    }
    merged.update(current)
    payload = {"files": {key: dict(sorted(codes.items())) for key, codes in sorted(merged.items())}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(
        f"Wrote {sum(sum(c.values()) for c in merged.values())} baselined violation(s) in {len(merged)} file(s) to {path}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "files",
        nargs="*",
        metavar="FILE",
        help="Specific file or directory paths to scan (default: scan _DEFAULT_ROOTS)",
    )
    parser.add_argument(
        "--path",
        metavar="DIR",
        action="append",
        dest="paths",
        help="Directory to scan (repeatable; default: benchbox/, tests/, scripts/, _project/)",
    )
    parser.add_argument("--baseline", metavar="FILE", default=_DEFAULT_BASELINE, help="Ratchet baseline file")
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="Rewrite the baseline for the scanned paths from current counts; refuses to raise any count",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent

    if args.files:
        passed = [Path(p) if Path(p).is_absolute() else (repo_root / p) for p in args.files]
        missing = [p for p in passed if not p.exists()]
        if missing:
            print(f"error: paths not found: {', '.join(str(m) for m in missing)}", file=sys.stderr)
            return 2
        py_files: list[Path] = []
        for p in passed:
            if p.is_file():
                if p.suffix == ".py" and not _is_excluded(p, repo_root):
                    py_files.append(p)
            elif p.is_dir():
                for f in sorted(p.rglob("*.py")):
                    if not _is_excluded(f, repo_root):
                        py_files.append(f)
        violations: list[Violation] = []
        for f in py_files:
            violations.extend(check_file(f))
        file_count = len(py_files)
        baseline_path = repo_root / args.baseline

        if args.write_baseline:
            return write_baseline(baseline_path, violations, repo_root, py_files)

        result = apply_baseline(violations, load_baseline(baseline_path), repo_root, py_files, explicit_files=py_files)
    else:
        roots = [repo_root / p for p in (args.paths or _DEFAULT_ROOTS)]
        missing = [r for r in roots if not r.exists()]
        if missing:
            print(f"error: paths not found: {', '.join(str(m) for m in missing)}", file=sys.stderr)
            return 2

        violations, file_count = check_tree(roots, repo_root)
        baseline_path = repo_root / args.baseline

        if args.write_baseline:
            return write_baseline(baseline_path, violations, repo_root, roots)

        result = apply_baseline(violations, load_baseline(baseline_path), repo_root, roots)

    if not result.clean:
        if result.regressions:
            print(f"Found {len(result.regressions)} Windows anti-pattern violation(s) above the baseline:\n")
            for v in result.regressions:
                print(f"  {v}")
            print()
        if result.stale:
            print(
                f"Baseline entries above the current count ({len(result.stale)}); lower them with --write-baseline:\n"
            )
            for line in result.stale:
                print(f"  {line}")
            print()
        print("See scripts/check_windows_antipatterns.py for remediation guidance.")
        return 1

    baselined = len(violations)
    print(f"No new Windows anti-pattern violations found ({file_count} files checked, {baselined} baselined).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
