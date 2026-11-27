# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from typing import Any

from benchbox.core.deployment import DEPLOYMENT_SELECTOR_OPTION
from benchbox.platforms.clickhouse import ClickHouseAdapter


class ClickHouseServerAdapter(ClickHouseAdapter):
    plan_capture_phase_eligible = True
    default_service_port = 9000

    def __init__(self, **config: Any) -> None:
        config[DEPLOYMENT_SELECTOR_OPTION] = "server"
        super().__init__(**config)
        self.deployment_selected = "self-hosted"
        self.deployment_selected_class = "self-hosted"

    @property
    def platform_name(self) -> str:
        return "ClickHouse Server"

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> ClickHouseServerAdapter:
        config = dict(config)
        config[DEPLOYMENT_SELECTOR_OPTION] = "server"
        return super().from_config(config)


__all__ = ["ClickHouseServerAdapter"]
