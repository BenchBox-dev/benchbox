#!/usr/bin/env python3

from __future__ import annotations

import argparse
import ast
import csv
import sys
from pathlib import Path

CLI_DESCRIPTION = (
    "Audit skip/xfail markers across the tests/ tree.\n"
    "\n"
    "Walks every .py file under tests/, extracts every ``@pytest.mark.skip``,\n"
    "``@pytest.mark.skipif``, ``@pytest.mark.xfail`` decorator and every inline\n"
    "``pytest.skip(...)`` / ``pytest.xfail(...)`` call, and emits a CSV with:\n"
    "\n"
    "    file, line, marker_type, condition, reason\n"
    "\n"
    "``reason`` is the text of the ``reason=`` keyword if present, otherwise\n"
    "``<none>``. Use ``--missing-only`` to restrict to markers lacking a reason.\n"
)

MARKERS = {"skip", "skipif", "xfail"}


def _literal(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        return "<unparseable>"


def _extract_reason(call: ast.Call) -> str:
    for kw in call.keywords:
        if kw.arg == "reason":
            return _literal(kw.value)
    return "<none>"


def _extract_condition(call: ast.Call, marker: str) -> str:
    if marker == "skipif" and call.args:
        return _literal(call.args[0])
    if marker in {"skip", "xfail"} and call.args and not any(kw.arg == "reason" for kw in call.keywords):
        return _literal(call.args[0])
    return ""


def _marker_name(func: ast.AST) -> str | None:
    cur = func
    while isinstance(cur, ast.Attribute):
        if cur.attr in MARKERS:
            return cur.attr
        cur = cur.value
    return None


def audit_file(path: Path) -> list[tuple[str, int, str, str, str]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    rows: list[tuple[str, int, str, str, str]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if (
                isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "mark"
                and node.func.attr in MARKERS
            ):
                marker = node.func.attr
                reason = _extract_reason(node)
                condition = _extract_condition(node, marker)
                rows.append((str(path), node.lineno, f"mark.{marker}", condition, reason))
            elif (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id == "pytest"
                and node.func.attr in {"skip", "xfail"}
            ):
                marker = node.func.attr
                reason = _extract_reason(node)
                first_arg = _literal(node.args[0]) if node.args else ""
                if reason == "<none>" and first_arg:
                    reason = first_arg
                    first_arg = ""
                rows.append((str(path), node.lineno, f"pytest.{marker}", first_arg, reason))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--root", default="tests", help="Root directory to walk")
    parser.add_argument("--output", default="-", help="CSV output path (- for stdout)")
    parser.add_argument(
        "--missing-only",
        action="store_true",
        help="Only emit rows where reason is missing",
    )
    args = parser.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2

    all_rows: list[tuple[str, int, str, str, str]] = []
    for py in sorted(root.rglob("*.py")):
        all_rows.extend(audit_file(py))

    if args.missing_only:
        all_rows = [r for r in all_rows if r[4] == "<none>"]

    out = sys.stdout if args.output == "-" else open(args.output, "w", encoding="utf-8", newline="")
    try:
        writer = csv.writer(out)
        writer.writerow(["file", "line", "marker_type", "condition", "reason"])
        writer.writerows(all_rows)
    finally:
        if out is not sys.stdout:
            out.close()

    print(
        f"audit: {len(all_rows)} markers ({'missing reason only' if args.missing_only else 'all'})",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
