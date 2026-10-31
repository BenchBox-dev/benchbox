#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from benchbox.utils import host_lock
from tests.uat import docker_assets, matrix

DOCUMENT_ONLY_PLATFORMS = frozenset()


def automated_platforms() -> tuple[str, ...]:
    return tuple(
        sorted(
            platform
            for platform, spec in docker_assets.docker_platform_specs().items()
            if spec.managed_start_allowed and not spec.fixed_container_names
        )
    )


def known_platforms() -> tuple[str, ...]:
    return tuple(sorted(set(automated_platforms()) | DOCUMENT_ONLY_PLATFORMS))


def _benchmark_runs_dir(value: str | None) -> Path:
    if value:
        return Path(value).expanduser()
    default = Path.home() / "Developer" / "benchmark_runs"
    return Path(os.environ.get("BENCHBOX_OUTPUT_DIR", str(default))).expanduser()


def _declared_need_gib(spec: docker_assets.DockerPlatformSpec, env: dict[str, str]) -> float | None:
    limits = docker_assets.compose_declared_memory_limits(spec, env=env)
    total = sum(docker_assets.parse_memory_bytes(limit) for limit in limits.values())
    return total / 1024**3 if total else None


def _admit_bring_up(platform: str, spec: docker_assets.DockerPlatformSpec, env: dict[str, str]) -> int:
    try:
        report = host_lock.check_admission(need_gib=_declared_need_gib(spec, env))
    except docker_assets.DockerAssetError as exc:
        print(f"UAT bring-up failed for {platform}: {exc}", file=sys.stderr)
        return 2
    except host_lock.AdmissionRefused as exc:
        print(f"UAT bring-up refused for {platform}: host is not ready for heavy work\n{exc.report}", file=sys.stderr)
        return host_lock.EXIT_ADMISSION
    print(report, file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="uat-bring-up")
    parser.add_argument("--platform", required=True, help="Local UAT platform to start")
    parser.add_argument("--timeout-s", type=int, default=300, help="docker compose --wait timeout")
    parser.add_argument("--project-name", default=None, help="Optional docker compose project name override")
    parser.add_argument(
        "--benchmark-runs-dir",
        default=None,
        help=(
            "Benchmark run root to mount into platforms with host-path contracts "
            "(default: BENCHBOX_OUTPUT_DIR or ~/Developer/benchmark_runs)"
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print/run command construction without Docker execution"
    )
    parser.add_argument(
        "--prepull-only",
        action="store_true",
        help=(
            "Run `compose pull --ignore-buildable` + `compose build` instead of `compose "
            "up` -- no health probe. Fetches images/builds ahead of a sweep so first-run "
            "downloads don't eat into cleanup.docker_start_timeout_s (make uat-prepull)."
        ),
    )
    parser.add_argument(
        "--prepull-timeout-s",
        type=int,
        default=900,
        help="Timeout for each prepull command (pull, then build); image pulls can be large (default: 900)",
    )
    args = parser.parse_args(argv)

    platform = args.platform.strip()
    if platform in DOCUMENT_ONLY_PLATFORMS:
        print(
            f"platform {platform!r} is document-only for UAT bring-up; see "
            f"https://benchbox.dev/docs/operations/uat-local-provisioning.html",
            file=sys.stderr,
        )
        return 2
    if platform not in automated_platforms():
        print(
            f"unknown platform {platform!r}; supported automated platforms: {', '.join(automated_platforms())}",
            file=sys.stderr,
        )
        return 2

    spec = docker_assets.docker_platform_spec(platform)
    try:
        docker_assets.validate_managed_start_allowed(spec)
        print(f"resolved container CLI: {docker_assets.resolve_container_cli()}")
    except docker_assets.DockerAssetError as exc:
        print(f"platform {platform!r} cannot be UAT-managed: {exc}", file=sys.stderr)
        return 2

    project_name = args.project_name or docker_assets.compose_project_name("manual", platform)
    benchmark_runs_dir = _benchmark_runs_dir(args.benchmark_runs_dir)
    try:
        compose_env = docker_assets.compose_environment(spec, benchmark_runs_dir=benchmark_runs_dir)
    except docker_assets.DockerAssetError as exc:
        print(f"UAT bring-up failed for {platform}: {exc}", file=sys.stderr)
        return 2

    try:
        docker_assets.validate_project_name_budget(spec, project_name)
    except docker_assets.DockerAssetError as exc:
        print(f"platform {platform!r} project name {project_name!r} is invalid: {exc}", file=sys.stderr)
        return 2

    if args.prepull_only:
        prepull_steps = (
            ("pull", docker_assets.compose_pull_command(spec, project_name)),
            ("build", docker_assets.compose_build_command(spec, project_name)),
        )
        for label, argv_cmd in prepull_steps:
            result = docker_assets.run_docker_command(
                argv_cmd,
                dry_run=args.dry_run,
                timeout_s=args.prepull_timeout_s,
                cwd=docker_assets.REPO_ROOT,
                env=compose_env,
            )
            print(result.command)
            if not result.succeeded:
                details = result.error or result.stderr or result.stdout or f"exit {result.returncode}"
                print(f"UAT prepull ({label}) failed for {platform}: {details}", file=sys.stderr)
                return result.returncode or 1
        print(f"UAT prepull OK for {platform} (project={project_name})")
        return 0

    if not args.dry_run:
        admission = _admit_bring_up(platform, spec, compose_env)
        if admission:
            return admission

    argv_up = docker_assets.compose_up_command(spec, project_name, start_timeout_s=args.timeout_s)
    result = docker_assets.run_docker_command(
        argv_up,
        dry_run=args.dry_run,
        timeout_s=args.timeout_s,
        cwd=docker_assets.REPO_ROOT,
        env=compose_env,
    )
    print(result.command)
    if not result.succeeded:
        details = result.error or result.stderr or result.stdout or f"exit {result.returncode}"
        print(f"UAT bring-up failed for {platform}: {details}", file=sys.stderr)
        return result.returncode or 1

    matrix.invalidate_reachability_cache_after_lifecycle_change()
    if not args.dry_run and not matrix.platform_is_reachable(platform):
        endpoint = spec.tcp_probe_label or "configured endpoint"
        print(f"UAT bring-up failed for {platform}: {endpoint} is still unreachable", file=sys.stderr)
        return 1

    print(f"UAT bring-up OK for {platform} (project={project_name})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
