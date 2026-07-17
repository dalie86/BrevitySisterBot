# Logs

Создай папку `logs` в корне проекта перед включением `cron`:

```bash
mkdir -p /Users/pavel/projects/BrevitySisterBot/logs
```

Основные логи:

- `logs/collect.log` - почасовой сбор сообщений и обработка команд
- `logs/prepare.log` - подготовка саммари и вызов LLM
- `logs/send.log` - отправка готовой сводки в Telegram
- `logs/archive.log` - перенос сегодняшних данных в `archive.db`
- `logs/cleanup.log` - очистка сегодняшних данных из рабочей базы
- `logs/ollama.log` - запросы к локальному Ollama, ответы модели и ошибки

Полезные команды проверки:

```bash
tail -n 50 /Users/pavel/projects/BrevitySisterBot/logs/collect.log
tail -n 50 /Users/pavel/projects/BrevitySisterBot/logs/prepare.log
tail -n 50 /Users/pavel/projects/BrevitySisterBot/logs/send.log
tail -n 50 /Users/pavel/projects/BrevitySisterBot/logs/archive.log
tail -n 50 /Users/pavel/projects/BrevitySisterBot/logs/cleanup.log
tail -n 50 /Users/pavel/projects/BrevitySisterBot/logs/ollama.log
```
