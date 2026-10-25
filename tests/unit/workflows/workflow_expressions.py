from __future__ import annotations

import re
from typing import Any


def render(template: str, context: dict[str, Any]) -> str:
    return re.sub(r"\$\{\{(.*?)\}\}(?=[ )\"]|$)", lambda m: str(evaluate(m.group(1), context) or ""), template)


def evaluate(expression: str, context: dict[str, Any]) -> Any:
    expression = " ".join(expression.split())

    def value(expression: str) -> Any:
        expression = expression.strip()
        if expression.startswith("(") and _closing_paren(expression) == len(expression) - 1:
            return value(expression[1:-1])
        for operator in ("||", "&&"):
            parts = _split_top_level(expression, operator)
            if len(parts) > 1:
                result = value(parts[0])
                for part in parts[1:]:
                    if operator == "||":
                        result = result or value(part)
                    else:
                        result = value(part) if result else result
                return result
        for operator in ("==", "!="):
            parts = _split_top_level(expression, operator)
            if len(parts) == 2:
                return (value(parts[0]) == value(parts[1])) == (operator == "==")
        if expression.startswith("!"):
            return not value(expression[1:])
        if expression.startswith("'"):
            return expression[1:-1]
        for name, test in (("contains(", lambda a, b: b in a), ("startsWith(", lambda a, b: a.startswith(b))):
            if expression.startswith(name):
                left, right = _split_top_level(expression[len(name) : -1], ",")
                return test(str(value(left) or ""), str(value(right) or ""))
        if expression.startswith("format("):
            template, *arguments = _split_top_level(expression[len("format(") : -1], ",")
            return re.sub(r"\{(\d+)\}", lambda m: str(value(arguments[int(m.group(1))])), value(template))
        found: Any = context
        for key in expression.split("."):
            found = found.get(key) if isinstance(found, dict) else None
        return found

    return value(expression)


def _closing_paren(expression: str) -> int:
    depth = 0
    for index, char in enumerate(expression):
        depth += char == "("
        depth -= char == ")"
        if depth == 0:
            return index
    return -1


def _split_top_level(expression: str, separator: str) -> list[str]:
    parts, depth, quoted, start = [], 0, False, 0
    index = 0
    while index < len(expression):
        char = expression[index]
        if char == "'":
            quoted = not quoted
        elif not quoted and char == "(":
            depth += 1
        elif not quoted and char == ")":
            depth -= 1
        elif not quoted and depth == 0 and expression.startswith(separator, index):
            parts.append(expression[start:index])
            start = index + len(separator)
            index = start - 1
        index += 1
    parts.append(expression[start:])
    return parts
