from __future__ import annotations

import importlib
import logging
import re
import shlex
import types
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from benchbox.cli.platform_hooks import PlatformHookRegistry, PlatformOptionError, PlatformOptionSpec
from benchbox.cli.run_platform_resolution import _apply_deployment_selector_option, _split_deployment_selector
from benchbox.core.deprecation_notices import begin_run
from benchbox.core.gateway import register_gateway_connection_specs

pytestmark = [pytest.mark.unit, pytest.mark.fast]

importlib.import_module("benchbox.cli.app")

ROOT = Path(__file__).resolve().parents[3]
DOCS = ROOT / "docs"
HELP_CATALOG = ROOT / "benchbox" / "data" / "cli_help_catalog.yaml"

EXCLUDED_DIRS = ("blog/",)

DEPRECATION_NOTES = {
    ("platforms/clickhouse-migration.md", "deployment_mode"),
}

FENCE = re.compile(r"^[ \t]*```[^\n]*\n(.*?)^[ \t]*```", re.DOTALL | re.MULTILINE)
INLINE = re.compile(r"`([^`\n]*--platform-option[^`\n]*)`")
PLACEHOLDER = re.compile(r"[<>{}]|\.\.\.")
PLACEHOLDER_KEY = re.compile(r"^[A-Z_]+$")
SHELL_SAMPLES = ("1", "true", "1.0")


@dataclass(frozen=True)
class Example:
    path: str
    platform: str | None
    pairs: tuple[tuple[str, str], ...]

    @property
    def label(self) -> str:
        options = " ".join(f"{key}={value}" for key, value in self.pairs)
        return f"{self.path}: --platform {self.platform or '?'} {options}"


def _tokens(command: str) -> list[str]:
    try:
        return shlex.split(command, comments=True)
    except ValueError:
        return command.split()


def _alternatives(assignment: str) -> list[tuple[str, str]]:
    key, value = assignment.split("=", 1)
    if "|" not in value:
        return [(key, value)]
    return [(key, choice) for choice in value.strip("[]").split("|")]


def _parses(spec: PlatformOptionSpec, value: str) -> bool:
    try:
        spec.parse(value)
    except (PlatformOptionError, ValueError):
        return False
    return True


def _shell_neutral(platform_key: str, pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    specs = PlatformHookRegistry.list_option_specs(platform_key)
    neutral: list[tuple[str, str]] = []
    for key, value in pairs:
        spec = specs.get(PlatformHookRegistry._resolve_option_name(platform_key, key))
        if "$" in value and spec is not None:
            value = next((sample for sample in SHELL_SAMPLES if _parses(spec, sample)), value)
        neutral.append((key, value))
    return neutral


def _example(path: str, command: str) -> Example | None:
    tokens = _tokens(command)
    platform: str | None = None
    pairs: list[tuple[str, str]] = []
    for index, token in enumerate(tokens):
        following = tokens[index + 1] if index + 1 < len(tokens) else ""
        if token == "--platform":
            platform = following
        elif token.startswith("--platform="):
            platform = token.split("=", 1)[1]
        elif token == "--platform-option" and "=" in following:
            pairs.extend(_alternatives(following))
        elif token.startswith("--platform-option=") and token.count("=") >= 2:
            pairs.extend(_alternatives(token.split("=", 1)[1]))
    pairs = [
        (key, value) for key, value in pairs if not (PLACEHOLDER_KEY.match(key) or PLACEHOLDER.search(key + value))
    ]
    if platform is not None and PLACEHOLDER.search(platform):
        platform = None
    if not pairs:
        return None
    return Example(path, platform, tuple(pairs))


def _block_commands(block: str) -> list[str]:
    return [line for line in block.replace("\\\n", " ").splitlines() if "--platform-option" in line]


def _commands(text: str) -> list[str]:
    commands: list[str] = []
    for block in FENCE.findall(text):
        commands.extend(_block_commands(block))
    commands.extend(INLINE.findall(FENCE.sub("", text)))
    return commands


def _help_catalog_examples() -> list[Example]:
    catalog = yaml.safe_load(HELP_CATALOG.read_text(encoding="utf-8"))
    examples: list[Example] = []
    for command, sections in catalog.get("command_examples", {}).items():
        for section, lines in sections.items():
            for line in _block_commands("\n".join(lines)):
                example = _example(f"cli_help_catalog.yaml {command}/{section}", line)
                if example is not None:
                    examples.append(example)
    return examples


def _examples() -> list[Example]:
    examples: list[Example] = []
    for doc in sorted(DOCS.rglob("*.md")):
        relative = doc.relative_to(DOCS).as_posix()
        if relative.startswith(EXCLUDED_DIRS):
            continue
        for command in _commands(doc.read_text(encoding="utf-8")):
            example = _example(relative, command)
            if example is not None:
                examples.append(example)
    return examples + _help_catalog_examples()


EXAMPLES = _examples()


class _Exit(Exception):
    pass


def _deprecations(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records if "deprecated" in record.getMessage().lower()]


def _check(platform: str, pairs: list[tuple[str, str]], caplog: pytest.LogCaptureFixture) -> str | None:
    begin_run()
    caplog.clear()
    platform_key, selector = _split_deployment_selector(platform)
    pairs = _shell_neutral(platform_key, pairs)
    try:
        parsed = PlatformHookRegistry.parse_options(platform_key, pairs)
    except PlatformOptionError as exc:
        return str(exc)
    state = types.SimpleNamespace(
        platform_key=platform_key,
        deployment_selector=selector,
        platform_option_pairs=pairs,
        parsed_platform_options=parsed,
        logger=None,
        ctx=types.SimpleNamespace(exit=lambda code: (_ for _ in ()).throw(_Exit(code))),
    )
    try:
        _apply_deployment_selector_option(state)
    except (_Exit, ValueError) as exc:
        return f"deployment selection rejected: {exc}"
    messages = _deprecations(caplog)
    return "; ".join(messages) if messages else None


@pytest.fixture(autouse=True)
def _registered_specs() -> None:
    register_gateway_connection_specs()


def test_docs_contain_platform_option_examples() -> None:
    assert len(EXAMPLES) > 100, "too few --platform-option examples found - did the extraction break?"


def test_every_documented_platform_option_is_accepted(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    failures: list[str] = []
    for example in EXAMPLES:
        pairs = [pair for pair in example.pairs if (example.path, pair[0]) not in DEPRECATION_NOTES]
        if not pairs:
            continue
        if example.platform is None:
            continue
        problem = _check(example.platform, pairs, caplog)
        if problem:
            failures.append(f"{example.label}\n    {problem}")
    assert not failures, "documented --platform-option examples the CLI rejects or deprecates:\n" + "\n".join(failures)


def test_platformless_examples_name_a_current_option(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    platforms = sorted(PlatformHookRegistry._option_specs)
    failures: list[str] = []
    for example in EXAMPLES:
        if example.platform is not None:
            continue
        for pair in example.pairs:
            if (example.path, pair[0]) in DEPRECATION_NOTES:
                continue
            if not any(_check(platform, [pair], caplog) is None for platform in platforms):
                failures.append(f"{example.path}: --platform-option {pair[0]}={pair[1]}")
    assert not failures, "documented --platform-option examples no platform accepts without a warning:\n" + "\n".join(
        failures
    )
