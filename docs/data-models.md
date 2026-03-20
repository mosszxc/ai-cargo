# Data Models — Cargo AI Assistant

## 1. rates.json — Тарифная сетка компании

Файл: `data/companies/{company_id}/rates.json`

### Структура

```json
{
  "company_name": "string",
  "currency": {
    "usd_cny": 7.25,        // курс доллар/юань
    "usd_rub": 88.5,        // курс доллар/рубль
    "display": "usd"         // валюта отображения (usd | rub | cny)
  },
  "min_weight_kg": 30,       // минимальный вес для принятия груза
  "routes": {
    "Город_отправки→Город_назначения": {
      "transport_type": {     // auto | rail | air
        "density_rates": [    // для auto и rail — по плотности
          {
            "min_density": 200,
            "max_density": 399,
            "rate_per_kg": 2.80    // ставка за кг (для плотного груза)
          },
          {
            "min_density": 0,
            "max_density": 99,
            "rate_per_m3": 350     // ставка за м³ (для лёгкого груза)
          }
        ],
        "days_min": 18,
        "days_max": 25
      }
    }
  },
  "category_surcharges": {    // множитель к ставке по категории
    "electronics": 1.5,
    "cosmetics": 1.0,
    "fragile": 1.2
  },
  "services": {               // дополнительные услуги (% от стоимости доставки)
    "crating_pct": 40,        // обрешётка (хрупкое)
    "palletizing_pct": 16,    // паллетирование (тяжёлое)
    "insurance_pct": 3,       // страховка (от стоимости товара)
    "inspection_cny_per_hour": 150,
    "repackaging_usd_per_unit": 3.5
  }
}
```

### Правила ставок по плотности

Плотность = вес (кг) / объём (м³).

- **Плотный груз** (высокая плотность, >200 кг/м³): расчёт по весу (`rate_per_kg`)
- **Лёгкий груз** (низкая плотность, <100 кг/м³): расчёт по объёму (`rate_per_m3`)
- **Авиа**: фиксированная ставка за кг (без density_rates)

Диапазоны `density_rates` перебираются от высокого к низкому. Первый совпавший диапазон определяет ставку.

---

## 2. SQLite Schema — trucks.db

Файл: `data/companies/{company_id}/trucks.db`

```sql
CREATE TABLE trucks (
    id TEXT PRIMARY KEY,                  -- номер фуры, напр. "025"
    route TEXT,                           -- "Гуанчжоу→Москва"
    status TEXT DEFAULT 'warehouse',
    status_updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    company_id TEXT DEFAULT 'test-company'
);

-- Допустимые статусы: warehouse, packed, departed, border, customs, moscow, delivered

CREATE TABLE truck_clients (
    truck_id TEXT,
    client_telegram_id TEXT,
    client_name TEXT,
    cargo_description TEXT,               -- "кроссовки, 800 шт"
    FOREIGN KEY (truck_id) REFERENCES trucks(id)
);

CREATE INDEX idx_truck_clients_truck ON truck_clients(truck_id);
CREATE INDEX idx_truck_clients_tg ON truck_clients(client_telegram_id);
```

## 2.1 SQLite Schema — logs.db

Файл: `data/logs.db`

```sql
CREATE TABLE dialog_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id TEXT,                        -- UUID для отслеживания цепочки
    user_id TEXT,                         -- Telegram ID
    company_id TEXT,
    skill_name TEXT,                      -- calc, status, admin
    message TEXT,                         -- входящее сообщение/параметры
    response TEXT,                        -- ответ скилла
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

## 2.2 SQLite Schema — rate_limits.db

Файл: `data/rate_limits.db`

```sql
CREATE TABLE rate_counts (
    user_id TEXT,
    company_id TEXT,
    skill TEXT,
    month TEXT,                           -- "2026-03"
    count INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, company_id, skill, month)
);
```

Лимиты по умолчанию: calc=100/мес, parser=50, status=500, admin=200, onboarding=10.

---

## 3. Формат сообщений

### Вход: что отправляет клиент

**Текстовый запрос (основной для MVP):**
```
500 кг одежда из Гуанчжоу в Москву
кроссовки 800 штук по 0.3 кг, Иу→Москва
200 кубов мебель Гуанчжоу Москва, хрупкое
телевизоры 100 шт, 15 кг штука, 0.1 м³ штука, Гуанчжоу→Москва
```

**Извлекаемые параметры:**
```json
{
  "product": "string",              // название товара
  "weight_kg": 500.0,              // общий вес (или null)
  "volume_m3": null,               // общий объём (или null)
  "pieces": null,                  // количество штук (или null)
  "weight_per_piece_kg": null,     // вес за штуку (или null)
  "volume_per_piece_m3": null,     // объём за штуку (или null)
  "origin": "Гуанчжоу",           // город отправки
  "destination": "Москва",         // город назначения
  "special": []                    // ["fragile", "electronics", "insurance"]
}
```

### Выход: что получает клиент

```
Кроссовки женские
800 шт | 240 кг | 1.2 м³ | Плотность: 200 кг/м³ | ¥45/шт

Закупка: ¥36 000 (~$4 960)

Доставка (ваши ставки, плотность 200):
  Авто: $2.80/кг → $672 | 18–25 дн
  ЖД:   $2.30/кг → $552 | 25–35 дн
  Авиа:  $6.50/кг → $1 560 | 5–7 дн

Доп. услуги: не требуются

Итого (авто): ~$5 632
За штуку: ~$7.04
```

---

## 4. Расчёт плотности — алгоритм

```
1. Определить общий вес:
   - Если есть weight_kg → используем
   - Если есть pieces + weight_per_piece → weight_kg = pieces × weight_per_piece

2. Определить общий объём:
   - Если есть volume_m3 → используем
   - Если есть pieces + volume_per_piece → volume_m3 = pieces × volume_per_piece
   - Если нет объёма → не можем считать плотность, используем rate_per_kg из средней плотности

3. Плотность = weight_kg / volume_m3

4. Для каждого транспорта в маршруте:
   - Пройти по density_rates от первого к последнему
   - Найти диапазон: min_density <= плотность <= max_density
   - Если rate_per_kg → стоимость = weight_kg × rate_per_kg
   - Если rate_per_m3 → стоимость = volume_m3 × rate_per_m3
   - Для авиа (без density_rates) → стоимость = weight_kg × rate_per_kg

5. Применить наценки:
   - fragile → delivery_cost × (crating_pct / 100)
   - electronics → delivery_cost × category_surcharges.electronics (множитель)
   - insurance → purchase_cost × (insurance_pct / 100)
```
