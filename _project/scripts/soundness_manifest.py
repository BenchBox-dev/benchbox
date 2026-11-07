from __future__ import annotations

from collections.abc import Iterable
from functools import cache
from pathlib import Path

try:
    from _project.scripts.soundness_paths import Matcher, load_rules
except ImportError:
    from soundness_paths import Matcher, load_rules

MANIFEST = Path(__file__).resolve().parents[2] / ".github" / "soundness-paths.txt"


@cache
def matcher(manifest: Path = MANIFEST) -> Matcher:
    return Matcher(load_rules(manifest))


def is_soundness_path(path: str, manifest: Path = MANIFEST) -> bool:
    return matcher(manifest).is_soundness_path(path)


def any_soundness_path(paths: Iterable[str], manifest: Path = MANIFEST) -> bool:
    return matcher(manifest).any_soundness_path(paths)
