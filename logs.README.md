# Logs

Создай папку `logs` в корне проекта перед включением `cron`:

```bash
mkdir -p /Users/pavel/Documents/BrevitySisterBot/logs
```

Основные логи:

- `logs/collect.log` - почасовой сбор сообщений и обработка команд
- `logs/prepare.log` - подготовка саммари и вызов LLM
- `logs/send.log` - отправка готовой сводки в Telegram

Полезные команды проверки:

```bash
tail -n 50 /Users/pavel/Documents/BrevitySisterBot/logs/collect.log
tail -n 50 /Users/pavel/Documents/BrevitySisterBot/logs/prepare.log
tail -n 50 /Users/pavel/Documents/BrevitySisterBot/logs/send.log
```
