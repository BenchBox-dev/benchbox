from __future__ import annotations

import sys
from typing import NoReturn

import click

from benchbox.cli.exit_codes import ExitCode
from benchbox.core.run_service import BenchmarkUnavailableError, refuse_unavailable_benchmark


def exit_unavailable_benchmark(exc: BenchmarkUnavailableError) -> NoReturn:
    click.echo(str(exc), err=True)
    sys.exit(ExitCode.USAGE)


def exit_if_benchmark_unavailable(benchmark: str | None) -> None:
    try:
        refuse_unavailable_benchmark(benchmark)
    except BenchmarkUnavailableError as exc:
        exit_unavailable_benchmark(exc)
