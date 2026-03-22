#!/usr/bin/env python3
"""
HTTP health-check endpoint for the outreach bot.

Checks:
  - SQLite database readable (not locked/corrupt)
  - Disk space > 500MB free
  - Returns JSON status on GET /health

Usage:
  python -m scripts.avito_outreach.healthcheck
  curl http://localhost:8080/health

Env vars:
  AVITO_DB_PATH  — SQLite database path (default: data/avito_sellers.db)
  HEALTH_PORT    — HTTP port (default: 8080)
"""

import json
import os
import shutil
import sqlite3
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "avito_sellers.db"
PORT = int(os.environ.get("HEALTH_PORT", "8080"))


def check_database() -> tuple[bool, str]:
    """Check SQLite database is readable."""
    db_path = os.environ.get("AVITO_DB_PATH", str(DEFAULT_DB_PATH))
    if not os.path.exists(db_path):
        return True, "db not created yet (ok for fresh deploy)"
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.execute("SELECT 1")
        conn.close()
        return True, "ok"
    except Exception as e:
        return False, f"db error: {e}"


def check_disk_space() -> tuple[bool, str]:
    """Check at least 500MB free disk space."""
    usage = shutil.disk_usage("/")
    free_mb = usage.free // (1024 * 1024)
    if free_mb < 500:
        return False, f"low disk: {free_mb}MB free"
    return True, f"{free_mb}MB free"


def run_checks() -> dict:
    """Run all health checks and return status dict."""
    checks = {}
    all_ok = True

    db_ok, db_msg = check_database()
    checks["database"] = {"ok": db_ok, "detail": db_msg}
    if not db_ok:
        all_ok = False

    disk_ok, disk_msg = check_disk_space()
    checks["disk"] = {"ok": disk_ok, "detail": disk_msg}
    if not disk_ok:
        all_ok = False

    return {"status": "healthy" if all_ok else "unhealthy", "checks": checks}


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            result = run_checks()
            status_code = 200 if result["status"] == "healthy" else 503
            body = json.dumps(result, ensure_ascii=False).encode()
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def write(self, data: bytes):
        self.wfile.write(data)

    def log_message(self, format, *args):
        # Suppress default access logs to keep output clean
        pass


def main():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    print(f"Health-check server listening on :{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Shutting down health-check server")
        server.server_close()


if __name__ == "__main__":
    main()
