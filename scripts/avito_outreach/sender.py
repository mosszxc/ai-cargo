#!/usr/bin/env python3
"""
Avito outreach sender — sends personalized messages to scored seller candidates.

Uses Playwright with antidetect browser profiles (GoLogin / Dolphin Anty)
for human-like browser automation. Includes account pool rotation,
rate limiting, warm-up mode, and captcha/block detection.

Usage:
  python -m scripts.avito_outreach.sender [--config accounts.json] [--warm-up]
  python scripts/avito_outreach/sender.py --stats
  python scripts/avito_outreach/sender.py --dry-run --limit 5

Env vars:
  AVITO_DB_PATH       — SQLite database path (default: data/avito_sellers.db)
  AVITO_ACCOUNTS_PATH — Path to accounts JSON config (default: data/avito_accounts.json)
  ANTHROPIC_API_KEY   — Claude API key for message personalization
  GOLOGIN_API_KEY     — GoLogin API key (if using GoLogin profiles)
"""

import asyncio
import json
import logging
import os
import random
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger("avito_sender")

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "avito_sellers.db"
DEFAULT_ACCOUNTS_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "avito_accounts.json"

# Rate limits
MAX_MESSAGES_PER_ACCOUNT_PER_DAY = 12  # conservative: 10-15 range
MIN_PAUSE_SECONDS = 5 * 60   # 5 minutes
MAX_PAUSE_SECONDS = 15 * 60  # 15 minutes
WARM_UP_DAYS = 4  # 3-5 day warm-up before sending

# Playwright selectors for Avito messenger
SELECTORS = {
    "login_email": 'input[data-marker="login-form/login"]',
    "login_password": 'input[data-marker="login-form/password"]',
    "login_submit": 'button[data-marker="login-form/submit"]',
    "message_input": 'textarea[data-marker="messenger/input"]',
    "message_send": 'button[data-marker="messenger/send"]',
    "captcha_frame": 'iframe[src*="captcha"]',
    "block_notice": '[data-marker="blocked-notice"]',
    "chat_link": 'a[data-marker="item-chat"]',
    "favorite_button": 'button[data-marker="item-favorite"]',
}


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class Account:
    login: str
    password: str
    profile_id: str  # GoLogin / Dolphin Anty profile ID
    user_id: Optional[int] = None  # Avito user ID (filled after login)
    created_at: Optional[str] = None  # When this account was added
    warm_up_start: Optional[str] = None  # When warm-up began
    provider: str = "gologin"  # "gologin" or "dolphin"


@dataclass
class SendResult:
    seller_id: int
    status: str  # "sent", "blocked", "captcha", "error", "skipped"
    message_text: str
    account_login: str
    error_detail: Optional[str] = None


@dataclass
class SessionStats:
    messages_sent: int = 0
    messages_failed: int = 0
    captchas_hit: int = 0
    blocks_hit: int = 0
    accounts_used: int = 0
    sellers_processed: int = 0


# ---------------------------------------------------------------------------
# SQLite outreach log
# ---------------------------------------------------------------------------

OUTREACH_LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS outreach_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seller_id INTEGER NOT NULL,
    message_text TEXT NOT NULL,
    sent_at TEXT DEFAULT (datetime('now')),
    account_used TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'sent',
    error_detail TEXT,
    FOREIGN KEY (seller_id) REFERENCES sellers(seller_id)
);
CREATE INDEX IF NOT EXISTS idx_outreach_seller ON outreach_log(seller_id);
CREATE INDEX IF NOT EXISTS idx_outreach_account ON outreach_log(account_used);
CREATE INDEX IF NOT EXISTS idx_outreach_sent_at ON outreach_log(sent_at);
"""


def _init_outreach_table(conn: sqlite3.Connection):
    conn.executescript(OUTREACH_LOG_SCHEMA)


def log_outreach(
    conn: sqlite3.Connection,
    result: SendResult,
):
    """Write a send result to the outreach log."""
    conn.execute(
        """INSERT INTO outreach_log (seller_id, message_text, account_used, status, error_detail)
           VALUES (?, ?, ?, ?, ?)""",
        (result.seller_id, result.message_text, result.account_login,
         result.status, result.error_detail),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Account pool management
# ---------------------------------------------------------------------------

def load_accounts(config_path: Optional[Path] = None) -> list[Account]:
    """Load account pool from JSON config.

    Expected format:
    [
      {
        "login": "user@mail.ru",
        "password": "...",
        "profile_id": "gologin-profile-uuid",
        "user_id": 12345678,
        "created_at": "2026-03-01",
        "warm_up_start": "2026-03-15",
        "provider": "gologin"
      }
    ]
    """
    config_path = config_path or Path(
        os.environ.get("AVITO_ACCOUNTS_PATH", str(DEFAULT_ACCOUNTS_PATH))
    )
    if not config_path.exists():
        logger.error("Accounts config not found: %s", config_path)
        return []

    with open(config_path) as f:
        data = json.load(f)

    accounts = []
    for entry in data:
        accounts.append(Account(
            login=entry["login"],
            password=entry["password"],
            profile_id=entry["profile_id"],
            user_id=entry.get("user_id"),
            created_at=entry.get("created_at"),
            warm_up_start=entry.get("warm_up_start"),
            provider=entry.get("provider", "gologin"),
        ))

    logger.info("Loaded %d accounts from %s", len(accounts), config_path)
    return accounts


def get_daily_send_count(conn: sqlite3.Connection, account_login: str) -> int:
    """Count messages sent by this account today."""
    row = conn.execute(
        """SELECT COUNT(*) FROM outreach_log
           WHERE account_used = ? AND sent_at >= date('now')
           AND status = 'sent'""",
        (account_login,),
    ).fetchone()
    return row[0] if row else 0


def is_account_warm(account: Account) -> bool:
    """Check if account has completed warm-up period."""
    if not account.warm_up_start:
        return False
    try:
        start = datetime.strptime(account.warm_up_start, "%Y-%m-%d")
        return (datetime.now() - start).days >= WARM_UP_DAYS
    except ValueError:
        return False


def pick_account(
    accounts: list[Account],
    conn: sqlite3.Connection,
    warm_up_mode: bool = False,
) -> Optional[Account]:
    """Select an account that hasn't hit its daily limit.

    In warm_up_mode, returns any account (warm or not) for warm-up actions.
    In normal mode, only returns warm accounts with remaining quota.
    """
    random.shuffle(accounts)
    for acc in accounts:
        if not warm_up_mode and not is_account_warm(acc):
            logger.debug("Skipping %s — not warm yet", acc.login)
            continue

        count = get_daily_send_count(conn, acc.login)
        if count >= MAX_MESSAGES_PER_ACCOUNT_PER_DAY:
            logger.debug("Skipping %s — daily limit reached (%d)", acc.login, count)
            continue

        return acc

    return None


# ---------------------------------------------------------------------------
# Candidate selection
# ---------------------------------------------------------------------------

def get_candidates(
    conn: sqlite3.Connection,
    limit: int = 50,
) -> list[dict]:
    """Get scored candidates that haven't been contacted yet."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT s.seller_id, s.item_id, s.title, s.price, s.category, s.city,
                  s.items_count, ss.score, ss.score_breakdown
           FROM scored_sellers ss
           JOIN sellers s ON s.seller_id = ss.seller_id
           WHERE ss.is_candidate = 1
             AND ss.seller_id NOT IN (
                 SELECT seller_id FROM outreach_log WHERE status = 'sent'
             )
           ORDER BY ss.score DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Browser profile launchers
# ---------------------------------------------------------------------------

async def _launch_gologin_profile(profile_id: str, api_key: Optional[str] = None):
    """Launch a GoLogin browser profile and return Playwright browser context.

    Requires gologin package: pip install gologin
    """
    api_key = api_key or os.environ.get("GOLOGIN_API_KEY")
    if not api_key:
        raise RuntimeError("GOLOGIN_API_KEY not set")

    from gologin import GoLogin

    gl = GoLogin({"token": api_key, "profile_id": profile_id})
    debugger_address = gl.start()

    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    browser = await pw.chromium.connect_over_cdp(f"http://{debugger_address}")
    context = browser.contexts[0]
    return pw, browser, context, gl


async def _launch_dolphin_profile(profile_id: str):
    """Launch a Dolphin Anty browser profile and return Playwright browser context.

    Dolphin Anty exposes a local API to start profiles.
    """
    import urllib.request

    # Dolphin Anty local API
    url = f"http://localhost:3001/v1.0/browser_profiles/{profile_id}/start?automation=1"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())

    ws_endpoint = data["automation"]["wsEndpoint"]
    port = data["automation"]["port"]

    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    browser = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}{ws_endpoint}")
    context = browser.contexts[0]
    return pw, browser, context, None


async def launch_browser(account: Account):
    """Launch antidetect browser for the given account. Returns (pw, browser, context, cleanup)."""
    if account.provider == "gologin":
        return await _launch_gologin_profile(account.profile_id)
    elif account.provider == "dolphin":
        return await _launch_dolphin_profile(account.profile_id)
    else:
        raise ValueError(f"Unknown provider: {account.provider}")


# ---------------------------------------------------------------------------
# Playwright automation: Avito actions
# ---------------------------------------------------------------------------

async def _avito_login(page, account: Account) -> bool:
    """Log into Avito with provided credentials. Returns True on success."""
    try:
        await page.goto("https://www.avito.ru/login", wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_selector(SELECTORS["login_email"], timeout=10000)

        await page.fill(SELECTORS["login_email"], account.login)
        await asyncio.sleep(random.uniform(0.5, 1.5))
        await page.fill(SELECTORS["login_password"], account.password)
        await asyncio.sleep(random.uniform(0.3, 1.0))
        await page.click(SELECTORS["login_submit"])

        # Wait for redirect to main page or profile
        await page.wait_for_url("**/profile/**", timeout=15000)
        logger.info("Logged in as %s", account.login)
        return True
    except Exception as e:
        logger.error("Login failed for %s: %s", account.login, e)
        return False


async def _check_captcha_or_block(page) -> Optional[str]:
    """Check if page shows captcha or block. Returns 'captcha', 'blocked', or None."""
    try:
        if await page.query_selector(SELECTORS["captcha_frame"]):
            return "captcha"
        if await page.query_selector(SELECTORS["block_notice"]):
            return "blocked"
    except Exception:
        pass
    return None


async def _send_message_to_seller(
    page,
    item_id: int,
    account: Account,
    message_text: str,
) -> str:
    """Navigate to item and send a message. Returns status string."""
    item_url = f"https://www.avito.ru/items/{item_id}"
    try:
        await page.goto(item_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(random.uniform(2.0, 5.0))  # Human-like browsing delay

        # Check for captcha/block
        obstacle = await _check_captcha_or_block(page)
        if obstacle:
            return obstacle

        # Click "Write message" button
        chat_btn = await page.query_selector(SELECTORS["chat_link"])
        if not chat_btn:
            # Try direct messenger URL
            user_id = account.user_id or 0
            chat_url = f"https://www.avito.ru/profile/messenger/channel/u2i-{item_id}-{user_id}"
            await page.goto(chat_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(random.uniform(1.5, 3.0))
        else:
            await chat_btn.click()
            await asyncio.sleep(random.uniform(2.0, 4.0))

        obstacle = await _check_captcha_or_block(page)
        if obstacle:
            return obstacle

        # Type and send message
        msg_input = await page.wait_for_selector(
            SELECTORS["message_input"], timeout=10000
        )
        # Type character-by-character for human-like input
        for char in message_text:
            await msg_input.type(char, delay=random.randint(30, 120))
            if random.random() < 0.02:  # Occasional longer pause
                await asyncio.sleep(random.uniform(0.3, 0.8))

        await asyncio.sleep(random.uniform(0.5, 2.0))

        send_btn = await page.query_selector(SELECTORS["message_send"])
        if send_btn:
            await send_btn.click()
        else:
            await msg_input.press("Enter")

        await asyncio.sleep(random.uniform(1.0, 2.0))
        return "sent"

    except Exception as e:
        logger.error("Failed to send message to item %d: %s", item_id, e)
        return "error"


async def _warm_up_actions(page, item_id: int):
    """Perform warm-up actions on an item: view listing, scroll, add to favorites."""
    item_url = f"https://www.avito.ru/items/{item_id}"
    try:
        await page.goto(item_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(random.uniform(3.0, 8.0))

        # Scroll down like a real user
        for _ in range(random.randint(2, 5)):
            await page.mouse.wheel(0, random.randint(200, 500))
            await asyncio.sleep(random.uniform(0.5, 2.0))

        # Sometimes add to favorites
        if random.random() < 0.3:
            fav_btn = await page.query_selector(SELECTORS["favorite_button"])
            if fav_btn:
                await fav_btn.click()
                await asyncio.sleep(random.uniform(1.0, 3.0))
                logger.debug("Added item %d to favorites", item_id)

        logger.debug("Warm-up view completed for item %d", item_id)
    except Exception as e:
        logger.debug("Warm-up action failed for item %d: %s", item_id, e)


# ---------------------------------------------------------------------------
# Main sender orchestrator
# ---------------------------------------------------------------------------

async def run_sender(
    db_path: Optional[Path] = None,
    accounts_path: Optional[Path] = None,
    warm_up: bool = False,
    dry_run: bool = False,
    limit: Optional[int] = None,
) -> SessionStats:
    """Run the outreach sender session.

    Args:
        db_path: SQLite database path.
        accounts_path: Accounts JSON config path.
        warm_up: If True, perform warm-up actions instead of sending messages.
        dry_run: If True, generate messages but don't send.
        limit: Max sellers to process this session.

    Returns:
        Session statistics.
    """
    from .message_templates import generate_message

    db_path = db_path or Path(os.environ.get("AVITO_DB_PATH", str(DEFAULT_DB_PATH)))
    stats = SessionStats()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _init_outreach_table(conn)

    accounts = load_accounts(accounts_path)
    if not accounts:
        logger.error("No accounts configured. Exiting.")
        conn.close()
        return stats

    candidates = get_candidates(conn, limit=limit or 50)
    if not candidates:
        logger.info("No new candidates to contact. Exiting.")
        conn.close()
        return stats

    logger.info("Found %d candidates to process", len(candidates))

    # Track which accounts we used
    used_accounts = set()
    pw_instance = None
    browser = None
    context = None
    cleanup = None
    current_account = None

    try:
        for seller in candidates:
            # Pick an account with remaining quota
            account = pick_account(accounts, conn, warm_up_mode=warm_up)
            if not account:
                logger.warning("All accounts exhausted for today. Stopping.")
                break

            # Switch browser profile if account changed
            if account.login != (current_account and current_account.login):
                # Close previous browser
                if browser:
                    await browser.close()
                if pw_instance:
                    await pw_instance.stop()
                if cleanup and hasattr(cleanup, "stop"):
                    cleanup.stop()

                if not dry_run:
                    pw_instance, browser, context, cleanup = await launch_browser(account)
                    page = await context.new_page()

                    # Login if needed
                    if not await _avito_login(page, account):
                        logger.error("Cannot login with %s, skipping", account.login)
                        stats.messages_failed += 1
                        continue

                current_account = account
                used_accounts.add(account.login)

            stats.sellers_processed += 1

            if warm_up:
                # Warm-up: just browse and view items
                if not dry_run:
                    await _warm_up_actions(page, seller["item_id"])
                logger.info(
                    "[WARM-UP] Viewed seller %d (item %d) via %s",
                    seller["seller_id"], seller["item_id"], account.login,
                )
                pause = random.uniform(60, 180)  # 1-3 min pause for warm-up
            else:
                # Generate personalized message
                message = generate_message(seller)

                if dry_run:
                    logger.info(
                        "[DRY-RUN] Would send to seller %d: %s",
                        seller["seller_id"], message[:80],
                    )
                    result = SendResult(
                        seller_id=seller["seller_id"],
                        status="skipped",
                        message_text=message,
                        account_login=account.login,
                        error_detail="dry_run",
                    )
                else:
                    status = await _send_message_to_seller(
                        page, seller["item_id"], account, message,
                    )
                    result = SendResult(
                        seller_id=seller["seller_id"],
                        status=status,
                        message_text=message,
                        account_login=account.login,
                    )

                # Log result
                log_outreach(conn, result)

                if result.status == "sent":
                    stats.messages_sent += 1
                    logger.info(
                        "Sent to seller %d via %s (score: %d)",
                        seller["seller_id"], account.login, seller.get("score", 0),
                    )
                elif result.status == "captcha":
                    stats.captchas_hit += 1
                    logger.warning("Captcha hit for seller %d, skipping", seller["seller_id"])
                elif result.status == "blocked":
                    stats.blocks_hit += 1
                    logger.warning("Account %s blocked, skipping", account.login)
                    # Remove this account from pool for this session
                    accounts = [a for a in accounts if a.login != account.login]
                else:
                    stats.messages_failed += 1

                # Random pause between messages (5-15 min)
                pause = random.uniform(MIN_PAUSE_SECONDS, MAX_PAUSE_SECONDS)

            if not dry_run:
                logger.info("Pausing %.0f seconds before next message...", pause)
                await asyncio.sleep(pause)

    finally:
        # Cleanup browser
        if browser:
            try:
                await browser.close()
            except Exception:
                pass
        if pw_instance:
            try:
                await pw_instance.stop()
            except Exception:
                pass
        if cleanup and hasattr(cleanup, "stop"):
            try:
                cleanup.stop()
            except Exception:
                pass
        conn.close()

    stats.accounts_used = len(used_accounts)

    logger.info(
        "Session done: %d sent, %d failed, %d captchas, %d blocks, %d accounts used",
        stats.messages_sent, stats.messages_failed,
        stats.captchas_hit, stats.blocks_hit, stats.accounts_used,
    )
    return stats


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def get_outreach_stats(db_path: Optional[Path] = None) -> dict:
    """Get outreach statistics from the log."""
    db_path = db_path or Path(os.environ.get("AVITO_DB_PATH", str(DEFAULT_DB_PATH)))
    conn = sqlite3.connect(db_path)

    try:
        total = conn.execute("SELECT COUNT(*) FROM outreach_log").fetchone()[0]
    except sqlite3.OperationalError:
        return {"error": "outreach_log table does not exist. Run sender first."}

    sent = conn.execute(
        "SELECT COUNT(*) FROM outreach_log WHERE status = 'sent'"
    ).fetchone()[0]

    by_status = dict(conn.execute(
        "SELECT status, COUNT(*) FROM outreach_log GROUP BY status"
    ).fetchall())

    by_account = dict(conn.execute(
        "SELECT account_used, COUNT(*) FROM outreach_log WHERE status = 'sent' GROUP BY account_used"
    ).fetchall())

    today_sent = conn.execute(
        "SELECT COUNT(*) FROM outreach_log WHERE status = 'sent' AND sent_at >= date('now')"
    ).fetchone()[0]

    recent = [
        dict(zip(["seller_id", "status", "account", "sent_at"], r))
        for r in conn.execute(
            "SELECT seller_id, status, account_used, sent_at FROM outreach_log "
            "ORDER BY sent_at DESC LIMIT 10"
        ).fetchall()
    ]

    conn.close()

    return {
        "total_attempts": total,
        "total_sent": sent,
        "today_sent": today_sent,
        "by_status": by_status,
        "by_account": by_account,
        "recent_10": recent,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Avito outreach sender")
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to accounts JSON config",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help="Path to SQLite database",
    )
    parser.add_argument(
        "--warm-up",
        action="store_true",
        help="Run warm-up actions (browse, view, favorite) instead of sending",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate messages but don't send (no browser)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max sellers to process this session",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Print outreach stats and exit",
    )

    args = parser.parse_args()
    db_path = Path(args.db_path) if args.db_path else None
    accounts_path = Path(args.config) if args.config else None

    if args.stats:
        result = get_outreach_stats(db_path)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    stats = asyncio.run(run_sender(
        db_path=db_path,
        accounts_path=accounts_path,
        warm_up=args.warm_up,
        dry_run=args.dry_run,
        limit=args.limit,
    ))
    print(json.dumps({
        "messages_sent": stats.messages_sent,
        "messages_failed": stats.messages_failed,
        "captchas_hit": stats.captchas_hit,
        "blocks_hit": stats.blocks_hit,
        "accounts_used": stats.accounts_used,
        "sellers_processed": stats.sellers_processed,
    }, indent=2))


if __name__ == "__main__":
    main()
