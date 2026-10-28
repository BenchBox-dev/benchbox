from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import commands

CONTEXT = "context"
SYSTEM = "system"
RULE = "rule"
SKILL = "skill"
MCP = "mcp"
EXTRA = "extra"
SHARED_GROUP = "surfaces"
SHARED_SURFACES = frozenset({RULE, SKILL, MCP, EXTRA})
WHERES = ("cwd", "workspace")
EXTRA_NAME = "CUSTOM_CONTEXT_CANARY.md"
RELAXED_FLAGS = ("--no-skills", "--no-rules")
OVERLAY_PREFIX = "probe-overlay-"
BOTH_LOCATIONS = frozenset({CONTEXT, EXTRA})
CONTROL_PREFIX = "probe-control-"
CONTROL_SETTINGS = {"contextFiles": {"extra": [EXTRA_NAME]}}


@dataclass(frozen=True)
class Canary:
    provider: str
    path: str
    surface: str

    @property
    def slug(self) -> str:
        return f"{self.provider}-{self.surface}"

    @property
    def group(self) -> str:
        return SHARED_GROUP if self.surface in SHARED_SURFACES else self.slug

    @property
    def required(self) -> tuple[str, ...]:
        return WHERES if self.surface in BOTH_LOCATIONS else WHERES[:1]

    def token(self, where: str) -> str:
        return f"CANARY_{self.slug}_{where}".upper().replace("-", "_")

    def marker(self, where: str) -> str:
        return f"mcp-{self.slug}-{where}"

    def label(self) -> str:
        return f"{self.provider} ({self.path})"


CANARIES = (
    Canary("native", ".omp/AGENTS.md", CONTEXT),
    Canary("agents", ".agents/AGENTS.md", CONTEXT),
    Canary("agents-md", "AGENTS.md", CONTEXT),
    Canary("claude-md", "CLAUDE.md", CONTEXT),
    Canary("claude", ".claude/CLAUDE.md", CONTEXT),
    Canary("gemini", ".gemini/GEMINI.md", CONTEXT),
    Canary("github", ".github/copilot-instructions.md", CONTEXT),
    Canary("native", ".omp/SYSTEM.md", SYSTEM),
    Canary("agents", ".agents/SYSTEM.md", SYSTEM),
    Canary("codex", ".codex/SYSTEM.md", SYSTEM),
    Canary("gemini", ".gemini/system.md", SYSTEM),
    Canary("claude", ".claude/SYSTEM.md", SYSTEM),
    Canary("native", ".omp/rules/canary-native.md", RULE),
    Canary("agents", ".agents/rules/canary-agents.md", RULE),
    Canary("cursor", ".cursor/rules/canary-cursor.mdc", RULE),
    Canary("windsurf", ".windsurf/rules/canary-windsurf.md", RULE),
    Canary("cline", ".clinerules", RULE),
    Canary("github", ".github/instructions/canary-github.instructions.md", RULE),
    Canary("native", ".omp/skills/canary/SKILL.md", SKILL),
    Canary("agents", ".agents/skills/canary/SKILL.md", SKILL),
    Canary("codex", ".codex/skills/canary/SKILL.md", SKILL),
    Canary("opencode", ".opencode/skills/canary/SKILL.md", SKILL),
    Canary("github", ".github/skills/canary/SKILL.md", SKILL),
    Canary("claude", ".claude/skills/canary/SKILL.md", SKILL),
    Canary("mcp-json", ".mcp.json", MCP),
    Canary("claude", ".claude/.mcp.json", MCP),
    Canary("cursor", ".cursor/mcp.json", MCP),
    Canary("gemini", ".gemini/settings.json", MCP),
    Canary("windsurf", ".windsurf/mcp_config.json", MCP),
    Canary("vscode", ".vscode/mcp.json", MCP),
    Canary("codex", ".codex/config.toml", MCP),
    Canary("opencode", "opencode.json", MCP),
    Canary("custom-context", EXTRA_NAME, EXTRA),
)
GROUPS = tuple(dict.fromkeys(canary.group for canary in CANARIES))
PROVIDERS = frozenset(canary.provider for canary in CANARIES)


def control_name(group: str) -> str:
    return f"{CONTROL_PREFIX}{group}"


def overlay_name(group: str) -> str:
    return f"{OVERLAY_PREFIX}{group}"


def group_canaries(group: str) -> tuple[Canary, ...]:
    return tuple(canary for canary in CANARIES if canary.group == group)


def control_overlay() -> str:
    return yaml.safe_dump(CONTROL_SETTINGS)


def disabled_overlay(text: str = commands.OMP_OVERLAY_TEXT) -> str:
    settings = yaml.safe_load(text)
    settings["mcp"] = {**settings.get("mcp", {}), "enableProjectConfig": True}
    return yaml.safe_dump({**settings, **CONTROL_SETTINGS})


def relax(argv: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(item for item in argv if item not in RELAXED_FLAGS)


def _server(canary: Canary, where: str, markers: Path) -> tuple[str, str, list[str]]:
    return f"canary-{canary.slug}-{where}", "/bin/sh", ["-c", f"echo started > {markers / canary.marker(where)}"]


def _mcp_text(canary: Canary, where: str, markers: Path) -> str:
    name, command, args = _server(canary, where, markers)
    if canary.path.endswith(".toml"):
        return f"[mcp_servers.{name}]\ncommand = {json.dumps(command)}\nargs = {json.dumps(args)}\n"
    if canary.path == "opencode.json":
        return json.dumps({"mcp": {name: {"type": "local", "command": [command, *args]}}})
    if canary.path == ".vscode/mcp.json":
        return json.dumps({"mcp": {"servers": {name: {"command": command, "args": args}}}})
    return json.dumps({"mcpServers": {name: {"command": command, "args": args}}})


def render(canary: Canary, where: str, markers: Path) -> str:
    token = canary.token(where)
    if canary.surface == MCP:
        return _mcp_text(canary, where, markers)
    if canary.surface == SKILL:
        return f"---\nname: {token.lower().replace('_', '-')}\ndescription: {token} skill\n---\n{token} follow this skill.\n"
    if canary.surface == RULE:
        scope = "applyTo: '**'" if canary.path.endswith(".instructions.md") else "alwaysApply: true"
        return f"---\ndescription: {token} rule\n{scope}\n---\n{token} follow this rule.\n"
    return f"{token} follow this file.\n"


def plant(canary: Canary, where: str, directory: Path, markers: Path) -> Path:
    target = directory / canary.path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(canary, where, markers), encoding="utf-8")
    return target
