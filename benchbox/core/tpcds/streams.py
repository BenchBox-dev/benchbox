# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ DS (TPC-DS) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-DS specification.

# Licensed under the MIT License. See LICENSE file in the project root for details.

import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Optional

MULTI_PART_QUERY_IDS = frozenset({14, 23, 24, 39})


class PermutationMode(Enum):
    SEQUENTIAL = "sequential"
    RANDOM = "random"
    TPCDS_STANDARD = "tpcds"


@dataclass
class QueryStreamConfig:
    stream_id: int
    query_ids: list[int]
    permutation_mode: PermutationMode
    seed: Optional[int] = None
    parameter_seed: Optional[int] = None


@dataclass
class StreamQuery:
    stream_id: int
    position: int
    query_id: int
    variant: Optional[str] = None
    parameters: Optional[dict[str, Any]] = None
    sql: Optional[str] = None


class TPCDSPermutationGenerator:
    def __init__(self, seed: Optional[int] = None) -> None:
        self.seed = seed
        if seed is not None:
            random.seed(seed)

    def generate_permutation(self, items: list[int], mode: PermutationMode) -> list[int]:
        if mode == PermutationMode.SEQUENTIAL:
            return sorted(items)
        elif mode == PermutationMode.RANDOM:
            return self._random_permutation(items)
        elif mode == PermutationMode.TPCDS_STANDARD:
            return self._tpcds_permutation(items)
        else:
            raise ValueError(f"Unknown permutation mode: {mode}")

    def _random_permutation(self, items: list[int]) -> list[int]:
        permuted = items.copy()
        random.shuffle(permuted)
        return permuted

    def _tpcds_permutation(self, items: list[int]) -> list[int]:
        n = len(items)
        if n <= 1:
            return items.copy()

        permuted = items.copy()

        for i in range(n - 1, 0, -1):
            if self.seed is not None:
                j = (self.seed + i * 17 + i * i * 7) % (i + 1)
            else:
                j = random.randint(0, i)

            permuted[i], permuted[j] = permuted[j], permuted[i]

        return permuted


class TPCDSStreamManager:
    PERMUTATION_SEED = 19620718

    def __init__(self, query_manager, stream_configs: Optional[list[QueryStreamConfig]] = None) -> None:
        self.query_manager = query_manager
        self.stream_configs = stream_configs or []
        self.streams: dict[int, list[StreamQuery]] = {}
        self.permutation_generator = TPCDSPermutationGenerator()

    def add_stream(self, config: QueryStreamConfig) -> None:
        self.stream_configs.append(config)

    def generate_streams(self) -> dict[int, list[StreamQuery]]:
        self.streams = {}

        for config in self.stream_configs:
            self.streams[config.stream_id] = self._generate_single_stream(config)

        return self.streams

    def _generate_single_stream(self, config: QueryStreamConfig) -> list[StreamQuery]:
        if config.seed is not None:
            self.permutation_generator.seed = config.seed

        if config.parameter_seed is not None:
            random.seed(config.parameter_seed)

        permuted_queries = self.permutation_generator.generate_permutation(config.query_ids, config.permutation_mode)

        stream_queries = []
        for position, query_id in enumerate(permuted_queries):
            variants = self._get_query_variants(query_id)

            for variant in variants:
                stream_query = StreamQuery(
                    stream_id=config.stream_id,
                    position=position,
                    query_id=query_id,
                    variant=variant,
                )

                try:
                    if variant:
                        if hasattr(self.query_manager, "get_query") and hasattr(
                            self.query_manager.get_query, "__code__"
                        ):
                            if "variant" in self.query_manager.get_query.__code__.co_varnames:
                                stream_query.sql = self.query_manager.get_query(
                                    query_id,
                                    seed=config.parameter_seed,
                                    variant=variant,
                                )
                            else:
                                base_query = self.query_manager.get_query(query_id, seed=config.parameter_seed)
                                stream_query.sql = f"-- Query {query_id}{variant}\n{base_query}"
                        else:
                            base_query = self.query_manager.get_query(query_id, seed=config.parameter_seed)
                            stream_query.sql = f"-- Query {query_id}{variant}\n{base_query}"
                    else:
                        stream_query.sql = self.query_manager.get_query(query_id, seed=config.parameter_seed)
                except Exception as e:
                    raise RuntimeError(
                        f"TPC-DS query {query_id}{variant or ''} generation failed in stream {config.stream_id}: {e}"
                    ) from e

                stream_queries.append(stream_query)

        return stream_queries

    def _get_query_variants(self, query_id: int) -> list[Optional[str]]:
        if query_id in MULTI_PART_QUERY_IDS:
            return ["a", "b"]
        else:
            return [None]

    def get_stream(self, stream_id: int) -> Optional[list[StreamQuery]]:
        return self.streams.get(stream_id)

    def get_stream_summary(self, stream_id: int) -> Optional[dict[str, Any]]:
        stream = self.get_stream(stream_id)
        if stream is None:
            return None

        unique_queries = set()
        for sq in stream:
            query_key = f"{sq.query_id}{sq.variant or ''}"
            unique_queries.add(query_key)

        return {
            "stream_id": stream_id,
            "total_queries": len(stream),
            "unique_queries": len(unique_queries),
            "query_list": [f"{sq.query_id}{sq.variant or ''}" for sq in stream],
        }


def create_standard_streams(
    query_manager,
    num_streams: int = 2,
    query_range: tuple[int, int] = (1, 99),
    base_seed: int = 42,
    query_ids: Optional[list[int]] = None,
) -> TPCDSStreamManager:
    manager = TPCDSStreamManager(query_manager)

    if query_ids is not None:
        query_ids = sorted(query_ids)
    else:
        query_ids = list(range(query_range[0], query_range[1] + 1))

    for stream_id in range(num_streams):
        config = QueryStreamConfig(
            stream_id=stream_id,
            query_ids=query_ids,
            permutation_mode=PermutationMode.TPCDS_STANDARD,
            seed=base_seed + stream_id,
            parameter_seed=base_seed + stream_id + 1000,
        )
        manager.add_stream(config)

    return manager


class DSQGenStreamsError(Exception):
    pass


_DSQGEN_BEGIN_STREAM_RE = re.compile(r"^BEGIN STREAM\s+(\d+)\s*$", re.IGNORECASE)
_DSQGEN_TEMPLATE_RE = re.compile(r"^Template:\s*query(\d+)([ab]?)\.tpl\s*$", re.IGNORECASE)
_DSQGEN_STATEMENT_SPLIT_RE = re.compile(r"(?<=;)\n+(?=\S)")


def _resolve_dsqgen_binary_and_templates() -> tuple[Path, Path]:
    from benchbox.core.tpcds.c_tools import _resolve_tpcds_tool_and_template_paths

    return _resolve_tpcds_tool_and_template_paths()


def _stage_dsqgen_streams_workdir(
    temp_path: Path, templates_dir: Path, tools_dir: Path, dsqgen_path: Path, scale_factor: float
) -> dict:
    from benchbox.core.tpcds.c_tools import apply_subscale_template_overrides

    shutil.copytree(templates_dir, temp_path / "q")
    apply_subscale_template_overrides(temp_path / "q", scale_factor)
    variants_src = templates_dir.parent / "query_variants"
    if variants_src.exists():
        shutil.copytree(variants_src, temp_path / "query_variants")

    env = os.environ.copy()
    env["DSS_QUERY"] = str(temp_path)

    dsqgen_dir = Path(dsqgen_path).parent
    for dist_file in ("tpcds.dst", "tpcds.idx"):
        if (temp_path / dist_file).exists():
            continue
        for candidate_dir in (dsqgen_dir, tools_dir):
            dist_src = candidate_dir / dist_file
            if dist_src.exists():
                shutil.copy2(dist_src, temp_path / dist_file)
                break

    return env


def _run_dsqgen_streams(
    cmd: list[str],
    *,
    temp_path: Path,
    timeout: int,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    from benchbox.core.tpcds.c_tools import run_dsqgen_process

    def discard_partial_output() -> None:
        for output_path in temp_path.glob("query_*.sql"):
            output_path.unlink()
        (temp_path / "stream_params.log").unlink(missing_ok=True)

    return run_dsqgen_process(
        cmd,
        cwd=temp_path,
        timeout=timeout,
        env=env,
        discard_partial_output=discard_partial_output,
    )


def _parse_dsqgen_stream_log(log_text: str) -> dict[int, list[tuple[int, Optional[str]]]]:
    streams: dict[int, list[tuple[int, Optional[str]]]] = {}
    current_stream: Optional[int] = None

    for line in log_text.splitlines():
        stripped = line.strip()

        begin_match = _DSQGEN_BEGIN_STREAM_RE.match(stripped)
        if begin_match:
            current_stream = int(begin_match.group(1))
            streams[current_stream] = []
            continue

        template_match = _DSQGEN_TEMPLATE_RE.match(stripped)
        if template_match and current_stream is not None:
            query_id = int(template_match.group(1))
            if query_id in MULTI_PART_QUERY_IDS:
                streams[current_stream].append((query_id, "a"))
                streams[current_stream].append((query_id, "b"))
            else:
                streams[current_stream].append((query_id, None))

    return streams


def _split_dsqgen_stream_sql(sql_text: str) -> list[str]:
    cleaned = sql_text.strip()
    if not cleaned:
        return []
    return [stmt.strip() for stmt in _DSQGEN_STATEMENT_SPLIT_RE.split(cleaned) if stmt.strip()]


def generate_dsqgen_streams(
    num_streams: int,
    scale_factor: float = 1.0,
    seed: Optional[int] = None,
    dialect: str = "netezza",
    timeout: int = 300,
) -> dict[int, list[StreamQuery]]:
    if num_streams < 1:
        raise ValueError(f"num_streams must be >= 1, got {num_streams}")

    tools_dir, templates_dir = _resolve_dsqgen_binary_and_templates()
    dsqgen_path = tools_dir / ("dsqgen.exe" if sys.platform == "win32" else "dsqgen")
    if not dsqgen_path.exists():
        raise DSQGenStreamsError(f"dsqgen binary not found at {dsqgen_path}")
    if not templates_dir.exists():
        raise DSQGenStreamsError(f"TPC-DS query templates not found at {templates_dir}")

    opt = "/" if sys.platform == "win32" else "-"
    log_file_name = "stream_params.log"

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        env = _stage_dsqgen_streams_workdir(temp_path, templates_dir, tools_dir, dsqgen_path, scale_factor)

        cmd = [
            str(dsqgen_path),
            f"{opt}STREAMS",
            str(num_streams),
            f"{opt}INPUT",
            "q/templates.lst",
            f"{opt}DIRECTORY",
            "q",
            f"{opt}SCALE",
            str(scale_factor),
            f"{opt}DIALECT",
            dialect,
            f"{opt}OUTPUT_DIR",
            ".",
            f"{opt}LOG",
            log_file_name,
            f"{opt}VERBOSE",
            "Y",
        ]
        if seed is not None:
            cmd.extend([f"{opt}RNGSEED", str(seed)])

        try:
            result = _run_dsqgen_streams(
                cmd,
                temp_path=temp_path,
                timeout=timeout,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise DSQGenStreamsError(f"dsqgen -STREAMS {num_streams} timed out after {timeout}s") from exc
        except FileNotFoundError as exc:
            raise DSQGenStreamsError(f"dsqgen binary not found at {dsqgen_path}") from exc

        if result.returncode != 0:
            raise DSQGenStreamsError(
                f"dsqgen -STREAMS {num_streams} failed (exit {result.returncode}): "
                f"stdout={result.stdout.strip()!r} stderr={result.stderr.strip()!r}"
            )

        log_path = temp_path / log_file_name
        if not log_path.exists():
            raise DSQGenStreamsError(
                f"dsqgen -STREAMS did not produce the expected log file {log_path}; stdout={result.stdout.strip()!r}"
            )
        ordering = _parse_dsqgen_stream_log(log_path.read_text(encoding="utf-8"))

        streams: dict[int, list[StreamQuery]] = {}
        for stream_id in range(num_streams):
            sql_path = temp_path / f"query_{stream_id}.sql"
            if not sql_path.exists():
                raise DSQGenStreamsError(f"dsqgen -STREAMS did not produce the expected stream file {sql_path}")

            statements = _split_dsqgen_stream_sql(sql_path.read_text(encoding="utf-8"))
            stream_order = ordering.get(stream_id, [])

            if len(statements) != len(stream_order):
                raise DSQGenStreamsError(
                    f"dsqgen -STREAMS stream {stream_id}: parsed {len(statements)} SQL statements but "
                    f"{len(stream_order)} query positions from the -LOG ordering -- dsqgen's output format "
                    "may have changed; refusing to guess a mapping."
                )

            stream_queries: list[StreamQuery] = []
            for position, ((query_id, variant), sql_text) in enumerate(zip(stream_order, statements)):
                stream_queries.append(
                    StreamQuery(
                        stream_id=stream_id,
                        position=position,
                        query_id=query_id,
                        variant=variant,
                        sql=sql_text,
                    )
                )
            streams[stream_id] = stream_queries

        return streams


TPCDSStreams = TPCDSStreamManager
TPCDSStreamRunner = TPCDSStreamManager
