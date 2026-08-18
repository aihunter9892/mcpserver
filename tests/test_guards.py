"""Tests for the production guards.

These run without any API key and without network access, so CI stays fast and
free. The LLM call itself is not tested here on purpose - that would spend
tokens on every push.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from guards import InputTooLarge, RateLimiter, _int_env, check_size  # noqa: E402


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

def test_rate_limiter_allows_up_to_the_limit():
    limiter = RateLimiter(per_minute=3)
    assert [limiter.allow("1.2.3.4") for _ in range(3)] == [True, True, True]


def test_rate_limiter_blocks_past_the_limit():
    limiter = RateLimiter(per_minute=2)
    limiter.allow("1.2.3.4")
    limiter.allow("1.2.3.4")
    assert limiter.allow("1.2.3.4") is False


def test_rate_limiter_is_per_client():
    """One noisy IP must not lock out everyone else."""
    limiter = RateLimiter(per_minute=1)
    assert limiter.allow("1.1.1.1") is True
    assert limiter.allow("1.1.1.1") is False
    assert limiter.allow("2.2.2.2") is True


def test_rate_limiter_disabled_when_zero():
    limiter = RateLimiter(per_minute=0)
    assert all(limiter.allow("1.2.3.4") for _ in range(100))


def test_rate_limiter_window_expires(monkeypatch):
    """Requests older than 60s must fall out of the window."""
    import guards

    clock = {"now": 1000.0}
    monkeypatch.setattr(guards.time, "monotonic", lambda: clock["now"])

    limiter = RateLimiter(per_minute=1)
    assert limiter.allow("1.2.3.4") is True
    assert limiter.allow("1.2.3.4") is False

    clock["now"] += 61.0
    assert limiter.allow("1.2.3.4") is True


# ---------------------------------------------------------------------------
# Input size caps
# ---------------------------------------------------------------------------

def test_check_size_passes_normal_input():
    assert check_size("hello", "text") == "hello"


def test_check_size_rejects_oversized_input():
    import guards

    with pytest.raises(InputTooLarge) as err:
        check_size("x" * (guards.MAX_INPUT_CHARS + 1), "text")
    assert "limit is" in str(err.value)


def test_check_size_boundary_is_inclusive():
    import guards

    exactly_at_limit = "x" * guards.MAX_INPUT_CHARS
    assert check_size(exactly_at_limit, "text") == exactly_at_limit


# ---------------------------------------------------------------------------
# Env parsing
# ---------------------------------------------------------------------------

def test_int_env_uses_default_when_unset(monkeypatch):
    monkeypatch.delenv("SOME_LIMIT", raising=False)
    assert _int_env("SOME_LIMIT", 42) == 42


def test_int_env_reads_value(monkeypatch):
    monkeypatch.setenv("SOME_LIMIT", "7")
    assert _int_env("SOME_LIMIT", 42) == 7


def test_int_env_survives_garbage(monkeypatch):
    """A typo in config must not crash the server on boot."""
    monkeypatch.setenv("SOME_LIMIT", "not-a-number")
    assert _int_env("SOME_LIMIT", 42) == 42
