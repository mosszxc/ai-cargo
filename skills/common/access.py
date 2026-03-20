"""Access control for cargo skills.

Loads manager IDs from company config.json.
Falls back to hardcoded IDs for test-company.
"""

import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "companies"

# Fallback for test-company
_DEFAULT_MANAGER_IDS = {"5093456686", "291678304"}


def get_manager_ids(company_id: str) -> set[str]:
    """Get manager Telegram IDs for a company."""
    config_path = DATA_DIR / company_id / "config.json"
    if config_path.exists():
        try:
            with open(config_path) as f:
                config = json.load(f)
            manager_id = config.get("manager_telegram_id")
            if manager_id:
                # Support both single ID and list of IDs
                if isinstance(manager_id, list):
                    return set(str(mid) for mid in manager_id)
                return {str(manager_id)} | _DEFAULT_MANAGER_IDS
        except Exception:
            pass
    return _DEFAULT_MANAGER_IDS


def is_manager(caller_id: str, company_id: str) -> bool:
    """Check if caller is a manager for the given company."""
    if not caller_id:
        return True  # No caller_id = called without access control (CLI/test)
    return str(caller_id) in get_manager_ids(company_id)


def require_manager(caller_id: str, company_id: str) -> dict | None:
    """Check manager access. Returns error dict if denied, None if allowed."""
    if is_manager(caller_id, company_id):
        return None
    return {
        "ok": False,
        "error": "У вас нет прав для выполнения этой команды. Обратитесь к администратору.",
        "access_denied": True,
    }
