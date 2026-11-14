from __future__ import annotations

import os
import sys

from rich.markup import escape

from benchbox.cli.exit_codes import ExitCode
from benchbox.cli.shared import console
from benchbox.utils.session import is_non_interactive_session

HOSTED_SUBMISSION_ACKNOWLEDGEMENT_ENV_VAR = "BENCHBOX_ACKNOWLEDGE_HOSTED_SUBMISSION"
MAINTAINER_LABEL_ENV_VAR = "BENCHBOX_ALLOW_MAINTAINER_LABEL"
MAINTAINER_LABEL = "maintainer-run"

_TRUTHY = frozenset({"1", "true", "yes", "on"})

HOSTED_SUBMISSION_FLAG_HELP = (
    "Acknowledge that this upload goes to a hosted service and becomes part of a shared corpus. A non-interactive "
    "--service submission is refused with exit 6 unless this flag or "
    f"{HOSTED_SUBMISSION_ACKNOWLEDGEMENT_ENV_VAR}=1 is set. --dry-run is exempt. Limits: any caller can set the "
    "flag or variable itself, so it is a statement of intent, not an access control."
)


def _env_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def enforce_hosted_submission_acknowledgement(*, flag: bool = False, dry_run: bool = False) -> None:
    if dry_run or flag or _env_enabled(HOSTED_SUBMISSION_ACKNOWLEDGEMENT_ENV_VAR):
        return
    if not is_non_interactive_session():
        return
    console.print(
        "[red]❌ Hosted submission refused: this session is non-interactive and no acknowledgement was given.[/red]\n"
        "   Uploading with --service shares the result with a hosted corpus without a human step.\n"
        f"   To proceed, add [bold]--acknowledge-hosted-submission[/bold] or set {HOSTED_SUBMISSION_ACKNOWLEDGEMENT_ENV_VAR}=1."
    )
    sys.exit(ExitCode.ACKNOWLEDGEMENT_REQUIRED)


def maintainer_label_allowed() -> bool:
    return _env_enabled(MAINTAINER_LABEL_ENV_VAR)


def maintainer_label_refusal(label: str) -> str | None:
    if label.lower() != MAINTAINER_LABEL or maintainer_label_allowed():
        return None
    return (
        f"The '{MAINTAINER_LABEL}' trust label is restricted to maintainers. Set {MAINTAINER_LABEL_ENV_VAR}=1 to "
        "use it, or choose another label such as 'community-submission'."
    )


def enforce_maintainer_label(label: str) -> None:
    refusal = maintainer_label_refusal(label)
    if refusal is None:
        return
    console.print(f"[red]❌ Publish refused:[/red] {escape(refusal)}")
    sys.exit(ExitCode.ACKNOWLEDGEMENT_REQUIRED)
