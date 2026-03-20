#!/usr/bin/env python3
"""Tests for rate limiter."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from skills.common.rate_limiter import RateLimiter


def test_basic_flow():
    """Test increment and check within limits."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        limiter = RateLimiter(db_path=Path(f.name))

    # Fresh user — should be allowed
    result = limiter.check("user1", "company1", "calc")
    assert result["allowed"] is True
    assert result["count"] == 0

    # Increment
    count = limiter.increment("user1", "company1", "calc")
    assert count == 1

    # Check again
    result = limiter.check("user1", "company1", "calc")
    assert result["allowed"] is True
    assert result["count"] == 1

    print("PASS: test_basic_flow")


def test_rate_limit_exceeded():
    """Test that limit is enforced."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        limiter = RateLimiter(db_path=Path(f.name))

    # Fill up to limit (use small skill with limit 10)
    for _ in range(10):
        limiter.increment("user1", "company1", "onboarding")

    # Should be denied
    result = limiter.check("user1", "company1", "onboarding")
    assert result["allowed"] is False
    assert result["count"] == 10
    assert "лимит" in result["error"]

    print("PASS: test_rate_limit_exceeded")


def test_separate_users():
    """Different users have separate counters."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        limiter = RateLimiter(db_path=Path(f.name))

    limiter.increment("user1", "company1", "calc")
    limiter.increment("user1", "company1", "calc")
    limiter.increment("user2", "company1", "calc")

    result1 = limiter.check("user1", "company1", "calc")
    result2 = limiter.check("user2", "company1", "calc")
    assert result1["count"] == 2
    assert result2["count"] == 1

    print("PASS: test_separate_users")


def test_get_usage():
    """Test usage summary."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        limiter = RateLimiter(db_path=Path(f.name))

    limiter.increment("user1", "co1", "calc")
    limiter.increment("user1", "co1", "calc")
    limiter.increment("user1", "co1", "parser")

    usage = limiter.get_usage("user1", "co1")
    assert usage["calc"]["count"] == 2
    assert usage["parser"]["count"] == 1

    print("PASS: test_get_usage")


if __name__ == "__main__":
    test_basic_flow()
    test_rate_limit_exceeded()
    test_separate_users()
    test_get_usage()
    print("\n=== ALL RATE LIMITER TESTS PASSED ===")
