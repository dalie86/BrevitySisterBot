# Production cutover 0.2.0

## До переключения

1. Отключить старые cron-задачи `0.1.0`, но не удалять SQLite-файлы.
2. Сделать неизменяемые копии `bot.db` и `archive.db`.
3. Создать отдельную MySQL-базу и пользователя BrevitySisterBot.
4. Заполнить remote `.env`; проверить Groq Zero Data Retention и BotFather
   privacy mode.
5. Выполнить deploy. Deploy обязан завершить `scripts/backup_db.sh` до миграций.
6. Импортировать MVP и сравнить количество групп, сообщений и архивных строк.

## Проверка до webhook

```bash
.venv/bin/python scripts/migrate.py
.venv/bin/python -m app.jobs.cli audit
.venv/bin/python -m app.jobs.cli cleanup --dry-run
.venv/bin/python scripts/smoke_preview.py
```

`cleanup --dry-run` до первого завершённого дневного цикла должен показывать
ноль строк.

## Переключение

```bash
.venv/bin/python scripts/set_webhook.py
```

После этого:

1. отправить тестовое сообщение в подключённую группу;
2. убедиться, что оно появилось в MySQL один раз;
3. проверить групповой `/summary`;
4. проверить личный `/summary` и выбор группы;
5. вручную выполнить `recover` дважды — второй запуск не должен создавать
   повторных эффектов;
6. проверить `/health`.

## Первый дневной цикл

До автоматической очистки вручную выполнить:

```bash
.venv/bin/python -m app.jobs.cli prepare
.venv/bin/python -m app.jobs.cli publish
.venv/bin/python -m app.jobs.cli archive
.venv/bin/python -m app.jobs.cli cleanup --dry-run
```

Сверить `daily_lifecycle`, `archived_messages` и `archived_summaries`. Только
после этого добавить production-команду `cleanup` в панели Beget.

## Restore drill

Backup считается проверенным только после тестового восстановления в отдельную
временную MySQL-базу:

```bash
gzip -dc backups/brevity-YYYYMMDDTHHMMSSZ.sql.gz \
  | MYSQL_PWD=... mysql -h ... -u ... temporary_restore_database
```

Проверить число таблиц и строк, затем удалить временную базу.

## Неоднозначная доставка Telegram

Статус `review_required` у публикации означает, что Telegram-вызов завершился
неопределённо. Такая публикация не отправляется автоматически повторно, чтобы
не создавать дубль. Перед ручным решением нужно проверить группу и логи.
То же правило применяется к `telegram_effects` обработанного update.

## Rollback

1. Не запускать cleanup.
2. Вернуть webhook старой версии или временно удалить webhook.
3. Включить старые cron-задачи.
4. Сохранить MySQL и логи 0.2.0 для разбора; не импортировать их обратно в
   SQLite автоматически.
