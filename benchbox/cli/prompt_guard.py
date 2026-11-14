from __future__ import annotations

import sys
from collections.abc import Callable

import click

from benchbox.cli.exit_codes import ExitCode
from benchbox.cli.shared import console
from benchbox.core.billable_platforms import is_non_interactive_session


def confirm_or_refuse(action: str, *, flag: str, ask: Callable[[], bool]) -> bool:
    try:
        return ask()
    except click.Abort:
        if not is_non_interactive_session():
            raise
        _refuse(action, flag)
    except EOFError:
        _refuse(action, flag)


def _refuse(action: str, flag: str) -> None:
    console.print(
        f"\n[red]❌ Refused: {action} needs confirmation, and no answer could be read from the terminal.[/red]\n"
        f"   Pass {flag} to confirm it explicitly."
    )
    sys.exit(ExitCode.ACKNOWLEDGEMENT_REQUIRED)
