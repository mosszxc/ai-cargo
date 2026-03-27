---
name: cargo-order
description: "Оформление заказа на доставку. Клиент нажал 'Оформить заказ' после расчёта — собери контактные данные, подтверди заказ, сохрани в базу, уведоми менеджера. Используй для callback action:place_order, order:confirm, order:cancel и команды 'оформить заказ', 'хочу заказать'."
metadata: { "openclaw": { "emoji": "package", "requires": { "bins": ["python3", "jq"] } } }
---

# Cargo Order — оформление заказа

Всегда отвечай на **русском языке**. Форматируй для Telegram (markdown).

## Утилита order_manager.py

```bash
OM="python3 /home/dev-moss/ai-cargo/skills/order/order_manager.py --company test-company --caller-id <sender_telegram_id>"
```

**ВАЖНО:**
- Всегда передавай `--caller-id` с Telegram ID отправителя
- Всегда инициализируй БД перед первым использованием если нужно: `$OM init-db`
- Компания всегда `test-company`

---

## Flow 1: Клиент нажал «Оформить заказ» (callback: `action:place_order`)

Это основной путь. Клиент только что получил расчёт и нажал кнопку.

### Шаг 1: Найди последний расчёт клиента

```bash
python3 /home/dev-moss/ai-cargo/skills/calc/history_cli.py recent \
  --user-id <sender_telegram_id> \
  --company test-company \
  --limit 1
```

Получи `calc_id` из первого элемента в `records`.

### Шаг 2: Покажи превью заказа

```bash
$OM preview --user-id <sender_telegram_id> --calc-id <calc_id>
```

Результат содержит поле `result_summary` (итог расчёта) и `reply_markup` с кнопками «Подтвердить» / «Отменить».

Отправь клиенту:

```
📝 *Оформление заказа*

[result_summary]

Подтвердить заказ?
```

Прикрепи `reply_markup` из ответа.

### Шаг 3: Спроси контакт (опционально)

Если у клиента нет контакта в профиле, добавь вопрос в сообщение:

> Для оформления укажите ваш телефон или @username для связи (или пропустите — мы свяжемся через Telegram).

Ожидай ответа клиента. Если клиент нажимает «Подтвердить» без контакта — это нормально.

---

## Flow 2: Клиент нажал «Подтвердить» (callback: `order:confirm`)

### Шаг 1: Найди calc_id

```bash
python3 /home/dev-moss/ai-cargo/skills/calc/history_cli.py recent \
  --user-id <sender_telegram_id> \
  --company test-company \
  --limit 1
```

### Шаг 2: Сохрани заказ

```bash
$OM place --user-id <sender_telegram_id> --calc-id <calc_id>
```

Или с контактом:

```bash
$OM place --user-id <sender_telegram_id> --calc-id <calc_id> --contact "@username"
```

Результат содержит:
- `order_id` — короткий ID заказа (8 символов)
- `client_message` — текст подтверждения для клиента
- `managers_to_notify` — список менеджеров для уведомления

### Шаг 3: Отправь клиенту подтверждение

Отправь `client_message` из ответа:

```
✅ *Заказ #XXXXXXXX принят!*
Менеджер свяжется с вами в течение 1-2 часов для уточнения деталей.
Товар: ...
Сумма: $XXX
```

### Шаг 4: Уведоми менеджеров

Для каждого элемента в `managers_to_notify`:

```bash
openclaw message send \
  --channel telegram \
  --account cargo-agent \
  --target <telegram_id> \
  --message "<message>"
```

**ВАЖНО:** отправляй уведомления всем менеджерам из списка, не только одному.

---

## Flow 3: Клиент нажал «Отменить» (callback: `order:cancel`)

Если заказ ещё не оформлен (клиент нажал «Отменить» на превью) — просто скажи:

> Хорошо, заказ отменён. Если передумаете — нажмите «Оформить заказ» в любое время.

Покажи клавиатуру нового расчёта.

Если заказ уже оформлен (есть `order_id`):

```bash
$OM cancel <order_id>
```

---

## Flow 4: Менеджер запрашивает список заказов

Триггеры: "покажи заказы", "новые заказы", "заказы"

```bash
$OM --caller-id <sender_telegram_id> list
```

Или только ожидающие:

```bash
$OM --caller-id <sender_telegram_id> list --status pending
```

Результат содержит `formatted` — готовый текст для менеджера.

---

## Flow 5: Менеджер подтверждает заказ

Триггеры: "подтверди заказ #XXXXXXXX", "подтвердить заказ"

```bash
$OM --caller-id <sender_telegram_id> confirm <order_id>
```

Результат содержит `client_id` и `client_message`. Отправь клиенту уведомление:

```bash
openclaw message send \
  --channel telegram \
  --account cargo-agent \
  --target <client_id> \
  --message "<client_message>"
```

---

## Правила

1. **Никогда не создавай заказ без явного подтверждения** от клиента — только через `order:confirm` или явную команду
2. **Всегда уведомляй менеджера** после создания заказа
3. **Не спрашивай лишнего** — контакт опционален, маршрут/товар уже в расчёте
4. **Если calc_id не найден** — скажи: "Не найден последний расчёт. Сначала рассчитайте стоимость доставки."
5. **Если orders.db не существует** — вызови `init-db` перед `place`
6. **Роль клиента vs менеджера**: клиент может только создавать/отменять свои заказы; менеджер может видеть все и подтверждать
