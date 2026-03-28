# E2E Test Results

**Последнее обновление:** 2026-03-28
**Задача:** [AIC-9](/AIC/issues/AIC-9)

## Результат: 277/277 PASSED + Smoke 9/9

### Smoke test (`scripts/smoke-test.sh`)
```
1. Calculator    ✅
2. Parser        ✅
3. Truck manager ✅
4. Rate manager  ✅
5. Access control ✅
6. E2E density   ✅
7. No jargon     ✅
8. Bot online    ✅
9. Workspace     ✅
```

### Unit + integration tests (`python3 -m pytest tests/ skills/ -v`)
277 passed, 1 skipped.

---

## Предыдущие результаты (2026-03-22)

**Задача:** [AICA-5](/AICA/issues/AICA-5)
**Тестовый файл:** `tests/test_e2e_scenarios.py`

## Результат: 12/12 PASSED

```
tests/test_e2e_scenarios.py::TestE2EVoiceMessages::test_voice_calc_basic PASSED
tests/test_e2e_scenarios.py::TestE2EVoiceMessages::test_voice_calc_with_pieces PASSED
tests/test_e2e_scenarios.py::TestE2EVoiceMessages::test_voice_transcript_numeral_parsing PASSED
tests/test_e2e_scenarios.py::TestE2EVoiceMessages::test_voice_parser_to_calc_pipeline PASSED
tests/test_e2e_scenarios.py::TestE2ENotifications::test_full_notification_flow PASSED
tests/test_e2e_scenarios.py::TestE2ENotifications::test_status_progression_notifications PASSED
tests/test_e2e_scenarios.py::TestE2ENotifications::test_no_clients_no_notifications PASSED
tests/test_e2e_scenarios.py::TestE2EWhereIsMyCargo::test_client_lookup_found PASSED
tests/test_e2e_scenarios.py::TestE2EWhereIsMyCargo::test_client_lookup_not_found PASSED
tests/test_e2e_scenarios.py::TestE2EWhereIsMyCargo::test_client_multiple_trucks PASSED
tests/test_e2e_scenarios.py::TestE2EWhereIsMyCargo::test_client_sees_updated_status PASSED
tests/test_e2e_scenarios.py::TestE2EFullIntegration::test_full_flow PASSED
```

## Сценарий 1 — Голосовые сообщения (4 теста)

Тестирует пайплайн: Whisper транскрибирует аудио -> текст обрабатывается как запрос расчёта.

| Тест | Описание | Результат |
|------|----------|-----------|
| `test_voice_calc_basic` | Голосовое "500 кг одежды Гуанчжоу->Москва" -> расчёт с 3 вариантами транспорта | PASS |
| `test_voice_calc_with_pieces` | Голосовое "200 шт кроссовок по 300 г" -> вес 60 кг, корректный расчёт | PASS |
| `test_voice_transcript_numeral_parsing` | Числительные "двадцать пять"->025, "сто три"->103 + маппинг статусов | PASS |
| `test_voice_parser_to_calc_pipeline` | parser_1688 -> adapt -> calculator (полный пайплайн с ценами и размерами) | PASS |

**Что проверено:**
- Whisper-транскрипт корректно парсится в параметры расчёта
- Числительные в русском тексте маппятся в truck_id
- Естественный язык маппится в status codes
- Пайплайн parser_1688 -> calculator работает end-to-end

## Сценарий 2 — Уведомления при смене статуса (3 теста)

| Тест | Описание | Результат |
|------|----------|-----------|
| `test_full_notification_flow` | create 099 -> add 2 клиента -> status departed -> 2 уведомления с маршрутом | PASS |
| `test_status_progression_notifications` | Полный lifecycle warehouse->..->delivered, уведомления на каждом шаге | PASS |
| `test_no_clients_no_notifications` | Смена статуса без клиентов -> 0 уведомлений (no crash) | PASS |

**Что проверено:**
- Менеджер создаёт фуру, привязывает клиентов
- При смене статуса генерируются уведомления для всех привязанных клиентов
- Текст уведомления содержит маршрут и описание статуса
- Все 7 статусов (warehouse -> delivered) корректно обрабатываются
- Пустой список клиентов не вызывает ошибку

## Сценарий 3 — «Где мой груз» (4 теста)

| Тест | Описание | Результат |
|------|----------|-----------|
| `test_client_lookup_found` | Клиент привязан -> получает статус "На границе" | PASS |
| `test_client_lookup_not_found` | Клиент не привязан -> "обратитесь к менеджеру" | PASS |
| `test_client_multiple_trucks` | Клиент на 2 фурах -> видит обе с корректными статусами | PASS |
| `test_client_sees_updated_status` | Статус обновился -> повторный запрос показывает новый | PASS |

**Что проверено:**
- Клиент запрашивает свой груз по telegram_id
- Возвращается актуальный статус с меткой (status_label)
- Несколько фур у одного клиента — все отображаются
- Статус обновляется в реальном времени

## Интеграционный тест (1 тест)

| Тест | Описание | Результат |
|------|----------|-----------|
| `test_full_flow` | Голосом создать фуру -> привязать клиента -> голосом сменить статус -> клиент lookup -> клиент расчёт | PASS |

**Полный сквозной сценарий:** менеджер голосом управляет фурой, клиент голосом спрашивает статус и считает доставку.

## Баги

Багов не обнаружено. Все пайплайны работают корректно.

## Существующие тесты (регрессия)

Все 22 существующих unit-теста (`skills/status/test_truck_manager.py` + `skills/calc/test_calculator.py`) прошли без ошибок.
