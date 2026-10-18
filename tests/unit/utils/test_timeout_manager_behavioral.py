# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import time

import pytest

from benchbox.utils.timeout_manager import (
    TimeoutConfig,
    TimeoutError,
    TimeoutManager,
    get_timeout_manager,
    run_with_timeout,
    timeout,
)

pytestmark = [pytest.mark.unit, pytest.mark.medium]


class TestRunWithTimeoutBehavior:
    def test_slow_function_actually_times_out(self):

        def slow():
            time.sleep(2.0)
            return "done"

        result, timed_out = run_with_timeout(slow, 0.1, "slow_test")
        assert timed_out is True
        assert result is None

    def test_fast_function_returns_result(self):

        def fast():
            return "hello"

        result, timed_out = run_with_timeout(fast, 1.0, "fast_test")
        assert timed_out is False
        assert result == "hello"

    def test_function_returning_none_distinguished_from_timeout(self):

        def returns_none():
            return None

        result, timed_out = run_with_timeout(returns_none, 1.0, "none_test")
        assert timed_out is False
        assert result is None

    def test_exception_propagated_not_swallowed(self):

        def raises():
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            run_with_timeout(raises, 1.0, "error_test")

    def test_args_and_kwargs_forwarded(self):

        def concat(a, b, sep="-"):
            return f"{a}{sep}{b}"

        result, timed_out = run_with_timeout(concat, 1.0, "concat", "foo", "bar", sep=":")
        assert timed_out is False
        assert result == "foo:bar"


class TestTimeoutContextManagerConvenience:
    def test_no_timeout_when_work_is_fast(self):
        with timeout(1.0, "fast_work") as ctx:
            x = 1 + 1
        assert ctx.timed_out is False
        assert x == 2

    def test_timeout_fires_when_work_is_slow(self):
        with timeout(0.1, "slow_work", raise_on_timeout=False) as ctx:
            time.sleep(0.3)
        assert ctx.timed_out is True

    def test_timeout_raises_by_default(self):
        with pytest.raises(TimeoutError) as exc_info:
            with timeout(0.1, "should_raise"):
                time.sleep(0.3)
        assert exc_info.value.timeout_seconds == 0.1
        assert exc_info.value.operation == "should_raise"

    def test_raise_on_timeout_false_returns_context(self):
        with timeout(0.1, "no_raise", raise_on_timeout=False) as ctx:
            time.sleep(0.3)
        assert ctx.timed_out is True
        assert ctx.triggered_at is not None
        assert ctx.triggered_at > 0


class TestTimeoutManagerSingleton:
    def test_returns_timeout_manager_instance(self):
        mgr = get_timeout_manager()
        assert isinstance(mgr, TimeoutManager)

    def test_same_instance_across_calls(self):
        mgr1 = get_timeout_manager()
        mgr2 = get_timeout_manager()
        assert mgr1 is mgr2

    def test_singleton_has_default_timeout(self):
        mgr = get_timeout_manager()
        assert mgr.default_timeout_seconds == 300


class TestNestedTimeoutContexts:
    def test_inner_timeout_fires_before_outer(self):
        manager = TimeoutManager(default_timeout_seconds=300)

        with manager.timeout_context(2.0, "outer", raise_on_timeout=False) as outer_ctx:
            with manager.timeout_context(0.1, "inner", raise_on_timeout=False) as inner_ctx:
                time.sleep(0.3)

        assert inner_ctx.timed_out is True
        assert outer_ctx.timed_out is False

    def test_inner_timeout_raises_without_affecting_outer(self):
        manager = TimeoutManager(default_timeout_seconds=300)

        with manager.timeout_context(2.0, "outer", raise_on_timeout=False) as outer_ctx:
            with pytest.raises(TimeoutError, match="inner"):
                with manager.timeout_context(0.1, "inner", raise_on_timeout=True):
                    time.sleep(0.3)

        assert outer_ctx.timed_out is False

    def test_both_contexts_complete_without_timeout(self):
        manager = TimeoutManager(default_timeout_seconds=300)

        with manager.timeout_context(1.0, "outer") as outer_ctx:
            with manager.timeout_context(0.5, "inner") as inner_ctx:
                pass

        assert inner_ctx.timed_out is False
        assert outer_ctx.timed_out is False


class TestTimeoutConfigCallback:
    def test_callback_stored_on_config(self):
        called = []

        def on_timeout():
            called.append(True)

        config = TimeoutConfig(timeout_seconds=1.0, operation_name="cb_test", on_timeout=on_timeout)
        assert config.on_timeout is on_timeout
        config.on_timeout()
        assert called == [True]

    def test_config_without_callback_defaults_to_none(self):
        config = TimeoutConfig(timeout_seconds=5.0)
        assert config.on_timeout is None

    def test_config_raise_on_timeout_default_true(self):
        config = TimeoutConfig(timeout_seconds=5.0)
        assert config.raise_on_timeout is True

    def test_config_raise_on_timeout_explicit_false(self):
        config = TimeoutConfig(timeout_seconds=5.0, raise_on_timeout=False)
        assert config.raise_on_timeout is False


class TestTimeoutContextUsagePatterns:
    def test_context_reports_operation_name(self):
        manager = TimeoutManager()
        with manager.timeout_context(1.0, "my_query") as ctx:
            pass
        assert ctx.operation_name == "my_query"
        assert ctx.timeout_seconds == 1.0

    def test_context_uses_default_timeout_when_none(self):
        manager = TimeoutManager(default_timeout_seconds=42.0)
        with manager.timeout_context(operation_name="default_test") as ctx:
            pass
        assert ctx.timeout_seconds == 42.0

    def test_timer_cleaned_up_after_normal_exit(self):
        manager = TimeoutManager()
        with manager.timeout_context(1.0, "cleanup_test"):
            pass
        assert len(manager._active_timers) == 0

    def test_timer_cleaned_up_after_timeout(self):
        manager = TimeoutManager()
        with manager.timeout_context(0.1, "cleanup_timeout", raise_on_timeout=False):
            time.sleep(0.3)
        assert len(manager._active_timers) == 0

    def test_cancel_all_timers_clears_active(self):
        manager = TimeoutManager()
        ctx_mgr = manager.timeout_context(300.0, "long_running")
        ctx_mgr.__enter__()
        assert len(manager._active_timers) == 1

        manager.cancel_all_timers()
        assert len(manager._active_timers) == 0

        ctx_mgr.__exit__(None, None, None)


class TestTimeoutErrorAttributes:
    def test_error_from_context_has_correct_fields(self):
        manager = TimeoutManager()
        with pytest.raises(TimeoutError) as exc_info:
            with manager.timeout_context(0.1, "attr_test", raise_on_timeout=True):
                time.sleep(0.3)
        err = exc_info.value
        assert err.timeout_seconds == 0.1
        assert err.operation == "attr_test"
        assert "attr_test" in str(err)
        assert "0.1" in str(err)

    def test_error_is_catchable_as_exception(self):
        err = TimeoutError("test", timeout_seconds=1.0)
        assert isinstance(err, Exception)


class TestEdgeCases:
    def test_very_short_timeout_does_not_crash(self):
        result, timed_out = run_with_timeout(lambda: time.sleep(0.5), 0.01, "tiny_timeout")
        assert timed_out is True
        assert result is None

    def test_timeout_config_rejects_zero(self):
        with pytest.raises(ValueError, match="positive"):
            TimeoutConfig(timeout_seconds=0)

    def test_timeout_config_rejects_negative(self):
        with pytest.raises(ValueError, match="positive"):
            TimeoutConfig(timeout_seconds=-1.0)

    def test_run_with_timeout_passes_complex_return(self):

        def complex_return():
            return {"key": [1, 2, 3], "nested": {"a": True}}

        result, timed_out = run_with_timeout(complex_return, 1.0, "complex")
        assert timed_out is False
        assert result == {"key": [1, 2, 3], "nested": {"a": True}}
