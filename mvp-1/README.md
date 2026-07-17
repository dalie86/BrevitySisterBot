# BrevitySisterBot MVP 0.1.0

Исторический snapshot cron/SQLite/Ollama-реализации до rewrite `0.2.0`.
Секреты, runtime-базы, логи и virtualenv в committed snapshot не входят.

Тесты:

```bash
cd mvp-1
python3 -m unittest discover -s tests -v
```

## Исходное описание MVP

# Telegram Daily Summary Bot

Бот для Telegram-группы, который работает через `cron`, а не как постоянно запущенный процесс.

Схема работы:

- с `10:00` до `20:59` по Москве бот раз в час забирает новые `updates` из Telegram и сохраняет сообщения в SQLite;
- в `20:40` запускается дополнительный финальный сборщик;
- в `20:45` бот собирает все сообщения за текущие сутки и готовит LLM-саммари;
- в `21:00` бот публикует уже готовую сводку в групповой чат.
- в `21:10` бот архивирует сегодняшние сообщения и готовую сводку в `archive.db`;
- в `21:15` бот очищает сегодняшние рабочие данные из `bot.db`.

## Важное ограничение Telegram

Telegram Bot API не умеет разово выгружать полную историю чата за день.
Поэтому бот накапливает только новые сообщения, которые пришли после:

1. добавления бота в группу;
2. отключения `Privacy Mode` через BotFather;
3. фактического чтения очереди `getUpdates` cron-джобой.

Сообщения сохраняются для тех групп, которые хотя бы один раз были подключены через `/enable_daily`.
Если потом вызвать `/disable_daily`, бот перестанет готовить и публиковать сводку, но продолжит буферизовать сообщения для этого уже известного чата, чтобы не терять историю.

## Файлы и режимы запуска

Главный вход:

```bash
python3 bot.py collect
python3 bot.py prepare
python3 bot.py send
python3 bot.py archive
python3 bot.py cleanup
```

То же самое можно вызывать напрямую:

- `python3 collect_updates.py`
- `python3 prepare_summary.py`
- `python3 send_summary.py`
- `python3 archive_day.py`
- `python3 cleanup_day.py`

## Установка

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Настройки `.env`

- `TELEGRAM_BOT_TOKEN` - токен бота из BotFather
- `BOT_TIMEZONE` - оставьте `Europe/Moscow`
- `COLLECT_INTERVAL_MINUTES` - интервал между регулярными запусками, по умолчанию `60`
- `COLLECT_START_HOUR=10`
- `COLLECT_START_MINUTE=0`
- `COLLECT_END_HOUR=20`
- `COLLECT_END_MINUTE=59`
- `FINAL_COLLECT_HOUR=20`
- `FINAL_COLLECT_MINUTE=40`
- `PREPARE_HOUR=20`
- `PREPARE_MINUTE=45`
- `SUMMARY_HOUR=21`
- `SUMMARY_MINUTE=0`
- `MIN_MESSAGES_FOR_SUMMARY=15` - если за день сообщений меньше этого порога, `prepare` не обращается к LLM, а `send` ничего не публикует
- `LLM_PROVIDER=ollama`

### Вариант 0. Локальная модель через Ollama

Если у вас локально поднят `Ollama`, бот может делать саммари через локальную модель, например `qwen2.5:14b-instruct`.

Минимально нужны:

- `LLM_PROVIDER=ollama`
- `OLLAMA_MODEL=qwen2.5:14b-instruct`

Опционально:

- `OLLAMA_API_URL`, по умолчанию `http://127.0.0.1:11434/api/chat`
- `OLLAMA_KEEP_ALIVE`, по умолчанию `10m`

Для отладки запросов к локальной модели смотри отдельный лог:

- `logs/ollama.log`

## Как это работает в группе

Так как бот не висит онлайн постоянно, команды обрабатываются при ближайшем запуске сборщика:

- `/start` - справка
- `/enable_daily` - включить ежедневную сводку
- `/disable_daily` - выключить ежедневную сводку
- `/summary` - прислать текущую сводку дня

Если, например, cron запускается раз в час, команда может выполниться не мгновенно, а при ближайшем почасовом запуске.

## Пример cron-расписания

Ниже пример для Linux/VPS или хостинга с поддержкой cron и Python.

Открыть crontab:

```bash
crontab -e
```

Добавить:

```cron
0 10-20 * * * cd /opt/telegram-daily-summary-bot && /opt/telegram-daily-summary-bot/.venv/bin/python bot.py collect >> /opt/telegram-daily-summary-bot/logs/collect.log 2>&1
40 20 * * * cd /opt/telegram-daily-summary-bot && /opt/telegram-daily-summary-bot/.venv/bin/python bot.py collect >> /opt/telegram-daily-summary-bot/logs/collect.log 2>&1
45 20 * * * cd /opt/telegram-daily-summary-bot && /opt/telegram-daily-summary-bot/.venv/bin/python bot.py prepare >> /opt/telegram-daily-summary-bot/logs/prepare.log 2>&1
0 21 * * * cd /opt/telegram-daily-summary-bot && /opt/telegram-daily-summary-bot/.venv/bin/python bot.py send >> /opt/telegram-daily-summary-bot/logs/send.log 2>&1
10 21 * * * cd /opt/telegram-daily-summary-bot && /opt/telegram-daily-summary-bot/.venv/bin/python bot.py archive >> /opt/telegram-daily-summary-bot/logs/archive.log 2>&1
15 21 * * * cd /opt/telegram-daily-summary-bot && /opt/telegram-daily-summary-bot/.venv/bin/python bot.py cleanup >> /opt/telegram-daily-summary-bot/logs/cleanup.log 2>&1
```

Перед этим создай папку для логов:

```bash
mkdir -p /opt/telegram-daily-summary-bot/logs
```

## Как внедрить

1. Создайте бота через `@BotFather` и получите `TELEGRAM_BOT_TOKEN`.
2. Отключите у бота `Privacy Mode`.
3. Разместите файлы проекта на сервере.
4. Создайте `.venv`, установите зависимости и заполните `.env`.
5. Запустите вручную:

```bash
python3 bot.py collect
```

6. Добавьте бота в группу.
7. Напишите в группе `/enable_daily`.
8. Запустите ещё раз `collect`, чтобы команда обработалась.
9. После этого настройте cron.

## Что важно помнить

- бот не вычитывает старую историю задним числом;
- он читает только новые `updates`, которые Telegram держит в очереди;
- один запуск `collect` вычитывает всю доступную очередь `getUpdates`, а не только одну страницу;
- для уже подключенных чатов бот продолжает буферизовать сообщения даже если сводка временно выключена через `/disable_daily`;
- регулярный сбор работает только в окне `10:00-20:59` по Москве;
- саммари строится по сообщениям за текущие сутки до `20:40` по Москве;
- если за день накопилось меньше `15` сообщений, подготовка саммари и публикация в чат пропускаются;
- в `21:00` публикуется уже готовый текст из буфера.
- в `21:10` данные дня копируются в `archive.db`;
- в `21:15` рабочая база очищается от сегодняшних сообщений и сводки.

## Тесты

```bash
python3 -m unittest discover -s tests -v
```
