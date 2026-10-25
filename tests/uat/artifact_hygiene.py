from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from benchbox.core.runtime_paths import DEFAULT_BENCHMARK_RUNS_ROOT
from benchbox.utils.path_utils import default_benchmark_runs_root, find_work_tree_root

LOCAL_RUNS_DIRNAME = str(DEFAULT_BENCHMARK_RUNS_ROOT)


class LocalArtifactGrowthError(AssertionError):
    pass


def dir_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file() and not child.is_symlink():
                total += child.stat().st_size
        except OSError:
            continue
    return total


def _guard_base(cwd: Path | str | None = None) -> Path:
    base = Path(cwd).resolve() if cwd is not None else Path.cwd().resolve()
    return find_work_tree_root(base) or base


def local_runs_root(cwd: Path | str | None = None) -> Path:
    return _guard_base(cwd) / LOCAL_RUNS_DIRNAME


def _normalize(path: str | Path) -> Path:
    return Path(path).expanduser()


def _default_runs_root(base: Path) -> Path | None:
    if find_work_tree_root(base) is None:
        return None
    return default_benchmark_runs_root(base)


def configured_external_root(
    env: Mapping[str, str] | None = None,
    *,
    cwd: Path | str | None = None,
    output: str | Path | None = None,
) -> Path | None:
    env_map = os.environ if env is None else env
    base = _guard_base(cwd)

    candidate: Path | None = None
    if output is not None and str(output).strip():
        candidate = _normalize(output)
    else:
        raw = env_map.get("BENCHBOX_OUTPUT_DIR")
        if raw is not None and raw.strip():
            candidate = _normalize(raw.strip())
        else:
            candidate = _default_runs_root(base)

    if candidate is None:
        return None

    resolved = candidate if candidate.is_absolute() else (base / candidate)
    resolved = resolved.resolve()
    if resolved == base or resolved.is_relative_to(base):
        return None
    return resolved


@dataclass(frozen=True)
class LocalRunsSnapshot:
    root: Path
    total_bytes: int
    file_sizes: dict[str, int]
    file_fingerprints: dict[str, tuple[int, int]]


def snapshot_local_runs(cwd: Path | str | None = None) -> LocalRunsSnapshot:
    root = local_runs_root(cwd)
    file_sizes: dict[str, int] = {}
    file_fingerprints: dict[str, tuple[int, int]] = {}
    if root.exists():
        for child in root.rglob("*"):
            try:
                if child.is_file() and not child.is_symlink():
                    stat = child.stat()
                    file_sizes[str(child)] = stat.st_size
                    file_fingerprints[str(child)] = (stat.st_mtime_ns, stat.st_ino)
            except OSError:
                continue
    return LocalRunsSnapshot(
        root=root,
        total_bytes=sum(file_sizes.values()),
        file_sizes=file_sizes,
        file_fingerprints=file_fingerprints,
    )


def _changed_entries(before: LocalRunsSnapshot, after: LocalRunsSnapshot) -> dict[str, int]:
    changed: dict[str, int] = {}
    for path, size in after.file_sizes.items():
        before_size = before.file_sizes.get(path)
        if before_size is None:
            changed[path] = size
        elif size != before_size:
            changed[path] = size - before_size
        elif after.file_fingerprints.get(path) != before.file_fingerprints.get(path):
            changed[path] = 0
    return changed


def _format_violation(
    *,
    local_root: Path,
    external_root: Path,
    grew_by: int,
    grown: Mapping[str, int],
) -> str:
    lines = [
        "Local benchmark_runs/ was written during an external-root run (output-root propagation regression — see PR #780).",
        f"  unexpected local path: {local_root}",
        f"  configured external root: {external_root}",
        f"  change: {grew_by} bytes across {len(grown)} file(s) (includes same-size rewrites)",
    ]
    top = sorted(grown.items(), key=lambda kv: kv[1], reverse=True)[:5]
    for path, delta in top:
        lines.append(f"    + {delta} bytes: {path}")
    lines.append("Guardrail is report-only; no artifacts were moved or deleted.")
    return "\n".join(lines)


def assert_no_local_growth(
    before: LocalRunsSnapshot,
    external_root: Path,
    *,
    cwd: Path | str | None = None,
) -> None:
    after = snapshot_local_runs(cwd if cwd is not None else before.root.parent)
    changed = _changed_entries(before, after)
    grew_by = after.total_bytes - before.total_bytes
    if changed or grew_by > 0:
        raise LocalArtifactGrowthError(
            _format_violation(
                local_root=before.root,
                external_root=external_root,
                grew_by=max(grew_by, sum(changed.values())),
                grown=changed,
            )
        )


def audit_local_datagen(
    *,
    cwd: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    output: str | Path | None = None,
    threshold_bytes: int = 0,
) -> str | None:
    base = Path(cwd) if cwd is not None else Path.cwd()
    external_root = configured_external_root(env, cwd=base, output=output)
    if external_root is None:
        return None

    datagen = local_runs_root(base) / "datagen"
    size = dir_size_bytes(datagen)
    if size <= threshold_bytes:
        return None

    lines = [
        "Local benchmark_runs/datagen holds artifacts while an external root is configured.",
        f"  unexpected local path: {datagen}",
        f"  configured external root: {external_root}",
        f"  size: {size} bytes (threshold {threshold_bytes} bytes)",
        "Datagen for external-root runs must land under the external root.",
        "Guardrail is report-only; inspect and relocate manually if intended.",
    ]
    return "\n".join(lines)


def _build_arg_parser():
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m tests.uat.artifact_hygiene",
        description=(
            "Audit the worktree-local benchmark_runs/datagen for the external-root "
            "artifact-leak incident shape. No-op unless an external root is configured."
        ),
    )
    parser.add_argument("--cwd", default=None, help="Working directory to audit (default: cwd).")
    parser.add_argument("--output", default=None, help="Explicit --output root to treat as external.")
    parser.add_argument(
        "--threshold-bytes",
        type=int,
        default=0,
        help="Local datagen byte budget before the gate fails (default: 0).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    message = audit_local_datagen(
        cwd=args.cwd,
        output=args.output,
        threshold_bytes=args.threshold_bytes,
    )
    if message is None:
        root = configured_external_root(cwd=args.cwd, output=args.output)
        if root is None:
            print("artifact-hygiene: no external root configured; skipping local datagen audit.")
        else:
            print(f"artifact-hygiene: OK — no local datagen growth (external root {root}).")
        return 0
    print(message)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
