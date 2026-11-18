#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from collections.abc import Sequence

DEVELOP = "develop"
LINE = re.compile(r"release/[0-9]+\.[0-9]+\.x")


def validate(base: str | None) -> str:
    if not base:
        return DEVELOP
    if base == DEVELOP or LINE.fullmatch(base):
        return base
    raise ValueError(f"base {base!r} is not {DEVELOP!r} or a release/X.Y.x maintenance line")


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: pr_base.py <base branch>", file=sys.stderr)
        return 2
    try:
        print(validate(args[0]))
    except ValueError as exc:
        print(f"pr-base: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
