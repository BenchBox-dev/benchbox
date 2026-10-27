#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import sys

RUNNER_INAPPLICABLE_GUARDS: dict[str, str] = {
    "agent-identity": (
        "agent-identity-check resolves the runner's own `git config user.*`, "
        "which an ephemeral runner does not have. Since #1558, an absent "
        "identity makes the check pass (audit_git_identity returns [] rather "
        "than fail); supplying a synthetic identity instead only swaps one "
        "always-passing input for another synthetic one that can never match "
        "a known agent identity either. Either way the check cannot fail on a "
        "runner, so it verifies nothing there -- see ci.yml's `code-lint` job, "
        "which already has no counterpart step for the same reason. "
        "agent-commit-range-check is the real merge-time control (it reads "
        "the commits the branch actually carries, not resolved config) and is "
        "never in this table."
    ),
    "skill-sync-check": (
        "skill-sync-check runs `tools/skill-sync check`, which compares the "
        "mirrors against the developer-local source checkout layout named in "
        "skill-sync.conf (`~/Developer/...`) that does not exist on a runner; "
        "left ungated it fails there for a reason that has nothing to do "
        "with the code under test. The real CI-side coverage is ci.yml's required "
        "`skill-integrity` job: for changed skill surfaces it validates "
        "config/receipt/tool-pin policy, clones the skill sources, and runs "
        "the full preview/apply/verify/check cycle before "
        "the tooling unit can pass. See "
        "https://benchbox.dev/docs/operations/ci-local-parity.html's 'Required skill-integrity "
        "lane' section for why that check has no local/ci-lint equivalent "
        "of its own."
    ),
}


CLI_DESCRIPTION = (
    "Decide whether a `make ci-lint` guard is meaningful on a CI runner.\n"
    "\n"
    "`ci-lint` (Makefile) exists to mirror the `ci.yml` `code-lint` job locally\n"
    "(https://benchbox.dev/docs/operations/ci-local-parity.html), but `make ci-lint` can also run\n"
    "directly on a real, ephemeral GitHub-hosted runner (no workflow does today; the\n"
    "post-merge workflow that did was retired with the six-unit CI). Most\n"
    "guards are equally meaningful there: they inspect the checked-out tree, the\n"
    "installed venv, or the registries the repo ships, none of which differ\n"
    "between a laptop and a runner.\n"
    "\n"
    "A small number of guards instead read state that only exists on a developer\n"
    "machine -- a resolved Git identity, a tool installed at a hardcoded local\n"
    "path -- and behave one of two bad ways on a runner that lacks it: they fail\n"
    "for a reason that has nothing to do with the code under test (the pre-#1558\n"
    "`agent-identity-check` behavior), or worse, they silently no-op and report\n"
    "success while checking nothing (`skill-sync-check` against a `$(SKILL_SYNC)`\n"
    "path that plainly does not exist on the runner). The second failure mode is\n"
    "the more dangerous one: a guard that cannot fail reads as coverage in the\n"
    "Actions log and is never investigated.\n"
    "\n"
    "This module is the single place that draws that boundary. Before the\n"
    "Makefile recipe runs a runner-inapplicable guard, it asks this module; if the\n"
    "guard is listed AND the process is actually running on a GitHub Actions\n"
    "runner (`GITHUB_ACTIONS=true`, the platform-set variable -- never\n"
    "hand-toggled), the guard is skipped with a printed reason instead of run and\n"
    "either silently passing or noisily failing. Every other guard -- the\n"
    "overwhelming majority -- is untouched: this is a narrow allowlist of\n"
    "documented exceptions, not a generic on/off switch, and adding a guard here\n"
    "requires the same reasoning as removing coverage, because that is exactly\n"
    "what it does on a runner.\n"
    "\n"
    "Local and CI-local-parity invocations (`GITHUB_ACTIONS` unset) are never\n"
    "affected by this table -- every guard always runs there, including the two\n"
    "listed below, exactly as before this module existed.\n"
)


def runs_on_runner(guard: str, *, github_actions: bool) -> tuple[bool, str | None]:
    if not github_actions:
        return True, None
    reason = RUNNER_INAPPLICABLE_GUARDS.get(guard)
    if reason is None:
        return True, None
    return False, reason


DECISION_RUN = "RUN"
DECISION_SKIP = "SKIP"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("guard", help="ci-lint guard slug, e.g. 'agent-identity' or 'skill-sync-check'")
    args = parser.parse_args(argv)

    github_actions = os.environ.get("GITHUB_ACTIONS", "") == "true"
    should_run, reason = runs_on_runner(args.guard, github_actions=github_actions)
    if should_run:
        print(DECISION_RUN)
        return 0
    print(DECISION_SKIP)
    print(
        f"ci-lint: skipping {args.guard} - environment-inapplicable on a CI runner -- {reason}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
