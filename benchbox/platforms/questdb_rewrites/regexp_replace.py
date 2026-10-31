from __future__ import annotations

import re

from benchbox.platforms.questdb_rewrites import exp

ORDER = 300

_BACKREFERENCE = re.compile(r"\\(\d)")


def _is_global_flag(modifiers: exp.Expr | None) -> bool:
    return isinstance(modifiers, exp.Literal) and modifiers.is_string and modifiers.this == "g"


def _rewrite(node: exp.Expr) -> exp.Expr:
    if not isinstance(node, exp.RegexpReplace) or not _is_global_flag(node.args.get("modifiers")):
        return node
    replacement = node.args.get("replacement")
    if isinstance(replacement, exp.Literal) and replacement.is_string:
        node.set("replacement", exp.Literal.string(_BACKREFERENCE.sub(r"$\1", replacement.this)))
    node.set("modifiers", None)
    node.set("single_replace", True)
    return node


def transform(expression: exp.Expr) -> exp.Expr:
    return expression.transform(_rewrite)


def applies(sql: str) -> bool:
    return "regexp_replace" in sql.lower()
