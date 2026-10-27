from __future__ import annotations

import os
from pathlib import Path

FULL_DIFF_PLACEHOLDER = "<<full-pull-request-diff-path>>"
FULL_DIFF_LINE = (
    f"The whole pull request diff is at {FULL_DIFF_PLACEHOLDER}; read it for context outside the changed files below.\n"
)
FILE_LIST_DIFF_LINE = (
    f"The whole pull request diff is at {FULL_DIFF_PLACEHOLDER}; read it for context, but it does not replace"
    " the direct reads required above. "
)


def with_full_diff(text: str, path: Path | None) -> str:
    if path is not None:
        return text.replace(FULL_DIFF_PLACEHOLDER, str(path), 1)
    first = text.find(FULL_DIFF_PLACEHOLDER)
    for line in (FULL_DIFF_LINE, FILE_LIST_DIFF_LINE):
        start = text.find(line)
        if start != -1 and start <= first < start + len(line):
            return text[:start] + text[start + len(line) :]
    return text


def write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
