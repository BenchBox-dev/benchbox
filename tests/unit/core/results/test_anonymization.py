# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.

import dataclasses
import inspect
import json
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock, mock_open, patch

import pytest

from benchbox.core.results.anonymization import (
    _IDENTIFIER_KEYS,
    PUBLIC_REDACTED_VALUE,
    AnonymizationConfig,
    AnonymizationManager,
    _is_public_pseudonym,
    anonymize_applied_tuning_payload,
    find_public_path_leaks,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestAnonymizationMachineID:
    def test_machine_id_is_string(self):
        manager = AnonymizationManager()
        machine_id = manager.get_anonymous_machine_id()

        assert isinstance(machine_id, str)
        assert machine_id.startswith("machine_")
        assert len(machine_id) == 24

    def test_machine_id_stability_within_instance(self):
        manager = AnonymizationManager()

        id1 = manager.get_anonymous_machine_id()
        id2 = manager.get_anonymous_machine_id()
        id3 = manager.get_anonymous_machine_id()

        assert id1 == id2 == id3

    def test_machine_id_stability_across_instances(self):
        manager1 = AnonymizationManager()
        manager2 = AnonymizationManager()
        manager3 = AnonymizationManager()

        id1 = manager1.get_anonymous_machine_id()
        id2 = manager2.get_anonymous_machine_id()
        id3 = manager3.get_anonymous_machine_id()

        assert id1 == id2 == id3, "Machine IDs should be identical across instances"

    def test_machine_id_uses_cache(self):
        manager = AnonymizationManager()

        assert manager._machine_id_cache is None
        id1 = manager.get_anonymous_machine_id()
        assert manager._machine_id_cache == id1

        id2 = manager.get_anonymous_machine_id()
        assert id2 == id1

    def test_machine_id_with_salt(self):
        config1 = AnonymizationConfig(machine_id_salt="salt1")
        config2 = AnonymizationConfig(machine_id_salt="salt2")
        config3 = AnonymizationConfig(machine_id_salt=None)

        manager1 = AnonymizationManager(config1)
        manager2 = AnonymizationManager(config2)
        manager3 = AnonymizationManager(config3)

        id1 = manager1.get_anonymous_machine_id()
        id2 = manager2.get_anonymous_machine_id()
        id3 = manager3.get_anonymous_machine_id()

        assert id1 != id2
        assert id1 != id3
        assert id2 != id3

    def test_machine_id_deterministic_with_same_salt(self):
        config1 = AnonymizationConfig(machine_id_salt="test_salt")
        config2 = AnonymizationConfig(machine_id_salt="test_salt")

        manager1 = AnonymizationManager(config1)
        manager2 = AnonymizationManager(config2)

        assert manager1.get_anonymous_machine_id() == manager2.get_anonymous_machine_id()


class TestMacOSPlatformUUID:
    @patch("platform.system")
    @patch("subprocess.run")
    def test_macos_platform_uuid_success(self, mock_run, mock_system):
        mock_system.return_value = "Darwin"
        mock_run.return_value = Mock(
            returncode=0,
            stdout="""
            {
              "IOPlatformUUID" = "F79092CB-6DA1-5604-BBD1-78EF17E58BEF"
              "IOPlatformSerialNumber" = "J6WP2C57KT"
            }
            """,
        )

        manager = AnonymizationManager()
        uuid = manager._get_macos_platform_uuid()

        assert uuid == "F79092CB-6DA1-5604-BBD1-78EF17E58BEF"
        mock_run.assert_called_once_with(
            ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )

    @patch("platform.system")
    @patch("subprocess.run")
    def test_macos_platform_uuid_command_failure(self, mock_run, mock_system):
        mock_system.return_value = "Darwin"
        mock_run.return_value = Mock(returncode=1, stdout="")

        manager = AnonymizationManager()
        uuid = manager._get_macos_platform_uuid()

        assert uuid is None

    @patch("platform.system")
    @patch("subprocess.run")
    def test_macos_platform_uuid_timeout(self, mock_run, mock_system):
        mock_system.return_value = "Darwin"
        mock_run.side_effect = subprocess.TimeoutExpired("ioreg", 5)

        manager = AnonymizationManager()
        uuid = manager._get_macos_platform_uuid()

        assert uuid is None

    @patch("platform.system")
    @patch("subprocess.run")
    def test_macos_platform_uuid_missing_from_output(self, mock_run, mock_system):
        mock_system.return_value = "Darwin"
        mock_run.return_value = Mock(returncode=0, stdout="Some other output without UUID")

        manager = AnonymizationManager()
        uuid = manager._get_macos_platform_uuid()

        assert uuid is None


class TestLinuxMachineID:
    @patch("platform.system")
    @patch("os.path.exists")
    @patch("builtins.open", new_callable=mock_open, read_data="abc123def456ghi789\n")
    def test_linux_machine_id_from_etc(self, mock_file, mock_exists, mock_system):
        mock_system.return_value = "Linux"
        mock_exists.side_effect = lambda path: path == "/etc/machine-id"

        manager = AnonymizationManager()
        machine_id = manager._get_linux_machine_id()

        assert machine_id == "abc123def456ghi789"
        mock_file.assert_called_with("/etc/machine-id", encoding="utf-8")

    @patch("platform.system")
    @patch("os.path.exists")
    @patch("builtins.open", new_callable=mock_open, read_data="xyz789abc123def456\n")
    def test_linux_machine_id_from_dbus(self, mock_file, mock_exists, mock_system):
        mock_system.return_value = "Linux"
        mock_exists.side_effect = lambda path: path == "/var/lib/dbus/machine-id"

        manager = AnonymizationManager()
        machine_id = manager._get_linux_machine_id()

        assert machine_id == "xyz789abc123def456"
        mock_file.assert_called_with("/var/lib/dbus/machine-id", encoding="utf-8")

    @patch("platform.system")
    @patch("os.path.exists")
    def test_linux_machine_id_not_found(self, mock_exists, mock_system):
        mock_system.return_value = "Linux"
        mock_exists.return_value = False

        manager = AnonymizationManager()
        machine_id = manager._get_linux_machine_id()

        assert machine_id is None

    @patch("platform.system")
    @patch("os.path.exists")
    @patch("builtins.open")
    def test_linux_machine_id_permission_error(self, mock_file, mock_exists, mock_system):
        mock_system.return_value = "Linux"
        mock_exists.return_value = True
        mock_file.side_effect = PermissionError("Access denied")

        manager = AnonymizationManager()
        machine_id = manager._get_linux_machine_id()

        assert machine_id is None


class TestWindowsMachineGUID:
    @patch("platform.system")
    def test_windows_machine_guid_success(self, mock_system):
        mock_system.return_value = "Windows"

        mock_winreg = MagicMock()
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value = mock_key
        mock_winreg.QueryValueEx.return_value = ("12345678-90AB-CDEF-1234-567890ABCDEF", 1)
        mock_winreg.HKEY_LOCAL_MACHINE = "HKLM"
        mock_winreg.KEY_READ = 0x20019

        with patch.dict("sys.modules", {"winreg": mock_winreg}):
            manager = AnonymizationManager()
            guid = manager._get_windows_machine_guid()

        assert guid == "12345678-90AB-CDEF-1234-567890ABCDEF"

    @pytest.mark.skipif(sys.platform == "win32", reason="winreg always available on Windows")
    @patch("platform.system")
    def test_windows_machine_guid_not_on_windows(self, mock_system):
        mock_system.return_value = "Darwin"

        manager = AnonymizationManager()
        guid = manager._get_windows_machine_guid()

        assert guid is None


class TestOSMachineID:
    @patch("platform.system")
    def test_os_machine_id_delegates_to_macos(self, mock_system):
        mock_system.return_value = "Darwin"

        manager = AnonymizationManager()
        with patch.object(manager, "_get_macos_platform_uuid", return_value="test-uuid") as mock_method:
            result = manager._get_os_machine_id()

        mock_method.assert_called_once()
        assert result == "test-uuid"

    @patch("platform.system")
    def test_os_machine_id_delegates_to_linux(self, mock_system):
        mock_system.return_value = "Linux"

        manager = AnonymizationManager()
        with patch.object(manager, "_get_linux_machine_id", return_value="test-machine-id") as mock_method:
            result = manager._get_os_machine_id()

        mock_method.assert_called_once()
        assert result == "test-machine-id"

    @patch("platform.system")
    def test_os_machine_id_delegates_to_windows(self, mock_system):
        mock_system.return_value = "Windows"

        manager = AnonymizationManager()
        with patch.object(manager, "_get_windows_machine_guid", return_value="test-guid") as mock_method:
            result = manager._get_os_machine_id()

        mock_method.assert_called_once()
        assert result == "test-guid"

    @patch("platform.system")
    def test_os_machine_id_unknown_os(self, mock_system):
        mock_system.return_value = "FreeBSD"

        manager = AnonymizationManager()
        result = manager._get_os_machine_id()

        assert result is None


class TestHardwareFingerprint:
    def test_hardware_fingerprint_format(self):
        manager = AnonymizationManager()
        fingerprint = manager._get_hardware_fingerprint()

        assert isinstance(fingerprint, str)
        assert "|" in fingerprint

        parts = fingerprint.split("|")
        assert len(parts) == 4

    def test_hardware_fingerprint_stability(self):
        manager = AnonymizationManager()

        fp1 = manager._get_hardware_fingerprint()
        fp2 = manager._get_hardware_fingerprint()

        assert fp1 == fp2

    @patch("platform.machine")
    @patch("platform.system")
    @patch("os.cpu_count")
    def test_hardware_fingerprint_components(self, mock_cpu_count, mock_system, mock_machine):
        mock_machine.return_value = "arm64"
        mock_system.return_value = "Darwin"
        mock_cpu_count.return_value = 10

        manager = AnonymizationManager()
        fingerprint = manager._get_hardware_fingerprint()

        assert "arm64" in fingerprint
        assert "Darwin" in fingerprint
        assert "10" in fingerprint

    @patch("platform.machine")
    def test_hardware_fingerprint_exception_handling(self, mock_machine):
        mock_machine.side_effect = Exception("Test error")

        manager = AnonymizationManager()
        fingerprint = manager._get_hardware_fingerprint()

        assert fingerprint == "fallback_fingerprint"


class TestStableMACAddress:
    @patch("uuid.getnode")
    def test_stable_mac_address_format(self, mock_getnode):
        mock_getnode.return_value = 0x2211EB301A8A

        manager = AnonymizationManager()
        mac = manager._get_stable_mac_address()

        assert isinstance(mac, str)
        assert mac == "2211EB301A8A"

    @patch("uuid.getnode")
    def test_stable_mac_address_exception(self, mock_getnode):
        mock_getnode.side_effect = Exception("Test error")

        manager = AnonymizationManager()
        mac = manager._get_stable_mac_address()

        assert mac == "unknown_mac"


class TestMachineIDIntegration:
    @patch("platform.system")
    @patch("subprocess.run")
    def test_macos_uses_os_level_id(self, mock_run, mock_system):
        mock_system.return_value = "Darwin"
        mock_run.return_value = Mock(returncode=0, stdout='"IOPlatformUUID" = "F79092CB-6DA1-5604-BBD1-78EF17E58BEF"')

        manager = AnonymizationManager()
        machine_id = manager.get_anonymous_machine_id()

        assert machine_id.startswith("machine_")
        assert len(machine_id) == 24

        assert machine_id == manager.get_anonymous_machine_id()

    @patch("platform.system")
    @patch("subprocess.run")
    def test_macos_falls_back_to_fingerprint(self, mock_run, mock_system):
        mock_system.return_value = "Darwin"
        mock_run.side_effect = FileNotFoundError("ioreg not found")

        manager = AnonymizationManager()
        machine_id = manager.get_anonymous_machine_id()

        assert machine_id.startswith("machine_")
        assert len(machine_id) == 24

    @patch("platform.system")
    @patch("os.path.exists")
    @patch("builtins.open", new_callable=mock_open, read_data="stable-machine-id-value\n")
    def test_linux_uses_os_level_id(self, mock_file, mock_exists, mock_system):
        mock_system.return_value = "Linux"
        mock_exists.side_effect = lambda path: path == "/etc/machine-id"

        manager1 = AnonymizationManager()
        manager2 = AnonymizationManager()

        id1 = manager1.get_anonymous_machine_id()
        id2 = manager2.get_anonymous_machine_id()

        assert id1 == id2
        assert id1.startswith("machine_")

    def test_fallback_chain_completeness(self):
        manager = AnonymizationManager()
        machine_id = manager.get_anonymous_machine_id()

        assert machine_id is not None
        assert isinstance(machine_id, str)
        assert machine_id.startswith("machine_")
        assert len(machine_id) == 24


class TestAnonymizationConfig:
    def test_default_config(self):
        config = AnonymizationConfig()

        assert config.machine_id_salt is None
        assert config.pii_patterns
        assert config.custom_sanitizers == {}

    def test_custom_config(self):
        config = AnonymizationConfig(machine_id_salt="custom_salt")

        assert config.machine_id_salt == "custom_salt"

    def test_no_flag_promises_privacy_it_does_not_deliver(self):
        retired = {
            "include_machine_id",
            "anonymize_paths",
            "allowed_path_prefixes",
            "include_system_profile",
            "anonymize_hostnames",
            "anonymize_usernames",
        }
        present = {f.name for f in dataclasses.fields(AnonymizationConfig)}
        assert not (present & retired), f"dead privacy flags are back: {sorted(present & retired)}"


class TestRemovePII:
    def test_removes_ip_address(self):
        manager = AnonymizationManager()
        result = manager.remove_pii("Server at 192.168.1.100 failed")
        assert "192.168.1.100" not in result
        assert "[REDACTED]" in result

    def test_removes_email(self):
        manager = AnonymizationManager()
        result = manager.remove_pii("Contact user@example.com for help")
        assert "user@example.com" not in result
        assert "[REDACTED]" in result

    def test_removes_ssn_like_pattern(self):
        manager = AnonymizationManager()
        result = manager.remove_pii("SSN: 123-45-6789")
        assert "123-45-6789" not in result

    def test_empty_string_returns_empty(self):
        manager = AnonymizationManager()
        assert manager.remove_pii("") == ""

    def test_custom_sanitizer_applied(self):
        config = AnonymizationConfig(custom_sanitizers={r"SECRET-\d+": "[TOKEN]"})
        manager = AnonymizationManager(config)
        result = manager.remove_pii("Using key SECRET-9876")
        assert "SECRET-9876" not in result
        assert "[TOKEN]" in result

    def test_clean_text_unchanged(self):
        manager = AnonymizationManager()
        clean = "Benchmark finished in 3.5 seconds"
        assert manager.remove_pii(clean) == clean


class TestPublicPayloadSecretKeys:
    @staticmethod
    def _payload(config):
        return {"platform_metadata": {"platform_raw_config": config}}

    def test_key_id_values_are_redacted(self):
        manager = AnonymizationManager()
        out = manager.anonymize_result_payload(
            self._payload({"s3_key_id": "AKIA-SENTINEL", "kms_key_id": "KMS-SENTINEL"})
        )
        raw = json.dumps(out, default=str)
        assert "AKIA-SENTINEL" not in raw
        assert "KMS-SENTINEL" not in raw

    def test_already_covered_keys_keep_behavior(self):
        manager = AnonymizationManager()
        out = manager.anonymize_result_payload(
            self._payload({"pg_password": "PW-SENTINEL", "username": "alice-sentinel"})
        )["platform_metadata"]["platform_raw_config"]
        assert out["pg_password"] == PUBLIC_REDACTED_VALUE
        assert out["username"] != "alice-sentinel"
        assert out["username"].startswith("user_")

    def test_secret_parts_shared_with_platform_options(self):
        from benchbox.core.results.anonymization import _SECRET_KEY_PARTS as anon_parts
        from benchbox.core.results.platform_options import _SECRET_KEY_PARTS as capture_parts

        assert set(capture_parts) <= set(anon_parts)

    def test_data_modelling_keys_are_not_over_redacted(self):
        manager = AnonymizationManager()
        out = manager.anonymize_result_payload(
            self._payload({"sort_key": "l_shipdate", "partition_key": "l_orderkey"})
        )["platform_metadata"]["platform_raw_config"]
        assert out["sort_key"] == "l_shipdate"
        assert out["partition_key"] == "l_orderkey"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestPublicPayloadApiKeyAndAccountKey:
    def test_api_key_and_storage_account_key_are_redacted(self):
        manager = AnonymizationManager()
        out = json.dumps(
            manager.anonymize_result_payload(
                {
                    "platform_metadata": {
                        "platform_raw_config": {
                            "api_key": "APIKEY-SENTINEL",
                            "onehouse_api_key": "OH-SENTINEL",
                            "storage_account_key": "AZKEY-SENTINEL",
                        }
                    }
                }
            ),
            default=str,
        )
        assert "APIKEY-SENTINEL" not in out
        assert "OH-SENTINEL" not in out
        assert "AZKEY-SENTINEL" not in out


class TestPublicPayloadCredentialAliases:
    def test_aliases_are_redacted_while_preserving_tuning_keys_and_path_contract(self):
        out = AnonymizationManager().anonymize_result_payload(
            {
                "platform_metadata": {
                    "platform_raw_config": {
                        "passwd": "PASSWD_SECRET",
                        "pwd": "PWD_SECRET",
                        "pat": "PAT_SECRET",
                        "path": "/tmp/data",
                        "sort_key": "o_orderkey",
                    }
                }
            }
        )["platform_metadata"]["platform_raw_config"]

        assert out["passwd"] == PUBLIC_REDACTED_VALUE
        assert out["pwd"] == PUBLIC_REDACTED_VALUE
        assert out["pat"] == PUBLIC_REDACTED_VALUE
        assert "path" not in out
        assert out["sort_key"] == "o_orderkey"


class TestPublicPayloadSslRootCert:
    def test_sslrootcert_path_is_hashed(self):
        out = json.dumps(
            AnonymizationManager().anonymize_result_payload(
                {"platform_metadata": {"platform_raw_config": {"sslrootcert": "/Users/joe/certs/root.pem"}}}
            ),
            default=str,
        )
        assert "/Users/joe" not in out

    def test_underscore_cert_paths_still_hashed(self):
        out = json.dumps(
            AnonymizationManager().anonymize_result_payload(
                {"platform_metadata": {"platform_raw_config": {"ca_cert_path": "/Users/joe/certs/ca.pem"}}}
            ),
            default=str,
        )
        assert "/Users/joe" not in out


class TestPublicPayloadTupleRecursion:
    def test_tuple_nested_identifier_is_anonymized(self):
        out = json.dumps(
            AnonymizationManager().anonymize_result_payload(
                {"platform_metadata": {"nested": ({"userid": "TUP-SENTINEL"},)}}
            ),
            default=str,
        )
        assert "TUP-SENTINEL" not in out

    def test_tuple_secret_key_is_redacted(self):
        out = json.dumps(
            AnonymizationManager().anonymize_result_payload(
                {"platform_metadata": {"configs": ({"password": "TUPPW-SENTINEL"},)}}
            ),
            default=str,
        )
        assert "TUPPW-SENTINEL" not in out

    def test_list_and_dict_recursion_unchanged(self):
        out = AnonymizationManager().anonymize_result_payload(
            {"platform_metadata": {"platform_raw_config": [{"username": "alice-sentinel"}]}}
        )["platform_metadata"]["platform_raw_config"]
        assert out[0]["username"].startswith("user_")

    def test_unordered_collections_are_sorted_before_export(self):
        out = AnonymizationManager().anonymize_result_payload(
            {"platform_metadata": {"values": frozenset({"zeta", "alpha"})}}
        )

        assert out["platform_metadata"]["values"] == ["alpha", "zeta"]


class TestTuningPayloadAnonymization:
    def test_unknown_constraint_fields_use_generic_public_sanitization(self):
        out = AnonymizationManager().anonymize_tuning_payload(
            {
                "requested": {
                    "constraints": {
                        "foreign_keys": {
                            "password": "CONSTRAINT-PASSWORD",
                            "owner_email": "owner@example.com",
                            "endpoint": "https://warehouse.example.com",
                            "path": "/Users/alice/private-run",
                        }
                    }
                }
            }
        )

        serialized = json.dumps(out)
        assert "CONSTRAINT-PASSWORD" not in serialized
        assert "owner@example.com" not in serialized
        assert "warehouse.example.com" not in serialized
        assert "/Users/alice" not in serialized

    def test_constraint_table_and_column_identifiers_are_pseudonymized(self):
        out = AnonymizationManager().anonymize_tuning_payload(
            {
                "requested": {
                    "constraints": {
                        "primary_keys": {
                            "enabled": True,
                            "tables": {"orders": ["o_orderkey"]},
                        },
                        "foreign_keys": {
                            "enabled": True,
                            "tables": {
                                "lineitem": {
                                    "columns": ["l_orderkey"],
                                    "referenced_table": "orders",
                                    "referenced_columns": ["o_orderkey"],
                                }
                            },
                        },
                    }
                }
            }
        )

        serialized = json.dumps(out)
        assert all(identifier not in serialized for identifier in ("orders", "lineitem", "o_orderkey", "l_orderkey"))
        constraints = out["requested"]["constraints"]
        assert next(iter(constraints["primary_keys"]["tables"])).startswith("table_")
        assert constraints["primary_keys"]["tables"][next(iter(constraints["primary_keys"]["tables"]))][0].startswith(
            "column_"
        )

    def test_nested_companion_constraint_shapes_are_pseudonymized(self):
        out = AnonymizationManager().anonymize_tuning_payload(
            {
                "requested": {
                    "constraints": {
                        "foreign_keys": {
                            "enabled": True,
                            "on_delete_action": "CASCADE",
                            "tables": [
                                {
                                    "table": "orders",
                                    "columns": ["o_orderkey"],
                                    "referenced_table": "customers",
                                    "referenced_columns": ["c_custkey"],
                                }
                            ],
                        },
                        "local_table": "orders/o_orderkey",
                        "references_table": "customers",
                        "references_column": "c_custkey",
                    }
                }
            }
        )

        serialized = json.dumps(out)
        assert all(identifier not in serialized for identifier in ("orders", "o_orderkey", "customers", "c_custkey"))
        constraints = out["requested"]["constraints"]
        fk = constraints["foreign_keys"]
        assert fk["enabled"] is True
        assert fk["on_delete_action"] == "CASCADE"
        assert isinstance(fk["tables"], list) and len(fk["tables"]) == 1
        entry = fk["tables"][0]
        assert entry["table"].startswith("table_")
        assert entry["referenced_table"].startswith("table_")
        assert entry["columns"][0].startswith("column_")
        assert entry["referenced_columns"][0].startswith("column_")
        assert constraints["local_table"].startswith("table_")
        assert constraints["references_table"].startswith("table_")
        assert constraints["references_column"].startswith("column_")

    def test_mixed_and_list_constraint_companion_shapes(self):
        out = AnonymizationManager().anonymize_tuning_payload(
            {
                "requested": {
                    "constraints": {
                        "foreign_keys": {
                            "tables": [
                                "orders",
                                {"table": "customers", "columns": [{"name": "c_custkey"}, "c_nationkey"]},
                            ]
                        },
                    }
                }
            }
        )
        serialized = json.dumps(out)
        assert all(identifier not in serialized for identifier in ("orders", "customers", "c_custkey", "c_nationkey"))
        tables = out["requested"]["constraints"]["foreign_keys"]["tables"]
        assert tables[0].startswith("table_")
        assert tables[1]["table"].startswith("table_")
        assert tables[1]["columns"][0]["name"].startswith("column_")
        assert tables[1]["columns"][1].startswith("column_")

        list_constraints = AnonymizationManager().anonymize_tuning_payload(
            {"requested": {"constraints": [{"table": "orders", "columns": ["o_orderkey"]}]}}
        )
        serialized_list = json.dumps(list_constraints)
        assert "orders" not in serialized_list and "o_orderkey" not in serialized_list
        assert list_constraints["requested"]["constraints"][0]["table"].startswith("table_")

    def test_table_and_column_identifiers_are_pseudonymized_but_source_is_preserved(self):
        manager = AnonymizationManager()
        out = manager.anonymize_tuning_payload(
            {
                "source_file": "examples/tunings/custom.yaml:0123456789abcdef",
                "requested": {
                    "table_tunings": {
                        "lineitem": {
                            "table_name": "lineitem",
                            "sorting": [{"name": "l_orderkey", "order": 1}],
                        }
                    }
                },
            }
        )

        tuning = out["requested"]["table_tunings"]
        table_key = next(iter(tuning))
        assert table_key.startswith("table_")
        assert tuning[table_key]["table_name"].startswith("table_")
        assert tuning[table_key]["sorting"][0]["name"].startswith("column_")
        assert out["source_file"] == "examples/tunings/custom.yaml:0123456789abcdef"

    @pytest.mark.parametrize(
        "source_file",
        [
            "/Users/alice/private/custom.yaml",
            r"C:\Users\alice\private\custom.yaml",
            "../private/custom.yaml",
            "source=/Users/alice/private/custom.yaml",
            "template.yaml\n/Users/alice/private/custom.yaml",
        ],
    )
    def test_untrusted_tuning_source_path_is_hashed(self, source_file):
        out = AnonymizationManager().anonymize_tuning_payload({"source_file": source_file})

        assert out["source_file"].startswith("path_")
        assert "alice" not in out["source_file"]


class TestPublicPayloadWorkspaceRoleAndApplicationIds:
    @staticmethod
    def _payload(config):
        return {"platform_metadata": {"platform_raw_config": config}}

    def test_workspace_name_pseudonymizes_like_workspace(self):
        out = AnonymizationManager().anonymize_result_payload(self._payload({"workspace_name": "WSN-SENTINEL"}))[
            "platform_metadata"
        ]["platform_raw_config"]
        assert out["workspace_name"] != "WSN-SENTINEL"
        assert out["workspace_name"].startswith("workspace_")

    def test_job_role_pseudonymizes_like_role(self):
        out = AnonymizationManager().anonymize_result_payload(self._payload({"job_role": "JR-SENTINEL"}))[
            "platform_metadata"
        ]["platform_raw_config"]
        assert out["job_role"] != "JR-SENTINEL"
        assert out["job_role"].startswith("role_")

    def test_application_id_is_hashed(self):
        out = AnonymizationManager().anonymize_result_payload(self._payload({"application_id": "APP-SENTINEL"}))[
            "platform_metadata"
        ]["platform_raw_config"]
        assert out["application_id"] != "APP-SENTINEL"
        assert out["application_id"].startswith("application_")


class TestPublicPayloadPgUserAndTenantId:
    @staticmethod
    def _payload(config):
        return {"platform_metadata": {"platform_raw_config": config}}

    def test_pg_user_pseudonymizes_like_username(self):
        manager = AnonymizationManager()
        out = manager.anonymize_result_payload(self._payload({"pg_user": "PGUSER-SENTINEL"}))["platform_metadata"][
            "platform_raw_config"
        ]
        assert out["pg_user"] != "PGUSER-SENTINEL"
        assert out["pg_user"].startswith("user_")

    def test_tenant_id_is_hashed(self):
        manager = AnonymizationManager()
        out = manager.anonymize_result_payload(self._payload({"tenant_id": "TENANT-SENTINEL"}))["platform_metadata"][
            "platform_raw_config"
        ]
        assert out["tenant_id"] != "TENANT-SENTINEL"
        assert out["tenant_id"].startswith("tenant_")

    def test_existing_identifier_behavior_unchanged(self):
        manager = AnonymizationManager()
        out = manager.anonymize_result_payload(
            self._payload({"username": "alice-sentinel", "account": "acct-sentinel"})
        )["platform_metadata"]["platform_raw_config"]
        assert out["username"].startswith("user_")
        assert out["account"].startswith("account_")


class TestPublicPayloadSecretMessages:
    def test_message_values_use_the_shared_secret_key_classifier(self):
        manager = AnonymizationManager()
        out = manager.anonymize_result_payload(
            {
                "platform_metadata": {
                    "error_message": "api_key=API-SENTINEL storage_account_key=AZ-SENTINEL",
                    "stderr": "AccountKey=AZ-CONNECTION-SENTINEL",
                }
            }
        )
        serialized = json.dumps(out, default=str)
        assert "API-SENTINEL" not in serialized
        assert "AZ-SENTINEL" not in serialized
        assert "AZ-CONNECTION-SENTINEL" not in serialized


class TestPublicPayloadPathPrivacy:
    def test_generic_working_directory_is_omitted(self):
        out = AnonymizationManager().anonymize_result_payload(
            {"platform_metadata": {"working_dir": "/Users/alice/benchbox/run", "mode": "sql"}}
        )
        serialized = json.dumps(out, default=str)
        assert "/Users/alice" not in serialized
        assert "working_dir" not in out["platform_metadata"]
        assert out["platform_metadata"]["mode"] == "sql"

    def test_nested_generic_metadata_paths_are_hashed_without_field_allowlist(self):
        out = AnonymizationManager().anonymize_result_payload(
            {"platform_metadata": {"raw_config": {"runtime": {"path_value": "/home/alice/cache"}}}}
        )
        assert "/home/alice" not in json.dumps(out, default=str)
        assert out["platform_metadata"]["raw_config"]["runtime"]["path_value"].startswith("path_")

    def test_relative_references_and_urls_are_not_false_positives(self):
        payload = {
            "source_ref": "examples/tunings/custom.yaml:0123456789abcdef",
            "url": "https://example.com/home/alice/reference",
        }
        assert find_public_path_leaks(payload) == []
        out = AnonymizationManager().anonymize_result_payload(payload)
        assert out["source_ref"] == payload["source_ref"]
        assert out["url"].startswith("endpoint_")

    def test_detector_reports_field_paths_without_echoing_values(self):
        leaks = find_public_path_leaks({"nested": [{"working_dir": "/Users/alice/private"}]})
        assert leaks == ["nested.0.working_dir"]

    def test_detector_flags_private_paths_encoded_as_object_keys(self):
        leaks = find_public_path_leaks({"per_path_timings": {"/Users/alice/db": 1.2}})
        assert leaks == ["per_path_timings.<key>"]

    def test_key_leak_report_does_not_echo_the_key(self):
        leaks = find_public_path_leaks({"metadata": {"/Users/alice/secret-project": True}})
        assert leaks and "alice" not in " ".join(leaks)
        assert "secret-project" not in " ".join(leaks)

    def test_detector_flags_a_leaking_key_at_the_root(self):
        leaks = find_public_path_leaks({"/Users/alice/db": 1})
        assert leaks == ["<key>"]

    def test_leaking_key_is_elided_from_descendant_field_paths(self):
        leaks = find_public_path_leaks({"/Users/alice/secret": {"working_dir": "/home/bob/private"}})
        joined = " ".join(leaks)
        assert "<key>.working_dir" in leaks
        assert "alice" not in joined
        assert "secret" not in joined
        assert "bob" not in joined

    def test_ordinary_keys_are_not_flagged(self):
        payload = {"queries": {"q1": 1.0, "tpch/q2": 2.0}, "platform": {"name": "DuckDB"}}
        assert find_public_path_leaks(payload) == []

    def test_root_owned_home_paths_are_private_even_under_generic_keys(self):
        payload = {"raw_config": {"location": "/root/private-run"}}

        assert find_public_path_leaks(payload)
        out = AnonymizationManager().anonymize_result_payload(payload)
        assert "/root/private-run" not in json.dumps(out, default=str)


class TestPublicUnreadIdentifierDrop:
    DROP_FIELDS = (
        "machine_id",
        "working_dir",
        "driver_runtime_python_executable",
        "database_path",
        "data_path",
        "engine_host",
    )

    def test_drop_fields_are_omitted_at_every_nesting_depth(self):
        payload = {
            "machine_id": "raw-machine",
            "environment": {
                "machine_id": "raw-machine",
                "client_host": {"machine_id": "raw-machine", "hostname": "alice-laptop"},
                "platform_runtime": {"engine_host": "db.internal", "runtime_type": "local"},
            },
            "platform": {
                "config": {
                    "working_dir": "/Users/alice/benchbox",
                    "database_path": "/tmp/db.duckdb",
                    "data_path": "/Users/alice/data",
                    "driver_runtime_python_executable": "/Users/alice/.venv/bin/python",
                    "endpoint": "https://example.invalid/warehouse",
                    "database_name": "analytics",
                }
            },
            "metadata": {"submission_path": "PR-based"},
        }
        out = AnonymizationManager().anonymize_result_payload(payload)
        serialized = json.dumps(out, default=str)
        for key in self.DROP_FIELDS:
            assert key not in serialized, f"drop-field key still present: {key}"
            assert f'"{key}"' not in serialized
        assert "alice" not in serialized
        assert "db.internal" not in serialized
        assert _is_public_pseudonym(out["platform"]["config"]["endpoint"], "endpoint")
        assert _is_public_pseudonym(out["platform"]["config"]["database_name"], "database")
        assert out["metadata"]["submission_path"] == "PR-based" or _is_public_pseudonym(
            out["metadata"]["submission_path"], "path"
        )
        assert out["environment"]["client_host"]["hostname"].startswith("host_")
        assert out["environment"]["platform_runtime"]["runtime_type"] == "local"

    def test_alias_drop_keys_of_unread_identifiers_are_omitted(self):
        payload = {
            "platform": {
                "config": {
                    "workdir": "/Users/alice/project",
                    "working_directory": "/Users/alice/project",
                    "working_root": "/Users/alice/project",
                    "data_dir": "/Users/alice/data",
                    "data_directory": "/Users/alice/data",
                    "python_executable": "/Users/alice/.venv/bin/python",
                    "endpoint": "https://example.invalid/warehouse",
                    "database_name": "analytics",
                }
            },
            "metadata": {"submission_path": "PR-based"},
        }
        out = AnonymizationManager().anonymize_result_payload(payload)
        cfg = out["platform"]["config"]
        for key in (
            "workdir",
            "working_directory",
            "working_root",
            "data_dir",
            "data_directory",
            "python_executable",
        ):
            assert key not in cfg, f"alias drop key still present: {key}"
        assert "alice" not in json.dumps(out)
        assert _is_public_pseudonym(cfg["endpoint"], "endpoint")
        assert _is_public_pseudonym(cfg["database_name"], "database")
        assert out["metadata"]["submission_path"] == "PR-based" or _is_public_pseudonym(
            out["metadata"]["submission_path"], "path"
        )

    def test_residual_local_path_keys_are_dropped_not_hashed(self):
        payload = {
            "platform": {
                "config": {
                    "output_dir": "/Users/alice/out",
                    "output_directory": "/Users/alice/out",
                    "output_path": "/Users/alice/out/run.json",
                    "output_location": "/Users/alice/out",
                    "result_dir": "/Users/alice/results",
                    "result_path": "/Users/alice/results/run.json",
                    "log_path": "/Users/alice/logs/run.log",
                    "log_file": "/Users/alice/logs/run.log",
                    "file_path": "/Users/alice/data/file.parquet",
                    "path": "/Users/alice/scratch",
                    "source_root": "/Users/alice/src",
                    "credential_file": "/Users/alice/.aws/credentials",
                    "sslrootcert": "/Users/alice/certs/root.pem",
                    "s3_staging_url": "s3://tenant-bucket/staging/",
                    "staging_location": "s3://tenant-bucket/staging/",
                    "staging_url": "s3://tenant-bucket/staging/",
                    "http_path": "/sql/1.0/warehouses/abc",
                    "host": "db.example.internal",
                    "hostname": "db.example.internal",
                    "server": "db.example.internal",
                    "endpoint": "https://example.invalid/warehouse",
                    "database_name": "analytics",
                }
            },
            "metadata": {"submission_path": "/Users/alice/submission.json"},
        }
        out = AnonymizationManager().anonymize_result_payload(payload)
        cfg = out["platform"]["config"]
        for key in (
            "output_dir",
            "output_directory",
            "output_path",
            "output_location",
            "result_dir",
            "result_path",
            "log_path",
            "log_file",
            "file_path",
            "path",
            "source_root",
            "credential_file",
        ):
            assert key not in cfg, f"residual local path key still present: {key}"
        serialized = json.dumps(out, default=str)
        assert "alice" not in serialized
        assert _is_public_pseudonym(cfg["sslrootcert"], "path")
        assert _is_public_pseudonym(cfg["s3_staging_url"], "endpoint")
        assert _is_public_pseudonym(cfg["staging_location"], "path")
        assert _is_public_pseudonym(cfg["staging_url"], "endpoint")
        assert _is_public_pseudonym(cfg["http_path"], "path")
        assert cfg["host"].startswith("host_")
        assert cfg["hostname"].startswith("host_")
        assert cfg["server"].startswith("host_")
        assert _is_public_pseudonym(cfg["endpoint"], "endpoint")
        assert _is_public_pseudonym(cfg["database_name"], "database")
        assert _is_public_pseudonym(out["metadata"]["submission_path"], "path")

    def test_empty_client_host_omitted_after_machine_id_drop(self):
        out = AnonymizationManager().anonymize_result_payload(
            {"environment": {"client_host": {"machine_id": "raw-machine"}, "other": 1}}
        )
        assert "client_host" not in out.get("environment", {})
        assert out["environment"]["other"] == 1

    def test_already_empty_client_host_is_omitted(self):
        payload = {"environment": {"client_host": {}, "other": 1}}
        out = AnonymizationManager().anonymize_result_payload(payload)
        assert "client_host" not in out.get("environment", {})
        assert out["environment"]["other"] == 1

    def test_nonempty_client_host_profile_still_exports(self):
        out = AnonymizationManager().anonymize_result_payload(
            {
                "environment": {
                    "client_host": {
                        "machine_id": "raw-machine",
                        "os": "macOS",
                        "arch": "arm64",
                        "cpu": "Apple M-series",
                        "memory_gb": 32,
                    }
                }
            }
        )
        host = out["environment"]["client_host"]
        assert "machine_id" not in host
        assert host["os"] == "macOS"
        assert host["arch"] == "arm64"
        assert host["cpu"] == "Apple M-series"
        assert host["memory_gb"] == 32

    def test_drop_is_idempotent_when_fields_already_absent(self):
        manager = AnonymizationManager()
        once = manager.anonymize_result_payload({"platform": {"config": {"endpoint": "https://x.example"}}})
        assert manager.anonymize_result_payload(once) == once
        assert "machine_id" not in once
        assert "working_dir" not in once.get("platform", {}).get("config", {})

    def test_tuning_constraints_omit_drop_keys_without_keyerror(self):
        out = AnonymizationManager().anonymize_tuning_payload(
            {
                "source_file": "templates/tuning/example.yaml",
                "requested": {
                    "constraints": {
                        "machine_id": "raw-machine",
                        "working_dir": "/Users/alice/run",
                        "table": "lineitem",
                        "nested": {"engine_host": "db.internal", "ok": 1},
                    },
                    "table_tunings": {
                        "lineitem": {
                            "machine_id": "should-drop",
                            "columns": [{"name": "l_orderkey"}],
                        }
                    },
                },
            }
        )
        constraints = out["requested"]["constraints"]
        assert "machine_id" not in constraints
        assert "working_dir" not in constraints
        assert "engine_host" not in constraints["nested"]
        assert constraints["nested"]["ok"] == 1
        assert "table" in constraints
        tunings = out["requested"]["table_tunings"]
        assert "lineitem" not in tunings
        assert tunings, "table_tunings should retain the hashed table entry"
        for table_entry in tunings.values():
            assert "machine_id" not in table_entry


class TestPublicPseudonymFixedPoint:
    PAYLOAD = {
        "environment": {
            "client_host": {"hostname": "host_0123456789ab"},
            "platform_runtime": {"runtime_type": "local"},
        },
        "platform": {
            "config": {
                "database": "analytics",
                "database_name": "analytics",
                "endpoint": "https://example.invalid/warehouse",
                "s3_bucket": "alice-results",
                "role_arn": "arn:aws:iam::1234:role/bench",
                "submission_path": "/Users/alice/submission.json",
            }
        },
        "config": {"platform_options": {"account_id": "acct-1", "cluster_id": "c-1", "schema_name": "public"}},
    }

    def test_result_payload_anonymization_is_idempotent(self):
        manager = AnonymizationManager()
        once = manager.anonymize_result_payload(self.PAYLOAD)
        assert manager.anonymize_result_payload(once) == once

    def test_tuning_payload_anonymization_is_idempotent(self):
        manager = AnonymizationManager()
        payload = {
            "source_file": "examples/tunings/custom.yaml",
            "requested": {
                "table_tunings": {"lineitem": {"columns": [{"name": "l_orderkey"}]}},
                "constraints": {"primary_key": {"table": "orders", "columns": ["o_orderkey"]}},
            },
        }
        once = manager.anonymize_tuning_payload(payload)
        assert manager.anonymize_tuning_payload(once) == once

    def test_pseudonym_survives_a_second_pass_unchanged(self):
        manager = AnonymizationManager()
        once = manager.anonymize_result_payload(self.PAYLOAD)
        twice = manager.anonymize_result_payload(once)
        assert twice["platform"]["config"]["endpoint"] == once["platform"]["config"]["endpoint"]
        assert twice["platform"]["config"]["database_name"] == once["platform"]["config"]["database_name"]
        assert twice["environment"]["client_host"]["hostname"] == once["environment"]["client_host"]["hostname"]

    def test_every_emitted_pseudonym_is_recognized_as_one(self):
        manager = AnonymizationManager()
        for prefix in sorted(set(_IDENTIFIER_KEYS.values()) | {"path", "host", "endpoint", "table", "column"}):
            emitted = manager._hash_public_identifier("some-private-value", prefix)
            assert _is_public_pseudonym(emitted, prefix), f"{prefix}: emitted {emitted!r} is not recognized"
            assert manager._hash_public_identifier(emitted, prefix) == emitted

    def test_capture_side_machine_id_is_omitted_not_published(self):
        manager = AnonymizationManager()
        internal = manager.get_anonymous_machine_id()
        published = manager.anonymize_result_payload({"machine_id": internal, "note": "keep"})
        assert "machine_id" not in published
        assert published["note"] == "keep"

    def test_pass_through_does_not_cross_prefixes(self):
        manager = AnonymizationManager()
        out = manager.anonymize_result_payload({"submission_path": "host_0123456789ab"})
        assert out["submission_path"] != "host_0123456789ab"
        assert _is_public_pseudonym(out["submission_path"], "path")

    @pytest.mark.parametrize(
        "value",
        [
            "path_0123456789",
            "path_0123456789abc",
            "path_0123456789zz",
            "path_",
            "pathx_0123456789ab",
        ],
    )
    def test_only_the_exact_pseudonym_shape_passes_through(self, value):
        assert not _is_public_pseudonym(value, "path")
        out = AnonymizationManager().anonymize_result_payload({"submission_path": value})
        assert out["submission_path"] != value


class TestLegacyAnonymizationSchemeIsGone:
    LEGACY_METHODS = (
        "sanitize_path",
        "anonymize_query_metadata",
        "anonymize_execution_metadata",
        "anonymize_system_profile",
        "validate_anonymization",
    )

    @pytest.mark.parametrize("name", LEGACY_METHODS)
    def test_legacy_method_is_gone(self, name):
        assert not hasattr(AnonymizationManager, name), f"{name} is back on the public surface"

    def test_no_unsalted_md5_pseudonym_scheme_remains(self):
        source = inspect.getsource(sys.modules[AnonymizationManager.__module__])
        assert "md5" not in source, "an md5 pseudonym scheme is back in the anonymizer"

    def test_every_pseudonym_emitter_uses_the_shared_salted_helper(self):
        source = inspect.getsource(sys.modules[AnonymizationManager.__module__])
        truncations = re.findall(r"hexdigest\(\)\[:[^\]]+\]", source)
        assert len(truncations) == 2, f"unexpected digest truncations: {truncations}"

    def test_no_sibling_module_mints_a_pseudonym_behind_the_boundary(self):
        package = Path(inspect.getfile(sys.modules[AnonymizationManager.__module__])).parent
        emitter = re.compile(r"f\"[a-z_]+_\{[^}]*hexdigest\(\)")
        offenders = []
        for path in sorted(package.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            if path.name == "anonymization.py":
                continue
            if emitter.search(text):
                offenders.append(path.name)
        assert not offenders, f"pseudonym emitters outside the shared helper: {offenders}"

    def test_public_surface_is_only_the_salted_boundary(self):
        public = {
            name for name, _ in inspect.getmembers(AnonymizationManager, inspect.isfunction) if not name.startswith("_")
        }
        assert public == {
            "get_anonymous_machine_id",
            "anonymize_result_payload",
            "anonymize_tuning_payload",
            "remove_pii",
        }, public

    def test_salt_does_not_reach_an_already_anonymized_value(self):
        a = AnonymizationManager(AnonymizationConfig(machine_id_salt="org-A"))
        b = AnonymizationManager(AnonymizationConfig(machine_id_salt="org-B"))
        raw = {"endpoint": "https://warehouse.example.invalid/sql"}

        published_by_a = a.anonymize_result_payload(raw)["endpoint"]
        published_by_b = b.anonymize_result_payload(raw)["endpoint"]
        assert published_by_a != published_by_b, "salt must scope pseudonyms derived from raw values"

        reanonymized = b.anonymize_result_payload({"endpoint": published_by_a})["endpoint"]
        assert reanonymized == published_by_a, "already-anonymized values are salt-independent by design"

    def test_no_public_method_returns_a_private_path_verbatim(self):
        manager = AnonymizationManager()
        private = "/Users/alice/bench"
        for payload in (
            {"working_dir": private},
            {"database_path": private},
            {"submission_path": private},
            {"note": private},
        ):
            out = manager.anonymize_result_payload(payload)
            assert find_public_path_leaks(out) == [], f"{payload} leaked: {out}"
            assert "alice" not in json.dumps(out)
            for dropped in ("working_dir", "database_path"):
                assert dropped not in out
            if "submission_path" in payload:
                assert _is_public_pseudonym(out["submission_path"], "path")


class TestCloudWarehouseAndOutputLocation:
    def test_databricks_warehouse_id_is_pseudonymised_everywhere(self):
        payload = {
            "platform": {
                "config": {"warehouse_id": "04851bf1f1d8ff51"},
                "compute": {"warehouse_id": "04851bf1f1d8ff51"},
                "raw_metadata": {"compute_configuration": {"warehouse_id": "04851bf1f1d8ff51"}},
            }
        }
        public = AnonymizationManager().anonymize_result_payload(payload)
        text = json.dumps(public)
        assert "04851bf1f1d8ff51" not in text
        assert public["platform"]["config"]["warehouse_id"].startswith("warehouse_")

    def test_cloud_default_output_location_is_pseudonymised(self):
        payload = {"config": {"platform_options": {"default_output_location": "gs://benchbox_uploads/"}}}
        public = AnonymizationManager().anonymize_result_payload(payload)
        assert "benchbox_uploads" not in json.dumps(public)

    def test_pseudonymised_values_are_a_fixed_point(self):
        manager = AnonymizationManager()
        payload = {"platform": {"config": {"warehouse_id": "04851bf1f1d8ff51"}}}
        once = manager.anonymize_result_payload(payload)
        assert manager.anonymize_result_payload(once) == once

    def test_cloud_namespace_fields_are_pseudonymised(self):
        payload = {
            "platform": {
                "config": {
                    "dataset_id": "tpch_sf1_notuning_noconstraints",
                    "uc_catalog": "workspace",
                    "uc_schema": "benchbox",
                    "uc_volume": "data",
                    "catalog": "duckdb",
                }
            }
        }
        config = AnonymizationManager().anonymize_result_payload(payload)["platform"]["config"]
        assert config["dataset_id"].startswith("dataset_")
        assert config["uc_catalog"].startswith("catalog_")
        assert config["uc_schema"].startswith("schema_")
        assert config["uc_volume"].startswith("volume_")
        assert config["catalog"] == "duckdb"

    def test_option_source_labels_stay_readable(self):
        payload = {
            "config": {
                "platform_options": {"default_output_location": "gs://bucket/", "database": "prod"},
                "platform_option_sources": {
                    "default_output_location": "saved_config",
                    "database": "cli_option",
                    "warehouse": "not-a-label",
                },
            }
        }
        config = AnonymizationManager().anonymize_result_payload(payload)["config"]
        assert config["platform_option_sources"]["default_output_location"] == "saved_config"
        assert config["platform_option_sources"]["database"] == "cli_option"
        assert config["platform_option_sources"]["warehouse"].startswith("warehouse_")
        assert "bucket" not in json.dumps(config["platform_options"])

    @pytest.mark.parametrize("engine", ["default", "in-memory", "streaming"])
    def test_generic_engine_option_values_are_hashed(self, engine):
        payload = {
            "config": {"platform_options": {"engine": engine}},
            "platform": {"raw_config": {"options": {"engine": engine}}},
        }

        anonymized = AnonymizationManager().anonymize_result_payload(payload)

        assert anonymized["config"]["platform_options"]["engine"].startswith("engine_")
        assert anonymized["platform"]["raw_config"]["options"]["engine"].startswith("engine_")

    @pytest.mark.parametrize("engine", ["auto", "in-memory", "streaming", "analytics-prod"])
    def test_collect_receipt_preserves_only_declared_engine_values(self, engine):
        payload = {
            "platform": {
                "execution_engine": {"applied_native": {"collect_kwargs": {"engine": engine}}},
                "raw_config": {
                    "execution_engine": {"applied_native": {"collect_kwargs": {"engine": engine}}},
                    "options": {"engine": engine},
                },
            }
        }

        anonymized = AnonymizationManager().anonymize_result_payload(payload)["platform"]
        for receipt in (anonymized["execution_engine"], anonymized["raw_config"]["execution_engine"]):
            actual = receipt["applied_native"]["collect_kwargs"]["engine"]
            assert actual == engine if engine != "analytics-prod" else actual.startswith("engine_")
        assert anonymized["raw_config"]["options"]["engine"].startswith("engine_")

    def test_other_engine_values_and_locations_stay_hashed(self):
        payload = {
            "config": {"platform_options": {"engine": "analytics-prod"}},
            "platform": {"config": {"engine": "in-memory"}},
        }

        anonymized = AnonymizationManager().anonymize_result_payload(payload)

        assert anonymized["config"]["platform_options"]["engine"].startswith("engine_")
        assert anonymized["platform"]["config"]["engine"].startswith("engine_")

    def test_option_source_labels_match_metadata_source_vocabulary(self):
        from typing import get_args

        from benchbox.core.results import anonymization
        from benchbox.core.results.environment import MetadataSource

        assert frozenset(get_args(MetadataSource)) == anonymization._OPTION_SOURCE_LABELS


class TestInlinedAppliedTuningRedaction:
    @staticmethod
    def _applied() -> dict:
        return {
            "status": "applied_unverified",
            "statements": [
                {"statement": "CREATE TABLE orders (o_id Int64) ENGINE = MergeTree() ORDER BY o_id", "phase": "ddl"},
                {"statement": "SET max_threads = 8", "phase": "session", "error": "denied", "table": "orders"},
            ],
            "dropped": [{"intent": "partitioning:orders", "reason": "load-time only"}],
            "satisfied": [{"intent": "sorted_ingestion orders", "reason": "ddl order by"}],
            "receipt": {"error": "lookup failed", "entries": [{"statement": "x", "reason": "absent"}]},
            "drift_check": {"is_valid": False, "errors": ["orders missing"]},
        }

    def test_result_payload_redacts_inlined_applied_tuning_like_export(self):
        from benchbox.validation.bundle import unanonymized_tuning_findings

        payload = {"platform": {"name": "clickhouse-local", "tuning": {"applied": self._applied()}}}
        assert unanonymized_tuning_findings(payload)

        anonymized = AnonymizationManager().anonymize_result_payload(payload)

        applied = anonymized["platform"]["tuning"]["applied"]
        assert unanonymized_tuning_findings(anonymized) == []
        assert "MergeTree" not in json.dumps(anonymized)
        assert all(entry["statement_redacted"] is True for entry in applied["statements"])
        assert applied["dropped"] == applied["satisfied"] == [{"redacted": True}]
        assert applied["receipt"]["error_redacted"] is True
        assert applied["drift_check"]["drift_redacted"] is True
        assert "statement" in payload["platform"]["tuning"]["applied"]["statements"][0]

        expected = AnonymizationManager().anonymize_result_payload(
            {"platform": {"tuning": {"applied": anonymize_applied_tuning_payload(self._applied())}}}
        )
        assert applied == expected["platform"]["tuning"]["applied"]


class TestIdentifierPrefixDependsOnParentKeys:
    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            (("platform_timing", "requests", "id"), "statement"),
            (("other", "requests", "id"), None),
            (("platform_timing", "other", "id"), None),
            (("id",), None),
            (("mounts", "source"), "path"),
            (("other", "source"), None),
            (("source",), None),
            (("a", "mounts", "target"), "path"),
            (("Mounts", "Destination"), "path"),
            (("Bucket-Name",), "bucket"),
            (("x", "log_bucket"), "bucket"),
            ((), None),
        ],
    )
    def test_prefix_is_decided_by_the_last_three_keys(self, path, expected):
        manager = AnonymizationManager()

        for _ in range(2):
            assert manager._identifier_prefix_for_key_path(path) == expected
            assert manager._identifier_prefix_for_key_path(("unrelated",) + path) == expected
