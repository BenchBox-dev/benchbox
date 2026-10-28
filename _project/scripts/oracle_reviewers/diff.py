from __future__ import annotations

import re

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,(?P<count>\d+))? @@")


def _target_path(line: str) -> str | None:
    target = line[4:].rstrip("\n")
    if target == "/dev/null":
        return None
    return target[2:] if target.startswith("b/") else target


def commentable_lines(diff_text: str) -> dict[str, frozenset[int]]:
    lines_by_path: dict[str, set[int]] = {}
    path: str | None = None
    new_line = 0
    remaining = 0
    for line in diff_text.split("\n"):
        if line.startswith("diff --git "):
            path = None
            remaining = 0
            continue
        if remaining == 0 and line.startswith("+++ "):
            path = _target_path(line)
            continue
        match = _HUNK.match(line)
        if match is not None:
            new_line = int(match.group("start"))
            remaining = int(match.group("count") or "1")
            continue
        if path is None or remaining <= 0:
            continue
        if line.startswith("+") or line.startswith(" "):
            lines_by_path.setdefault(path, set()).add(new_line)
            new_line += 1
            remaining -= 1
        elif line.startswith("\\"):
            continue
    return {name: frozenset(numbers) for name, numbers in lines_by_path.items()}


def select_files(diff_text: str, paths: frozenset[str]) -> str:
    sections = re.split(r"(?m)^(?=diff --git )", diff_text)
    kept = []
    for section in sections:
        header = section.split("\n", 1)[0]
        match = re.fullmatch(r"diff --git a/(?P<old>.+) b/(?P<new>.+)", header)
        if match is not None and (match.group("new") in paths or match.group("old") in paths):
            kept.append(section)
    return "".join(kept)


def binary_notice_count(diff_text: str) -> int:
    return len(re.findall(r"(?m)^(?:Binary files .+ differ|GIT binary patch)$", diff_text))


def binary_files(diff_text: str) -> frozenset[str]:
    names: set[str] = set()
    for section in re.split(r"(?m)^(?=diff --git )", diff_text):
        lines = section.split("\n")
        match = re.fullmatch(r"diff --git a/(?P<old>.+) b/(?P<new>.+)", lines[0])
        if match is None:
            continue
        old, new = match.group("old"), match.group("new")
        notices = {
            f"Binary files a/{old} and b/{new} differ",
            f"Binary files /dev/null and b/{new} differ",
            f"Binary files a/{old} and /dev/null differ",
        }
        for line in lines[1:]:
            if line.startswith("@@"):
                break
            if line == "GIT binary patch" or line in notices:
                names.add(new)
                break
    return frozenset(names)
