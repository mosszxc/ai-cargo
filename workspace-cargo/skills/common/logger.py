import sqlite3
import uuid
import functools
from pathlib import Path

# Логи храним отдельно от бизнес-данных (trucks, rates)
LOG_DB_PATH = Path(__file__).parent.parent.parent / "data" / "logs.db"

class DialogLogger:
    def __init__(self):
        self.db_path = LOG_DB_PATH
        self._init_db()

    def _init_db(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS dialog_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trace_id TEXT,
                    user_id TEXT,
                    company_id TEXT,
                    skill_name TEXT,
                    message TEXT,
                    response TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

    def log(self, user_id, company_id, skill_name, message, response, trace_id=None):
        if not trace_id:
            trace_id = str(uuid.uuid4())
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO dialog_logs (trace_id, user_id, company_id, skill_name, message, response) VALUES (?, ?, ?, ?, ?, ?)",
                (trace_id, str(user_id), company_id, skill_name, message, response)
            )
        return trace_id

# Глобальный экземпляр логгера
logger = DialogLogger()

def log_interaction(skill_name):
    """Декоратор для автоматического логирования ответов скиллов."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Предполагаем, что первые три аргумента функции - это user_id, company_id, message
            user_id = args[0] if len(args) > 0 else kwargs.get("user_id")
            company_id = args[1] if len(args) > 1 else kwargs.get("company_id")
            message = args[2] if len(args) > 2 else kwargs.get("message")
            
            response = func(*args, **kwargs)
            
            logger.log(user_id, company_id, skill_name, message, response)
            return response
        return wrapper
    return decorator
