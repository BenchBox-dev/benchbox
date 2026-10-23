# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
import re
from typing import Any

from benchbox.core.exceptions import ConfigurationError

logger = logging.getLogger(__name__)


def empty_cache_control_receipt() -> dict[str, Any]:
    return {
        "validated": False,
        "cache_disabled": False,
        "settings": {},
        "warnings": [],
        "errors": [],
    }


def sanitize_cache_control_receipt(receipt: Any) -> dict[str, Any] | None:
    if not isinstance(receipt, dict):
        return None
    clean = empty_cache_control_receipt()
    clean["validated"] = receipt.get("validated") is True
    clean["cache_disabled"] = receipt.get("cache_disabled") is True
    settings = receipt.get("settings")
    if isinstance(settings, dict):
        clean["settings"] = {str(k): str(v) for k, v in settings.items()}
    for key in ("warnings", "errors"):
        messages = receipt.get(key)
        if isinstance(messages, list):
            clean[key] = [str(m) for m in messages]
    return clean


def explicit_cache_enabled_receipt(setting_key: str, enabled_value: str) -> dict[str, Any]:
    receipt = empty_cache_control_receipt()
    receipt["validated"] = True
    receipt["settings"] = {setting_key: enabled_value}
    receipt["warnings"] = [
        "result cache explicitly left enabled (disable_result_cache=False); "
        "timings measured under an enabled cache are not comparable clean evidence"
    ]
    return receipt


def validate_session_cache_control(
    *,
    connection: Any,
    query: str,
    setting_key: str,
    disabled_value: str,
    enabled_value: str,
    normalize: str,
    platform_name: str,
    disable_result_cache: bool,
    strict_validation: bool,
    adapter_logger: logging.Logger | None = None,
    value_column_index: int = 0,
) -> dict[str, Any]:
    log = adapter_logger or logger
    cursor = connection.cursor()
    result: dict[str, Any] = empty_cache_control_receipt()

    try:
        cursor.execute(query)
        row = cursor.fetchone()

        if row:
            raw = str(row[value_column_index])
            actual_value = raw.lower() if normalize == "lower" else raw.upper()
            result["settings"][setting_key] = actual_value

            expected_value = disabled_value if disable_result_cache else enabled_value

            if actual_value == expected_value:
                result["validated"] = True
                result["cache_disabled"] = actual_value == disabled_value
                log.debug(f"Cache control validated: {setting_key}={actual_value} (expected {expected_value})")
            else:
                error_msg = (
                    f"Cache control validation failed: expected {setting_key}={expected_value}, got {actual_value}"
                )
                result["errors"].append(error_msg)
                log.error(error_msg)

                if strict_validation:
                    raise ConfigurationError(
                        f"{platform_name} session cache control validation failed - "
                        "benchmark results may be incorrect due to cached query results",
                        details=result,
                    )
        else:
            warning_msg = f"Could not retrieve {setting_key} parameter from session"
            result["warnings"].append(warning_msg)
            log.warning(warning_msg)

    except ConfigurationError:
        raise
    except Exception as e:
        error_msg = f"Validation query failed: {e}"
        result["errors"].append(error_msg)
        log.error(f"Cache control validation error: {e}")

        if strict_validation:
            raise ConfigurationError(
                f"Failed to validate {platform_name} cache control settings",
                details={"original_error": str(e), "validation_result": result},
            ) from e
    finally:
        cursor.close()

    return result


_LEADING_SQL_COMMENTS_RE = re.compile(r"\A(?:\s|--[^\n]*(?:\n|\Z)|/\*.*?\*/)*", re.DOTALL)


def split_leading_sql_comments(statement: str) -> tuple[str, str]:
    match = _LEADING_SQL_COMMENTS_RE.match(statement)
    prefix = match.group(0) if match else ""
    return prefix, statement[len(prefix) :]
