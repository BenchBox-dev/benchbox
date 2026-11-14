from __future__ import annotations

import sys
from collections.abc import Mapping

from rich.markup import escape

from benchbox.cli.exit_codes import ExitCode
from benchbox.cli.shared import console
from benchbox.core.billable_platforms import (
    acknowledgement_record_for,
    assess_platform,
    refusal_message,
    resolve_acknowledgement,
)
from benchbox.utils.session import is_non_interactive_session

ACKNOWLEDGEMENT_FLAG_HELP = (
    "Acknowledge that running a billable platform (registry category 'cloud' or a managed deployment) may "
    "incur cost. A non-interactive run of such a platform is refused with exit 6, before any adapter, credential "
    "or connection is used, unless this flag or BENCHBOX_ACKNOWLEDGE_BILLABLE_PLATFORM=1 is set; the "
    "acknowledgement and its source are recorded in the result bundle. Limits: any caller can set the flag or "
    "variable itself, so it is a statement of intent, not an access control; platform credentials saved in "
    "~/.benchbox/credentials.yaml are stored in plain text and readable by any process the user runs; "
    "--dry-run is exempt: it still reads that file but installs no package and makes no Databricks network call. "
    "Also not guarded: setup --validate-only and --diagnose (their purpose is to connect, and a Snowflake check "
    "can resume a warehouse), remote start_benchmark (gated only by the operator allow-list) and shell with an "
    "md: MotherDuck path."
)


def enforce_billable_acknowledgement(
    platform: str,
    *,
    deployment: str | None = None,
    options: Mapping[str, object] | None = None,
    flag: bool = False,
    non_interactive: bool = False,
) -> dict[str, object] | None:
    assessment = assess_platform(platform, deployment, options)
    acknowledgement = resolve_acknowledgement(flag)
    if assessment.billable and acknowledgement is None and is_non_interactive_session(non_interactive):
        console.print(f"[red]❌ {escape(refusal_message(assessment))}[/red]")
        sys.exit(ExitCode.ACKNOWLEDGEMENT_REQUIRED)
    return acknowledgement_record_for(assessment, acknowledgement)
