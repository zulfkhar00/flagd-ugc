from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from flagd_ugc.scheduler import parse_run_time, seconds_until_run


def test_schedule_targets_eight_am_california():
    now = datetime(2026, 9, 16, 7, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert seconds_until_run(now, time(8, 0)) == 30 * 60


def test_schedule_rolls_to_next_day_after_run_time():
    now = datetime(2026, 9, 16, 8, 1, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert seconds_until_run(now, time(8, 0)) == 23 * 3600 + 59 * 60


def test_schedule_observes_california_spring_clock_change():
    now = datetime(2026, 3, 7, 9, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert seconds_until_run(now, time(8, 0)) == 22 * 3600


def test_schedule_observes_california_autumn_clock_change():
    now = datetime(2026, 10, 31, 9, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert seconds_until_run(now, time(8, 0)) == 24 * 3600


@pytest.mark.parametrize("value", ["6", "25:00", "06:99"])
def test_invalid_run_time(value):
    with pytest.raises(ValueError):
        parse_run_time(value)
