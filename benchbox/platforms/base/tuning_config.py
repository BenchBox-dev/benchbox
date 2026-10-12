from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from benchbox.core.tuning.interface import UnifiedTuningConfiguration


def apply_standard_unified_tuning(adapter: Any, unified_config: UnifiedTuningConfiguration, connection: Any) -> None:
    if not unified_config:
        return

    from benchbox.core.tuning.applied_ledger import PHASE_DDL, recording_connection

    recording = recording_connection(
        connection,
        getattr(adapter, "_applied_tuning_ledger", None),
        PHASE_DDL,
        execute_verbs=getattr(adapter, "ledger_execute_verbs", ()),
    )

    adapter.apply_constraint_configuration(unified_config.primary_keys, unified_config.foreign_keys, recording)
    if unified_config.platform_optimizations:
        adapter.apply_platform_optimizations(unified_config.platform_optimizations, recording)
    for _table_name, table_tuning in unified_config.table_tunings.items():
        adapter.apply_table_tunings(table_tuning, recording)


class TuningConfigMixin:
    platform_name: str
    canonical_platform_type: str
    logger: logging.Logger
    tuning_enabled: bool
    _tuning_marker_save_failed: bool

    def apply_unified_tuning(self, unified_config: UnifiedTuningConfiguration, connection: Any) -> None:
        if unified_config:
            self.log_verbose(f"Unified tuning not implemented for {self.platform_name} - using base class no-op")
        else:
            self.log_very_verbose("No unified tuning configuration provided")
        return None

    def get_effective_tuning_configuration(self) -> UnifiedTuningConfiguration | None:
        return getattr(self, "unified_tuning_configuration", None)

    def validate_tuning_configuration_for_platform(self) -> list[str]:
        effective_config = self.get_effective_tuning_configuration()
        if not effective_config:
            return []

        return effective_config.validate_for_platform(self.canonical_platform_type)

    def validate_tuning_configuration(self, unified_config: UnifiedTuningConfiguration) -> list[str]:
        if not unified_config:
            return []

        return unified_config.validate_for_platform(self.canonical_platform_type)

    def _validate_database_tunings(self, **connection_config):
        try:
            from benchbox.core.tuning.metadata import (
                MetadataValidationResult,
                TuningMetadataManager,
            )

            self._validating_database = True
            temp_connection = None
            if hasattr(self, "_create_direct_connection"):
                temp_connection = self._create_direct_connection(**connection_config)
            else:
                temp_connection = self.create_connection(**connection_config)

            try:
                metadata_manager = TuningMetadataManager(
                    self, connection_config=connection_config, connection=temp_connection
                )

                effective_config = self.get_effective_tuning_configuration()
                if effective_config and self.tuning_enabled:
                    result = metadata_manager.validate_unified_tunings(effective_config)
                else:
                    existing_tunings = metadata_manager.load_unified_tunings()
                    result = MetadataValidationResult()
                    if metadata_manager.last_load_error:
                        result.add_error(f"Failed to load tuning metadata: {metadata_manager.last_load_error}")
                    elif existing_tunings is not None or self._has_tuned_run_marker(metadata_manager):
                        result.add_warning("Database contains tuning metadata but no tunings expected")
                        if not self.tuning_enabled:
                            result.add_error(
                                "Refusing to reuse a tuned database for a notuning run; recreate the database first"
                            )
                self._drift_validation_result = result
                return result

            finally:
                self.close_connection(temp_connection)
                self._validating_database = False

        except Exception as e:
            from benchbox.core.tuning.metadata import MetadataValidationResult

            self._validating_database = False
            result = MetadataValidationResult()
            result.add_error(f"Failed to validate database tunings: {e}")
            self._drift_validation_result = result
            return result

    @staticmethod
    def _has_tuned_run_marker(metadata_manager: Any) -> bool:
        probe = getattr(metadata_manager, "has_tuned_run_marker", None)
        if not callable(probe):
            return False
        try:
            return bool(probe())
        except Exception:
            return False

    def ensure_tuned_run_marker(self, connection: Any) -> bool:
        effective_config = self.get_effective_tuning_configuration()
        if not self.tuning_enabled or not effective_config:
            return True

        try:
            from benchbox.core.tuning.metadata import TuningMetadataManager

            metadata_manager = TuningMetadataManager(self, connection=connection)
            return bool(metadata_manager.write_tuned_run_marker())

        except Exception as e:
            self.logger.error(f"Failed to write tuned-run marker: {e}")
            return False

    def save_tuning_metadata(self, connection: Any) -> bool:
        self._tuning_marker_save_failed = False
        effective_config = self.get_effective_tuning_configuration()
        if not self.tuning_enabled or not effective_config:
            return True

        try:
            from benchbox.core.tuning.metadata import TuningMetadataManager

            metadata_manager = TuningMetadataManager(self, connection=connection)
            saved = metadata_manager.save_unified_tunings(effective_config)
            self._tuning_marker_save_failed = bool(metadata_manager.marker_save_failed)
            if metadata_manager.marker_save_failed:
                from benchbox.core.tuning.metadata import MetadataValidationResult

                marker_result = MetadataValidationResult()
                marker_result.add_warning(
                    "Tuning metadata section markers were not saved; future drift checks will report reduced coverage"
                )
                self._drift_validation_result = marker_result
            return saved

        except Exception as e:
            self.logger.error(f"Failed to save tuning metadata: {e}")
            return False
