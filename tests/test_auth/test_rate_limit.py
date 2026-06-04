"""Unit tests for SlidingWindowLimiter (specs/06-auth.md)."""
from __future__ import annotations

import pytest

from dbzap.auth.rate_limit import SlidingWindowLimiter


def _fake_clock() -> tuple[list[float], callable]:
    """Return (state, time_func). state[0] is "now" — mutate it from tests."""
    state = [0.0]

    def now() -> float:
        return state[0]

    return state, now


def test_allows_up_to_max_calls() -> None:
    _state, clock = _fake_clock()
    lim = SlidingWindowLimiter(max_calls=3, window_seconds=60.0, time_func=clock)
    for _ in range(3):
        ok, retry = lim.check("1.2.3.4")
        assert ok is True
        assert retry == 0.0


def test_blocks_after_max_calls() -> None:
    _state, clock = _fake_clock()
    lim = SlidingWindowLimiter(max_calls=3, window_seconds=60.0, time_func=clock)
    for _ in range(3):
        lim.check("1.2.3.4")
    ok, retry = lim.check("1.2.3.4")
    assert ok is False
    # retry_after points to when the oldest entry expires (= window_seconds
    # since we never advanced the clock).
    assert retry == pytest.approx(60.0, abs=0.01)


def test_recovers_after_window() -> None:
    state, clock = _fake_clock()
    lim = SlidingWindowLimiter(max_calls=2, window_seconds=10.0, time_func=clock)
    lim.check("ip")
    lim.check("ip")
    assert lim.check("ip")[0] is False

    # Advance past the window — the previous entries fall out.
    state[0] = 11.0
    ok, retry = lim.check("ip")
    assert ok is True
    assert retry == 0.0


def test_keys_are_independent() -> None:
    _state, clock = _fake_clock()
    lim = SlidingWindowLimiter(max_calls=1, window_seconds=60.0, time_func=clock)
    assert lim.check("a")[0] is True
    assert lim.check("a")[0] is False  # locked
    # ``b`` has its own bucket — must still be allowed.
    assert lim.check("b")[0] is True


def test_max_calls_zero_disables() -> None:
    _state, clock = _fake_clock()
    lim = SlidingWindowLimiter(max_calls=0, window_seconds=60.0, time_func=clock)
    assert lim.enabled is False
    for _ in range(100):
        assert lim.check("ip")[0] is True


def test_invalid_args_raise() -> None:
    with pytest.raises(ValueError):
        SlidingWindowLimiter(max_calls=-1, window_seconds=60.0)
    with pytest.raises(ValueError):
        SlidingWindowLimiter(max_calls=10, window_seconds=0.0)


def test_reset_clears_state() -> None:
    _state, clock = _fake_clock()
    lim = SlidingWindowLimiter(max_calls=1, window_seconds=60.0, time_func=clock)
    lim.check("ip")
    assert lim.check("ip")[0] is False
    lim.reset("ip")
    assert lim.check("ip")[0] is True
