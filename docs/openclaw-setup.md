# OpenClaw Setup for Cargo AI Project

**Date:** 2026-03-20
**OpenClaw version:** 2026.3.13
**Config:** `~/.openclaw/openclaw.json`

---

## Архитектура

**Один бот → один агент → роли по Telegram ID.**

```
@AiCargoManager_bot (token: 8567476869:...)
    ↓
agent: main (workspace: /home/dev-moss/workspace-cargo/)
    ↓
Telegram ID == 5093456686 или 291678304 → менеджер (полный доступ)
Все остальные ID → клиент (calc + status lookup)
```

Access control: на уровне SKILL.md инструкций + на уровне Python-кода (`--caller-id`).

---

## КРИТИЧНО: Workspace Resolution

OpenClaw резолвит `"workspace": "workspace-cargo"` в конфиге как **`/home/dev-moss/workspace-cargo/`** (относительно home dir), НЕ как `~/.openclaw/workspace-cargo/`.

**Все файлы бота должны быть в `/home/dev-moss/workspace-cargo/`!**

Существует 3 разных директории, НЕ ПУТАТЬ:
| Путь | Назначение |
|------|-----------|
| `/home/dev-moss/workspace-cargo/` | **РАБОЧАЯ** — сюда смотрит OpenClaw agent |
| `~/.openclaw/workspace-cargo/` | Git-копия, НЕ используется агентом |
| `~/ai-cargo/workspace-cargo/` | Исходники проекта, нужно синхронизировать |

### Синхронизация (после любых изменений)

```bash
cp ~/ai-cargo/workspace-cargo/*.md /home/dev-moss/workspace-cargo/
```

---

## Model Configuration

### Primary: Claude Haiku (cloud, надёжный)
- **Model:** `anthropic/claude-haiku-4-5`
- **Fallback:** `ollama/qwen3.5-nothinker` (local, free)

### Почему не Qwen primary

Qwen 3.5 9B (nothinker) **не справляется** с complex system prompt:
- Игнорирует SOUL.md инструкции
- Запускает bootstrap-диалог "кто я?" вместо работы
- Ollama ломает tool calling для Qwen 3.5 ([ollama#14745](https://github.com/ollama/ollama/issues/14745))
- Комьюнити рекомендует 32B+ для complex agent tasks
- nothinker хуже thinker по instruction following (нет chain-of-thought)

### Альтернативы
- `qwen3.5:9b` (с thinking) — лучше следует инструкциям, но медленнее
- `qwen3.5:32b` — надёжный, но требует ~20GB VRAM
- Haiku — ~$0.001/сообщение, ~30 руб/1000 сообщений — рекомендован

---

## Telegram Bot

| Account ID  | Bot Username | Token prefix | Purpose |
|-------------|-------------|--------------|---------|
| `cargo-bot` | @AiCargoManager_bot | 8567476869:... | Единый бот |

- `dmPolicy: "open"` + `allowFrom: ["*"]`
- `session.dmScope: "per-channel-peer"` — изолированные сессии

---

## Workspace Files

Все файлы в `/home/dev-moss/workspace-cargo/`:

| File | Purpose | Обязательно заполнен |
|------|---------|---------------------|
| `SOUL.md` | Роль бота, правила поведения, роли менеджер/клиент | ✅ |
| `AGENTS.md` | Инструкции: какие скиллы когда использовать, НЕ спрашивать "кто я" | ✅ |
| `IDENTITY.md` | Имя: Cargo, emoji: 📦 | ✅ |
| `USER.md` | Менеджер FastCargo, MSK timezone | ✅ |
| `TOOLS.md` | Список скиллов, инфраструктура | ✅ |
| `HEARTBEAT.md` | Периодические проверки | ✅ |
| **`BOOTSTRAP.md`** | **НЕ ДОЛЖЕН СУЩЕСТВОВАТЬ!** Если есть — бот запустит "кто я?" | ❌ |

### workspace-state.json

Файл `.openclaw/workspace-state.json` — обязателен:
```json
{
  "version": 1,
  "bootstrapSeededAt": "2026-03-19T16:19:28.976Z",
  "onboardingCompletedAt": "2026-03-19T16:20:00.000Z"
}
```

Без `bootstrapSeededAt` OpenClaw пересоздаст шаблоны. Без `onboardingCompletedAt` может запустить onboarding wizard.

### Лимиты размера
- Per-file max: 20,000 chars (`agents.defaults.bootstrapMaxChars`)
- Total max: 150,000 chars (`agents.defaults.bootstrapTotalMaxChars`)
- Текущий total: ~6,350 chars — в норме

---

## Skills

Skills — симлинки из workspace в shared skills dir:

```
/home/dev-moss/workspace-cargo/skills/
  cargo-calc -> ~/.openclaw/workspace/skills/cargo-calc
  cargo-status -> ~/.openclaw/workspace/skills/cargo-status
  cargo-admin -> ~/.openclaw/workspace/skills/cargo-admin
  cargo-onboarding -> ~/.openclaw/workspace/skills/cargo-onboarding
```

Shared skills dir: `~/.openclaw/workspace/skills/`

| Skill | Files | Access |
|-------|-------|--------|
| cargo-calc | calculator.py, parser_1688.py, SKILL.md | Все |
| cargo-status | truck_manager.py, SKILL.md | Менеджер: всё. Клиент: lookup |
| cargo-admin | rate_manager.py, SKILL.md | Только менеджер |
| cargo-onboarding | onboarding.py, SKILL.md | Только менеджер |
| common | access.py, logger.py, rate_limiter.py | Внутренний модуль |

### Exec safeBins
`python3` добавлен в safeBins — без него скиллы не вызываются.

---

## Audio Transcription (local Whisper)

- Script: `/home/dev-moss/ai-cargo/scripts/whisper-transcribe.sh`
- Model: faster-whisper large-v3
- Device: CUDA GPU, float16
- Languages: auto-detect (Russian + Chinese)
- Timeout: 60 seconds

---

## Gateway

- **Mode:** local
- **Bind:** LAN (0.0.0.0:18789)
- **Auth:** token-based
- **Service:** systemd (`openclaw-gateway.service`)

```bash
openclaw gateway restart
openclaw status
openclaw agents list
openclaw channels status --probe
openclaw logs --follow
```

---

## Чеклист перед запуском

```bash
# 1. Workspace files
ls /home/dev-moss/workspace-cargo/*.md
# Должно быть: AGENTS.md HEARTBEAT.md IDENTITY.md SOUL.md TOOLS.md USER.md
# НЕ должно быть: BOOTSTRAP.md

# 2. SOUL.md — cargo content
head -1 /home/dev-moss/workspace-cargo/SOUL.md
# "# SOUL.md - Cargo Bot (FastCargo)"

# 3. AGENTS.md — no bootstrap
grep "НЕ спрашивай" /home/dev-moss/workspace-cargo/AGENTS.md
# Должна быть строка

# 4. workspace-state.json
cat /home/dev-moss/workspace-cargo/.openclaw/workspace-state.json
# Должен быть bootstrapSeededAt + onboardingCompletedAt

# 5. Skills
ls /home/dev-moss/workspace-cargo/skills/
# cargo-calc cargo-status cargo-admin cargo-onboarding

# 6. No BOOTSTRAP.md anywhere
find /home/dev-moss/workspace-cargo/ /home/dev-moss/.openclaw/ -name "BOOTSTRAP.md"
# Пусто

# 7. Bot online
openclaw channels status --probe | grep cargo-bot
# "works"
```

---

## Troubleshooting

### Бот говорит "кто я? кто ты?"
1. Проверить что нет BOOTSTRAP.md: `find ~ -name BOOTSTRAP.md`
2. Проверить SOUL.md контент: `head -3 /home/dev-moss/workspace-cargo/SOUL.md`
3. Проверить workspace-state.json
4. Удалить сессии: `rm -rf ~/.openclaw/agents/main/sessions/*`
5. Перезапустить: `openclaw gateway restart`

### Бот не отвечает
1. `openclaw channels status --probe` — бот works?
2. `openclaw logs --follow` — есть ли incoming messages?
3. Проверить что пишешь в правильный бот (@AiCargoManager_bot)

### Скиллы не работают
1. Проверить что `python3` в safeBins
2. Проверить симлинки: `ls -la /home/dev-moss/workspace-cargo/skills/`
3. Проверить что SKILL.md в каждом скилле: `cat ~/.openclaw/workspace/skills/cargo-calc/SKILL.md | head -5`

---

## Rollback

```bash
cp ~/.openclaw/openclaw.json.bak ~/.openclaw/openclaw.json
openclaw gateway restart
```
