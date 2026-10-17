from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

BOOTSTRAP_BASE = "ed5c263c513ba65499f4918d3a7de607f280c65b"
TRUSTED_FILES = (
    "scripts/check_comment_policy.py",
    "scripts/check_comment_cleanup_scope.py",
    "scripts/comment_syntax.py",
    "scripts/comment_payloads.py",
    "scripts/comment_execution.py",
    "scripts/comment_policy_entry.py",
    "quality/comment-policy-requirements.txt",
)


def parser_environment(trusted: Path) -> tuple[Path, dict[str, str]]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "PYTHONPATH",
            "PYTHONHOME",
            "VIRTUAL_ENV",
            "UV_PROJECT_ENVIRONMENT",
        }
        and not key.upper().startswith(("UV_", "PIP_"))
    }
    python = trusted / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run(
        ["uv", "--no-config", "venv", "--python", sys.executable, str(trusted / "venv")], env=env, check=True
    )
    subprocess.run(
        [
            "uv",
            "--no-config",
            "pip",
            "install",
            "--python",
            str(python),
            "--require-hashes",
            "--only-binary",
            ":all:",
            "--no-deps",
            "--index-url",
            "https://pypi.org/simple",
            "-r",
            str(trusted / "comment-policy-requirements.txt"),
        ],
        env=env,
        check=True,
    )
    return python, env


def resolve_base(root: Path, requested: str | None) -> str:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        if os.environ.get("GITHUB_EVENT_NAME") == "pull_request":
            base = event["pull_request"]["base"]["sha"]
        elif os.environ.get("GITHUB_EVENT_NAME") == "merge_group":
            base = event["merge_group"]["base_sha"]
        else:
            raise ValueError("unsupported CI event")
        if requested and requested != base:
            raise ValueError("supplied base does not match the platform event")
    else:
        base = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "--verify", f"{requested or 'origin/develop'}^{{commit}}"], text=True
        ).strip()
        stale = subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", base, "HEAD"]).returncode != 0
        if not requested and stale:
            base = subprocess.check_output(["git", "-C", str(root), "merge-base", base, "HEAD"], text=True).strip()
    if not re.fullmatch(r"[a-f0-9]{40}", base):
        raise ValueError("invalid base SHA")
    subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", base, "HEAD"], check=True)
    return base


def bootstrap_base_allowed(root: Path, base: str) -> bool:
    contains_rollout = (
        subprocess.run(
            ["git", "-C", str(root), "merge-base", "--is-ancestor", BOOTSTRAP_BASE, base], capture_output=True
        ).returncode
        == 0
    )
    installed = any(
        subprocess.run(["git", "-C", str(root), "cat-file", "-e", f"{base}:{path}"], capture_output=True).returncode
        == 0
        for path in ("scripts/run_comment_policy.py", "quality/comment-policy.json")
    )
    return contains_rollout and not installed


def comparison_base(root: Path, base: str) -> str:
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("GITHUB_EVENT_NAME") != "pull_request":
        return base
    parents = subprocess.check_output(["git", "-C", str(root), "rev-list", "--parents", "-n", "1", "HEAD"], text=True)
    parents = parents.split()[1:]
    if len(parents) != 2:
        return base
    if subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", base, parents[0]]).returncode != 0:
        raise ValueError("event base is not an ancestor of the merge target")
    return parents[0]


def checker_command(
    python: Path, trusted: Path, root: Path, compare_base: str, *, bootstrap: bool, staged: bool
) -> list[str]:
    command = [
        str(python),
        "-I",
        str(trusted / "comment_policy_entry.py"),
        "--root",
        str(root),
        "--mode",
        "transition",
        "--base",
        compare_base,
    ]
    if bootstrap:
        command.append("--bootstrap")
    if staged:
        command.append("--staged")
    return command


def plain_text(value: str) -> str:
    text = "".join(f"\\x{ord(char):02x}" if ord(char) < 32 or ord(char) == 127 else char for char in value)
    text = text.replace("##[", "\\x23#[")
    return f"\\{text}" if text.lstrip().startswith("::") else text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the comment policy with immutable base parsers.")
    parser.add_argument("--base", default=os.environ.get("BASE_REF"))
    parser.add_argument("--staged", action="store_true")
    args = parser.parse_args(argv)
    root = Path.cwd().resolve()
    try:
        base = resolve_base(root, args.base)
        with tempfile.TemporaryDirectory(prefix="benchbox-comment-policy-") as directory:
            trusted = Path(directory)
            missing = []
            for name in TRUSTED_FILES:
                result = subprocess.run(["git", "show", f"{base}:{name}"], cwd=root, capture_output=True)
                if result.returncode:
                    missing.append(name)
                else:
                    (trusted / Path(name).name).write_bytes(result.stdout)
            bootstrap = bool(missing)
            if bootstrap:
                if not bootstrap_base_allowed(root, base) or len(missing) != len(TRUSTED_FILES):
                    raise ValueError(
                        "trusted checker missing on a base that does not contain the initial rollout commit"
                    )
                for name in TRUSTED_FILES:
                    (trusted / Path(name).name).write_bytes((root / name).read_bytes())
            python, env = parser_environment(trusted)
            command = checker_command(
                python, trusted, root, comparison_base(root, base), bootstrap=bootstrap, staged=args.staged
            )
            result = subprocess.run(command, cwd=trusted, env=env, check=False).returncode
            return result
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(plain_text(f"comment-policy: trusted invocation failed: {exc}"), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
