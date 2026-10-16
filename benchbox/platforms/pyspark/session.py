# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

import atexit
import logging
import os
import subprocess
import sys
import threading
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from benchbox.core.results.platform_options import sanitize_platform_options
from benchbox.platforms._spark_helpers import spark_aqe_conf_entries
from benchbox.platforms.base.spark_logging import suppress_window_exec_warning
from benchbox.utils.dependencies import get_package_install_message

try:
    import pyspark
    from pyspark.sql import SparkSession

    PYSPARK_AVAILABLE = True
    PYSPARK_VERSION = pyspark.__version__
except ImportError:
    PYSPARK_AVAILABLE = False
    PYSPARK_VERSION = None
    pyspark = None
    SparkSession = Any

logger = logging.getLogger(__name__)

SUPPORTED_JAVA_VERSIONS = frozenset({17, 21})
MAX_SUPPORTED_JAVA_VERSION = 22

BENCHBOX_JAVA_HOME_ENV = "BENCHBOX_JAVA_HOME"


class SparkSessionError(RuntimeError):
    pass


class SparkUnavailableError(SparkSessionError):
    pass


class SparkConfigurationError(SparkSessionError):
    pass


class JavaVersionError(SparkSessionError):
    pass


@dataclass(frozen=True)
class SparkSessionConfig:
    master: str
    app_name: str
    driver_memory: str | None
    executor_memory: str | None
    shuffle_partitions: int | None
    enable_aqe: bool | None
    extra_configs: tuple[tuple[str, str], ...]
    verbose: bool = False


def _normalize_extra_configs(extra: Mapping[str, Any] | None) -> tuple[tuple[str, str], ...]:
    if not extra:
        return ()
    normalized: list[tuple[str, str]] = []
    for key, value in extra.items():
        normalized.append((str(key), str(value)))
    normalized.sort(key=lambda item: item[0])
    return tuple(normalized)


def get_effective_java_home() -> str | None:
    return os.environ.get(BENCHBOX_JAVA_HOME_ENV) or os.environ.get("JAVA_HOME")


def detect_java_version(java_home: str | None = None) -> int | None:
    if java_home is None:
        java_home = get_effective_java_home()

    java_path = Path(java_home, "bin", "java") if java_home else Path("java")

    try:
        result = subprocess.run(
            [str(java_path), "-version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.SubprocessError, FileNotFoundError, PermissionError):
        return None

    version_line = (result.stderr or result.stdout).splitlines()
    if not version_line:
        return None
    line = version_line[0]
    if '"' not in line:
        return None
    version_str = line.split('"')[1]
    major = version_str.split(".")[0]
    try:
        return int(major)
    except ValueError:
        return None


def find_supported_java_home() -> tuple[str, int] | None:
    helper = Path("/usr/libexec/java_home")
    if sys.platform != "darwin" or not helper.exists():
        return None

    for version in sorted(SUPPORTED_JAVA_VERSIONS, reverse=True):
        try:
            result = subprocess.run(
                [str(helper), "-v", str(version)],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )
        except (subprocess.SubprocessError, FileNotFoundError):
            continue

        candidate = result.stdout.strip()
        if candidate:
            return candidate, version

    return None


def is_java_compatible(version: int | None = None) -> bool:
    if version is None:
        version = detect_java_version()
    return version in SUPPORTED_JAVA_VERSIONS


def ensure_compatible_java() -> tuple[int | None, str | None]:
    benchbox_java_home = os.environ.get(BENCHBOX_JAVA_HOME_ENV)
    if benchbox_java_home:
        version = detect_java_version(benchbox_java_home)
        if is_java_compatible(version):
            os.environ["JAVA_HOME"] = benchbox_java_home
            return version, benchbox_java_home
        logger.warning(
            "%s=%s points to Java %s which is not compatible with PySpark 4.x",
            BENCHBOX_JAVA_HOME_ENV,
            benchbox_java_home,
            version,
        )
        return version, benchbox_java_home

    current_java_home = os.environ.get("JAVA_HOME")
    if current_java_home:
        version = detect_java_version(current_java_home)
        if is_java_compatible(version):
            return version, current_java_home

    version = detect_java_version(None)
    if is_java_compatible(version):
        return version, None

    alternative = find_supported_java_home()
    if alternative:
        path, version = alternative
        os.environ["JAVA_HOME"] = path
        logger.info("Switching JAVA_HOME to %s (Java %s) for PySpark compatibility", path, version)
        return version, path

    return detect_java_version(), get_effective_java_home()


def get_java_skip_reason() -> str:
    if not PYSPARK_AVAILABLE:
        return "PySpark not installed"

    version, java_home = ensure_compatible_java()
    if is_java_compatible(version):
        return ""

    if version is None:
        return "Java not found"
    if version >= MAX_SUPPORTED_JAVA_VERSION + 1:
        return f"PySpark 4.x not compatible with Java {version}"
    return f"PySpark 4.x requires Java 17 or 21 (found Java {version})"


def _validate_java_version() -> None:
    version, _ = ensure_compatible_java()

    if is_java_compatible(version):
        return

    if version is None:
        raise JavaVersionError("Unable to determine Java version. PySpark SQL mode requires Java 17 or 21.")

    if version >= MAX_SUPPORTED_JAVA_VERSION + 1:
        raise JavaVersionError(f"Detected Java {version}. PySpark 4.x is not compatible with Java 23 or newer.")

    raise JavaVersionError(f"Detected Java {version}. Please install Java 17 or 21 for PySpark 4.x.")


class SparkSessionManager:
    _lock = threading.Lock()
    _session: SparkSession | None = None
    _config: SparkSessionConfig | None = None
    _refcount = 0
    _java_validated = False

    @classmethod
    def get_or_create(
        cls,
        *,
        master: str,
        app_name: str,
        driver_memory: str | None,
        executor_memory: str | None,
        shuffle_partitions: int | None,
        enable_aqe: bool | None,
        extra_configs: Mapping[str, Any] | None = None,
        verbose: bool = False,
    ) -> SparkSession:
        if not PYSPARK_AVAILABLE:
            raise SparkUnavailableError(get_package_install_message("pyspark pyarrow", "PySpark is not installed."))

        config = SparkSessionConfig(
            master=master,
            app_name=app_name,
            driver_memory=driver_memory,
            executor_memory=executor_memory,
            shuffle_partitions=shuffle_partitions,
            enable_aqe=enable_aqe,
            extra_configs=_normalize_extra_configs(extra_configs),
            verbose=verbose,
        )

        with cls._lock:
            if cls._session is None:
                logger.debug(
                    "Creating SparkSession with config: %s",
                    replace(
                        config,
                        extra_configs=tuple(sorted(sanitize_platform_options(dict(config.extra_configs)).items())),
                    ),
                )
                if not cls._java_validated:
                    _validate_java_version()
                    cls._java_validated = True
                cls._session = cls._create_session(config)
                cls._config = config
            else:
                cls._ensure_compatible_config(config)

            cls._refcount += 1
            return cls._session

    @classmethod
    def release(cls) -> None:
        with cls._lock:
            if cls._refcount == 0:
                return
            cls._refcount -= 1
            if cls._refcount == 0:
                cls._stop_session()

    @classmethod
    def close(cls) -> None:
        with cls._lock:
            cls._refcount = 0
            cls._stop_session()

    @classmethod
    def _ensure_compatible_config(cls, config: SparkSessionConfig) -> None:
        if cls._config == config:
            return
        raise SparkConfigurationError(
            "SparkSession already created with a different configuration. "
            "Ensure all PySpark adapters share the same Spark settings."
        )

    @classmethod
    def _create_session(cls, config: SparkSessionConfig) -> SparkSession:
        cls._configure_spark_logging(config.verbose)

        log4j_config_name = "log4j2-verbose.properties" if config.verbose else "log4j2-quiet.properties"
        log4j_config_path = Path(__file__).parent / log4j_config_name
        log4j_opts = f"-Dlog4j.configurationFile=file:{log4j_config_path}"

        user_java_opts = dict(config.extra_configs).get("spark.driver.extraJavaOptions", "")
        if user_java_opts:
            log4j_opts = f"{log4j_opts} {user_java_opts}"

        builder = (
            SparkSession.builder.master(config.master)
            .appName(config.app_name)
            .config("spark.driver.extraJavaOptions", log4j_opts)
            .config("spark.sql.inMemoryColumnarStorage.enabled", "false")
        )

        if config.driver_memory:
            builder = builder.config("spark.driver.memory", config.driver_memory)
        if config.executor_memory:
            builder = builder.config("spark.executor.memory", config.executor_memory)
        if config.shuffle_partitions is not None:
            builder = builder.config("spark.sql.shuffle.partitions", str(config.shuffle_partitions))

        if config.enable_aqe is not None:
            for aqe_key, aqe_value in spark_aqe_conf_entries(config.enable_aqe).items():
                builder = builder.config(aqe_key, aqe_value)

        for key, value in config.extra_configs:
            if key != "spark.driver.extraJavaOptions":
                builder = builder.config(key, value)

        session = builder.getOrCreate()

        spark_log_level = "WARN" if config.verbose else "ERROR"
        session.sparkContext.setLogLevel(spark_log_level)

        suppress_window_exec_warning(session)

        logger.info("SparkSession created with master=%s app=%s", config.master, config.app_name)
        return session

    @classmethod
    def _configure_spark_logging(cls, verbose: bool) -> None:
        try:
            import logging

            py4j_logger = logging.getLogger("py4j")
            py4j_logger.setLevel(logging.ERROR if not verbose else logging.WARNING)
        except Exception:
            pass

    @classmethod
    def _stop_session(cls) -> None:
        if cls._session is None:
            return
        try:
            logger.info("Stopping shared SparkSession")
            cls._session.stop()
        finally:
            cls._session = None
            cls._config = None
            cls._java_validated = False


atexit.register(SparkSessionManager.close)


__all__ = [
    "PYSPARK_AVAILABLE",
    "PYSPARK_VERSION",
    "SUPPORTED_JAVA_VERSIONS",
    "detect_java_version",
    "find_supported_java_home",
    "is_java_compatible",
    "ensure_compatible_java",
    "get_java_skip_reason",
    "get_effective_java_home",
    "SparkSessionManager",
    "suppress_window_exec_warning",
    "SparkSessionError",
    "SparkUnavailableError",
    "SparkConfigurationError",
    "JavaVersionError",
]
