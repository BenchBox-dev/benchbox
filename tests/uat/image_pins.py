from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

COMPOSE_GLOB = "docker/**/docker-compose*.y*ml"

_DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}$")
_ENV_DEFAULT_RE = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*:-(?P<default>[^}]*)\}$")
_FROM_RE = re.compile(r"^\s*FROM\s+(?:--\S+\s+)*(?P<image>\S+)(?:\s+AS\s+(?P<alias>\S+))?\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class FloatingImage:
    path: Path
    line: int
    reference: str
    reason: str

    def render(self, root: Path) -> str:
        try:
            shown = self.path.relative_to(root)
        except ValueError:
            shown = self.path
        return f"{shown}:{self.line}: {self.reference}: {self.reason}"


def compose_files(root: Path) -> list[Path]:
    return sorted(root.glob(COMPOSE_GLOB))


def _resolve_default(reference: str) -> str:
    match = _ENV_DEFAULT_RE.match(reference)
    return match.group("default") if match else reference


def _line_of(lines: Sequence[str], needle: str) -> int:
    for number, text in enumerate(lines, start=1):
        if needle in text:
            return number
    return 1


def _digest_problem(reference: str) -> str | None:
    if "$" in reference:
        return "reference is not resolvable without environment variables"
    if not _DIGEST_RE.search(reference):
        return "reference has no @sha256 digest"
    return None


def _dockerfile_violations(dockerfile: Path) -> list[FloatingImage]:
    if not dockerfile.is_file():
        return [FloatingImage(dockerfile, 1, str(dockerfile), "referenced Dockerfile does not exist")]
    violations: list[FloatingImage] = []
    aliases: set[str] = set()
    for number, text in enumerate(dockerfile.read_text(encoding="utf-8").splitlines(), start=1):
        match = _FROM_RE.match(text)
        if match is None:
            continue
        image = match.group("image")
        if image.lower() != "scratch" and image not in aliases:
            problem = _digest_problem(image)
            if problem:
                violations.append(FloatingImage(dockerfile, number, image, problem))
        if match.group("alias"):
            aliases.add(match.group("alias"))
    return violations


def _compose_violations(path: Path) -> list[FloatingImage]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    document = yaml.safe_load(text) or {}
    services = document.get("services") or {}
    violations: list[FloatingImage] = []
    for service in services.values():
        if not isinstance(service, dict):
            continue
        build = service.get("build")
        if build is not None:
            context = build.get("context", ".") if isinstance(build, dict) else build
            dockerfile = build.get("dockerfile", "Dockerfile") if isinstance(build, dict) else "Dockerfile"
            violations.extend(_dockerfile_violations((path.parent / context / dockerfile).resolve()))
            continue
        raw = service.get("image")
        if raw is None:
            continue
        reference = _resolve_default(str(raw))
        problem = _digest_problem(reference)
        if problem:
            violations.append(FloatingImage(path, _line_of(lines, str(raw)), reference, problem))
    return violations


def find_floating_images(root: Path) -> list[FloatingImage]:
    root = root.resolve()
    violations: list[FloatingImage] = []
    for path in compose_files(root):
        violations.extend(_compose_violations(path))
    seen: set[tuple[Path, int]] = set()
    unique: list[FloatingImage] = []
    for violation in violations:
        key = (violation.path, violation.line)
        if key not in seen:
            seen.add(key)
            unique.append(violation)
    return unique


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reject UAT Docker images that are not pinned to a digest")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    root = args.root.resolve()
    violations = find_floating_images(root)
    for violation in violations:
        print(violation.render(root), file=sys.stderr)
    if violations:
        return 1
    print(f"all images in {len(compose_files(root))} compose files are pinned to digests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
