"""Tests for skills/order/order_manager.py

Coverage: init_db, cmd_preview, cmd_place, cmd_confirm, cmd_cancel, cmd_list
"""

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import skills.order.order_manager as om

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

FAKE_CALC = {
    "id": 1,
    "user_id": "123",
    "company_id": "test-co",
    "product": "электроника",
    "params": {
        "weight_kg": 100,
        "origin": "Гуанчжоу",
        "destination": "Москва",
    },
    "result": {
        "results": [
            {"transport": "авиа", "total_usd": 500},
            {"transport": "море", "total_usd": 200},
        ]
    },
    "summary": "Тестовый расчёт",
    "total_usd": 200,
    "cheapest_transport": "море",
}


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Redirect orders DB and DATA_DIR to a temp directory."""
    db_path = tmp_path / "orders.db"
    monkeypatch.setattr(om, "ORDERS_DB_PATH", db_path)
    monkeypatch.setattr(om, "DATA_DIR", tmp_path)
    return db_path, tmp_path


@pytest.fixture
def inited_db(tmp_db):
    """Initialize the orders DB schema."""
    db_path, data_dir = tmp_db
    om.init_db()
    return db_path, data_dir


def _insert_order(db_path: Path, order_id: str = "ABC12345", user_id: str = "123",
                  company_id: str = "test-co", status: str = "pending") -> str:
    """Insert a test order directly into the DB."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """INSERT INTO orders (id, user_id, company_id, product, weight_kg,
           origin, destination, transport, total_usd, contact,
           params_json, result_json, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (order_id, user_id, company_id, "электроника", 100,
         "Гуанчжоу", "Москва", "море", 200, "",
         json.dumps({"weight_kg": 100}), json.dumps({}), status),
    )
    conn.commit()
    conn.close()
    return order_id


# ---------------------------------------------------------------------------
# init_db
# ---------------------------------------------------------------------------

def test_init_db_creates_orders_table(tmp_db):
    """init_db creates the orders table."""
    db_path, _ = tmp_db
    om.init_db()
    conn = sqlite3.connect(str(db_path))
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    conn.close()
    assert "orders" in tables


def test_init_db_idempotent(tmp_db):
    """init_db can be called multiple times without error."""
    om.init_db()
    om.init_db()


def test_init_db_prints_ok(tmp_db, capsys):
    """init_db prints a JSON ok response."""
    om.init_db()
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True


# ---------------------------------------------------------------------------
# cmd_preview
# ---------------------------------------------------------------------------

def test_preview_returns_preview(inited_db, capsys):
    """cmd_preview returns order preview without writing to DB."""
    db_path, _ = inited_db
    with patch("skills.order.order_manager.CalculationHistory") as MockHist:
        MockHist.return_value.get_by_id.return_value = FAKE_CALC
        om.cmd_preview("123", "test-co", 1)
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    assert result["calc_id"] == 1
    assert result["product"] == "электроника"
    assert result["transport"] == "море"
    assert result["total_usd"] == 200
    assert result["weight_kg"] == 100
    # Verify no DB write occurred
    conn = sqlite3.connect(str(db_path))
    count = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    conn.close()
    assert count == 0


def test_preview_picks_cheapest_transport(inited_db, capsys):
    """cmd_preview selects the cheapest transport option."""
    with patch("skills.order.order_manager.CalculationHistory") as MockHist:
        MockHist.return_value.get_by_id.return_value = FAKE_CALC
        om.cmd_preview("123", "test-co", 1)
    result = json.loads(capsys.readouterr().out)
    # море (200) is cheaper than авиа (500)
    assert result["transport"] == "море"
    assert result["total_usd"] == 200


def test_preview_calc_not_found(inited_db, capsys):
    """cmd_preview exits with error if calc not found."""
    with patch("skills.order.order_manager.CalculationHistory") as MockHist:
        MockHist.return_value.get_by_id.return_value = None
        with pytest.raises(SystemExit):
            om.cmd_preview("123", "test-co", 99)
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False
    assert "error" in result


# ---------------------------------------------------------------------------
# cmd_place
# ---------------------------------------------------------------------------

def test_place_creates_order(inited_db, capsys):
    """cmd_place saves an order to DB and returns order_id."""
    db_path, _ = inited_db
    with patch("skills.order.order_manager.CalculationHistory") as MockHist, \
         patch("skills.order.order_manager._load_config", return_value={}), \
         patch("skills.order.order_manager.logger"):
        MockHist.return_value.get_by_id.return_value = FAKE_CALC
        om.cmd_place("123", "test-co", 1, "Иван +79001234567")
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    assert "order_id" in result
    assert result["status"] == "pending"
    # Verify DB row was created
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT * FROM orders WHERE id = ?", (result["order_id"],)).fetchone()
    conn.close()
    assert row is not None


def test_place_without_contact(inited_db, capsys):
    """cmd_place works when contact is None."""
    with patch("skills.order.order_manager.CalculationHistory") as MockHist, \
         patch("skills.order.order_manager._load_config", return_value={}), \
         patch("skills.order.order_manager.logger"):
        MockHist.return_value.get_by_id.return_value = FAKE_CALC
        om.cmd_place("123", "test-co", 1, None)
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True


def test_place_notifies_managers(inited_db, capsys):
    """cmd_place includes manager notifications in response."""
    config = {"manager_telegram_id": ["999", "888"]}
    with patch("skills.order.order_manager.CalculationHistory") as MockHist, \
         patch("skills.order.order_manager._load_config", return_value=config), \
         patch("skills.order.order_manager.logger"):
        MockHist.return_value.get_by_id.return_value = FAKE_CALC
        om.cmd_place("123", "test-co", 1, None)
    result = json.loads(capsys.readouterr().out)
    assert len(result["managers_to_notify"]) == 2
    tg_ids = [m["telegram_id"] for m in result["managers_to_notify"]]
    assert "999" in tg_ids
    assert "888" in tg_ids


def test_place_calc_not_found(inited_db, capsys):
    """cmd_place exits with error if calc not found."""
    with patch("skills.order.order_manager.CalculationHistory") as MockHist:
        MockHist.return_value.get_by_id.return_value = None
        with pytest.raises(SystemExit):
            om.cmd_place("123", "test-co", 99, None)
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False


# ---------------------------------------------------------------------------
# cmd_confirm
# ---------------------------------------------------------------------------

def test_confirm_sets_status_confirmed(inited_db, capsys):
    """cmd_confirm updates order status to confirmed."""
    db_path, _ = inited_db
    order_id = _insert_order(db_path)
    with patch("skills.order.order_manager.require_manager", return_value=None):
        om.cmd_confirm(order_id, "test-co", "999")
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    assert result["status"] == "confirmed"
    assert result["order_id"] == order_id
    # Verify DB status
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT status FROM orders WHERE id = ?", (order_id,)).fetchone()
    conn.close()
    assert row[0] == "confirmed"


def test_confirm_returns_client_id(inited_db, capsys):
    """cmd_confirm returns the client user_id for sending notifications."""
    db_path, _ = inited_db
    order_id = _insert_order(db_path, user_id="456")
    with patch("skills.order.order_manager.require_manager", return_value=None):
        om.cmd_confirm(order_id, "test-co", "999")
    result = json.loads(capsys.readouterr().out)
    assert result["client_id"] == "456"


def test_confirm_order_not_found(inited_db, capsys):
    """cmd_confirm exits with error if order not found."""
    with patch("skills.order.order_manager.require_manager", return_value=None):
        with pytest.raises(SystemExit):
            om.cmd_confirm("NOTFOUND", "test-co", "999")
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False


# ---------------------------------------------------------------------------
# cmd_cancel
# ---------------------------------------------------------------------------

def test_cancel_by_order_owner(inited_db, capsys):
    """cmd_cancel allows the order owner to cancel."""
    db_path, _ = inited_db
    order_id = _insert_order(db_path, user_id="123")
    om.cmd_cancel(order_id, "test-co", "123")
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    assert result["status"] == "cancelled"
    # Verify DB
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT status FROM orders WHERE id = ?", (order_id,)).fetchone()
    conn.close()
    assert row[0] == "cancelled"


def test_cancel_already_confirmed(inited_db, capsys):
    """cmd_cancel fails if order is already confirmed."""
    db_path, _ = inited_db
    order_id = _insert_order(db_path, status="confirmed")
    with pytest.raises(SystemExit):
        om.cmd_cancel(order_id, "test-co", "123")
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False


def test_cancel_already_cancelled(inited_db, capsys):
    """cmd_cancel fails if order is already cancelled."""
    db_path, _ = inited_db
    order_id = _insert_order(db_path, status="cancelled")
    with pytest.raises(SystemExit):
        om.cmd_cancel(order_id, "test-co", "123")
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False


def test_cancel_order_not_found(inited_db, capsys):
    """cmd_cancel exits with error if order not found."""
    with pytest.raises(SystemExit):
        om.cmd_cancel("NOTFOUND", "test-co", "123")
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False


# ---------------------------------------------------------------------------
# cmd_list
# ---------------------------------------------------------------------------

def test_list_returns_all_orders(inited_db, capsys):
    """cmd_list returns all orders for a company."""
    db_path, _ = inited_db
    _insert_order(db_path, order_id="ORD0001")
    _insert_order(db_path, order_id="ORD0002")
    with patch("skills.order.order_manager.require_manager", return_value=None):
        om.cmd_list("test-co", "999", None)
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    assert result["count"] == 2
    assert len(result["orders"]) == 2


def test_list_filter_by_status(inited_db, capsys):
    """cmd_list filters results when status_filter is provided."""
    db_path, _ = inited_db
    _insert_order(db_path, order_id="ORD0001", status="pending")
    _insert_order(db_path, order_id="ORD0002", status="confirmed")
    with patch("skills.order.order_manager.require_manager", return_value=None):
        om.cmd_list("test-co", "999", "confirmed")
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    assert result["count"] == 1
    assert result["orders"][0]["status"] == "confirmed"


def test_list_empty_company(inited_db, capsys):
    """cmd_list returns empty list when company has no orders."""
    db_path, _ = inited_db
    _insert_order(db_path, company_id="other-company")
    with patch("skills.order.order_manager.require_manager", return_value=None):
        om.cmd_list("test-co", "999", None)
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    assert result["count"] == 0


def test_list_includes_formatted_text(inited_db, capsys):
    """cmd_list response includes formatted text."""
    db_path, _ = inited_db
    _insert_order(db_path)
    with patch("skills.order.order_manager.require_manager", return_value=None):
        om.cmd_list("test-co", "999", None)
    result = json.loads(capsys.readouterr().out)
    assert "formatted" in result
    assert "Заказы" in result["formatted"]


def test_list_order_has_expected_fields(inited_db, capsys):
    """Each order in cmd_list has the required fields."""
    db_path, _ = inited_db
    _insert_order(db_path)
    with patch("skills.order.order_manager.require_manager", return_value=None):
        om.cmd_list("test-co", "999", None)
    result = json.loads(capsys.readouterr().out)
    order = result["orders"][0]
    for field in ("order_id", "status", "product", "weight_kg", "total_usd", "user_id", "created_at"):
        assert field in order, f"Missing field: {field}"
