# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging

from mcp.server.mcpserver import MCPServer

logger = logging.getLogger(__name__)


def _build_analyze_results_prompt(benchmark: str, platform: str, focus: str | None) -> str:
    focus_text = ""
    if focus:
        focus_text = f"\nPay special attention to {focus} in your analysis."

    return f"""Analyze the {benchmark.upper()} benchmark results on {platform}.

Please use the following tools to gather data:
1. get_results(platform="{platform}", benchmark="{benchmark}") - Find recent runs
2. get_results(result_file="<filename>") - Get detailed results

Based on the results, provide:
1. **Performance Summary**: Overall execution time and query statistics
2. **Query Analysis**: Identify the 5 slowest queries and potential causes
3. **Pattern Recognition**: Look for patterns (e.g., join-heavy queries, aggregations)
4. **Recommendations**: Specific optimizations for this platform{focus_text}

Format your analysis with clear sections and include specific timing data."""


def _build_compare_platforms_prompt(benchmark: str, platforms: str, scale_factor: float) -> str:
    platform_list = [p.strip() for p in platforms.split(",")]

    return f"""Compare {benchmark.upper()} benchmark performance across these platforms:
{", ".join(platform_list)}

Scale Factor: {scale_factor}

Please use these tools:
1. run_benchmark(platform="<platform>", benchmark="{benchmark.lower()}", validate_only=True) for each platform to check availability
2. run_benchmark(platform="<platform>", benchmark="{benchmark.lower()}", scale_factor={scale_factor}) for each platform (if results don't exist)
3. get_results(benchmark="{benchmark.lower()}") to find existing results
4. validate_results(result_file="<filename>") on every run before using it
5. analyze_results(analysis="compare", file1="<baseline_file>", file2="<comparison_file>") to compare two runs at a time

Only compare runs whose benchmark, scale, phase, configuration, tuning, hardware and evidence are compatible; a shared benchmark name alone is not enough.

Provide a comprehensive comparison including:
1. **Overall Performance**: Total runtime for each platform
2. **Query-by-Query Analysis**: Which platform wins on each query type
3. **Strengths/Weaknesses**: What each platform does well
4. **Recommendations**: Which platform to use for different use cases

Use tables and specific metrics to support your analysis."""


def _build_regressions_prompt(baseline_run: str | None, comparison_run: str | None, threshold_percent: float) -> str:
    if baseline_run and comparison_run:
        return f"""Analyze performance regressions between two benchmark runs.

Baseline: {baseline_run}
Comparison: {comparison_run}
Regression Threshold: {threshold_percent}%

Use these tools:
1. analyze_results(analysis="compare", file1="{baseline_run}", file2="{comparison_run}", threshold_percent={threshold_percent})
2. get_results(result_file="<filename>") for detailed query information if needed

Provide analysis including:
1. **Regression Summary**: Number and severity of regressions
2. **Detailed Regressions**: List each regressed query with timing delta
3. **Root Cause Analysis**: Possible reasons for each regression
4. **Improvement Opportunities**: Any queries that got faster (to understand what helped)
5. **Action Items**: Specific steps to investigate/fix regressions"""

    return f"""Identify performance regressions in recent benchmark runs.

Regression Threshold: {threshold_percent}%

Use these tools:
1. get_results() - List recent runs and pick the two most recent comparable ones
2. analyze_results(analysis="regressions", threshold_percent={threshold_percent}) - Detect regressions across recent runs
3. get_results(result_file="<filename>") - Get details on any regressed queries

Provide analysis including:
1. **Regression Summary**: Overview of any regressions found
2. **Trend Analysis**: Is performance improving or declining over time?
3. **Action Items**: Recommended next steps for investigation"""


def _build_benchmark_planning_prompt(use_case: str, platforms: str | None, time_budget_minutes: int) -> str:
    return f"""Help plan a benchmark strategy for the following requirements:

Use Case: {use_case}
Time Budget: {time_budget_minutes} minutes
Target Platforms: {platforms or "To be determined based on use case"}

Use these tools to gather information:
1. list_available(category="platforms") - See available platforms and their capabilities
2. list_available(category="benchmarks") - See available benchmarks
3. system_profile() - Check system resources for scale factor recommendations

Based on this information, provide:
1. **Recommended Benchmark**: Which benchmark(s) to run
2. **Scale Factor**: Appropriate scale factor for time budget and system
3. **Platform Selection**: Which platforms make sense for the use case
4. **Phase Strategy**: Which phases to run (load, power, throughput)
5. **Query Subset**: If time-constrained, which queries to prioritize
6. **Execution Plan**: Step-by-step commands to execute

Consider tradeoffs between:
- Statistical significance (higher scale, more iterations)
- Time constraints
- Resource availability"""


def _build_troubleshoot_prompt(error_message: str | None, platform: str | None, benchmark: str | None) -> str:
    context = []
    if error_message:
        context.append(f"Error Message: {error_message}")
    if platform:
        context.append(f"Platform: {platform}")
    if benchmark:
        context.append(f"Benchmark: {benchmark}")

    context_str = "\n".join(context) if context else "No specific context provided"

    return f"""Troubleshoot a benchmark failure with the following context:

{context_str}

Use these tools to diagnose:
1. run_benchmark(platform="<platform>", benchmark="<benchmark>", validate_only=True) - Check if configuration is valid
2. system_profile() - Check system resources
3. list_available(category="platforms") - Verify platform availability

Common issues to check:
1. **Platform Dependencies**: Is the platform properly installed?
2. **Scale Factor**: Is the scale factor valid for this benchmark?
3. **Resource Limits**: Sufficient memory/disk for the scale factor?
4. **Credentials**: For cloud platforms, are credentials configured?
5. **Data Generation**: Does generated data exist and is it valid?

Provide:
1. **Diagnosis**: Most likely cause of the failure
2. **Verification Steps**: How to confirm the cause
3. **Resolution**: Step-by-step fix instructions
4. **Prevention**: How to avoid this issue in the future"""


def _build_benchmark_run_prompt(platform: str, benchmark: str, scale_factor: float, queries: str | None) -> str:
    queries_text = ""
    if queries:
        queries_text = f"\nQuery subset: {queries}"

    return f"""Execute a benchmark with the following configuration:

Platform: {platform}
Benchmark: {benchmark.upper()}
Scale Factor: {scale_factor}{queries_text}

Before running, please:
1. run_benchmark(platform="{platform}", benchmark="{benchmark}", scale_factor={scale_factor}, validate_only=True) - Verify the configuration is valid
2. check_dependencies(platform="{platform}") - Ensure dependencies are installed

If validation passes, execute:
3. run_benchmark(platform="{platform}", benchmark="{benchmark}", scale_factor={scale_factor}{f', queries="{queries}"' if queries else ""})

Before reporting the results:
4. validate_results(result_file="<result_file from the run>") - Check integrity and report any WARN or FAIL findings

After execution, provide:
1. **Execution Summary**: Overall status and runtime
2. **Performance Highlights**: Fastest and slowest queries
3. **Results Location**: Where to find detailed results
4. **Recommendations**: Any optimizations based on results

If any step fails, provide diagnostic information and suggest fixes."""


def _build_platform_tuning_prompt(platform: str, workload: str | None) -> str:
    workload_text = ""
    if workload:
        workload_text = f"\nWorkload characteristics: {workload}"

    return f"""Provide tuning recommendations for {platform} platform.{workload_text}

Use these tools to gather information:
1. system_profile() - Get system resources (CPU, memory, disk)
2. check_dependencies(platform="{platform}", verbose=True) - Verify platform configuration

Based on the system profile and platform capabilities, provide:

1. **Memory Configuration**
   - Recommended memory limits based on available RAM
   - Buffer pool/cache sizing recommendations

2. **Parallelism Settings**
   - Thread count recommendations based on CPU cores
   - Worker/executor configuration

3. **I/O Optimization**
   - Disk I/O settings if applicable
   - Compression recommendations for data format

4. **Query Optimization**
   - Join strategies for analytical workloads
   - Aggregation optimizations

5. **Platform-Specific Settings**
   - Settings unique to {platform}
   - Best practices for benchmark workloads

Format recommendations as:
- Setting name: recommended value
- Explanation of why this value is optimal for the system

Include example configuration snippets where applicable."""


ANALYZE_RESULTS_DESCRIPTION = "Analyze benchmark results. (benchmark=tpch, platform=duckdb, focus=optional)"


COMPARE_PLATFORMS_DESCRIPTION = (
    "Compare performance across platforms. (benchmark=tpch, platforms=duckdb,polars-df, scale_factor=0.01)"
)


IDENTIFY_REGRESSIONS_DESCRIPTION = (
    "Find performance regressions between runs. (baseline_run, comparison_run, threshold_percent=10)"
)


BENCHMARK_PLANNING_DESCRIPTION = (
    "Plan a benchmark strategy. (use_case=testing|production|comparison, platforms, time_budget_minutes=30)"
)


TROUBLESHOOT_FAILURE_DESCRIPTION = "Diagnose benchmark failures. (error_message, platform, benchmark)"


BENCHMARK_RUN_DESCRIPTION = (
    "Execute a planned benchmark. (platform=duckdb, benchmark=tpch, scale_factor=0.01, queries=optional)"
)


PLATFORM_TUNING_DESCRIPTION = "Get tuning recommendations for a platform. (platform=duckdb, workload=optional)"


def register_all_prompts(mcp: MCPServer) -> None:

    @mcp.prompt(description=ANALYZE_RESULTS_DESCRIPTION)
    def analyze_results(
        benchmark: str = "tpch",
        platform: str = "duckdb",
        focus: str | None = None,
    ) -> str:
        return _build_analyze_results_prompt(benchmark, platform, focus)

    @mcp.prompt(description=COMPARE_PLATFORMS_DESCRIPTION)
    def compare_platforms(
        benchmark: str = "tpch",
        platforms: str = "duckdb,polars-df",
        scale_factor: float = 0.01,
    ) -> str:
        return _build_compare_platforms_prompt(benchmark, platforms, scale_factor)

    @mcp.prompt(description=IDENTIFY_REGRESSIONS_DESCRIPTION)
    def identify_regressions(
        baseline_run: str | None = None,
        comparison_run: str | None = None,
        threshold_percent: float = 10.0,
    ) -> str:
        return _build_regressions_prompt(baseline_run, comparison_run, threshold_percent)

    @mcp.prompt(description=BENCHMARK_PLANNING_DESCRIPTION)
    def benchmark_planning(
        use_case: str = "testing",
        platforms: str | None = None,
        time_budget_minutes: int = 30,
    ) -> str:
        return _build_benchmark_planning_prompt(use_case, platforms, time_budget_minutes)

    @mcp.prompt(description=TROUBLESHOOT_FAILURE_DESCRIPTION)
    def troubleshoot_failure(
        error_message: str | None = None,
        platform: str | None = None,
        benchmark: str | None = None,
    ) -> str:
        return _build_troubleshoot_prompt(error_message, platform, benchmark)

    @mcp.prompt(description=BENCHMARK_RUN_DESCRIPTION)
    def benchmark_run(
        platform: str = "duckdb",
        benchmark: str = "tpch",
        scale_factor: float = 0.01,
        queries: str | None = None,
    ) -> str:
        return _build_benchmark_run_prompt(platform, benchmark, scale_factor, queries)

    @mcp.prompt(description=PLATFORM_TUNING_DESCRIPTION)
    def platform_tuning(
        platform: str = "duckdb",
        workload: str | None = None,
    ) -> str:
        return _build_platform_tuning_prompt(platform, workload)

    logger.info("Registered MCP prompts")
