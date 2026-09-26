"""Tests for the broker clock (SPEC C2.4)."""

from __future__ import annotations

from datetime import UTC

import pytest

from app.analysis.broker_time import BrokerClock


def _clock(fixed_utc: float) -> BrokerClock:
    return BrokerClock(utc_now_fn=lambda: fixed_utc)


class TestDetection:
    def test_first_sample_sets_offset(self) -> None:
        clock = _clock(fixed_utc=1_700_000_000.0)
        # broker UTC+3: server shows utc+3h encoded as epoch
        assert clock.sample(1_700_000_000.0 + 3 * 3600) is True
        assert clock.offset_minutes == 180
        assert clock.detected is True
        assert clock.utc_offset_string() == "UTC+03:00"

    def test_majority_vote_needed_after_detection(self) -> None:
        clock = _clock(fixed_utc=1_700_000_000.0)
        clock.sample(1_700_000_000.0 + 2 * 3600)  # UTC+2
        # a single outlier sample must NOT flip the offset
        assert clock.sample(1_700_000_000.0 + 3 * 3600) is False
        assert clock.offset_minutes == 120
        # three consecutive agreeing samples flip it
        assert clock.sample(1_700_000_000.0 + 3 * 3600) is False
        assert clock.sample(1_700_000_000.0 + 3 * 3600) is True
        assert clock.offset_minutes == 180

    def test_quarter_hour_latency_absorbed(self) -> None:
        clock = _clock(fixed_utc=1_700_000_000.0)
        # server +3h minus 40 s of latency still quantizes to +180
        assert clock.sample(1_700_000_000.0 + 3 * 3600 - 40) is True
        assert clock.offset_minutes == 180

    def test_dst_change_is_whole_hour(self) -> None:
        clock = _clock(fixed_utc=1_700_000_000.0)
        clock.sample(1_700_000_000.0 + 2 * 3600)  # UTC+2
        for _ in range(3):
            clock.sample(1_700_000_000.0 + 3600)  # UTC+1 (winter time)
        assert clock.offset_minutes == 60

    def test_clamped_to_plausible_range(self) -> None:
        clock = _clock(fixed_utc=1_700_000_000.0)
        clock.sample(1_700_000_000.0 + 100 * 3600)  # absurd offset
        assert abs(clock.offset_minutes) <= 14 * 60


class TestConversion:
    def test_to_utc_and_back(self) -> None:
        clock = _clock(fixed_utc=0.0)
        clock.sample(3 * 3600)
        server = 10_000_000.0
        assert clock.to_utc(server) == server - 3 * 3600
        assert clock.to_server(clock.to_utc(server)) == server

    def test_broker_day_uses_server_wall_time(self) -> None:
        from datetime import datetime

        clock = _clock(fixed_utc=0.0)
        clock.sample(3 * 3600)  # UTC+3
        # 2024-03-10 22:30 UTC → broker wall 2024-03-11 01:30
        utc_epoch = datetime(2024, 3, 10, 22, 30, tzinfo=UTC).timestamp()
        assert clock.broker_day(utc_epoch) == "2024-03-11"

    def test_offset_string_zero_and_negative(self) -> None:
        clock = _clock(fixed_utc=0.0)
        clock.sample(0)
        assert clock.utc_offset_string() == "UTC"
        clock2 = _clock(fixed_utc=0.0)
        clock2.sample(-5 * 3600 - 1800)  # UTC-05:30
        assert clock2.utc_offset_string() == "UTC-05:30"

    def test_sample_with_zero_clock_and_negative_server(self) -> None:
        clock = _clock(fixed_utc=1_700_000_000.0)
        with pytest.raises(TypeError):
            clock.sample("not-a-number")  # type: ignore[arg-type]
