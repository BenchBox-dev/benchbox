from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

_SHA1 = re.compile(r"[0-9a-f]{40}")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def verify_tag_objects(tag_object: str, peeled_commit: str, expected_tag_object: str, expected_sha: str) -> None:
    _require(bool(_SHA1.fullmatch(tag_object)), "hosted tag object is not a SHA")
    _require(bool(_SHA1.fullmatch(peeled_commit)), "hosted tag does not peel to a SHA")
    _require(tag_object == expected_tag_object, "hosted tag object differs from the admission receipt")
    _require(peeled_commit == expected_sha, "hosted tag commit differs from the admission receipt")


def hosted_tag_objects(tag: str) -> tuple[str, str]:
    result = subprocess.run(
        ["git", "ls-remote", "origin", f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    objects: dict[str, str] = {}
    for line in result.stdout.splitlines():
        sha, _, ref = line.partition("\t")
        objects[ref.strip()] = sha.strip()
    tag_object = objects.get(f"refs/tags/{tag}", "")
    peeled = objects.get(f"refs/tags/{tag}^{{}}", tag_object)
    _require(bool(tag_object), "hosted tag is missing")
    return tag_object, peeled


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refuse publication when the hosted tag moved after admission.")
    parser.add_argument("--admitted", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    args = parser.parse_args(argv)
    try:
        receipt = json.loads((args.admitted / "admission-receipt.json").read_text(encoding="utf-8"))
        _require(receipt.get("tag") == args.tag, "admission receipt is for another tag")
        tag_object, peeled = hosted_tag_objects(args.tag)
        verify_tag_objects(tag_object, peeled, str(receipt["tag_object"]), str(receipt["head_sha"]))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Tag re-check failed: {exc}\n")
    print(json.dumps({"tag": args.tag, "tag_object": tag_object, "head_sha": peeled}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
