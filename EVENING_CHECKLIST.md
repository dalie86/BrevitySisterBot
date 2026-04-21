# Evening Checklist

Проверка вечерней цепочки:

- `20:45` — `prepare`
- `21:00` — `send`
- `21:10` — `archive`
- `21:15` — `cleanup`

## Быстрая проверка статуса

```bash
date '+%Y-%m-%d %H:%M:%S %Z'
crontab -l
```

## После `20:45`

Проверить, что сводка собрана:

```bash
tail -n 20 /Users/pavel/projects/BrevitySisterBot/logs/prepare.log
tail -n 20 /Users/pavel/projects/BrevitySisterBot/logs/ollama.log
sqlite3 /Users/pavel/projects/BrevitySisterBot/bot.db "SELECT summary_date, prepared_at, length(summary_text) FROM prepared_summaries ORDER BY prepared_at DESC LIMIT 5;"
sqlite3 /Users/pavel/projects/BrevitySisterBot/bot.db "SELECT summary_text FROM prepared_summaries ORDER BY prepared_at DESC LIMIT 1;"
```

Ожидаемый результат:

- в `prepare.log` есть строка `Prepared summary for chat ...`
- в `ollama.log` есть свежий запрос и свежий ответ модели
- в `prepared_summaries` есть запись за сегодня

## После `21:00`

Проверить, что сводка отправлена в группу:

```bash
tail -n 20 /Users/pavel/projects/BrevitySisterBot/logs/send.log
```

Ожидаемый результат:

- в `send.log` есть строка `Sent summary to chat ...`

## После `21:10`

Проверить, что данные дня перенесены в архив:

```bash
tail -n 20 /Users/pavel/projects/BrevitySisterBot/logs/archive.log
sqlite3 /Users/pavel/projects/BrevitySisterBot/archive.db "SELECT COUNT(*), MIN(archived_date), MAX(archived_date) FROM archived_messages;"
sqlite3 /Users/pavel/projects/BrevitySisterBot/archive.db "SELECT chat_id, summary_date, archived_at FROM archived_summaries ORDER BY archived_at DESC LIMIT 10;"
```

Ожидаемый результат:

- в `archive.log` есть строка `Archived ... messages and ... summaries`
- `archive.db` существует
- в архивной базе есть сообщения и сводка за сегодня

## После `21:15`

Проверить, что рабочая база очищена от сегодняшних данных:

```bash
tail -n 20 /Users/pavel/projects/BrevitySisterBot/logs/cleanup.log
sqlite3 /Users/pavel/projects/BrevitySisterBot/bot.db "SELECT COUNT(*) FROM messages WHERE sent_at >= date('now','localtime') || 'T00:00:00+03:00' AND sent_at < date('now','localtime','+1 day') || 'T00:00:00+03:00';"
sqlite3 /Users/pavel/projects/BrevitySisterBot/bot.db "SELECT COUNT(*) FROM prepared_summaries WHERE summary_date = date('now','localtime');"
```

Ожидаемый результат:

- в `cleanup.log` есть строка `Cleanup completed`
- сообщений за сегодня в `bot.db` больше нет
- prepared summary за сегодня в `bot.db` больше нет

## Если что-то не сработало

Принудительный запуск по шагам:

```bash
cd /Users/pavel/projects/BrevitySisterBot
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python bot.py collect
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python bot.py prepare
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python bot.py send
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python bot.py archive
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python bot.py cleanup
```

## Самая короткая проверка

Если нужен совсем короткий контрольный набор:

```bash
tail -n 20 /Users/pavel/projects/BrevitySisterBot/logs/prepare.log
tail -n 20 /Users/pavel/projects/BrevitySisterBot/logs/send.log
tail -n 20 /Users/pavel/projects/BrevitySisterBot/logs/archive.log
tail -n 20 /Users/pavel/projects/BrevitySisterBot/logs/cleanup.log
```
