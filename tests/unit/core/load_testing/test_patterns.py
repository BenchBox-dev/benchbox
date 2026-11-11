# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.experimental.load_testing.patterns import (
    BurstPattern,
    RampUpPattern,
    SpikePattern,
    SteadyPattern,
    StepPattern,
    WavePattern,
    WorkloadPhase,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestSteadyPattern:
    def test_basic_configuration(self):
        pattern = SteadyPattern(concurrency=10, duration_seconds=60)
        assert pattern.max_concurrency == 10
        assert pattern.total_duration == 60

    def test_single_phase(self):
        pattern = SteadyPattern(concurrency=5, duration_seconds=30)
        phases = pattern.get_phases()
        assert len(phases) == 1
        assert phases[0].concurrency == 5
        assert phases[0].duration_seconds == 30
        assert phases[0].phase_name == "steady"

    def test_concurrency_at_time(self):
        pattern = SteadyPattern(concurrency=10, duration_seconds=60)
        assert pattern.get_concurrency_at(0) == 10
        assert pattern.get_concurrency_at(30) == 10
        assert pattern.get_concurrency_at(60) == 10
        assert pattern.get_concurrency_at(61) == 0
        assert pattern.get_concurrency_at(-1) == 0

    def test_invalid_concurrency(self):
        with pytest.raises(ValueError, match="must be at least 1"):
            SteadyPattern(concurrency=0, duration_seconds=60)
        with pytest.raises(ValueError, match="must be at least 1"):
            SteadyPattern(concurrency=-1, duration_seconds=60)

    def test_invalid_duration(self):
        with pytest.raises(ValueError, match="must be positive"):
            SteadyPattern(concurrency=10, duration_seconds=0)
        with pytest.raises(ValueError, match="must be positive"):
            SteadyPattern(concurrency=10, duration_seconds=-10)


class TestBurstPattern:
    def test_basic_configuration(self):
        pattern = BurstPattern(
            base_concurrency=2,
            burst_concurrency=20,
            burst_duration_seconds=5,
            quiet_duration_seconds=10,
            num_bursts=3,
        )
        assert pattern.max_concurrency == 20

    def test_total_duration(self):
        pattern = BurstPattern(
            base_concurrency=2,
            burst_concurrency=20,
            burst_duration_seconds=5,
            quiet_duration_seconds=10,
            num_bursts=3,
        )
        assert pattern.total_duration == 35

    def test_phases(self):
        pattern = BurstPattern(
            base_concurrency=1,
            burst_concurrency=10,
            burst_duration_seconds=5,
            quiet_duration_seconds=5,
            num_bursts=2,
        )
        phases = pattern.get_phases()
        assert len(phases) == 3
        assert phases[0].concurrency == 10
        assert phases[1].concurrency == 1
        assert phases[2].concurrency == 10

    def test_concurrency_at_time(self):
        pattern = BurstPattern(
            base_concurrency=1,
            burst_concurrency=10,
            burst_duration_seconds=5,
            quiet_duration_seconds=5,
            num_bursts=2,
        )
        assert pattern.get_concurrency_at(2) == 10
        assert pattern.get_concurrency_at(7) == 1
        assert pattern.get_concurrency_at(12) == 10

    def test_invalid_parameters(self):
        with pytest.raises(ValueError, match="base_concurrency"):
            BurstPattern(0, 10, 5, 5, 2)
        with pytest.raises(ValueError, match="burst_concurrency"):
            BurstPattern(10, 5, 5, 5, 2)
        with pytest.raises(ValueError, match="num_bursts"):
            BurstPattern(1, 10, 5, 5, 0)


class TestRampUpPattern:
    def test_basic_configuration(self):
        pattern = RampUpPattern(
            start_concurrency=1,
            end_concurrency=100,
            ramp_duration_seconds=60,
        )
        assert pattern.max_concurrency == 100
        assert pattern.total_duration == 60

    def test_with_hold_duration(self):
        pattern = RampUpPattern(
            start_concurrency=1,
            end_concurrency=10,
            ramp_duration_seconds=30,
            hold_duration_seconds=10,
        )
        assert pattern.total_duration == 40

    def test_smooth_ramp_concurrency(self):
        pattern = RampUpPattern(
            start_concurrency=1,
            end_concurrency=101,
            ramp_duration_seconds=100,
        )
        assert pattern.get_concurrency_at(0) == 1
        assert pattern.get_concurrency_at(50) == 51
        assert pattern.get_concurrency_at(99.9) == 100

    def test_stepped_ramp(self):
        pattern = RampUpPattern(
            start_concurrency=1,
            end_concurrency=11,
            ramp_duration_seconds=10,
            step_count=5,
        )
        assert pattern.get_concurrency_at(0) == 1
        assert pattern.get_concurrency_at(2) == 3
        assert pattern.get_concurrency_at(4) == 5

    def test_invalid_parameters(self):
        with pytest.raises(ValueError, match="start_concurrency"):
            RampUpPattern(0, 10, 60)
        with pytest.raises(ValueError, match="end_concurrency"):
            RampUpPattern(10, 5, 60)
        with pytest.raises(ValueError, match="step_count"):
            RampUpPattern(1, 10, 60, step_count=0)


class TestSpikePattern:
    def test_basic_configuration(self):
        pattern = SpikePattern(
            baseline_concurrency=5,
            spike_concurrency=50,
            pre_spike_duration_seconds=10,
            spike_duration_seconds=5,
            post_spike_duration_seconds=10,
        )
        assert pattern.max_concurrency == 50
        assert pattern.total_duration == 25

    def test_phases(self):
        pattern = SpikePattern(
            baseline_concurrency=1,
            spike_concurrency=10,
            pre_spike_duration_seconds=5,
            spike_duration_seconds=3,
            post_spike_duration_seconds=5,
        )
        phases = pattern.get_phases()
        assert len(phases) == 3
        assert phases[0].phase_name == "pre_spike"
        assert phases[1].phase_name == "spike"
        assert phases[2].phase_name == "post_spike"

    def test_concurrency_at_time(self):
        pattern = SpikePattern(
            baseline_concurrency=1,
            spike_concurrency=10,
            pre_spike_duration_seconds=5,
            spike_duration_seconds=3,
            post_spike_duration_seconds=5,
        )
        assert pattern.get_concurrency_at(2) == 1
        assert pattern.get_concurrency_at(6) == 10
        assert pattern.get_concurrency_at(10) == 1

    def test_no_pre_spike(self):
        pattern = SpikePattern(
            baseline_concurrency=1,
            spike_concurrency=10,
            pre_spike_duration_seconds=0,
            spike_duration_seconds=5,
            post_spike_duration_seconds=5,
        )
        phases = pattern.get_phases()
        assert len(phases) == 2
        assert phases[0].phase_name == "spike"


class TestStepPattern:
    def test_basic_configuration(self):
        pattern = StepPattern([(5, 10), (10, 10), (15, 10)])
        assert pattern.max_concurrency == 15
        assert pattern.total_duration == 30

    def test_phases(self):
        pattern = StepPattern([(1, 5), (5, 5), (10, 5)])
        phases = pattern.get_phases()
        assert len(phases) == 3
        assert phases[0].concurrency == 1
        assert phases[1].concurrency == 5
        assert phases[2].concurrency == 10

    def test_concurrency_at_time(self):
        pattern = StepPattern([(1, 10), (5, 10), (10, 10)])
        assert pattern.get_concurrency_at(5) == 1
        assert pattern.get_concurrency_at(15) == 5
        assert pattern.get_concurrency_at(25) == 10
        assert pattern.get_concurrency_at(30) == 0

    def test_empty_steps(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            StepPattern([])

    def test_invalid_step(self):
        with pytest.raises(ValueError, match="concurrency must be at least 1"):
            StepPattern([(0, 10)])
        with pytest.raises(ValueError, match="duration must be positive"):
            StepPattern([(5, 0)])


class TestWavePattern:
    def test_basic_configuration(self):
        pattern = WavePattern(
            min_concurrency=1,
            max_concurrency=10,
            period_seconds=60,
            num_periods=2,
        )
        assert pattern.max_concurrency == 10
        assert pattern.total_duration == 120

    def test_concurrency_oscillation(self):
        pattern = WavePattern(
            min_concurrency=1,
            max_concurrency=11,
            period_seconds=4,
            num_periods=1,
        )
        assert pattern.get_concurrency_at(0) == 1
        assert pattern.get_concurrency_at(2) == 11
        assert pattern.get_concurrency_at(3.9) <= 2

    def test_single_phase(self):
        pattern = WavePattern(
            min_concurrency=1,
            max_concurrency=10,
            period_seconds=30,
        )
        phases = pattern.get_phases()
        assert len(phases) == 1
        assert phases[0].concurrency == 10

    def test_invalid_parameters(self):
        with pytest.raises(ValueError, match="min_concurrency"):
            WavePattern(0, 10, 60)
        with pytest.raises(ValueError, match="max_concurrency"):
            WavePattern(10, 5, 60)
        with pytest.raises(ValueError, match="period_seconds"):
            WavePattern(1, 10, 0)
        with pytest.raises(ValueError, match="num_periods"):
            WavePattern(1, 10, 60, num_periods=0)


class TestWorkloadPhase:
    def test_basic_creation(self):
        phase = WorkloadPhase(concurrency=10, duration_seconds=30, phase_name="test")
        assert phase.concurrency == 10
        assert phase.duration_seconds == 30
        assert phase.phase_name == "test"

    def test_default_phase_name(self):
        phase = WorkloadPhase(concurrency=5, duration_seconds=10)
        assert phase.phase_name == ""


class TestPatternIteration:
    def test_iter_phases(self):
        pattern = StepPattern([(1, 5), (5, 5)])
        phases = list(pattern.iter_phases())
        assert len(phases) == 2

    def test_phases_are_workload_phase(self):
        patterns = [
            SteadyPattern(5, 10),
            BurstPattern(1, 10, 5, 5, 2),
            RampUpPattern(1, 10, 30),
            SpikePattern(1, 10, 5, 5, 5),
            StepPattern([(1, 5), (5, 5)]),
            WavePattern(1, 10, 30),
        ]
        for pattern in patterns:
            for phase in pattern.get_phases():
                assert isinstance(phase, WorkloadPhase)


class TestMultiWriterPattern:
    def test_basic_configuration(self):
        from benchbox.experimental.load_testing.patterns import MultiWriterPattern

        pattern = MultiWriterPattern(writers=3, readers=5, duration_seconds=60)
        assert pattern.max_concurrency == 8
        assert pattern.total_duration == 65.0
        assert pattern.writer_count == 3
        assert pattern.reader_count == 5

    def test_two_phases(self):
        from benchbox.experimental.load_testing.patterns import MultiWriterPattern

        pattern = MultiWriterPattern(writers=2, readers=2, duration_seconds=30, drain_seconds=5)
        phases = pattern.get_phases()
        assert len(phases) == 2
        assert phases[0].concurrency == 4
        assert phases[0].phase_name == "read-write"
        assert phases[0].roles == {"writer": 2, "reader": 2}
        assert phases[1].concurrency == 2
        assert phases[1].phase_name == "write-drain"
        assert phases[1].roles == {"writer": 2}

    def test_phase_role_targets(self):
        from benchbox.experimental.load_testing.patterns import MultiWriterPattern

        pattern = MultiWriterPattern(writers=2, readers=3, duration_seconds=30, drain_seconds=5)
        assert pattern.writers_in_phase("read-write") == 2
        assert pattern.readers_in_phase("read-write") == 3
        assert pattern.writers_in_phase("write-drain") == 2
        assert pattern.readers_in_phase("write-drain") == 0
        with pytest.raises(ValueError, match="unknown"):
            pattern.writers_in_phase("nope")
        with pytest.raises(ValueError, match="unknown"):
            pattern.readers_in_phase("nope")

    def test_drainless_pattern_has_no_drain_roles(self):
        from benchbox.experimental.load_testing.patterns import MultiWriterPattern

        pattern = MultiWriterPattern(writers=2, readers=2, duration_seconds=30, drain_seconds=0)
        assert [phase.roles for phase in pattern.get_phases()] == [{"writer": 2, "reader": 2}]
        with pytest.raises(ValueError, match="absent"):
            pattern.writers_in_phase("write-drain")
        with pytest.raises(ValueError, match="absent"):
            pattern.readers_in_phase("write-drain")

    def test_package_api_exports_multi_writer_pattern(self):
        import benchbox.experimental.load_testing as load_testing

        assert load_testing.MultiWriterPattern.__name__ == "MultiWriterPattern"
        assert "MultiWriterPattern" in load_testing.__all__

    def test_no_drain_single_phase(self):
        from benchbox.experimental.load_testing.patterns import MultiWriterPattern

        pattern = MultiWriterPattern(writers=2, readers=2, duration_seconds=30, drain_seconds=0)
        assert len(pattern.get_phases()) == 1

    def test_concurrency_over_time(self):
        from benchbox.experimental.load_testing.patterns import MultiWriterPattern

        pattern = MultiWriterPattern(writers=2, readers=3, duration_seconds=30, drain_seconds=10)
        assert pattern.get_concurrency_at(-1) == 0
        assert pattern.get_concurrency_at(0) == 5
        assert pattern.get_concurrency_at(30) == 5
        assert pattern.get_concurrency_at(35) == 2
        assert pattern.get_concurrency_at(41) == 0

    def test_rejects_bad_inputs(self):
        from benchbox.experimental.load_testing.patterns import MultiWriterPattern

        with pytest.raises(ValueError):
            MultiWriterPattern(writers=0, readers=1, duration_seconds=10)
        with pytest.raises(ValueError):
            MultiWriterPattern(writers=1, readers=0, duration_seconds=10)
        with pytest.raises(ValueError):
            MultiWriterPattern(writers=1, readers=1, duration_seconds=0)


