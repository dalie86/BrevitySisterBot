# BrevitySisterBot 0.3.0 — DeepSeek API и контроль расходов

## Решение

Production-суммаризация полностью переходит с Groq на DeepSeek API. Основная
модель — `deepseek-v4-flash` в non-thinking режиме. Groq не остаётся скрытым
резервным провайдером: при недоступности DeepSeek используется существующая
локальная эвристическая сводка.

Изменение объединяется с уже начатыми исправлениями личного `/summary` из
спецификации `2026-07-17-private-summary-flow-0.2.2-design.md` и выпускается
одним релизом `0.3.0`. Уже созданные локальные коммиты сохраняются; работа не
переносится в `mvp-1/`.

## Почему выбран этот вариант

Рассматривались три схемы:

1. Полная замена Groq на DeepSeek Flash с локальным fallback — выбрана.
2. DeepSeek с Groq в качестве сетевого fallback — отклонена из-за двух
   балансов, двух ключей и непрозрачной итоговой стоимости.
3. DeepSeek Flash для основного ответа и Pro для repair-запроса — отклонена:
   JSON Mode уже поддерживается Flash, а дополнительный роутинг не оправдывает
   трёхкратную разницу в тарифе.

Схема оставляет одного платного провайдера, предсказуемый бюджет и
детерминированное поведение при его отказе.

## DeepSeek API

Используется OpenAI-совместимый endpoint:

```text
POST https://api.deepseek.com/chat/completions
```

Параметры production-запроса:

```json
{
  "model": "deepseek-v4-flash",
  "thinking": {"type": "disabled"},
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "..."}
  ],
  "temperature": 0.2,
  "max_tokens": 1200,
  "response_format": {"type": "json_object"},
  "stream": false
}
```

Существующий системный промпт уже содержит слово JSON и точный пример
структуры, что является обязательным условием DeepSeek JSON Output. Ответ
по-прежнему проходит локальную проверку, экранирование Telegram HTML и repair
одним дополнительным запросом при невалидной структуре.

Клиент реализуется на стандартном `urllib`, как текущий Groq-клиент. Новая
зависимость OpenAI SDK не добавляется. Таймаут запроса — 90 секунд. API-ключ
передаётся только в Bearer-заголовке, не попадает в исключения, БД, логи,
Telegram или git.

## Конфигурация и переключение

Production-переменные:

```dotenv
DEEPSEEK_API_KEY=
DEEPSEEK_MODEL=deepseek-v4-flash
```

`GROQ_API_KEY` и `GROQ_MODEL` удаляются из обязательной конфигурации, примеров,
композиции приложения и документации. Старые значения могут оставаться в
серверном `.env`, но код их не читает. Перед deploy пользователь добавляет
`DEEPSEEK_API_KEY` в production `.env`; deploy не должен публиковать код без
успешной проверки ключа и модели безопасным smoke-запросом.

Точки композиции webhook и CLI используют один `DeepSeekClient`. В БД новые
сводки получают `provider = "deepseek"` и фактическое имя модели из ответа API.

## Тариф и расчёт стоимости

В релизе фиксируется официальный тариф, действующий с 16 августа 2026 года.
Для `deepseek-v4-flash` за один миллион токенов:

| Период | Cache hit input | Cache miss input | Output |
|---|---:|---:|---:|
| Off-peak | $0.007 | $0.22 | $0.66 |
| Peak | $0.014 | $0.44 | $1.32 |

Peak действует по будням `01:00–04:00` и `06:00–10:00 UTC`; всё остальное
время считается off-peak. Плановая сводка в `21:00 Europe/Moscow` попадает в
off-peak.

Стоимость каждого успешного вызова вычисляется в целых micro-USD, чтобы не
накапливать ошибки float:

```text
cost = cache_hit_tokens × hit_rate
     + cache_miss_tokens × miss_rate
     + completion_tokens × output_rate
```

Расчёт — локальная оценка по зафиксированному тарифу. Источником истины для
остатка средств остаётся DeepSeek `/user/balance`.

## Сохранение usage

Существующая таблица `prepared_summaries` уже содержит `latency_ms`,
`input_tokens`, `output_tokens` и `input_characters`, но текущий код их не
заполняет. Additive migration добавляет:

```text
cache_hit_input_tokens INT
cache_miss_input_tokens INT
llm_call_count INT
estimated_cost_microusd BIGINT
```

`LlmResult` переносит usage одного вызова. `SummaryService` агрегирует initial
и возможный repair-вызов, а `GeneratedSummary` передаёт суммарные метрики в
репозиторий. Так repair не теряется и не считается одной дешёвой операцией.

Для эвристической сводки сохраняются уже полученные метрики успешного, но
невалидного ответа, если они были. Сетевой/HTTP-отказ без usage не создаёт
фиктивную стоимость. Повторная выдача готовой сводки из MySQL не вызывает API
и стоит ноль.

Миграция не изменяет и не удаляет старые строки. Метрики старых Groq-сводок
остаются `NULL` и не включаются в DeepSeek-отчёт.

## Реальный баланс

Тот же API-ключ используется для:

```text
GET https://api.deepseek.com/user/balance
```

Ответ содержит доступность API и остатки `total_balance`, `granted_balance`,
`topped_up_balance` в CNY или USD. Значения обрабатываются через `Decimal`.

Добавляется таблица `llm_balance_snapshots`:

```text
id BIGINT AUTO_INCREMENT PRIMARY KEY
provider VARCHAR(32)
currency VARCHAR(8)
total_balance DECIMAL(18,6)
granted_balance DECIMAL(18,6)
topped_up_balance DECIMAL(18,6)
is_available TINYINT(1)
captured_at DATETIME(6)
report_week_start DATE
report_text TEXT
UNIQUE KEY (provider, currency, report_week_start)
```

Снимок создаётся недельным отчётом. Разница с предыдущим снимком показывает
фактическое изменение баланса DeepSeek, включая другие ключи этого аккаунта.
Если баланс вырос из-за пополнения, прогноз срока не строится и отчёт явно
показывает пополнение.

## CLI и недельный отчёт

Добавляются команды:

```bash
python -m app.jobs.cli usage
python -m app.jobs.cli usage-report
```

`usage` ничего не отправляет в Telegram и печатает JSON:

- DeepSeek-вызовы за текущую неделю и текущий месяц;
- initial/repair call count;
- cache hit/miss и output tokens;
- локально оценённую стоимость в USD;
- текущий баланс DeepSeek;
- дату и значение предыдущего balance snapshot.

`usage-report` строит те же данные, сохраняет новый balance snapshot и
отправляет одно личное сообщение на `OWNER_CHAT_ID`. Если `OWNER_CHAT_ID` не
задан, команда завершается с понятной ошибкой до обращения к Telegram.

Недельная отправка идемпотентна. Для недели вычисляется стабильный отрицательный
технический update id, после чего сообщение проходит через существующий
`DurableTelegramGateway` и `telegram_effects`. Повторный cron/manual retry
видит уже отправленный effect и не создаёт дубликат. Balance snapshot также
создаётся через `INSERT ... ON DUPLICATE KEY` по неделе; сбой Telegram после
сохранения снимка безопасно возобновляется с тем же текстом и тем же effect key.
Поле `report_text` замораживает payload при первом запуске недели: повторный
запуск не пересчитывает его из уже изменившейся статистики и поэтому не
создаёт payload-hash conflict в durable effect.

Production cron:

```text
30 21 * * 0
```

Timezone Beget/cron должна быть подтверждена как `Europe/Moscow`; если cron
работает в другой timezone, выражение пересчитывается так, чтобы отчёт приходил
по воскресеньям в `21:30 МСК`, после вечерней сводки.

Текст отчёта:

```text
💳 DeepSeek за неделю

Генераций: N (repair: M)
Токены: hit H / miss I / output O
Оценка бота: $0.0042
Баланс: XX.XX CNY
Изменение баланса за неделю: −X.XX CNY
Прогноз: примерно N недель
```

Прогноз показывается только при наличии предыдущего снимка и положительного
расхода. При первом отчёте выводится «прогноз появится через неделю». Ошибка
balance API не отменяет локальный usage-отчёт: баланс помечается как временно
недоступный. Ошибка отправки Telegram возвращает ненулевой exit code, чтобы
попасть в cron-лог и operational audit.

## Ошибки и fallback

- `400`, `401`, `402`, `422`: нормализованная ошибка без API-ключа в тексте;
  `402` отдельно обозначается как недостаточный баланс.
- `429`, `500`, `503` и сетевой timeout: нормализованная временная ошибка.
- SummaryService сохраняет существующее пользовательское поведение: при
  недоступности или невалидном ответе формируется локальная эвристическая
  сводка, а webhook не ждёт пятиминутный recovery только ради провайдера.
- DeepSeek не переключается на Groq.
- Неуспешный balance API не блокирует генерацию сводок.
- Недельный отчёт не содержит тексты сообщений, названия групп, API-ключи или
  персональные данные участников.

## Производительность

Webhook остаётся синхронным: после выбора группы пользователь немедленно
получает ранее согласованное сообщение «подготовка может занять до 2 минут»,
затем выполняется DeepSeek-запрос. Искусственная задержка не добавляется.

DeepSeek может посылать keep-alive пустые строки до JSON-тела. Клиент должен
читать итоговое тело корректно и не считать пустой префикс malformed JSON.
Один запрос ограничен 90 секундами; durable Telegram effects и recovery
защищают от повторных уведомлений при повторной доставке update.

## Конфиденциальность

Для суммаризации DeepSeek получает до 18 000 символов сегодняшней переписки,
включая отображаемые имена участников. DeepSeek указывает, что инфраструктура
обработки и хранения персональных данных находится в КНР. Это изменение
фиксируется в README/production-документации до deploy. Секреты и технические
идентификаторы БД в модель не передаются.

Релиз не добавляет изображения, файлы, инструменты, web search или историю
диалога DeepSeek; каждый запрос является отдельной операцией суммаризации.

## Проверка и выкладка

Реализация выполняется через RED–GREEN TDD поверх текущей ветки
`codex/private-summary-implementation`:

- contract tests тела запроса, JSON Mode, non-thinking и usage DeepSeek;
- contract tests ошибок и balance API без утечки ключа;
- unit tests peak/off-peak micro-USD расчёта на границах timezone;
- integration tests additive migration и сохранения агрегированных метрик;
- unit tests usage query и недельного текста/прогноза;
- composition tests отсутствия Groq и обязательности DeepSeek config;
- сохранение всех регрессий личного `/summary` из плана 0.2.2;
- полный pytest, Ruff, compileall, pip check, copy renderer и diff check;
- независимый task review и финальный review;
- безопасный DeepSeek smoke без публикации текста группы;
- backup БД, migration, Passenger restart, health/audit/usage smoke;
- push ветки и release-коммитов в GitHub.

API-ключ никогда не коммитится и вводится пользователем только в production
`.env` перед deploy.

## Официальные источники на дату проектирования

- Models & Pricing: <https://api-docs.deepseek.com/quick_start/pricing/>
- Chat Completions API: <https://api-docs.deepseek.com/api/create-chat-completion/>
- JSON Output: <https://api-docs.deepseek.com/guides/json_mode/>
- User Balance: <https://api-docs.deepseek.com/api/get-user-balance/>
- Rate Limit & Isolation: <https://api-docs.deepseek.com/quick_start/rate_limit/>
- Privacy Policy: <https://cdn.deepseek.com/policies/en-US/deepseek-privacy-policy.html>
