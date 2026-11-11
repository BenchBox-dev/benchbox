from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


REPO_ROOT = Path(__file__).resolve().parents[3]
BENCHBOX_ROOT = REPO_ROOT / "benchbox"

_PRINT_ALLOWLIST: frozenset[str] = frozenset(
    {
        "benchbox/utils/printing.py",
    }
)

_PENDING_MIGRATION: dict[str, int] = {
    "benchbox/core/equivalence/cross_surface.py": 18,
    "benchbox/core/tpchavoc/dataframe_equivalence.py": 8,
    "benchbox/core/tpchavoc/equivalence.py": 12,
}

_RAW_PRINT = re.compile(r"(?<![\w.])print\(")

_CONSOLE_SINGLETON = re.compile(r"\bconsole\s*=\s*Console\(")


def _benchbox_runtime_files() -> list[Path]:
    return [p for p in BENCHBOX_ROOT.rglob("*.py") if "test" not in p.name]


def _rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def test_no_new_raw_print_in_benchbox_runtime() -> None:
    offenders: list[str] = []
    for path in _benchbox_runtime_files():
        rel = _rel(path)
        if rel in _PRINT_ALLOWLIST:
            continue
        actual_count = len(_RAW_PRINT.findall(path.read_text(encoding="utf-8")))
        allowed_count = _PENDING_MIGRATION.get(rel, 0)
        if actual_count > allowed_count:
            offenders.append(f"{rel}  (found {actual_count}, pinned to {allowed_count})")
    assert not offenders, (
        "Raw print() count increased beyond what _PENDING_MIGRATION pins"
        " (use emit() instead, or raise the pinned count only if the addition is pre-existing):\n"
        + "\n".join(f"  {f}" for f in sorted(offenders))
    )


def test_new_print_in_an_exempted_file_still_fails(tmp_path, monkeypatch) -> None:
    fake_root = tmp_path / "benchbox"
    fake_file = fake_root / "core" / "equivalence" / "cross_surface.py"
    fake_file.parent.mkdir(parents=True)
    pinned_count = _PENDING_MIGRATION["benchbox/core/equivalence/cross_surface.py"]
    fake_file.write_text("\n".join("print('x')" for _ in range(pinned_count + 1)), encoding="utf-8")

    monkeypatch.setattr(sys.modules[__name__], "BENCHBOX_ROOT", fake_root)
    monkeypatch.setattr(sys.modules[__name__], "REPO_ROOT", tmp_path)

    offenders: list[str] = []
    for path in _benchbox_runtime_files():
        rel = _rel(path)
        if rel in _PRINT_ALLOWLIST:
            continue
        actual_count = len(_RAW_PRINT.findall(path.read_text(encoding="utf-8")))
        allowed_count = _PENDING_MIGRATION.get(rel, 0)
        if actual_count > allowed_count:
            offenders.append(rel)
    assert offenders == ["benchbox/core/equivalence/cross_surface.py"]


def test_pending_migration_set_contains_no_stale_entries() -> None:
    stale: list[str] = []
    for rel, allowed_count in sorted(_PENDING_MIGRATION.items()):
        path = REPO_ROOT / rel
        if not path.exists():
            stale.append(f"{rel}  (file no longer exists)")
            continue
        actual_count = len(_RAW_PRINT.findall(path.read_text(encoding="utf-8")))
        if actual_count == 0:
            stale.append(f"{rel}  (no longer uses raw print - remove from _PENDING_MIGRATION)")
        elif actual_count < allowed_count:
            stale.append(f"{rel}  (pinned to {allowed_count} but only {actual_count} remain - lower the pinned count)")
    assert not stale, "Stale entries in _PENDING_MIGRATION:\n" + "\n".join(f"  {s}" for s in stale)


def test_cli_commands_do_not_use_direct_console_singletons() -> None:
    offenders: list[str] = []
    cli_commands_root = BENCHBOX_ROOT / "cli" / "commands"
    for path in cli_commands_root.rglob("*.py"):
        if "test" in path.name:
            continue
        if _CONSOLE_SINGLETON.search(path.read_text(encoding="utf-8")):
            offenders.append(_rel(path))
    assert not offenders, (
        "Direct Console() singleton detected in CLI commands"
        " (import console from benchbox.cli.shared instead):\n" + "\n".join(f"  {f}" for f in sorted(offenders))
    )


