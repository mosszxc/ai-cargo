#!/usr/bin/env python3
"""
CRM-воронка: трекинг outreach-кампании в Google Sheets.

Статусы воронки: отправлено → ответил → перешёл_в_TG → пилот
Автозапись при отправке, обновление при ответе, классификация через LLM.

Требует:
  pip install gspread google-auth anthropic
  Переменные окружения:
    GOOGLE_SHEETS_CREDENTIALS_FILE — путь к service account JSON
    CRM_SPREADSHEET_KEY — ID Google Sheets таблицы
    ANTHROPIC_API_KEY — ключ для классификации ответов (опционально)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

SHEET_HEADERS = [
    "seller_id",
    "seller_name",
    "avito_url",
    "category",
    "city",
    "score",
    "status",
    "message_text",
    "sent_at",
    "reply_text",
    "reply_at",
    "reply_class",
    "tg_username",
    "tg_joined_at",
    "pilot_started_at",
    "notes",
]


class FunnelStatus(str, Enum):
    SENT = "отправлено"
    REPLIED = "ответил"
    MOVED_TO_TG = "перешёл_в_TG"
    PILOT = "пилот"
    REFUSED = "отказ"


class CRM:
    """Google Sheets CRM для Avito outreach."""

    def __init__(
        self,
        credentials_file: Optional[str] = None,
        spreadsheet_key: Optional[str] = None,
        worksheet_name: str = "CRM",
    ):
        creds_file = credentials_file or os.environ["GOOGLE_SHEETS_CREDENTIALS_FILE"]
        self._spreadsheet_key = spreadsheet_key or os.environ["CRM_SPREADSHEET_KEY"]
        self._worksheet_name = worksheet_name

        creds = Credentials.from_service_account_file(creds_file, scopes=SCOPES)
        self._gc = gspread.authorize(creds)
        self._sheet: Optional[gspread.Worksheet] = None

    @property
    def sheet(self) -> gspread.Worksheet:
        if self._sheet is None:
            spreadsheet = self._gc.open_by_key(self._spreadsheet_key)
            try:
                self._sheet = spreadsheet.worksheet(self._worksheet_name)
            except gspread.WorksheetNotFound:
                self._sheet = spreadsheet.add_worksheet(
                    title=self._worksheet_name, rows=1000, cols=len(SHEET_HEADERS)
                )
                self._sheet.append_row(SHEET_HEADERS)
                self._sheet.format("1", {"textFormat": {"bold": True}})
            self._ensure_headers()
        return self._sheet

    def _ensure_headers(self) -> None:
        first_row = self._sheet.row_values(1)
        if first_row != SHEET_HEADERS:
            self._sheet.update("A1", [SHEET_HEADERS])

    def _find_row(self, seller_id: str) -> Optional[int]:
        """Найти номер строки по seller_id (1-based)."""
        try:
            cell = self.sheet.find(seller_id, in_column=1)
            return cell.row if cell else None
        except gspread.exceptions.CellNotFound:
            return None

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    def record_sent(
        self,
        seller_id: str,
        seller_name: str,
        avito_url: str,
        message_text: str,
        category: str = "",
        city: str = "",
        score: float = 0.0,
    ) -> int:
        """Записать отправку сообщения продавцу. Возвращает номер строки."""
        existing = self._find_row(seller_id)
        if existing:
            logger.info("Seller %s уже в CRM (строка %d), обновляю", seller_id, existing)
            self.sheet.update_cell(existing, SHEET_HEADERS.index("status") + 1, FunnelStatus.SENT.value)
            self.sheet.update_cell(existing, SHEET_HEADERS.index("message_text") + 1, message_text)
            self.sheet.update_cell(existing, SHEET_HEADERS.index("sent_at") + 1, self._now_iso())
            return existing

        row = [
            seller_id,
            seller_name,
            avito_url,
            category,
            city,
            str(score),
            FunnelStatus.SENT.value,
            message_text,
            self._now_iso(),
            "",  # reply_text
            "",  # reply_at
            "",  # reply_class
            "",  # tg_username
            "",  # tg_joined_at
            "",  # pilot_started_at
            "",  # notes
        ]
        self.sheet.append_row(row, value_input_option="USER_ENTERED")
        new_row = len(self.sheet.get_all_values())
        logger.info("Записан seller %s в строку %d", seller_id, new_row)
        return new_row

    def record_reply(
        self,
        seller_id: str,
        reply_text: str,
        auto_classify: bool = True,
    ) -> Optional[str]:
        """Записать ответ от продавца. Возвращает класс ответа."""
        row = self._find_row(seller_id)
        if not row:
            logger.warning("Seller %s не найден в CRM", seller_id)
            return None

        self.sheet.update_cell(row, SHEET_HEADERS.index("status") + 1, FunnelStatus.REPLIED.value)
        self.sheet.update_cell(row, SHEET_HEADERS.index("reply_text") + 1, reply_text)
        self.sheet.update_cell(row, SHEET_HEADERS.index("reply_at") + 1, self._now_iso())

        reply_class = None
        if auto_classify:
            reply_class = classify_reply(reply_text)
            if reply_class:
                self.sheet.update_cell(row, SHEET_HEADERS.index("reply_class") + 1, reply_class)
                if reply_class == "отказ":
                    self.sheet.update_cell(
                        row, SHEET_HEADERS.index("status") + 1, FunnelStatus.REFUSED.value
                    )

        logger.info("Ответ seller %s записан, класс: %s", seller_id, reply_class)
        return reply_class

    def record_tg_join(self, seller_id: str, tg_username: str) -> bool:
        """Записать переход продавца в Telegram."""
        row = self._find_row(seller_id)
        if not row:
            logger.warning("Seller %s не найден в CRM", seller_id)
            return False

        self.sheet.update_cell(row, SHEET_HEADERS.index("status") + 1, FunnelStatus.MOVED_TO_TG.value)
        self.sheet.update_cell(row, SHEET_HEADERS.index("tg_username") + 1, tg_username)
        self.sheet.update_cell(row, SHEET_HEADERS.index("tg_joined_at") + 1, self._now_iso())
        logger.info("Seller %s перешёл в TG: @%s", seller_id, tg_username)
        return True

    def record_pilot(self, seller_id: str, notes: str = "") -> bool:
        """Записать начало пилота с продавцом."""
        row = self._find_row(seller_id)
        if not row:
            logger.warning("Seller %s не найден в CRM", seller_id)
            return False

        self.sheet.update_cell(row, SHEET_HEADERS.index("status") + 1, FunnelStatus.PILOT.value)
        self.sheet.update_cell(row, SHEET_HEADERS.index("pilot_started_at") + 1, self._now_iso())
        if notes:
            self.sheet.update_cell(row, SHEET_HEADERS.index("notes") + 1, notes)
        logger.info("Seller %s начал пилот", seller_id)
        return True

    def get_funnel_stats(self) -> dict[str, int]:
        """Получить статистику воронки."""
        records = self.sheet.get_all_records()
        stats: dict[str, int] = {}
        for rec in records:
            status = rec.get("status", "")
            stats[status] = stats.get(status, 0) + 1
        return stats

    def get_sellers_by_status(self, status: FunnelStatus) -> list[dict]:
        """Получить всех продавцов с указанным статусом."""
        records = self.sheet.get_all_records()
        return [r for r in records if r.get("status") == status.value]


def classify_reply(reply_text: str) -> Optional[str]:
    """
    Классифицировать ответ продавца через Anthropic Claude API.

    Возвращает: "интерес", "отказ", "вопрос" или None при ошибке.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY не задан, классификация пропущена")
        return None

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=50,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Классифицируй ответ продавца на предложение логистических услуг. "
                        "Ответь ОДНИМ словом: интерес, отказ или вопрос.\n\n"
                        f"Ответ продавца: \"{reply_text}\""
                    ),
                }
            ],
        )
        result = response.content[0].text.strip().lower()
        if result in ("интерес", "отказ", "вопрос"):
            return result
        logger.warning("Неожиданный класс от LLM: %s", result)
        return result
    except Exception:
        logger.exception("Ошибка при классификации ответа")
        return None
