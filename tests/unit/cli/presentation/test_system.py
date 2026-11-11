from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from benchbox.cli.presentation.system import display_system_recommendations

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.mark.parametrize(
    ("profile", "expected_messages"),
    [
        (
            SimpleNamespace(memory_total_gb=64, cpu_cores_logical=16),
            [
                "High-memory system detected",
                "Multi-core system",
                "Optimal Setup",
            ],
        ),
        (
            SimpleNamespace(memory_total_gb=16, cpu_cores_logical=4),
            [
                "Mid-range system detected",
                "Quad-core system",
                "Good Setup",
            ],
        ),
        (
            SimpleNamespace(memory_total_gb=4, cpu_cores_logical=2),
            [
                "Limited memory system",
                "Limited cores",
                "Basic Setup",
            ],
        ),
    ],
)
def test_display_system_recommendations_covers_resource_bands(profile, expected_messages) -> None:
    with patch("benchbox.cli.presentation.system.console.print") as mock_print:
        display_system_recommendations(profile)

    rendered = " ".join(str(call.args[0]) for call in mock_print.call_args_list if call.args)
    for message in expected_messages:
        assert message in rendered


