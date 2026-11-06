#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import theseus_cli

DEVELOP_REF = theseus_cli.DEVELOP_REF
EXIT_UNAVAILABLE = theseus_cli.EXIT_UNAVAILABLE
RECORD_OUT_ENV = "REVIEW_LOCAL_RECORD_OUT"

Launcher = Callable[[list[str], dict[str, str]], int]


def trusted_ref(args: argparse.Namespace) -> str:
    return args.trusted_ref or DEVELOP_REF


def review_command(args: argparse.Namespace) -> list[str]:
    command = [
        "theseus",
        "local-review",
        "--policy",
        args.policy,
        "--base-ref",
        args.base_ref,
        "--trusted-ref",
        trusted_ref(args),
        "--head",
        args.head,
    ]
    if args.member:
        command.append("--member")
    if args.result_json:
        command += ["--result-json", args.result_json]
    if args.rebuttals:
        command += ["--rebuttals", args.rebuttals]
    if args.adjudicate:
        command += ["--adjudicate", args.adjudicate]
    return command


def launch(argv: list[str], env: dict[str, str]) -> int:
    return subprocess.run(argv, env=env, check=False).returncode


def run(args: argparse.Namespace, launcher: Launcher = launch) -> int:
    trusted = trusted_ref(args)
    try:
        program = theseus_cli.executable(Path.cwd(), trusted)
    except theseus_cli.TheseusError as exc:
        print(
            f"Refusing to open the PR: the verified Theseus install at {trusted} could not be prepared "
            f"({exc}); the local oracle review cannot run.",
            file=sys.stderr,
        )
        return EXIT_UNAVAILABLE
    env = theseus_cli.launcher_environment(program)
    if args.record_out:
        env[RECORD_OUT_ENV] = args.record_out
    return launcher(review_command(args), env)


def parser() -> argparse.ArgumentParser:
    built = argparse.ArgumentParser(description="Run the local oracle review from the verified Theseus install.")
    built.add_argument("--policy", default=theseus_cli.POLICY_PATH)
    built.add_argument("--base-ref", default=DEVELOP_REF)
    built.add_argument("--trusted-ref", default="")
    built.add_argument("--head", default="HEAD")
    built.add_argument("--rebuttals", default="")
    built.add_argument("--adjudicate", default="")
    built.add_argument("--record-out", default="")
    built.add_argument("--member", action="store_true")
    built.add_argument("--result-json", default="")
    return built


def main(argv: Sequence[str] | None = None) -> int:
    return run(parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
