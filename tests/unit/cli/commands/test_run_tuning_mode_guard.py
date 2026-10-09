from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from benchbox.cli.commands.run import _load_unified_tuning_config
from benchbox.cli.tuning_resolver import TuningMode, TuningSource

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _run_state(tmp_path, *, resolved_mode):
    df_file = tmp_path / "df.yaml"
    df_file.write_text("execution:\n  streaming_mode: false\n_metadata:\n  format: dataframe_tuning\n")
    ctx = MagicMock()
    ctx.exit.side_effect = SystemExit(1)
    return SimpleNamespace(
        loaded_unified_config="sentinel",
        tuning_resolution=SimpleNamespace(config_file=df_file, source=TuningSource.EXPLICIT_FILE),
        resolved_mode=resolved_mode,
        config=MagicMock(),
        logger=None,
        ctx=ctx,
        platform="datafusion",
    )


def test_sql_run_refuses_dataframe_tuning_file(tmp_path):
    s = _run_state(tmp_path, resolved_mode="sql")

    with pytest.raises(SystemExit):
        _load_unified_tuning_config(s)

    s.config.load_unified_tuning_config.assert_not_called()


def test_dataframe_run_loads_without_the_sql_guard(tmp_path):
    s = _run_state(tmp_path, resolved_mode="dataframe")
    s.config.load_unified_tuning_config.return_value = "loaded"

    _load_unified_tuning_config(s)

    assert s.loaded_unified_config == "loaded"


@pytest.mark.parametrize(
    ("mode", "source"),
    [(TuningMode.TUNED, TuningSource.FALLBACK), (TuningMode.AUTO, TuningSource.SMART_DEFAULTS)],
)
@pytest.mark.parametrize(("platform", "check_enabled"), [("duckdb", False), ("postgresql", True)])
def test_fallback_and_auto_request_check_constraints_except_on_duckdb(mode, source, platform, check_enabled):
    s = SimpleNamespace(
        tuning_resolution=SimpleNamespace(config_file=None, source=source, mode=mode),
        resolved_mode="sql",
        config=MagicMock(),
        logger=None,
        ctx=MagicMock(),
        platform=platform,
        non_interactive=True,
        quiet=True,
    )

    _load_unified_tuning_config(s)

    assert s.loaded_unified_config.check_constraints.enabled is check_enabled
    assert s.loaded_unified_config.primary_keys.enabled is True
