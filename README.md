# BrevitySisterBot 0.2.0

Production-версия Telegram-бота ежедневных групповых сводок:

- webhook на Python WSGI/Passenger;
- MySQL как единственная рабочая и архивная база;
- Groq API с моделью `qwen/qwen3.6-27b`;
- локальный эвристический fallback при недоступности модели;
- автоматическая публикация в `21:00` по Москве;
- групповые и приватные сводки по запросу;
- архив, backup, диагностика и recovery без постоянно работающего процесса.

Историческая версия `0.1.0` сохранена без runtime-секретов в
[`mvp-1/`](mvp-1/).

## Как работает

Webhook немедленно сохраняет каждый Telegram update в MySQL и сразу пытается
обработать его. Поэтому запрос `/summary` видит все сообщения, уже доставленные
webhook’ом, и не ждёт пятиминутный cron. Recovery раз в пять минут нужен только
для подбора прерванных или временно неуспешных задач.

Сообщения уникальны по `(chat_id, telegram_message_id)`. Генерация сводки
резервируется по хэшу исходных сообщений до обращения в Groq, поэтому
параллельные одинаковые запросы не запускают модель повторно.

Исходящие Telegram-ответы внутри webhook фиксируются в `telegram_effects` до
API-вызова. Recovery пропускает уже начатый эффект, поэтому падение Passenger в
неоднозначный момент не создаёт повторное сообщение; такой эффект виден в
операционном аудите как `review_required`.

Основное расписание:

- `20:45` — подготовка автоматических сводок;
- `21:00` — публикация;
- `21:15` — идемпотентное копирование текущего и предыдущего дня в архив;
- `21:30` — очистка предыдущего дня, только после успешной сводки либо явно
  зафиксированного пропуска и проверки архивной копии;
- каждые 5 минут — recovery Telegram update’ов;
- `03:15` — retention;
- `03:30` — backup MySQL.

## Команды бота

- `/enable_daily` — подключить группу, только для администратора;
- `/disable_daily` — выключить ежедневную публикацию; сообщения продолжат
  накапливаться для ручных сводок;
- `/summary` в группе — подготовить сводку и опубликовать её в группе;
- `/summary` в личном чате — получить сводку приватно.

Подключить группу можно, только когда и вызвавший `/enable_daily` пользователь,
и сам бот являются её администраторами. После подключения бот публикует кнопку
«Получить сводку лично»: она открывает личный чат, проверяет членство и сразу
запускает подготовку сводки выбранной группы.

Можно также сразу отправить `/summary` в личном чате, не выполняя команду в
группе заранее. Если у пользователя нет запомненной доступной группы, бот
показывает нативную кнопку выбора Telegram; если доступно несколько известных
групп — предлагает выбрать одну из них. Перед каждой приватной выдачей членство
повторно проверяется через `getChatMember`.

Порог автоматической сводки — 15 сообщений, ручной — 5. Перед генерацией бот
предупреждает, что обработка может занять до 2 минут.

## Локальная установка

Требуется Python 3.10+ и MySQL 8-compatible.

```bash
python3.10 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
cp .env.example .env
```

Заполните `.env`, затем примените схему:

```bash
.venv/bin/python scripts/migrate.py
```

Проверка:

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check app scripts tests passenger_wsgi.py public_html/passenger_wsgi.py
.venv/bin/python scripts/render_copy_deck.py --check
```

Пользовательские тексты, шаблоны сводки и LLM-промпты находятся в
[`content/bot_copy.ru.toml`](content/bot_copy.ru.toml). После изменения TOML
перезапустите процесс бота и перегенерируйте редакторскую копию командой
`.venv/bin/python scripts/render_copy_deck.py`; режим `--check` проверяет, что
[`docs/BOT_COPY_RU.md`](docs/BOT_COPY_RU.md) синхронизирован, не изменяя файл.

## Beget

Production URL:

```text
https://brevity.prosto-kosmos.ru
https://brevity.prosto-kosmos.ru/telegram/webhook
```

Ожидаемая структура:

```text
~/brevity.prosto-kosmos.ru/
  .env
  .venv/
  app/
  content/
  migrations/
  scripts/
  public_html/
    passenger_wsgi.py
  backups/
  logs/
  tmp/
```

Подготовка:

1. Создать отдельную MySQL-базу и пользователя для BrevitySisterBot.
2. В BotFather отключить privacy mode, иначе бот не получит обычные сообщения
   группы и не сможет составить сводку.
3. В настройках проекта Groq включить Zero Data Retention и создать API key.
4. Создать remote `.env` по [`.env.example`](.env.example). Обязательно задать
   `TELEGRAM_BOT_USERNAME` без символа `@`: он нужен для групповой кнопки и
   private deep link.
5. Создать локальный `.env.hosting` по
   [`hosting.env.example`](hosting.env.example).
6. Настроить Python/Passenger для каталога поддомена.
7. Запустить:

```bash
scripts/deploy_beget.exp
```

Deploy не переносит `.env`, SQLite, backup, логи, `.git` и локальные virtualenv,
но загружает runtime-каталог `content/` вместе с приложением. После загрузки он
до применения миграций запускает `scripts/backup_db.sh`, затем устанавливает
зависимости, применяет миграции (включая `002_private_summary_onboarding.sql`) и
создаёт `tmp/restart.txt` для перезапуска Passenger. Такой порядок сохраняет
предмиграционную копию MySQL и доставляет TOML до перезапуска процесса.

После первого deploy:

```bash
.venv/bin/python scripts/set_webhook.py
.venv/bin/python scripts/smoke_preview.py
```

`set_webhook.py` подписывает webhook на `message`, `callback_query` и
`chat_member`. События `chat_member` нужны, чтобы актуализировать доступ к
приватным сводкам после вступления пользователя в группу или выхода из неё.

Задания из [`crontab.beget.example`](crontab.beget.example) нужно добавить
через панель Beget, заменив `LOGIN` на логин аккаунта.

## Импорт 0.1.0

Сначала сделайте неизменяемые копии старых SQLite-файлов. Импорт открывает
источники в read-only режиме и использует идемпотентные вставки:

```bash
.venv/bin/python scripts/import_mvp.py \
  --bot-db /path/to/bot.db \
  --archive-db /path/to/archive.db
```

Автоматическую очистку следует включать только после проверки количества
импортированных строк и успешного backup MySQL.

## Операционные команды

```bash
.venv/bin/python -m app.jobs.cli recover
.venv/bin/python -m app.jobs.cli prepare
.venv/bin/python -m app.jobs.cli publish
.venv/bin/python -m app.jobs.cli archive
.venv/bin/python -m app.jobs.cli cleanup
.venv/bin/python -m app.jobs.cli purge
.venv/bin/python -m app.jobs.cli audit
/bin/sh scripts/backup_db.sh
```

Все cron-команды используют MySQL lease, чтобы одинаковые задания не
выполнялись параллельно.

## Документация

- [Архитектурная спецификация на русском](docs/superpowers/specs/2026-07-16-brevity-sister-bot-0.2.0-design-ru.md)
- [Architecture specification in English](docs/superpowers/specs/2026-07-16-brevity-sister-bot-0.2.0-design.md)
- [Implementation plan](docs/superpowers/plans/2026-07-16-brevity-sister-bot-0.2.0.md)
- [Production cutover checklist](docs/PRODUCTION_CUTOVER.md)
