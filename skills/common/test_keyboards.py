#!/usr/bin/env python3
"""Tests for inline keyboard builders."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from keyboards import (
    button,
    keyboard,
    transport_select_keyboard,
    client_actions_keyboard,
    after_calc_keyboard,
    order_confirm_keyboard,
    manager_menu_keyboard,
    truck_actions_keyboard,
    truck_status_keyboard,
    rate_actions_keyboard,
)


def test_button():
    """Button returns correct Telegram format."""
    b = button("Test", "cb:data")
    assert b == {"text": "Test", "callback_data": "cb:data"}
    print("PASS: test_button")


def test_keyboard_structure():
    """Keyboard wraps rows in inline_keyboard."""
    kb = keyboard([[button("A", "a")], [button("B", "b")]])
    assert "inline_keyboard" in kb
    assert len(kb["inline_keyboard"]) == 2
    assert kb["inline_keyboard"][0][0]["text"] == "A"
    print("PASS: test_keyboard_structure")


def test_transport_select():
    """Transport keyboard has 4 transport options in 2 rows."""
    kb = transport_select_keyboard()
    rows = kb["inline_keyboard"]
    assert len(rows) == 2
    all_buttons = [b for row in rows for b in row]
    assert len(all_buttons) == 4
    callbacks = {b["callback_data"] for b in all_buttons}
    assert callbacks == {"transport:air", "transport:auto", "transport:rail", "transport:sea"}
    print("PASS: test_transport_select")


def test_client_actions():
    """Client actions keyboard has 3 actions."""
    kb = client_actions_keyboard()
    all_buttons = [b for row in kb["inline_keyboard"] for b in row]
    callbacks = {b["callback_data"] for b in all_buttons}
    assert "action:new_calc" in callbacks
    assert "action:my_calcs" in callbacks
    assert "action:cargo_status" in callbacks
    print("PASS: test_client_actions")


def test_after_calc_with_results():
    """After calc keyboard includes order button when has_results=True."""
    kb = after_calc_keyboard(has_results=True)
    all_buttons = [b for row in kb["inline_keyboard"] for b in row]
    callbacks = {b["callback_data"] for b in all_buttons}
    assert "action:place_order" in callbacks
    assert "action:new_calc" in callbacks
    print("PASS: test_after_calc_with_results")


def test_after_calc_without_results():
    """After calc keyboard excludes order button when has_results=False."""
    kb = after_calc_keyboard(has_results=False)
    all_buttons = [b for row in kb["inline_keyboard"] for b in row]
    callbacks = {b["callback_data"] for b in all_buttons}
    assert "action:place_order" not in callbacks
    assert "action:new_calc" in callbacks
    print("PASS: test_after_calc_without_results")


def test_order_confirm():
    """Order confirm keyboard has confirm and cancel."""
    kb = order_confirm_keyboard()
    all_buttons = [b for row in kb["inline_keyboard"] for b in row]
    callbacks = {b["callback_data"] for b in all_buttons}
    assert callbacks == {"order:confirm", "order:cancel"}
    print("PASS: test_order_confirm")


def test_manager_menu():
    """Manager menu has rates, trucks, calc, clients."""
    kb = manager_menu_keyboard()
    all_buttons = [b for row in kb["inline_keyboard"] for b in row]
    callbacks = {b["callback_data"] for b in all_buttons}
    assert "mgr:rates" in callbacks
    assert "mgr:trucks" in callbacks
    assert "action:new_calc" in callbacks
    assert "mgr:clients" in callbacks
    print("PASS: test_manager_menu")


def test_truck_actions():
    """Truck actions keyboard includes truck_id in callbacks."""
    kb = truck_actions_keyboard("025")
    all_buttons = [b for row in kb["inline_keyboard"] for b in row]
    callbacks = {b["callback_data"] for b in all_buttons}
    assert "truck:status:025" in callbacks
    assert "truck:clients:025" in callbacks
    assert "truck:add_client:025" in callbacks
    assert "truck:delete:025" in callbacks
    print("PASS: test_truck_actions")


def test_truck_status():
    """Truck status keyboard has all 7 statuses."""
    kb = truck_status_keyboard("025")
    all_buttons = [b for row in kb["inline_keyboard"] for b in row]
    assert len(all_buttons) == 7
    # All callbacks should start with truck:set_status:025:
    for b in all_buttons:
        assert b["callback_data"].startswith("truck:set_status:025:")
    print("PASS: test_truck_status")


def test_rate_actions():
    """Rate actions keyboard has show, update, add_route, currency."""
    kb = rate_actions_keyboard()
    all_buttons = [b for row in kb["inline_keyboard"] for b in row]
    callbacks = {b["callback_data"] for b in all_buttons}
    assert callbacks == {"rate:show", "rate:update", "rate:add_route", "rate:currency"}
    print("PASS: test_rate_actions")


if __name__ == "__main__":
    test_button()
    test_keyboard_structure()
    test_transport_select()
    test_client_actions()
    test_after_calc_with_results()
    test_after_calc_without_results()
    test_order_confirm()
    test_manager_menu()
    test_truck_actions()
    test_truck_status()
    test_rate_actions()
    print("\n=== ALL KEYBOARD TESTS PASSED ===")
