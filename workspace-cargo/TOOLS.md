# TOOLS.md - Local Notes

## Skills

| Skill | Purpose | CLI |
|-------|---------|-----|
| cargo-calc | Расчёт стоимости доставки | `python3 skills/calc/calculator.py` |
| cargo-calc (parser) | Парсинг товаров с 1688.com | `python3 skills/calc/parser_1688.py` |
| cargo-status | Управление фурами и уведомления | `python3 skills/status/truck_manager.py` |
| cargo-admin | Управление ставками | `python3 skills/admin/rate_manager.py` |
| cargo-onboarding | Настройка новой компании | `python3 skills/onboarding/onboarding.py` |

## Infrastructure

- **LLM (simple):** Ollama — ollama/qwen3.5-nothinker (localhost:11434)
- **LLM (complex):** Anthropic Haiku (API key in .env)
- **STT:** faster-whisper large-v3 (local GPU)
- **Scraping:** Scrapling StealthyFetcher + Playwright

## Data

- Company configs: `data/companies/<company_id>/`
- Parser cache: `data/cache/` (24h TTL)
- Logs: `data/logs.db`

## Manager IDs

- 5093456686
- 291678304
