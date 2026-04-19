# Telegram Daily Summary Bot

Бот для Telegram-группы, который работает через `cron`, а не как постоянно запущенный процесс.

Схема работы:

- с `10:00` до `20:59` по Москве бот раз в час забирает новые `updates` из Telegram и сохраняет сообщения в SQLite;
- в `20:40` запускается дополнительный финальный сборщик;
- в `20:45` бот собирает все сообщения за текущие сутки и готовит LLM-саммари;
- в `21:00` бот публикует уже готовую сводку в групповой чат.

## Важное ограничение Telegram

Telegram Bot API не умеет разово выгружать полную историю чата за день.  
Поэтому бот накапливает только новые сообщения, которые пришли после:

1. добавления бота в группу;
2. отключения `Privacy Mode` через BotFather;
3. фактического чтения очереди `getUpdates` cron-джобой.

Сообщения сохраняются только для тех групп, где был вызван `/enable_daily`.

## Файлы и режимы запуска

Главный вход:

```bash
python3 bot.py collect
python3 bot.py prepare
python3 bot.py send
```

То же самое можно вызывать напрямую:

- `python3 collect_updates.py`
- `python3 prepare_summary.py`
- `python3 send_summary.py`

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
- `LLM_PROVIDER` - `yandex` или `gigachat`

### Вариант 1. YandexGPT

Минимально нужны:

- `YANDEX_API_KEY`
- `YANDEX_FOLDER_ID`

Опционально:

- `YANDEX_MODEL_URI`, например `gpt://<folder_id>/yandexgpt-lite/latest`
- `YANDEX_API_URL`

### Вариант 2. GigaChat

Минимально нужен:

- `GIGACHAT_AUTH_KEY`

Опционально:

- `GIGACHAT_MODEL`, по умолчанию `GigaChat-2-Lite`
- `GIGACHAT_SCOPE`, по умолчанию `GIGACHAT_API_PERS`
- `GIGACHAT_CA_BUNDLE_FILE`
- `GIGACHAT_VERIFY_SSL_CERTS=false` только если понимаете риск

## Как это работает в группе

Так как бот не висит онлайн постоянно, команды обрабатываются при ближайшем запуске сборщика:

- `/start` - справка
- `/enable_daily` - включить сбор и сводку
- `/disable_daily` - выключить сбор
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
- регулярный сбор работает только в окне `10:00-20:59` по Москве;
- саммари строится по сообщениям за текущие сутки до `20:40` по Москве;
- в `21:00` публикуется уже готовый текст из буфера.

## Тесты

```bash
python3 -m unittest discover -s tests -v
```
