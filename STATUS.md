# Статус проекта Cargo AI

**Дата:** 2026-03-20
**Фаза:** MVP, pre-launch

---

## Архитектура (финальная)

**Один бот → один агент → роли по Telegram ID.** Access control на уровне Python-кода + SKILL.md.

```
MVP:
  cargo-bot → agent: main → workspace-cargo/
    Менеджеры (ID 5093456686, 291678304) → полный доступ (calc, status, admin, onboarding)
    Все остальные → клиент (calc + status lookup)
    Session isolation: per-channel-peer

Масштаб:
  @FastCargo_bot    → компания FastCargo
  @SpeedCargo_bot   → компания SpeedCargo
  Каждый создаёт бот через @BotFather → даёт токен → подключаем
```

---

## Что сделано ✅

### Инфраструктура
- [x] OpenClaw настроен (Ollama qwen3.5-nothinker + Anthropic Haiku)
- [x] Telegram подключён (3 аккаунта, все OK)
- [x] Whisper — локальный faster-whisper (large-v3, GPU)
- [x] Playwright + Scrapling установлены
- [x] **Стейл-сессия GPT-4o** — скрипт удаления готов (`scripts/fix-stale-session.sh`)
- [x] **BOOTSTRAP.md удалён** из всех workspace (по документации OpenClaw)
- [x] **Workspace заполнены** — IDENTITY.md, USER.md, TOOLS.md, HEARTBEAT.md, SOUL.md

### Skills (4 штуки, 101 тест)
- [x] **calc** — расчёт стоимости по плотности, density-based ставки (7 тестов, вкл. adapt_parser_output)
- [x] **parser_1688** — Scrapling + LLM extraction + кэш (7 тестов)
- [x] **status** — фуры, статусы, уведомления клиентам (15 тестов)
- [x] **admin** — управление ставками (13 тестов)
- [x] **onboarding** — визард настройки через чат (60 тестов)

### Инфраструктурные модули (новое)
- [x] **access control** — `skills/common/access.py` + `--caller-id` во всех CLI (3 теста)
- [x] **rate limiter** — `skills/common/rate_limiter.py`, SQLite, лимиты по skill/user/month (4 теста)
- [x] **логирование** — `skills/common/logger.py`, SQLite, dialog_logs с trace_id
- [x] **парсер→калькулятор адаптер** — `adapt_parser_output()` преобразует price_cny variants → price_per_piece_cny float

### Агентная архитектура (новое)
- [x] **1 агент** — `main` с workspace-cargo, роли по Telegram ID
- [x] **SOUL.md** — единый, с описанием ролей менеджер/клиент
- [x] **OpenClaw config** — 1 бот (cargo-bot), session isolation per-channel-peer
- [x] **python3 в safeBins**, whisper path fix, qwen3.5:9b убран из fallback

### Парсер 1688 (Scrapling + Haiku)
- [x] Загрузка страниц: 80% (4/5)
- [x] Названия товаров: 100% правильные (не магазины)
- [x] Цены + варианты SKU: 100%
- [x] Вес: 75%
- [x] Кэширование: 24ч, 0.05 сек повторный запрос
- [x] Извлечение фото из HTML

### Данные
- [x] rates.json — FastCargo, density-based, 2 маршрута, 3 транспорта
- [x] trucks.db — SQLite инициализирован
- [x] config.json — тестовая компания

### Документация
- [x] PRD.md — архитектура
- [x] roadmap.md — фазы + DoD
- [x] offer-and-pricing.md — тарифы 29/49/79K + roadmap цен
- [x] gap-analysis.md — конкуренты, Wisor/Raft/易境通
- [x] exhibition-playbook.md — скрипт выставки
- [x] onboarding-playbook.md — автоонбординг через бот
- [x] data-models.md — схемы данных
- [x] openclaw-setup.md — настройка инфры
- [x] spike-results.md — результаты парсинга

---

## Что НЕ сделано ❌

### Критические (без этого не работает в Telegram)
- [ ] **E2E тест** — ни разу не проверяли что бот реально отвечает в Telegram

### Важные (можно без них для демо, но нужны для пилота)
- [ ] **Мультитенант БД** — companies таблица, client_company_map
- [ ] **Верификация 10 расчётов** — не сверяли с ручным расчётом
- [ ] **Обработка ошибок** — пустые сообщения, таймауты, невалидные ссылки

### На потом (v2)
- [ ] Мультитенант полноценный (несколько компаний)
- [ ] Динамическое ценообразование
- [ ] Аналитика/дашборд
- [ ] WhatsApp/WeChat

---

## Definition of Done: MVP launch

MVP считается готовым когда:

### Must have (без этого не запускаем)
- [ ] Бот отвечает на текстовый запрос расчётом
- [ ] Бот отвечает на ссылку 1688 + количество расчётом (с фото и вариантами)
- [ ] Бот отвечает на голосовое сообщение расчётом
- [ ] Менеджер (по ID) может: создать фуру, обновить статус, обновить ставки
- [ ] Клиент получает уведомление при смене статуса фуры
- [ ] Клиент может спросить «где мой груз»
- [ ] Расчёт совпадает с ручным на >90% (проверено на 5+ примерах)
- [ ] Бот не крашится на невалидном вводе

### Should have (желательно к запуску)
- [x] ~~Онбординг через визард работает end-to-end~~
- [x] ~~Счётчик расчётов~~ (rate_limiter.py)
- [x] ~~Базовое логирование~~ (logger.py → logs.db)

### Nice to have (можно после запуска)
- [x] ~~Лимиты по тарифам~~ (rate_limiter.py, 100 calc/мес по умолчанию)
- [ ] Мультитенант БД
- [ ] Аналитика
