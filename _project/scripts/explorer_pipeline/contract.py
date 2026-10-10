from __future__ import annotations

EXPLORER_BUILD_CONTRACT_VERSION = "6"
EXPLORER_READ_MODEL_VERSION = 15
EXPLORER_READ_MODEL_COMPATIBILITY = {
    "minimum_supported": EXPLORER_READ_MODEL_VERSION,
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
    "EXPLORER_READ_MODEL_COMPATIBILITY",
    "EXPLORER_READ_MODEL_VERSION",
]
