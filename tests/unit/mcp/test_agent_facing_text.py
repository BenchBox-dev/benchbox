from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import anyio
import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

pytest.importorskip("mcp", reason="MCP SDK not installed. Install with: uv add benchbox --extra mcp")

CALL_PATTERN = re.compile(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\(([^()]*)\)")
SNAKE_PATTERN = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
KEYWORD_PATTERN = re.compile(r"\b([a-z_][a-z0-9_]*)=")

PROMPT_ARGUMENTS: dict[str, dict[str, str]] = {
    "analyze_results": {"benchmark": "tpch", "platform": "duckdb", "focus": "joins"},
    "compare_platforms": {"benchmark": "tpch", "platforms": "duckdb,polars-df", "scale_factor": "0.01"},
    "identify_regressions": {},
    "benchmark_planning": {"use_case": "testing", "platforms": "duckdb", "time_budget_minutes": "30"},
    "troubleshoot_failure": {"error_message": "boom", "platform": "duckdb", "benchmark": "tpch"},
    "benchmark_run": {"platform": "duckdb", "benchmark": "tpch", "scale_factor": "0.01", "queries": "1,3"},
    "platform_tuning": {"platform": "duckdb", "workload": "joins"},
}
EXTRA_PROMPT_CASES = (
    ("identify_regressions", {"baseline_run": "a.json", "comparison_run": "b.json", "threshold_percent": "5"}),
    ("benchmark_run", {"platform": "duckdb", "benchmark": "tpch"}),
)


def _local_server() -> Any:
    from benchbox.mcp import create_server

    return create_server()


def _remote_server(tmp_path: Path) -> Any:
    from benchbox.mcp import create_server
    from benchbox.mcp.security import RemoteSecurityRuntime
    from tests.integration.mcp._security import write_security_config

    config = write_security_config(
        tmp_path,
        tokens={"token": ("tenant", ("benchbox:read", "benchbox:execute"))},
    )
    return create_server(remote_security=RemoteSecurityRuntime.from_file(config))


def _tool_arguments(server: Any) -> dict[str, set[str]]:
    async def collect() -> dict[str, set[str]]:
        return {tool.name: set(tool.input_schema.get("properties", {})) for tool in await server.list_tools()}

    return anyio.run(collect)


def _prompt_texts(server: Any) -> dict[str, str]:
    async def collect() -> dict[str, str]:
        texts: dict[str, str] = {}
        prompts = {prompt.name for prompt in await server.list_prompts()}
        assert prompts == set(PROMPT_ARGUMENTS)
        cases = [*PROMPT_ARGUMENTS.items(), *EXTRA_PROMPT_CASES]
        for index, (name, args) in enumerate(cases):
            result = await server.get_prompt(name, args)
            texts[f"{name}#{index}"] = "\n".join(message.content.text for message in result.messages)
        return texts

    return anyio.run(collect)


def agent_text_violations(
    label: str, text: str, local_tools: dict[str, set[str]], remote_only_tools: set[str]
) -> list[str]:
    violations: list[str] = []
    all_tools = {**local_tools}
    known_arguments = set().union(*local_tools.values())
    verbs = {name.split("_", 1)[0] for name in set(local_tools) | remote_only_tools}
    for paragraph in re.split(r"\n\s*\n", text):
        mentions_remote = "remote" in paragraph.lower()
        for name in set(SNAKE_PATTERN.findall(paragraph)):
            if name in remote_only_tools and not mentions_remote:
                violations.append(f"{label}: remote-only tool {name} appears outside remote-mode text")
            elif name not in all_tools and name not in remote_only_tools:
                if name.split("_", 1)[0] in verbs and name not in known_arguments:
                    violations.append(f"{label}: {name} is not a registered tool")
    for name, arguments in CALL_PATTERN.findall(text):
        if name in local_tools:
            valid = local_tools[name]
        elif name in remote_only_tools:
            continue
        else:
            violations.append(f"{label}: call to unregistered tool {name}")
            continue
        for keyword in KEYWORD_PATTERN.findall(arguments):
            if keyword not in valid:
                violations.append(f"{label}: {name} has no argument {keyword}")
    return violations


@pytest.fixture(scope="module")
def registry(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    local = _tool_arguments(_local_server())
    remote = _tool_arguments(_remote_server(tmp_path_factory.mktemp("remote")))
    remote_only = {name: arguments for name, arguments in remote.items() if name not in local}
    return {"local": local, "remote_only": remote_only}


def _checked_texts() -> dict[str, str]:
    server = _local_server()
    texts = _prompt_texts(server)
    texts["server instructions"] = server.instructions or ""
    return texts


class TestAgentFacingTextNamesRegisteredTools:
    def test_registry_is_read_from_live_servers(self, registry: dict[str, Any]):
        assert "run_benchmark" in registry["local"]
        assert "start_benchmark" in registry["remote_only"]
        assert "start_benchmark" not in registry["local"]
        assert "validate_only" in registry["local"]["run_benchmark"]

    def test_prompts_and_instructions_name_only_registered_tools_and_arguments(self, registry: dict[str, Any]):
        violations: list[str] = []
        for label, text in _checked_texts().items():
            violations.extend(agent_text_violations(label, text, registry["local"], set(registry["remote_only"])))

        assert violations == []

    def test_checker_rejects_the_retired_tool_names(self, registry: dict[str, Any]):
        stale = (
            'list_recent_runs(platform="duckdb")\n'
            'validate_config(platform="duckdb")\n'
            'compare_results(file1="a", file2="b")\n'
            "list_platforms()\n"
            "list_benchmarks()\n"
        )

        violations = agent_text_violations("stale", stale, registry["local"], set(registry["remote_only"]))

        flagged = " ".join(violations)
        for name in ("list_recent_runs", "validate_config", "compare_results", "list_platforms", "list_benchmarks"):
            assert name in flagged

    def test_checker_rejects_unknown_arguments_and_unqualified_remote_tools(self, registry: dict[str, Any]):
        local = registry["local"]
        remote_only = set(registry["remote_only"])

        assert agent_text_violations("args", 'get_results(nonexistent_arg="x")', local, remote_only)
        assert agent_text_violations("remote", "Queue work with start_benchmark(platform=...)", local, remote_only)
        assert not agent_text_violations(
            "remote", "In remote mode only, queue work with start_benchmark.", local, remote_only
        )

    def test_every_prompt_is_rendered(self):
        texts = _prompt_texts(_local_server())

        assert {key.split("#")[0] for key in texts} == set(PROMPT_ARGUMENTS)
        assert all(text.strip() for text in texts.values())


class TestServerInstructionsCarryMethodology:
    def test_instructions_require_validation_before_reporting(self):
        instructions = _local_server().instructions or ""

        assert "validate_results" in instructions
        assert "Before reporting" in instructions

    def test_instructions_state_the_comparability_rule(self):
        instructions = " ".join((_local_server().instructions or "").split())

        assert (
            "Compare two results only when their benchmark, scale, phase, configuration, tuning, hardware and "
            "evidence are compatible; a shared benchmark name alone is not enough."
        ) in instructions

    def test_scale_wording_follows_the_subscale_policy(self):
        instructions = " ".join((_local_server().instructions or "").split())

        assert "below scale factor 1 are unofficial subscale results and remain ranked" in instructions
        assert not re.search(r"cannot be ranked|not a ranking|not ranked|unranked", instructions, re.IGNORECASE)

    def test_run_benchmark_description_requires_validation_and_comparability(self):
        async def collect() -> str:
            tools = {tool.name: tool for tool in await _local_server().list_tools()}
            return " ".join((tools["run_benchmark"].description or "").split())

        description = anyio.run(collect)

        assert "call validate_results" in description
        assert "a shared benchmark name alone is not enough" in description
        assert not re.search(r"cannot be ranked|not a ranking|not ranked|unranked", description, re.IGNORECASE)

    def test_remote_tool_names_are_confined_to_remote_mode_text(self, registry: dict[str, Any]):
        instructions = _local_server().instructions or ""
        mentions = [
            paragraph
            for paragraph in re.split(r"\n\s*\n", instructions)
            if any(name in paragraph for name in registry["remote_only"])
        ]

        assert mentions
        assert all("remote mode only" in paragraph for paragraph in mentions)
