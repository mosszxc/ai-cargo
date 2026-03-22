"""Telegram inline keyboard builders for cargo bot.

Generates InlineKeyboardMarkup-compatible dicts that the bot agent
includes as `reply_markup` in Telegram sendMessage calls.

Telegram API format:
{
  "inline_keyboard": [
    [{"text": "Label", "callback_data": "action:payload"}],
    ...
  ]
}
"""


def button(text: str, callback_data: str) -> dict:
    """Single inline button."""
    return {"text": text, "callback_data": callback_data}


def keyboard(rows: list[list[dict]]) -> dict:
    """Wrap button rows into InlineKeyboardMarkup."""
    return {"inline_keyboard": rows}


# --- Transport selection after cargo description ---

def transport_select_keyboard() -> dict:
    """Keyboard for choosing transport type after cargo input."""
    return keyboard([
        [
            button("✈ Авиа", "transport:air"),
            button("🚛 Авто", "transport:auto"),
        ],
        [
            button("🚂 Ж/Д", "transport:rail"),
            button("🚢 Море", "transport:sea"),
        ],
    ])


# --- Quick actions for clients ---

def client_actions_keyboard() -> dict:
    """Main menu keyboard for clients."""
    return keyboard([
        [button("📦 Новый расчёт", "action:new_calc")],
        [button("📋 Мои расчёты", "action:my_calcs")],
        [button("🔍 Статус груза", "action:cargo_status")],
    ])


# --- Post-calculation actions ---

def after_calc_keyboard(has_results: bool = True) -> dict:
    """Keyboard shown after calculation results."""
    rows = []
    if has_results:
        rows.append([button("📝 Оформить заказ", "action:place_order")])
    rows.append([
        button("🔄 Новый расчёт", "action:new_calc"),
        button("📋 Мои расчёты", "action:my_calcs"),
    ])
    return keyboard(rows)


# --- Order confirmation ---

def order_confirm_keyboard() -> dict:
    """Confirmation keyboard before placing an order."""
    return keyboard([
        [
            button("✅ Подтвердить", "order:confirm"),
            button("❌ Отменить", "order:cancel"),
        ],
    ])


# --- Manager menu ---

def manager_menu_keyboard() -> dict:
    """Main menu keyboard for managers."""
    return keyboard([
        [
            button("📊 Ставки", "mgr:rates"),
            button("🚛 Фуры", "mgr:trucks"),
        ],
        [
            button("📦 Новый расчёт", "action:new_calc"),
            button("👥 Клиенты", "mgr:clients"),
        ],
    ])


# --- Manager: truck actions ---

def truck_actions_keyboard(truck_id: str) -> dict:
    """Actions for a specific truck."""
    return keyboard([
        [
            button("📍 Обновить статус", f"truck:status:{truck_id}"),
            button("👥 Клиенты", f"truck:clients:{truck_id}"),
        ],
        [
            button("➕ Добавить клиента", f"truck:add_client:{truck_id}"),
            button("🗑 Удалить", f"truck:delete:{truck_id}"),
        ],
    ])


# --- Manager: truck status selection ---

def truck_status_keyboard(truck_id: str) -> dict:
    """Keyboard for selecting new truck status."""
    statuses = [
        ("📦 На складе", "warehouse"),
        ("📋 Упакован", "packed"),
        ("🚛 Отправлен", "departed"),
        ("🛃 На границе", "border"),
        ("✅ Таможня", "customs"),
        ("🏙 В Москве", "moscow"),
        ("🎉 Выдан", "delivered"),
    ]
    rows = []
    for i in range(0, len(statuses), 2):
        row = [button(label, f"truck:set_status:{truck_id}:{code}")
               for label, code in statuses[i:i+2]]
        rows.append(row)
    return keyboard(rows)


# --- Manager: rate management ---

def rate_actions_keyboard() -> dict:
    """Keyboard for rate management actions."""
    return keyboard([
        [
            button("📋 Показать ставки", "rate:show"),
            button("✏️ Обновить ставку", "rate:update"),
        ],
        [
            button("➕ Добавить маршрут", "rate:add_route"),
            button("💱 Курсы валют", "rate:currency"),
        ],
    ])
