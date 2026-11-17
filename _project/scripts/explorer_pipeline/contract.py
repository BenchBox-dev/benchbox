from __future__ import annotations

EXPLORER_BUILD_CONTRACT_VERSION = "6"
EXPLORER_MINIMUM_READ_MODEL_VERSION = 17
EXPLORER_TRANSLATOR_READ_MODEL_VERSION = 18
EXPLORER_HARDWARE_READ_MODEL_VERSION = 19
EXPLORER_READ_MODEL_VERSION = EXPLORER_HARDWARE_READ_MODEL_VERSION
SELECTABLE_READ_MODEL_VERSIONS = (
    EXPLORER_MINIMUM_READ_MODEL_VERSION,
    EXPLORER_TRANSLATOR_READ_MODEL_VERSION,
    EXPLORER_READ_MODEL_VERSION,
)
TRANSLATOR_RESULT_COLUMNS = (
    ("translator_version", "VARCHAR"),
    ("translator_fork_sha", "VARCHAR"),
    ("translator_version_source", "VARCHAR"),
)
HARDWARE_RESULT_COLUMNS = (
    ("hardware_class", "VARCHAR"),
    ("hardware_score", "DOUBLE"),
    ("hardware_basis", "VARCHAR"),
    ("hardware_table_version", "VARCHAR"),
    ("service_size", "VARCHAR"),
    ("known_defect_reason", "VARCHAR"),
    ("benchbox_version", "VARCHAR"),
    ("benchbox_version_basis", "VARCHAR"),
)
EXPLORER_READ_MODEL_COMPATIBILITY = {
    "minimum_supported": EXPLORER_MINIMUM_READ_MODEL_VERSION,
    "newer_policy": "warn-and-continue",
}

EXPLORER_BUILD_CONTRACT = {
    "version": EXPLORER_BUILD_CONTRACT_VERSION,
    "read_model_version": EXPLORER_READ_MODEL_VERSION,
    "read_model_compatibility": EXPLORER_READ_MODEL_COMPATIBILITY,
    "command": "uv run -- python _project/scripts/explorer_publish.py build",
    "flags": [
        "--data-dir",
        "--output",
        "--trust-label",
        "--visibility",
    ],
    "outputs": {
        "required": [
            "results.duckdb",
            "bundles/{result_id}.json",
        ],
        "removed_legacy": [
            "manifest.json",
            "benchmarks/",
            "details/",
            "compare/",
            "meta_leaderboard.json",
            "short_ids.json",
            "results_schema.json",
        ],
    },
}

__all__ = [
    "EXPLORER_BUILD_CONTRACT",
    "EXPLORER_BUILD_CONTRACT_VERSION",
    "EXPLORER_MINIMUM_READ_MODEL_VERSION",
    "EXPLORER_READ_MODEL_COMPATIBILITY",
    "EXPLORER_READ_MODEL_VERSION",
    "EXPLORER_HARDWARE_READ_MODEL_VERSION",
    "HARDWARE_RESULT_COLUMNS",
    "EXPLORER_TRANSLATOR_READ_MODEL_VERSION",
    "SELECTABLE_READ_MODEL_VERSIONS",
    "TRANSLATOR_RESULT_COLUMNS",
]
