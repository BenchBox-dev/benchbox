from __future__ import annotations

import fnmatch
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .policy import ClassifierRules


@dataclass(frozen=True)
class ChangedFile:
    path: str
    previous_path: str | None
    additions: int
    deletions: int
    sha: str = ""
    status: str = ""

    @property
    def removed(self) -> bool:
        return self.status == "removed"

    @property
    def lines(self) -> int:
        return self.additions + self.deletions

    @property
    def paths(self) -> tuple[str, ...]:
        return (self.path,) if not self.previous_path else (self.path, self.previous_path)

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> ChangedFile:
        return cls(
            path=str(data["filename"]),
            previous_path=data.get("previous_filename") or None,
            additions=int(data.get("additions") or 0),
            deletions=int(data.get("deletions") or 0),
            sha=str(data.get("sha") or ""),
            status=str(data.get("status") or ""),
        )


@dataclass(frozen=True)
class SoundnessScope:
    soundness: bool
    reasons: tuple[str, ...]
    paths: tuple[str, ...]
    gate_paths: tuple[str, ...]
    logic_paths: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "soundness": self.soundness,
            "reasons": list(self.reasons),
            "paths": list(self.paths),
            "gate_paths": list(self.gate_paths),
            "logic_paths": list(self.logic_paths),
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> SoundnessScope:
        return cls(
            soundness=bool(data["soundness"]),
            reasons=tuple(data.get("reasons", [])),
            paths=tuple(data.get("paths", [])),
            gate_paths=tuple(data.get("gate_paths", [])),
            logic_paths=tuple(data.get("logic_paths", [])),
        )


def _matches(path: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def _in_scope(path: str, rules: ClassifierRules, is_soundness_path: Callable[[str], bool]) -> bool:
    if is_soundness_path(path) or _matches(path, rules.gate_paths):
        return True
    return not _matches(path, rules.exempt_paths)


def soundness_scope(
    files: list[ChangedFile],
    rules: ClassifierRules,
    is_soundness_path: Callable[[str], bool],
) -> SoundnessScope:
    in_scope = [item for item in files if any(_in_scope(path, rules, is_soundness_path) for path in item.paths)]
    if not in_scope:
        return SoundnessScope(False, ("only exempt paths changed",), (), (), ())
    gate = sorted({path for item in in_scope for path in item.paths if _matches(path, rules.gate_paths)})
    code = [item for item in in_scope if not all(_matches(path, rules.data_paths) for path in item.paths)]
    logic = sorted({path for item in code for path in item.paths if _matches(path, rules.logic_paths)})
    reasons = []
    if gate:
        reasons.append("changes the gate: " + ", ".join(gate))
    if logic:
        reasons.append("changes comparison, validation or result logic: " + ", ".join(logic))
    if not reasons:
        reasons.append(f"changes {len(in_scope)} files outside the exempt paths")
        soundness = sorted({path for item in in_scope for path in item.paths if is_soundness_path(path)})
        if soundness:
            reasons.append("touches soundness paths: " + ", ".join(soundness))
    paths = tuple(sorted({path for item in in_scope for path in item.paths}))
    return SoundnessScope(True, tuple(reasons), paths, tuple(gate), tuple(logic))


def matching(paths: Iterable[str], patterns: Iterable[str]) -> list[str]:
    return sorted({path for path in paths if _matches(path, patterns)})
