import pytest

from flagd_ugc.errors import QualityError, TransientError
from flagd_ugc.retry import retry_quality, retry_transient


def test_transient_failure_is_retried_three_times_after_initial_attempt():
    calls = 0

    def operation():
        nonlocal calls
        calls += 1
        raise TransientError("later")

    with pytest.raises(TransientError):
        retry_transient(operation, retries=3, base_delay=0, sleep=lambda _: None)
    assert calls == 4


def test_non_transient_failure_is_not_retried():
    calls = 0

    def operation():
        nonlocal calls
        calls += 1
        raise ValueError("bad request")

    with pytest.raises(ValueError):
        retry_transient(operation, retries=3, base_delay=0, sleep=lambda _: None)
    assert calls == 1


def test_quality_failure_gets_two_retries():
    calls = 0

    def operation():
        nonlocal calls
        calls += 1
        raise QualityError("bad frame")

    with pytest.raises(QualityError):
        retry_quality(operation, retries=2)
    assert calls == 3
