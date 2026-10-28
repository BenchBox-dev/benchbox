from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from _project.scripts.oracle_reviewers import omp_bump_gate
from _project.scripts.oracle_reviewers.omp_bump_gate import BumpEvidenceError

Runner = Callable[[list[str]], tuple[int, str]]

FILE_LIST_CAP = 3000
BASE_REF = "develop"
COMMENT_JQ = (
    ".[] | {login: .user.login, user_type: .user.type, body: .body, created_at: .created_at, updated_at: .updated_at}"
)


def _show(run: Runner, ref: str) -> str:
    code, out = run(["git", "show", f"{ref}:{omp_bump_gate.POLICY_PATH}"])
    if code != 0:
        raise BumpEvidenceError(f"cannot read {omp_bump_gate.POLICY_PATH} at {ref}: {out.strip()[:200]}")
    return out


def _base_policy(run: Runner, repo: str) -> str:
    code, out = run(
        [
            "gh",
            "api",
            "-H",
            "Accept: application/vnd.github.raw",
            f"repos/{repo}/contents/{omp_bump_gate.POLICY_PATH}?ref={BASE_REF}",
        ]
    )
    if code != 0:
        raise BumpEvidenceError(f"cannot read {omp_bump_gate.POLICY_PATH} at {BASE_REF}: {out.strip()[:200]}")
    return out


def _changed_files(run: Runner, repo: str, number: int) -> list[str]:
    code, out = run(["gh", "api", "--paginate", "--jq", ".[].filename", f"repos/{repo}/pulls/{number}/files"])
    if code != 0:
        raise BumpEvidenceError(f"cannot list the files changed by PR #{number}: {out.strip()[:200]}")
    names = [line for line in out.splitlines() if line]
    if len(names) >= FILE_LIST_CAP:
        raise BumpEvidenceError(f"PR #{number} changes {len(names)} or more files, past the file list the API returns")
    return names


def _comments(run: Runner, repo: str, number: int) -> list[dict[str, Any]]:
    code, out = run(["gh", "api", "--paginate", "--jq", COMMENT_JQ, f"repos/{repo}/issues/{number}/comments"])
    if code != 0:
        raise BumpEvidenceError(f"cannot read the comments of PR #{number}: {out.strip()[:200]}")
    decoder = json.JSONDecoder()
    position, found = 0, []
    while position < len(out):
        if out[position].isspace():
            position += 1
            continue
        item, position = decoder.raw_decode(out, position)
        found.append(item)
    return found


def bump_refusals(run: Runner, repo: str, number: int, head: str) -> list[str]:
    try:
        files = _changed_files(run, repo, number)
        if omp_bump_gate.POLICY_PATH not in files:
            return []
        base_text, head_text = _base_policy(run, repo), _show(run, head)
        if omp_bump_gate.pin_section(base_text) == omp_bump_gate.pin_section(head_text):
            return []
        return omp_bump_gate.refusals(files, base_text, head_text, _comments(run, repo, number), head)
    except BumpEvidenceError as exc:
        return [f"omp pin bump evidence could not be verified: {exc}"]
