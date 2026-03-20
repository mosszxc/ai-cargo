#!/usr/bin/env python3
"""Tests for access control module."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from skills.common.access import is_manager, require_manager, get_manager_ids


def test_default_manager_ids():
    """Default managers for test-company."""
    ids = get_manager_ids("test-company")
    assert "291678304" in ids
    assert "5093456686" in ids
    print("PASS: test_default_manager_ids")


def test_is_manager():
    assert is_manager("291678304", "test-company") is True
    assert is_manager("5093456686", "test-company") is True
    assert is_manager("999999999", "test-company") is False
    # Empty caller_id = no access control (CLI mode)
    assert is_manager("", "test-company") is True
    print("PASS: test_is_manager")


def test_require_manager():
    # Manager — no error
    assert require_manager("291678304", "test-company") is None

    # Client — access denied
    result = require_manager("999999999", "test-company")
    assert result is not None
    assert result["ok"] is False
    assert result["access_denied"] is True
    assert "нет прав" in result["error"]
    print("PASS: test_require_manager")


if __name__ == "__main__":
    test_default_manager_ids()
    test_is_manager()
    test_require_manager()
    print("\n=== ALL ACCESS TESTS PASSED ===")
