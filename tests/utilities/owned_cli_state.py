from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager

OWNED_ENV_KEYS = ("BENCHBOX_NON_INTERACTIVE", "BENCHBOX_DATA_ORGANIZATION_CONFIG_JSON")

CLI_CONFIGURED_LOGGERS = (
    "benchbox",
    "benchbox.cli",
    "benchbox.platforms",
    "benchbox.core",
    "benchbox.utils",
    "urllib3",
    "requests",
    "py4j",
    "py4j.java_gateway",
    "py4j.clientserver",
    "pyspark",
    "pyspark.sql",
    "sqlalchemy",
)

LoggingSnapshot = tuple[int, list[logging.Handler], dict[str, int]]


def snapshot_logging() -> LoggingSnapshot:
    root = logging.getLogger()
    return (
        root.level,
        list(root.handlers),
        {name: logging.getLogger(name).level for name in CLI_CONFIGURED_LOGGERS},
    )


def restore_logging(snapshot: LoggingSnapshot) -> None:
    level, handlers, logger_levels = snapshot
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        if handler not in handlers:
            root.removeHandler(handler)
    for handler in handlers:
        if handler not in root.handlers:
            root.addHandler(handler)
    for name, logger_level in logger_levels.items():
        logging.getLogger(name).setLevel(logger_level)


@contextmanager
def owned_cli_state() -> Iterator[None]:
    import benchbox.utils.config_interface as config_interface
    import benchbox.utils.printing as printing

    quiet = printing._QUIET
    provider = config_interface._config_provider
    logging_state = snapshot_logging()
    environment = {key: os.environ.get(key) for key in OWNED_ENV_KEYS}
    try:
        yield
    finally:
        printing._QUIET = quiet
        config_interface._config_provider = provider
        restore_logging(logging_state)
        for key, value in environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
