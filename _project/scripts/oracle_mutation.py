from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
STAGE = Path(os.environ.get("ORACLE_MUTATION_STAGE") or ROOT / "build" / "oracle-mutation")
ALWAYS_COPIED = ("tests", "pytest.ini", "pyproject.toml", "_benchbox_pytest_xdist_safety.py")
COUNTED = ("killed", "timeout", "survived", "no tests", "not checked")


def load_settings(root: Path) -> dict[str, Any]:
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return data["tool"]["oracle-mutation"]


def stage(root: Path, target: Path, settings: dict[str, Any]) -> None:
    target.mkdir(parents=True, exist_ok=True)
    paths = (*settings["paths_to_mutate"], *settings["also_copy"], *ALWAYS_COPIED)
    for name in sorted({Path(path).parts[0] for path in paths}):
        link = target / name
        if link.is_symlink() and link.readlink() != root / name:
            link.unlink()
        if not link.is_symlink():
            link.symlink_to(root / name)


def engine_config(settings: dict[str, Any]) -> Any:
    from mutmut.__main__ import Config

    return Config(
        also_copy=[Path(path) for path in (*settings["also_copy"], *ALWAYS_COPIED)],
        do_not_mutate=[],
        max_stack_depth=-1,
        debug=False,
        paths_to_mutate=[Path(path) for path in settings["paths_to_mutate"]],
        pytest_add_cli_args=list(settings["pytest_add_cli_args"]),
        pytest_add_cli_args_test_selection=[],
        tests_dir=list(settings["tests_dir"]),
        mutate_only_covered_lines=False,
        type_check_command=[],
    )


def tally(target: Path, paths: Sequence[str]) -> list[tuple[str, Counter[str]]]:
    from mutmut.__main__ import status_by_exit_code

    rows = []
    for path in paths:
        meta = target / "mutants" / f"{path}.meta"
        codes = json.loads(meta.read_text(encoding="utf-8"))["exit_code_by_key"] if meta.is_file() else {}
        rows.append((path, Counter(status_by_exit_code[code] for code in codes.values())))
    return rows


def kill_rate(counts: Counter[str]) -> str:
    checked = sum(counts.values()) - counts["not checked"]
    return f"{100 * (counts['killed'] + counts['timeout']) / checked:.1f}%" if checked else "n/a"


def report(target: Path, paths: Sequence[str]) -> str:
    header = ("module", "mutants", *COUNTED, "other", "kill rate")
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    total: Counter[str] = Counter()
    for path, counts in [*tally(target, paths), ("total", total)]:
        if path != "total":
            total.update(counts)
        other = sum(counts.values()) - sum(counts[status] for status in COUNTED)
        cells = [path, sum(counts.values()), *(counts[status] for status in COUNTED), other, kill_rate(counts)]
        lines.append("| " + " | ".join(str(cell) for cell in cells) + " |")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mutation-test the oracle decision modules against the oracle tests.")
    parser.add_argument("command", choices=("run", "results", "show", "report"))
    parser.add_argument("names", nargs="*")
    args = parser.parse_args(argv)
    settings = load_settings(ROOT)
    stage(ROOT, STAGE, settings)
    os.chdir(STAGE)
    if args.command == "report":
        print(report(STAGE, settings["paths_to_mutate"]))
        return 0
    import mutmut
    from mutmut import __main__ as engine

    engine.setproctitle = lambda title: None
    mutmut.config = engine_config(settings)
    return engine.cli.main(args=[args.command, *args.names], prog_name="mutmut", standalone_mode=False) or 0


if __name__ == "__main__":
    sys.exit(main())
