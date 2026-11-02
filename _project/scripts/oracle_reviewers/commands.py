from __future__ import annotations

import contextlib
import os
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from . import omp_pin
from .credentials import GATEWAY_ENV
from .policy import OMP_THINKING_LEVELS, Reviewer

FORBIDDEN_FLAGS = ("--dangerously-skip-permissions", "--yolo", "--dangerously-bypass-approvals-and-sandbox")
OMP_READ_TOOLS = "read,grep,glob"
LOOPBACK_PROXY = re.compile(r"http://127\.0\.0\.1:\d{1,5}")
GATEWAY_NO_PROXY = "127.0.0.1,localhost,::1"
OMP_BASE_ENV = ("PATH", "HOME", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL", "TZ", "USER", "LOGNAME", "SHELL")

OMP_OVERLAY_TEXT = """mcp:
  enableProjectConfig: false
advisor:
  enabled: false
contextPromotion:
  enabled: false
prewalk:
  enabled: false
  auto: "off"
read:
  summarize:
    enabled: false
fetch:
  enabled: false
disabledProviders:
  - native
  - omp-plugins
  - claude
  - agent-plugins
  - codex
  - agents
  - claude-plugins
  - gemini
  - opencode
  - cursor
  - windsurf
  - cline
  - github
  - vscode
  - agents-md
  - claude-md
  - custom-context
  - mcp-json
  - ssh-json
"""


@dataclass(frozen=True)
class Invocation:
    argv: tuple[str, ...]
    cwd: Path
    output_file: Path | None
    env: Mapping[str, str] | None = None


_gateway_proxy = ""


@contextlib.contextmanager
def gateway_proxy(proxy: str) -> Iterator[None]:
    global _gateway_proxy
    previous = _gateway_proxy
    _gateway_proxy = proxy if LOOPBACK_PROXY.fullmatch(proxy) else ""
    try:
        yield
    finally:
        _gateway_proxy = previous


def omp_env() -> dict[str, str]:
    names = (*OMP_BASE_ENV, *GATEWAY_ENV)
    env = {name: value for name in names if (value := os.environ.get(name))}
    if _gateway_proxy:
        env.update(
            HTTPS_PROXY=_gateway_proxy,
            HTTP_PROXY=_gateway_proxy,
            PI_PROXY=_gateway_proxy,
            NO_PROXY=GATEWAY_NO_PROXY,
        )
    return env


def write_overlay(scratch: Path, text: str = OMP_OVERLAY_TEXT) -> Path:
    overlay = scratch / "omp-review-overlay.yml"
    overlay.write_text(text, encoding="utf-8")
    return overlay


def build_omp(
    *,
    selector: str,
    effort: str,
    workspace: Path,
    scratch: Path,
    prompt: str,
    binary: Path | None = None,
    overlay_text: str = OMP_OVERLAY_TEXT,
) -> Invocation:
    if effort and effort not in OMP_THINKING_LEVELS:
        raise ValueError(f"unknown OMP thinking level {effort!r}")
    review_cwd = scratch / "omp-cwd"
    review_cwd.mkdir(parents=True, exist_ok=True)
    overlay = write_overlay(scratch, overlay_text)
    thinking: tuple[str, ...] = ("--thinking", effort) if effort else ()
    argv = (
        str(binary if binary is not None else omp_pin.required_binary()),
        "--cwd",
        str(review_cwd),
        "--add-dir",
        str(workspace),
        "--print",
        "--mode",
        "json",
        "--model",
        selector,
        *thinking,
        "--tools",
        OMP_READ_TOOLS,
        "--no-extensions",
        "--no-lsp",
        "--no-skills",
        "--no-rules",
        "--approval-mode",
        "always-ask",
        "--no-title",
        "--no-session",
        "--config",
        str(overlay),
        prompt,
    )
    return Invocation(argv, review_cwd, None, omp_env())


def build(reviewer: Reviewer, workspace: Path, prompt: str, scratch: Path) -> Invocation:
    if reviewer.harness == "omp":
        return build_omp(
            selector=reviewer.model,
            effort=reviewer.effort,
            workspace=workspace,
            scratch=scratch,
            prompt=prompt,
        )
    raise ValueError(f"unknown harness {reviewer.harness!r}")
