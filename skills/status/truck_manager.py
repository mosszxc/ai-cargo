#!/usr/bin/env python3
"""
truck_manager.py — CLI for managing trucks and client notifications.

Usage:
    python3 truck_manager.py create <truck_id> <route> [--company <id>]
    python3 truck_manager.py status <truck_id> <new_status> [--company <id>]
    python3 truck_manager.py add-client <truck_id> <telegram_id> <name> [--cargo <desc>] [--company <id>]
    python3 truck_manager.py remove-client <truck_id> <telegram_id> [--company <id>]
    python3 truck_manager.py list [--company <id>]
    python3 truck_manager.py clients <truck_id> [--company <id>]
    python3 truck_manager.py lookup <telegram_id> [--company <id>]
    python3 truck_manager.py delete <truck_id> [--company <id>]
    python3 truck_manager.py init-db [--company <id>]
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from skills.common.access import require_manager
from skills.common.keyboards import (
    client_actions_keyboard,
    truck_actions_keyboard,
    truck_status_keyboard,
)
from skills.common.logger import logger

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "companies"

VALID_STATUSES = [
    "warehouse", "packed", "departed", "border", "customs", "moscow", "delivered"
]

STATUS_TEMPLATES = {
    "warehouse": "Ваш груз принят на склад в Китае. Ожидайте отправки.",
    "packed": "Ваш груз упакован и готовится к отправке.",
    "departed": "Ваш груз отправлен! Маршрут: {route}. Ориентировочно: 18-25 дн.",
    "border": "Ваш груз на погранпереходе. Ожидание прохождения.",
    "customs": "Ваш груз прошёл таможню, в пути до Москвы.",
    "moscow": "Ваш груз прибыл в Москву! Свяжитесь для получения.",
    "delivered": "Ваш груз выдан. Спасибо за доверие!",
}

STATUS_LABELS = {
    "warehouse": "На складе в Китае",
    "packed": "Упакован",
    "departed": "Отправлен",
    "border": "На границе",
    "customs": "Таможня пройдена",
    "moscow": "Прибыл в Москву",
    "delivered": "Выдан",
}


def get_db_path(company_id: str) -> Path:
    return DATA_DIR / company_id / "trucks.db"


def get_connection(company_id: str) -> sqlite3.Connection:
    db_path = get_db_path(company_id)
    if not db_path.exists():
        print(f"Ошибка: база данных не найдена: {db_path}", file=sys.stderr)
        print(f"Запустите: python3 {__file__} init-db --company {company_id}", file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(company_id: str):
    db_path = get_db_path(company_id)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trucks (
            id TEXT PRIMARY KEY,
            route TEXT,
            status TEXT DEFAULT 'warehouse',
            status_updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            company_id TEXT DEFAULT 'test-company'
        );
        CREATE TABLE IF NOT EXISTS truck_clients (
            truck_id TEXT,
            client_telegram_id TEXT,
            client_name TEXT,
            cargo_description TEXT,
            FOREIGN KEY (truck_id) REFERENCES trucks(id)
        );
        CREATE INDEX IF NOT EXISTS idx_truck_clients_truck ON truck_clients(truck_id);
        CREATE INDEX IF NOT EXISTS idx_truck_clients_tg ON truck_clients(client_telegram_id);
    """)
    conn.commit()
    conn.close()
    print(json.dumps({"ok": True, "db_path": str(db_path)}))


def create_truck(company_id: str, truck_id: str, route: str):
    conn = get_connection(company_id)
    try:
        conn.execute(
            "INSERT INTO trucks (id, route, status, status_updated_at, company_id) VALUES (?, ?, 'warehouse', ?, ?)",
            (truck_id, route, datetime.now().isoformat(), company_id),
        )
        conn.commit()
        print(json.dumps({
            "ok": True,
            "truck_id": truck_id,
            "route": route,
            "status": "warehouse",
            "status_label": STATUS_LABELS["warehouse"],
        }))
    except sqlite3.IntegrityError:
        print(json.dumps({"ok": False, "error": f"Фура {truck_id} уже существует"}))
        sys.exit(1)
    finally:
        conn.close()


def update_status(company_id: str, truck_id: str, new_status: str):
    if new_status not in VALID_STATUSES:
        print(json.dumps({
            "ok": False,
            "error": f"Неизвестный статус: {new_status}. Доступные: {', '.join(VALID_STATUSES)}"
        }))
        sys.exit(1)

    conn = get_connection(company_id)
    cursor = conn.execute("SELECT id, route, status FROM trucks WHERE id = ?", (truck_id,))
    truck = cursor.fetchone()
    if not truck:
        print(json.dumps({"ok": False, "error": f"Фура {truck_id} не найдена"}))
        conn.close()
        sys.exit(1)

    old_status = truck["status"]
    conn.execute(
        "UPDATE trucks SET status = ?, status_updated_at = ? WHERE id = ?",
        (new_status, datetime.now().isoformat(), truck_id),
    )
    conn.commit()

    # Get clients to notify
    clients = conn.execute(
        "SELECT client_telegram_id, client_name FROM truck_clients WHERE truck_id = ?",
        (truck_id,),
    ).fetchall()
    conn.close()

    template = STATUS_TEMPLATES[new_status]
    notification = template.format(route=truck["route"])

    notifications = []
    for client in clients:
        notifications.append({
            "telegram_id": client["client_telegram_id"],
            "name": client["client_name"],
            "message": notification,
        })

    print(json.dumps({
        "ok": True,
        "truck_id": truck_id,
        "old_status": old_status,
        "old_status_label": STATUS_LABELS.get(old_status, old_status),
        "new_status": new_status,
        "new_status_label": STATUS_LABELS[new_status],
        "notification_text": notification,
        "clients_to_notify": notifications,
        "notify_count": len(notifications),
        "reply_markup": truck_actions_keyboard(truck_id),
    }))


def add_client(company_id: str, truck_id: str, telegram_id: str, name: str, cargo: str = ""):
    conn = get_connection(company_id)
    # Check truck exists
    truck = conn.execute("SELECT id FROM trucks WHERE id = ?", (truck_id,)).fetchone()
    if not truck:
        print(json.dumps({"ok": False, "error": f"Фура {truck_id} не найдена"}))
        conn.close()
        sys.exit(1)

    # Check not already linked
    existing = conn.execute(
        "SELECT 1 FROM truck_clients WHERE truck_id = ? AND client_telegram_id = ?",
        (truck_id, telegram_id),
    ).fetchone()
    if existing:
        print(json.dumps({"ok": False, "error": f"Клиент {name} уже привязан к фуре {truck_id}"}))
        conn.close()
        sys.exit(1)

    conn.execute(
        "INSERT INTO truck_clients (truck_id, client_telegram_id, client_name, cargo_description) VALUES (?, ?, ?, ?)",
        (truck_id, telegram_id, name, cargo),
    )
    conn.commit()
    conn.close()
    print(json.dumps({
        "ok": True,
        "truck_id": truck_id,
        "client_name": name,
        "telegram_id": telegram_id,
    }))


def remove_client(company_id: str, truck_id: str, telegram_id: str):
    conn = get_connection(company_id)
    cursor = conn.execute(
        "DELETE FROM truck_clients WHERE truck_id = ? AND client_telegram_id = ?",
        (truck_id, telegram_id),
    )
    conn.commit()
    deleted = cursor.rowcount
    conn.close()
    if deleted:
        print(json.dumps({"ok": True, "truck_id": truck_id, "telegram_id": telegram_id}))
    else:
        print(json.dumps({"ok": False, "error": f"Клиент {telegram_id} не найден на фуре {truck_id}"}))
        sys.exit(1)


def list_trucks(company_id: str):
    conn = get_connection(company_id)
    trucks = conn.execute(
        "SELECT id, route, status, status_updated_at FROM trucks ORDER BY status_updated_at DESC"
    ).fetchall()

    result = []
    for t in trucks:
        client_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM truck_clients WHERE truck_id = ?", (t["id"],)
        ).fetchone()["cnt"]
        result.append({
            "id": t["id"],
            "route": t["route"],
            "status": t["status"],
            "status_label": STATUS_LABELS.get(t["status"], t["status"]),
            "updated_at": t["status_updated_at"],
            "client_count": client_count,
        })
    conn.close()
    # Each truck gets an actions keyboard
    for t in result:
        t["reply_markup"] = truck_actions_keyboard(t["id"])
    print(json.dumps({"ok": True, "trucks": result, "count": len(result)}))


def list_clients(company_id: str, truck_id: str):
    conn = get_connection(company_id)
    truck = conn.execute("SELECT id, route, status FROM trucks WHERE id = ?", (truck_id,)).fetchone()
    if not truck:
        print(json.dumps({"ok": False, "error": f"Фура {truck_id} не найдена"}))
        conn.close()
        sys.exit(1)

    clients = conn.execute(
        "SELECT client_telegram_id, client_name, cargo_description FROM truck_clients WHERE truck_id = ?",
        (truck_id,),
    ).fetchall()
    conn.close()

    print(json.dumps({
        "ok": True,
        "truck_id": truck_id,
        "route": truck["route"],
        "status": truck["status"],
        "status_label": STATUS_LABELS.get(truck["status"], truck["status"]),
        "clients": [
            {
                "telegram_id": c["client_telegram_id"],
                "name": c["client_name"],
                "cargo": c["cargo_description"],
            }
            for c in clients
        ],
        "count": len(clients),
    }))


def lookup_client(company_id: str, telegram_id: str):
    conn = get_connection(company_id)
    rows = conn.execute(
        """SELECT t.id, t.route, t.status, t.status_updated_at, tc.cargo_description
           FROM truck_clients tc
           JOIN trucks t ON tc.truck_id = t.id
           WHERE tc.client_telegram_id = ?
           ORDER BY t.status_updated_at DESC""",
        (telegram_id,),
    ).fetchall()
    conn.close()

    if not rows:
        print(json.dumps({
            "ok": True,
            "found": False,
            "message": "Не нашёл ваш груз. Обратитесь к менеджеру для привязки к фуре.",
            "reply_markup": client_actions_keyboard(),
        }))
        return

    trucks = []
    for r in rows:
        trucks.append({
            "truck_id": r["id"],
            "route": r["route"],
            "status": r["status"],
            "status_label": STATUS_LABELS.get(r["status"], r["status"]),
            "updated_at": r["status_updated_at"],
            "cargo": r["cargo_description"],
        })

    print(json.dumps({
        "ok": True,
        "found": True,
        "trucks": trucks,
        "count": len(trucks),
        "reply_markup": client_actions_keyboard(),
    }))


def delete_truck(company_id: str, truck_id: str):
    conn = get_connection(company_id)
    # Remove clients first
    conn.execute("DELETE FROM truck_clients WHERE truck_id = ?", (truck_id,))
    cursor = conn.execute("DELETE FROM trucks WHERE id = ?", (truck_id,))
    conn.commit()
    deleted = cursor.rowcount
    conn.close()
    if deleted:
        print(json.dumps({"ok": True, "truck_id": truck_id}))
    else:
        print(json.dumps({"ok": False, "error": f"Фура {truck_id} не найдена"}))
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Truck status manager")
    parser.add_argument("--company", default="test-company", help="Company ID")
    parser.add_argument("--caller-id", default="", help="Telegram ID of caller for access control")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # init-db
    subparsers.add_parser("init-db", help="Initialize database")

    # create
    p_create = subparsers.add_parser("create", help="Create a truck")
    p_create.add_argument("truck_id", help="Truck ID, e.g. '025'")
    p_create.add_argument("route", help="Route, e.g. 'Гуанчжоу→Москва'")

    # status
    p_status = subparsers.add_parser("status", help="Update truck status")
    p_status.add_argument("truck_id", help="Truck ID")
    p_status.add_argument("new_status", choices=VALID_STATUSES, help="New status")

    # add-client
    p_add = subparsers.add_parser("add-client", help="Link client to truck")
    p_add.add_argument("truck_id", help="Truck ID")
    p_add.add_argument("telegram_id", help="Client Telegram ID")
    p_add.add_argument("name", help="Client name")
    p_add.add_argument("--cargo", default="", help="Cargo description")

    # remove-client
    p_rm = subparsers.add_parser("remove-client", help="Unlink client from truck")
    p_rm.add_argument("truck_id", help="Truck ID")
    p_rm.add_argument("telegram_id", help="Client Telegram ID")

    # list
    subparsers.add_parser("list", help="List all trucks")

    # clients
    p_clients = subparsers.add_parser("clients", help="List truck clients")
    p_clients.add_argument("truck_id", help="Truck ID")

    # lookup
    p_lookup = subparsers.add_parser("lookup", help="Lookup trucks by client telegram ID")
    p_lookup.add_argument("telegram_id", help="Client Telegram ID")

    # delete
    p_delete = subparsers.add_parser("delete", help="Delete a truck")
    p_delete.add_argument("truck_id", help="Truck ID")

    args = parser.parse_args()

    # Commands that require manager access
    manager_commands = {"init-db", "create", "status", "add-client", "remove-client", "list", "clients", "delete"}

    if args.command in manager_commands:
        denied = require_manager(args.caller_id, args.company)
        if denied:
            print(json.dumps(denied))
            sys.exit(1)

    # Log the command
    if args.caller_id:
        cmd_args = " ".join(sys.argv[1:])
        logger.log(args.caller_id, args.company, "status", f"{args.command} {cmd_args}", "")

    if args.command == "init-db":
        init_db(args.company)
    elif args.command == "create":
        create_truck(args.company, args.truck_id, args.route)
    elif args.command == "status":
        update_status(args.company, args.truck_id, args.new_status)
    elif args.command == "add-client":
        add_client(args.company, args.truck_id, args.telegram_id, args.name, args.cargo)
    elif args.command == "remove-client":
        remove_client(args.company, args.truck_id, args.telegram_id)
    elif args.command == "list":
        list_trucks(args.company)
    elif args.command == "clients":
        list_clients(args.company, args.truck_id)
    elif args.command == "lookup":
        lookup_client(args.company, args.telegram_id)
    elif args.command == "delete":
        delete_truck(args.company, args.truck_id)


if __name__ == "__main__":
    main()
