#!/usr/bin/env python3
"""
order_manager.py — CLI for managing cargo orders.

Usage:
    python3 order_manager.py place --user-id <id> --company <id> --calc-id <id> [--contact <text>]
    python3 order_manager.py confirm <order_id> --company <id> --caller-id <manager_tg_id>
    python3 order_manager.py cancel <order_id> --company <id> --caller-id <tg_id>
    python3 order_manager.py get <order_id> --company <id> --caller-id <tg_id>
    python3 order_manager.py list --company <id> --caller-id <manager_tg_id> [--status pending]
    python3 order_manager.py preview --user-id <id> --company <id> --calc-id <id>
    python3 order_manager.py init-db
"""

import argparse
import json
import sqlite3
import sys
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from skills.common.access import require_manager
from skills.common.history import CalculationHistory
from skills.common.keyboards import order_confirm_keyboard, manager_menu_keyboard
from skills.common.logger import logger

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
ORDERS_DB_PATH = DATA_DIR / "orders.db"


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(ORDERS_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(ORDERS_DB_PATH))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            company_id TEXT NOT NULL,
            calc_id INTEGER,
            product TEXT,
            weight_kg REAL,
            origin TEXT,
            destination TEXT,
            transport TEXT,
            total_usd REAL,
            contact TEXT,
            params_json TEXT,
            result_json TEXT,
            status TEXT DEFAULT 'pending',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_orders_user
            ON orders (user_id, company_id, id DESC);
        CREATE INDEX IF NOT EXISTS idx_orders_company_status
            ON orders (company_id, status, created_at DESC);
    """)
    conn.commit()
    conn.close()
    print(json.dumps({"ok": True, "db_path": str(ORDERS_DB_PATH)}))


def _load_config(company_id: str) -> dict:
    config_path = DATA_DIR / "companies" / company_id / "config.json"
    if config_path.exists():
        return json.loads(config_path.read_text())
    return {}


def cmd_preview(user_id: str, company_id: str, calc_id: int):
    """Return order preview from a calculation — no DB write."""
    history = CalculationHistory()
    calc = history.get_by_id(calc_id, user_id)
    if not calc:
        print(json.dumps({
            "ok": False,
            "error": "Расчёт не найден или не принадлежит вам.",
        }))
        sys.exit(1)

    params = calc["params"]
    result = calc.get("result", {})
    results_list = result.get("results", [])
    cheapest = None
    if results_list:
        cheapest = min(results_list, key=lambda r: r.get("total_usd", float("inf")))

    total_usd = cheapest.get("total_usd") if cheapest else calc.get("total_usd")
    transport = cheapest.get("transport") if cheapest else calc.get("cheapest_transport")
    weight_kg = params.get("weight_kg") or (
        (params.get("pieces") or 1) * (params.get("weight_per_piece_kg") or 0)
    )

    preview = {
        "ok": True,
        "calc_id": calc_id,
        "product": calc.get("product", "груз"),
        "weight_kg": weight_kg,
        "origin": params.get("origin", "Гуанчжоу"),
        "destination": params.get("destination", "Москва"),
        "transport": transport or "—",
        "total_usd": total_usd,
        "params": params,
        "result_summary": calc.get("summary", ""),
        "reply_markup": order_confirm_keyboard(),
    }
    print(json.dumps(preview, ensure_ascii=False))


def cmd_place(user_id: str, company_id: str, calc_id: int, contact: str | None):
    """Save a new order based on a calculation."""
    history = CalculationHistory()
    calc = history.get_by_id(calc_id, user_id)
    if not calc:
        print(json.dumps({
            "ok": False,
            "error": "Расчёт не найден или не принадлежит вам.",
        }))
        sys.exit(1)

    params = calc["params"]
    result = calc.get("result", {})
    results_list = result.get("results", [])
    cheapest = None
    if results_list:
        cheapest = min(results_list, key=lambda r: r.get("total_usd", float("inf")))

    total_usd = cheapest.get("total_usd") if cheapest else calc.get("total_usd")
    transport = cheapest.get("transport") if cheapest else calc.get("cheapest_transport")
    weight_kg = params.get("weight_kg") or (
        (params.get("pieces") or 1) * (params.get("weight_per_piece_kg") or 0)
    )

    order_id = str(uuid.uuid4())[:8].upper()
    now = datetime.now().isoformat()

    conn = _get_conn()
    conn.execute(
        """INSERT INTO orders
           (id, user_id, company_id, calc_id, product, weight_kg,
            origin, destination, transport, total_usd, contact,
            params_json, result_json, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
        (
            order_id,
            str(user_id),
            company_id,
            calc_id,
            calc.get("product", "груз"),
            weight_kg,
            params.get("origin", "Гуанчжоу"),
            params.get("destination", "Москва"),
            transport or "",
            total_usd,
            contact or "",
            json.dumps(params, ensure_ascii=False),
            json.dumps(result, ensure_ascii=False),
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()

    config = _load_config(company_id)
    manager_ids = config.get("manager_telegram_id", [])

    total_str = f"${total_usd:,.0f}" if total_usd else "—"
    notification = (
        f"📦 *Новый заказ #{order_id}*\n"
        f"Товар: {calc.get('product', 'груз')}\n"
        f"Маршрут: {params.get('origin', 'Гуанчжоу')}→{params.get('destination', 'Москва')}\n"
        f"Вес: {weight_kg} кг | Транспорт: {transport or '—'}\n"
        f"Сумма: {total_str}\n"
        f"Клиент TG ID: {user_id}"
        + (f"\nКонтакт: {contact}" if contact else "")
    )

    logger.log(
        user_id=str(user_id),
        company_id=company_id,
        skill_name="order",
        message=f"Order placed: {order_id}",
    )

    print(json.dumps({
        "ok": True,
        "order_id": order_id,
        "status": "pending",
        "product": calc.get("product", "груз"),
        "total_usd": total_usd,
        "transport": transport or "—",
        "managers_to_notify": [
            {"telegram_id": mid, "message": notification}
            for mid in manager_ids
        ],
        "client_message": (
            f"✅ *Заказ #{order_id} принят!*\n"
            f"Менеджер свяжется с вами в течение 1-2 часов для уточнения деталей.\n"
            f"Товар: {calc.get('product', 'груз')}\n"
            f"Сумма: {total_str}"
        ),
    }, ensure_ascii=False))


def cmd_confirm(order_id: str, company_id: str, caller_id: str):
    """Manager confirms an order."""
    require_manager(caller_id, company_id)

    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM orders WHERE id = ? AND company_id = ?",
        (order_id.upper(), company_id),
    ).fetchone()
    if not row:
        print(json.dumps({"ok": False, "error": f"Заказ {order_id} не найден"}))
        conn.close()
        sys.exit(1)

    now = datetime.now().isoformat()
    conn.execute(
        "UPDATE orders SET status = 'confirmed', updated_at = ? WHERE id = ?",
        (now, order_id.upper()),
    )
    conn.commit()
    conn.close()

    print(json.dumps({
        "ok": True,
        "order_id": order_id.upper(),
        "status": "confirmed",
        "client_id": row["user_id"],
        "client_message": (
            f"✅ Заказ #{order_id.upper()} подтверждён!\n"
            "Менеджер скоро выйдет на связь для согласования деталей отправки."
        ),
    }, ensure_ascii=False))


def cmd_cancel(order_id: str, company_id: str, caller_id: str):
    """Client or manager cancels an order."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM orders WHERE id = ? AND company_id = ?",
        (order_id.upper(), company_id),
    ).fetchone()
    if not row:
        print(json.dumps({"ok": False, "error": f"Заказ {order_id} не найден"}))
        conn.close()
        sys.exit(1)

    # Clients can only cancel their own orders
    is_manager = False
    try:
        require_manager(caller_id, company_id)
        is_manager = True
    except SystemExit:
        pass

    if not is_manager and str(row["user_id"]) != str(caller_id):
        print(json.dumps({"ok": False, "error": "Нет прав для отмены этого заказа"}))
        conn.close()
        sys.exit(1)

    if row["status"] in ("confirmed", "cancelled"):
        print(json.dumps({
            "ok": False,
            "error": f"Заказ {order_id} уже {row['status']} — отмена невозможна",
        }))
        conn.close()
        sys.exit(1)

    now = datetime.now().isoformat()
    conn.execute(
        "UPDATE orders SET status = 'cancelled', updated_at = ? WHERE id = ?",
        (now, order_id.upper()),
    )
    conn.commit()
    conn.close()

    print(json.dumps({
        "ok": True,
        "order_id": order_id.upper(),
        "status": "cancelled",
        "message": f"Заказ #{order_id.upper()} отменён.",
    }, ensure_ascii=False))


def cmd_get(order_id: str, company_id: str, caller_id: str):
    """Get order details."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM orders WHERE id = ? AND company_id = ?",
        (order_id.upper(), company_id),
    ).fetchone()
    conn.close()

    if not row:
        print(json.dumps({"ok": False, "error": f"Заказ {order_id} не найден"}))
        sys.exit(1)

    is_manager = False
    try:
        require_manager(caller_id, company_id)
        is_manager = True
    except SystemExit:
        pass

    if not is_manager and str(row["user_id"]) != str(caller_id):
        print(json.dumps({"ok": False, "error": "Нет доступа к этому заказу"}))
        sys.exit(1)

    STATUS_LABELS = {
        "pending": "Ожидает подтверждения",
        "confirmed": "Подтверждён",
        "cancelled": "Отменён",
    }
    total_str = f"${row['total_usd']:,.0f}" if row["total_usd"] else "—"

    print(json.dumps({
        "ok": True,
        "order_id": row["id"],
        "status": row["status"],
        "status_label": STATUS_LABELS.get(row["status"], row["status"]),
        "product": row["product"],
        "weight_kg": row["weight_kg"],
        "origin": row["origin"],
        "destination": row["destination"],
        "transport": row["transport"],
        "total_usd": row["total_usd"],
        "total_str": total_str,
        "contact": row["contact"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }, ensure_ascii=False))


def cmd_list(company_id: str, caller_id: str, status_filter: str | None):
    """List orders for a company (manager only)."""
    require_manager(caller_id, company_id)

    conn = _get_conn()
    if status_filter:
        rows = conn.execute(
            "SELECT * FROM orders WHERE company_id = ? AND status = ? ORDER BY created_at DESC LIMIT 20",
            (company_id, status_filter),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM orders WHERE company_id = ? ORDER BY created_at DESC LIMIT 20",
            (company_id,),
        ).fetchall()
    conn.close()

    orders = []
    for row in rows:
        total_str = f"${row['total_usd']:,.0f}" if row["total_usd"] else "—"
        orders.append({
            "order_id": row["id"],
            "status": row["status"],
            "product": row["product"],
            "weight_kg": row["weight_kg"],
            "total_usd": row["total_usd"],
            "total_str": total_str,
            "user_id": row["user_id"],
            "created_at": row["created_at"],
        })

    STATUS_LABELS = {
        "pending": "⏳ Ожидает",
        "confirmed": "✅ Подтверждён",
        "cancelled": "❌ Отменён",
    }

    lines = [f"📦 *Заказы{' (' + status_filter + ')' if status_filter else ''}:*\n"]
    if not orders:
        lines.append("Заказов нет.")
    for o in orders:
        label = STATUS_LABELS.get(o["status"], o["status"])
        lines.append(
            f"{label} *#{o['order_id']}* — {o['product']}, {o['total_str']}\n"
            f"  Клиент: {o['user_id']} | {o['created_at'][:10]}"
        )

    print(json.dumps({
        "ok": True,
        "orders": orders,
        "count": len(orders),
        "formatted": "\n".join(lines),
        "reply_markup": manager_menu_keyboard(),
    }, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="Order manager CLI")
    parser.add_argument("--company", default="test-company")
    parser.add_argument("--caller-id", dest="caller_id", default="")

    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("init-db")

    p_preview = sub.add_parser("preview")
    p_preview.add_argument("--user-id", required=True)
    p_preview.add_argument("--calc-id", required=True, type=int)

    p_place = sub.add_parser("place")
    p_place.add_argument("--user-id", required=True)
    p_place.add_argument("--calc-id", required=True, type=int)
    p_place.add_argument("--contact", default=None)

    p_confirm = sub.add_parser("confirm")
    p_confirm.add_argument("order_id")

    p_cancel = sub.add_parser("cancel")
    p_cancel.add_argument("order_id")

    p_get = sub.add_parser("get")
    p_get.add_argument("order_id")

    p_list = sub.add_parser("list")
    p_list.add_argument("--status", default=None)

    args = parser.parse_args()

    if args.cmd == "init-db":
        init_db()
    elif args.cmd == "preview":
        cmd_preview(args.user_id, args.company, args.calc_id)
    elif args.cmd == "place":
        cmd_place(args.user_id, args.company, args.calc_id, args.contact)
    elif args.cmd == "confirm":
        cmd_confirm(args.order_id, args.company, args.caller_id)
    elif args.cmd == "cancel":
        cmd_cancel(args.order_id, args.company, args.caller_id)
    elif args.cmd == "get":
        cmd_get(args.order_id, args.company, args.caller_id)
    elif args.cmd == "list":
        cmd_list(args.company, args.caller_id, args.status)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
