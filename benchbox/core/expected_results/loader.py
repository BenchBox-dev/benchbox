# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)
_TPCH_VALUE_DIGEST_REFERENCE_PATH = Path(__file__).with_name("reference_digests") / "tpch_value_digests_sf1.json"


def parse_tpch_answer_file(answer_file_path: Path) -> int:
    if not answer_file_path.exists():
        raise FileNotFoundError(f"TPC-H answer file not found: {answer_file_path}")

    try:
        with open(answer_file_path, encoding="utf-8") as f:
            lines = f.readlines()

        non_empty_lines = [line.strip() for line in lines if line.strip()]

        if len(non_empty_lines) < 1:
            raise ValueError(f"TPC-H answer file is empty: {answer_file_path}")

        row_count = len(non_empty_lines) - 1

        logger.debug(f"Parsed TPC-H answer file {answer_file_path.name}: {row_count} rows")
        return row_count

    except Exception as e:
        logger.error(f"Failed to parse TPC-H answer file {answer_file_path}: {e}")
        raise


def parse_tpcds_answer_file(answer_file_path: Path) -> int:
    if not answer_file_path.exists():
        raise FileNotFoundError(f"TPC-DS answer file not found: {answer_file_path}")

    encodings_to_try = ["utf-8", "latin-1"]

    for encoding in encodings_to_try:
        try:
            with open(answer_file_path, encoding=encoding) as f:
                lines = f.readlines()

            non_empty_lines = [line.strip() for line in lines if line.strip()]

            if len(non_empty_lines) < 2:
                raise ValueError(f"TPC-DS answer file has insufficient lines: {answer_file_path}")

            row_count = len(non_empty_lines) - 2

            if encoding != "utf-8":
                logger.debug(f"Used {encoding} encoding for {answer_file_path.name}")

            logger.debug(f"Parsed TPC-DS answer file {answer_file_path.name}: {row_count} rows")
            return row_count

        except UnicodeDecodeError:
            if encoding == encodings_to_try[-1]:
                logger.error(f"Failed to decode TPC-DS answer file {answer_file_path} with any encoding")
                raise
            continue
        except Exception as e:
            logger.error(f"Failed to parse TPC-DS answer file {answer_file_path}: {e}")
            raise

    raise RuntimeError(f"Unexpected: no encoding worked for {answer_file_path}")


@dataclass(frozen=True)
class TpcdsAnswerBlock:
    columns: tuple[str, ...]
    rows: tuple[tuple[str | None, ...], ...]


_SEPARATOR_LINE = re.compile(r"[-\s|+]*--[-\s|+]*")

_NOISE_LINE = re.compile(
    r"\s*(\(\d+ rows?\)|\d+ rows? selected\.?|\d+ record\(s\) selected\.?|Warning:.*|SQL>.*|INSERT \d+ \d+|-+ OUTPUT Query \d+)\s*",
    re.IGNORECASE,
)
_TAB_STOP = 8


_MAX_COLUMN_DRIFT = 2
_FIELD = re.compile(r"\S+(?: \S+)*")


def _read_answer_text(answer_file_path: Path) -> str:
    raw = answer_file_path.read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _cells_from_pipes(line: str) -> tuple[str | None, ...]:
    return tuple((cell.strip() or None) for cell in line.split("|"))


def _cells_from_spans(line: str, spans: list[tuple[int, int]], source: str) -> tuple[str | None, ...]:
    text = line.expandtabs(_TAB_STOP)
    cells: list[str | None] = []
    for index, (start, end) in enumerate(spans):
        left = 0 if index == 0 else start
        right = spans[index + 1][0] if index + 1 < len(spans) else len(text)
        if index + 1 < len(spans) and text[end : spans[index + 1][0]].strip():
            raise ValueError(f"{source}: text falls between columns {index + 1} and {index + 2}: {line.strip()[:80]!r}")
        cells.append(text[left:right].strip() or None)
    return tuple(cells)


def _cells_from_drifting_spans(line: str, spans: list[tuple[int, int]], source: str) -> tuple[str | None, ...]:
    text = line.expandtabs(_TAB_STOP)
    fields = list(_FIELD.finditer(text))
    if len(fields) != len(spans):
        raise ValueError(f"{source}: found {len(fields)} fields for {len(spans)} columns: {line.strip()[:80]!r}")
    last = len(spans) - 1
    for index, (field, (start, end)) in enumerate(zip(fields, spans)):
        if field.start() < start - _MAX_COLUMN_DRIFT or (index < last and field.end() > end + _MAX_COLUMN_DRIFT):
            raise ValueError(f"{source}: field {index + 1} is not in its column: {line.strip()[:80]!r}")
    return tuple(field.group() for field in fields)


def _header_from_spans(line: str, spans: list[tuple[int, int]], source: str) -> tuple[str | None, ...]:
    try:
        return _cells_from_spans(line, spans, source)
    except ValueError:
        words = tuple(line.split())
        if len(words) != len(spans):
            raise
        return words


def parse_tpcds_answer_values(answer_file_path: Path) -> tuple[TpcdsAnswerBlock, ...]:
    if not answer_file_path.exists():
        raise FileNotFoundError(f"TPC-DS answer file not found: {answer_file_path}")
    source = answer_file_path.name
    lines = _read_answer_text(answer_file_path).splitlines()
    blocks: list[TpcdsAnswerBlock] = []

    def is_data(index: int) -> bool:
        return bool(lines[index].strip()) and not _NOISE_LINE.fullmatch(lines[index])

    separators = [i for i, line in enumerate(lines) if _SEPARATOR_LINE.fullmatch(line) and line.strip()]
    if not separators:
        data = [i for i in range(len(lines)) if is_data(i)]
        if not data or "|" not in lines[data[0]]:
            raise ValueError(f"{source}: no separator line and no pipe-delimited header")
        header = _cells_from_pipes(lines[data[0]])
        starts = [data[0]]
        for previous, current in zip(data, data[1:]):
            if current > previous + 1 and _cells_from_pipes(lines[current]) == header:
                starts.append(current)
        for number, (start, end) in enumerate(zip(starts, [*starts[1:], len(lines)]), start=1):
            rows = tuple(_cells_from_pipes(lines[i]) for i in range(start + 1, end) if is_data(i))
            try:
                _check_widths(source, header, rows)
            except ValueError as exc:
                raise ValueError(f"{exc} (result set {number} of {len(starts)}, header at line {start + 1})") from exc
            blocks.append(TpcdsAnswerBlock(tuple(name or "" for name in header), rows))
        return tuple(blocks)

    for number, separator in enumerate(separators, start=1):
        try:
            blocks.append(_parse_separated_block(lines, separator, source, is_data))
        except ValueError as exc:
            raise ValueError(
                f"{exc} (result set {number} of {len(separators)}, separator at line {separator + 1})"
            ) from exc
    return tuple(blocks)


def _parse_separated_block(lines: list[str], separator: int, source: str, is_data) -> TpcdsAnswerBlock:
    header_index = next((i for i in range(separator - 1, -1, -1) if is_data(i)), None)
    if header_index is None:
        raise ValueError(f"{source}: separator at line {separator + 1} has no header")
    end = separator + 1
    while end < len(lines) and is_data(end):
        end += 1
    if "|" in lines[separator]:
        header = _cells_from_pipes(lines[header_index])
        rows = tuple(_cells_from_pipes(lines[i]) for i in range(separator + 1, end))
    else:
        spans = [(m.start(), m.end()) for m in re.finditer(r"-+", lines[separator])]
        header = _header_from_spans(lines[header_index], spans, source)
        try:
            rows = tuple(_cells_from_spans(lines[i], spans, source) for i in range(separator + 1, end))
        except ValueError as exact_error:
            if not _MAX_COLUMN_DRIFT:
                raise
            try:
                rows = tuple(_cells_from_drifting_spans(lines[i], spans, source) for i in range(separator + 1, end))
            except ValueError:
                raise exact_error from None
    _check_widths(source, header, rows)
    return TpcdsAnswerBlock(tuple(name or "" for name in header), rows)


def _check_widths(source: str, header: tuple[str | None, ...], rows: tuple[tuple[str | None, ...], ...]) -> None:
    for number, row in enumerate(rows, start=1):
        if len(row) != len(header):
            raise ValueError(f"{source}: row {number} has {len(row)} cells but the header has {len(header)}")


def _find_tpch_answers_dir() -> Path:
    from benchbox.core.expected_results.download import (
        download_tpch_answers,
        get_tpch_cache_dir,
        is_download_disabled,
    )

    try:
        import benchbox

        package_root = Path(benchbox.__file__).parent.parent
        src_dir = package_root / "_sources" / "tpc-h" / "dbgen" / "answers"
        if src_dir.exists():
            return src_dir
    except (AttributeError, TypeError):
        pass

    cache_dir = get_tpch_cache_dir()
    if cache_dir.exists() and any(cache_dir.glob("q*.out")):
        return cache_dir

    if not is_download_disabled():
        logger.info("TPC-H answer files not found locally; attempting on-demand download...")
        result = download_tpch_answers()
        if result is not None and result.exists():
            return result

    raise FileNotFoundError(
        "TPC-H answer files not found. "
        "They are included in source distributions but not in wheel installs. "
        "To download them: benchbox download-answers --benchmark tpch"
    )


def load_tpch_expected_results(scale_factor: float = 1.0) -> dict[str, int]:
    if scale_factor != 1.0:
        raise ValueError(
            f"TPC-H expected results are only available for scale factor 1.0. "
            f"Requested: {scale_factor}. "
            f"To use other scale factors, disable validation with validate_results=False."
        )

    answers_dir = _find_tpch_answers_dir()

    expected_results = {}
    for query_num in range(1, 23):
        answer_file = answers_dir / f"q{query_num}.out"
        if answer_file.exists():
            try:
                row_count = parse_tpch_answer_file(answer_file)
                expected_results[str(query_num)] = row_count
            except Exception as e:
                logger.warning(f"Failed to parse TPC-H answer file for query {query_num}: {e}")
        else:
            logger.warning(f"TPC-H answer file not found for query {query_num}: {answer_file}")

    logger.info(f"Loaded expected results for {len(expected_results)} TPC-H queries at SF={scale_factor}")
    return expected_results


def load_tpch_value_digest_seed() -> int | None:
    payload = json.loads(_TPCH_VALUE_DIGEST_REFERENCE_PATH.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("benchmark") != "tpch"
        or type(payload.get("scale_factor")) not in (int, float)
        or payload["scale_factor"] != 1.0
    ):
        raise ValueError("TPC-H value-digest reference must describe the SF=1 TPC-H snapshot")
    if "reference_seed" not in payload:
        raise ValueError("TPC-H value-digest reference must record reference_seed (an integer or null)")
    seed = payload["reference_seed"]
    if seed is not None and type(seed) is not int:
        raise ValueError("TPC-H value-digest reference_seed must be an integer or null")
    return seed


def load_tpch_value_digests(scale_factor: float = 1.0) -> dict[str, str]:
    if scale_factor != 1.0:
        return {}

    digest_file = _TPCH_VALUE_DIGEST_REFERENCE_PATH
    if not digest_file.exists():
        logger.warning("TPC-H value-digest reference file not found: %s", digest_file)
        return {}

    try:
        payload = json.loads(digest_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Failed to read TPC-H value-digest reference file %s: %s", digest_file, exc)
        return {}

    digests = payload.get("digests", {})
    return {str(query_id): str(digest) for query_id, digest in digests.items()}


def _find_tpcds_answers_dir() -> Path:
    from benchbox.core.expected_results.download import (
        download_tpcds_answers,
        get_tpcds_cache_dir,
        is_download_disabled,
    )

    try:
        import benchbox

        package_root = Path(benchbox.__file__).parent.parent
        src_dir = package_root / "_sources" / "tpc-ds" / "answer_sets"
        if src_dir.exists():
            return src_dir
    except (AttributeError, TypeError):
        pass

    cache_dir = get_tpcds_cache_dir()
    if cache_dir.exists() and any(cache_dir.glob("*.ans")):
        return cache_dir

    if not is_download_disabled():
        logger.info("TPC-DS answer files not found locally; attempting on-demand download...")
        result = download_tpcds_answers()
        if result is not None and result.exists():
            return result

    raise FileNotFoundError(
        "TPC-DS answer files not found. "
        "They are included in source distributions but not in wheel installs. "
        "To download them: benchbox download-answers --benchmark tpcds"
    )


def load_tpcds_expected_results(scale_factor: float = 1.0) -> dict[str, int]:
    if scale_factor != 1.0:
        raise ValueError(
            f"TPC-DS expected results are only available for scale factor 1.0. "
            f"Requested: {scale_factor}. "
            f"To use other scale factors, disable validation with validate_results=False."
        )

    answers_dir = _find_tpcds_answers_dir()

    expected_results = {}

    for query_num in range(1, 100):
        answer_file = answers_dir / f"{query_num}.ans"

        if answer_file.exists():
            try:
                row_count = parse_tpcds_answer_file(answer_file)
                expected_results[str(query_num)] = row_count
            except Exception as e:
                logger.warning(f"Failed to parse TPC-DS answer file for query {query_num}: {e}")
        else:
            answer_file_variant = answers_dir / f"{query_num}_NULLS_FIRST.ans"
            if answer_file_variant.exists():
                try:
                    row_count = parse_tpcds_answer_file(answer_file_variant)
                    expected_results[str(query_num)] = row_count
                    logger.debug(f"Using NULLS_FIRST variant for TPC-DS query {query_num}")
                except Exception as e:
                    logger.warning(f"Failed to parse TPC-DS answer file variant for query {query_num}: {e}")

    MULTI_PART_QUERIES = {
        "14": ["14a", "14b"],
        "23": ["23a", "23b"],
    }

    for base_query, variants in MULTI_PART_QUERIES.items():
        if base_query in expected_results:
            base_count = expected_results[base_query]
            for variant in variants:
                expected_results[variant] = base_count
                logger.debug(
                    f"Registered TPC-DS variant {variant} with base query {base_query} expected row count: {base_count}"
                )

    logger.info(f"Loaded expected results for {len(expected_results)} TPC-DS queries at SF={scale_factor}")
    return expected_results


def load_tpcds_answer_values(
    scale_factor: float = 1.0, null_order: str = "first"
) -> dict[str, tuple[TpcdsAnswerBlock, ...]]:
    if scale_factor != 1.0:
        raise ValueError(f"TPC-DS answer sets are only available for scale factor 1.0. Requested: {scale_factor}.")
    if null_order not in ("first", "last"):
        raise ValueError(f"null_order must be 'first' or 'last', not {null_order!r}")

    answers_dir = _find_tpcds_answers_dir()
    suffix = f"_NULLS_{null_order.upper()}"
    answers: dict[str, tuple[TpcdsAnswerBlock, ...]] = {}
    for query_num in range(1, 100):
        for name in (f"{query_num}.ans", f"{query_num}{suffix}.ans"):
            answer_file = answers_dir / name
            if not answer_file.exists():
                continue
            try:
                answers[str(query_num)] = parse_tpcds_answer_values(answer_file)
            except ValueError as exc:
                raise ValueError(f"Cannot parse TPC-DS answer file {answer_file}: {exc}") from exc
            break
    return answers
